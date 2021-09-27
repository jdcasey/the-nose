#!/usr/bin/env python3

import time
import board
import busio
from adafruit_pm25.i2c import PM25_I2C
from adafruit_sgp30 import Adafruit_SGP30
import libhoney
from ruamel.yaml import YAML
import os
import sys

WRITE_KEY = 'write_key'
DATASET = 'dataset'
SAMPLE_FREQUENCY = 'sample_frequency'
CO2_BASELINE = 'co2_baseline'
TVOC_BASELINE = 'tvoc_baseline'
NODE_ID = 'node_id'
QUIET_MODE = 'quiet'

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} [CONFIG_YAML_PATH]")
    exit(1)

CONFIG_PATH = os.path.join(sys.argv[1])


def read_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return YAML().load(f)

    return None


def read_particulates(sensor, event, quiet=False):
    try:
        aqdata = sensor.read()
        # print(aqdata)
    except RuntimeError:
        print("Unable to read from sensor, retrying...")
        return

    if not quiet:
        print()
        print("Concentration Units (standard)")
        print("---------------------------------------")
        print(
            "PM 1.0: %d\tPM2.5: %d\tPM10: %d"
            % (aqdata["pm10 standard"], aqdata["pm25 standard"], aqdata["pm100 standard"])
        )
        print("Concentration Units (environmental)")
        print("---------------------------------------")
        print(
            "PM 1.0: %d\tPM2.5: %d\tPM10: %d"
            % (aqdata["pm10 env"], aqdata["pm25 env"], aqdata["pm100 env"])
        )
        print("---------------------------------------")
        print("Particles > 0.3um / 0.1L air:", aqdata["particles 03um"])
        print("Particles > 0.5um / 0.1L air:", aqdata["particles 05um"])
        print("Particles > 1.0um / 0.1L air:", aqdata["particles 10um"])
        print("Particles > 2.5um / 0.1L air:", aqdata["particles 25um"])
        print("Particles > 5.0um / 0.1L air:", aqdata["particles 50um"])
        print("Particles > 10 um / 0.1L air:", aqdata["particles 100um"])
        print("---------------------------------------")

    if event is not None:
        for k, v in aqdata.items():
            event.add_field(k.replace(" ", "_"), v)


def read_volatiles(sensor, since_baseline, event, quiet=False):
    since_baseline += 1

    if not quiet:
        print(f"eCO2 = {sensor.eCO2} ppm \t TVOC = {sensor.TVOC} ppb")

    if event is not None:
        event.add_field("eCO2_ppm", sensor.eCO2)
        event.add_field("TVOC_ppb", sensor.TVOC)

    if since_baseline > 10:
        since_baseline = 0

        if not quiet:
            print(f"Baseline values: eCO2 = 0x{sensor.baseline_eCO2:x}, TVOC = 0x{sensor.baseline_TVOC:x}")

        if event is not None:
            event.add_field("baseline_eCO2", f"0x{sensor.baseline_eCO2:x}")
            event.add_field("baseline_TVOC", f"0x{sensor.baseline_TVOC:x}")

    return since_baseline


def init_electronics(config):
    reset_pin = None

    print("Starting I2C bus...")

    # Create library object, use 'slow' 100KHz frequency!
    i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)

    print("Starting PM2.5 sensor")
    pm25 = PM25_I2C(i2c, reset_pin)

    print("Found PM2.5 sensor.\nStarting SGP30 MOX sensor")

    sgp30 = Adafruit_SGP30(i2c)

    if not quiet:
        print("Found SGP30, serial #", [hex(i) for i in sgp30.serial])

    sgp30.iaq_init()
    if config.get(CO2_BASELINE) is not None and config.get(TVOC_BASELINE) is not None:
        # sgp30.set_iaq_baseline(0x8973, 0x8AAE)
        sgp30.set_iaq_baseline(hex(int(config[CO2_BASELINE], 16)), hex(int(config[TVOC_BASELINE], 16)))

    return pm25, sgp30


def run():
    config = read_config()
    if config is None:
        print(f"No configuration found on: {CONFIG_PATH}")

    pm25, sgp30 = init_electronics(config)

    honeycomb_enabled = False
    if config.get(WRITE_KEY) is not None and config.get(DATASET) is not None:
        libhoney.init(writekey=config[WRITE_KEY], dataset=config[DATASET])
        honeycomb_enabled = True

    since_baseline = 0

    sample_freq = config.get(SAMPLE_FREQUENCY) or 60
    node_id = config.get(NODE_ID) or "unknown"
    quiet = config.get(QUIET_MODE) or False

    while True:
        time.sleep(sample_freq)

        event = None
        if honeycomb_enabled:
            event = libhoney.new_event()
            event.add_field("node", node_id)

        read_particulates(pm25, event, quiet)
        since_baseline = read_volatiles(sgp30, since_baseline, event, quiet)

        if honeycomb_enabled:
            event.send()

            # This is going to be a low-frequency sensor, so let's avoid buffering.
            libhoney.flush()


if __name__ == "__main__":
    run()
