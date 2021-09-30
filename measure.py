#!/usr/bin/env python3


import time
from board import SDA, SCL
import busio
from adafruit_pm25.i2c import PM25_I2C
from adafruit_sgp30 import Adafruit_SGP30
from adafruit_scd30 import SCD30
import libhoney
from ruamel.yaml import YAML
import os
import sys

# These are configuration keys, to be found in your config.yaml file.
CONFIG_WRITE_KEY = "write_key"
CONFIG_DATASET = "dataset"
CONFIG_SAMPLE_FREQUENCY = "sample_frequency"
CONFIG_CO2_BASELINE = "co2_baseline"
CONFIG_TVOC_BASELINE = "tvoc_baseline"
CONFIG_NODE_ID = "node_id"
CONFIG_QUIET_MODE = "quiet"
CONFIG_SENSORS = "sensors"

SENSOR_PM25 = "pm25"
SENSOR_SGP30 = "sgp30"
SENSOR_SCD30 = "scd30"
DEFAULT_SENSORS = [SENSOR_PM25, SENSOR_SGP30]

BASELINE_FREQUENCY = 60

# Require the config-file to be present as a command-line arg. This works best with Systemd.
if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} [CONFIG_YAML_PATH]")
    exit(1)

# Path to the configuration file, which is referenced in a couple of places.
CONFIG_PATH = sys.argv[1]


def read_config():
    """
    Read the configuration YAML file into a dict.
    :return: The dict containing the configuration
    """
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return YAML().load(f)

    return None


def read_particulates(sensor, event, quiet=False):
    """
    Read the PMSA003I air quality sensor to get particulate data.
    :param sensor: The sensor object to read from
    :param event: The Honeycomb event into which sensor reading should be added. Can be None
    :param quiet: If True (and the Honeycomb event is available), sensor readings are suppressed from STDOUT
    :return: None
    """
    try:
        aqdata = sensor.read()
        # print(aqdata)
    except RuntimeError:
        print("Unable to read from sensor, retrying...")
        return

    if event is not None:
        if quiet is False:
            # NOTE: This output format was copied from the Adafruit example for this sensor, at:
            # https://github.com/adafruit/Adafruit_CircuitPython_PM25/blob/main/examples/pm25_simpletest.py#L60-L80
            print()
            print("Concentration Units (standard)")
            print("---------------------------------------")
            print(
                "PM 1.0: %d\tPM2.5: %d\tPM10: %d"
                % (
                    aqdata["pm10 standard"],
                    aqdata["pm25 standard"],
                    aqdata["pm100 standard"],
                )
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

        for k, v in aqdata.items():
            event.add_field(k.replace(" ", "_"), v)


def read_volatiles(sensor, baseline_counter, event, quiet=False):
    """
    Read the SGP30 MOX sensor to get CO₂ and VOC data
    :param sensor: The SGP30 sensor to read
    :param baseline_counter: The last time we collected / printed candidate baseline settings (useful for calibrating)
    :param event: The Honeycomb event, into which sensor readings should be reported
    :param quiet: If True (and the Honeycomb event is available), sensor readings are suppressed from STDOUT
    :return: The new since_baseline counter value
    """
    baseline_counter += 1

    if event is not None:
        if quiet is False:
            print(f"eCO2 = {sensor.eCO2} ppm \t TVOC = {sensor.TVOC} ppb")

        event.add_field("eCO2_ppm", sensor.eCO2)
        event.add_field("TVOC_ppb", sensor.TVOC)

    # Print / report a new candidate baseline every 10th measurement. This helps us calibrate the sensor when needed.
    if baseline_counter > BASELINE_FREQUENCY:
        baseline_counter = 0

        if event is not None:
            if quiet is False:
                print(
                    f"Baseline values: eCO2 = 0x{sensor.baseline_eCO2:x}, TVOC = 0x{sensor.baseline_TVOC:x}"
                )

            event.add_field("baseline_eCO2", f"0x{sensor.baseline_eCO2:x}")
            event.add_field("baseline_TVOC", f"0x{sensor.baseline_TVOC:x}")

    return baseline_counter


def read_real_co2(sensor, event, quiet=False):
    """
    Read the SCD30 sensor to get a direct measurement of CO₂ ppm
    :param sensor: The SCD30 sensor to read
    :param event: The Honeycomb event, into which sensor readings should be reported (may be None)
    :param quiet: If True (and the Honeycomb event is available), sensor readings are suppressed from STDOUT
    :return: None
    """
    for i in range(30):
        if sensor.data_available:
            if event is not None:
                if quiet is False:
                    print(
                        f"CO₂: {sensor.CO2:.3g} PPM / "
                        f"Temp: {sensor.temperature:.3g}\N{DEGREE SIGN} C / "
                        f"Humidity: {sensor.relative_humidity:.3g}%"
                    )

                event.add_field("scd30.actual_CO2", sensor.CO2)
                event.add_field("scd30.temp_C", sensor.temperature)
                event.add_field("scd30.temp_F", ((9 / 5) * sensor.temperature) + 32)
                event.add_field("scd30.rel_humidity", round(sensor.relative_humidity))

            return None

        time.sleep(0.5)


def init_electronics(config):
    """
    Setup the sensors and the I²C bus. If we have a baseline configuration for the SGP30 sensor, set that here too.
    :param config: The configuration dict as read from the YAML file
    :return: The sensor objects, for use in the rest of the application
    """
    reset_pin = None

    print("Starting I2C bus...")

    # Create library object, use 'slow' 100KHz frequency!
    i2c = busio.I2C(SCL, SDA, frequency=100000)

    sensors = config.get(CONFIG_SENSORS) or DEFAULT_SENSORS

    pm25 = None
    if SENSOR_PM25 in sensors:
        print("Starting PM2.5 sensor")
        pm25 = PM25_I2C(i2c, reset_pin)
        print("Found PM2.5 sensor.")

    sgp30 = None
    if SENSOR_SGP30 in sensors:
        print("Starting SGP30 MOX sensor")
        sgp30 = Adafruit_SGP30(i2c)
        print("Found SGP30, serial #", [hex(i) for i in sgp30.serial])

        sgp30.iaq_init()
        if (
            config.get(CONFIG_CO2_BASELINE) is not None
            and config.get(CONFIG_TVOC_BASELINE) is not None
        ):
            # This is the baseline that Adafruit mentions in their example code.
            # sgp30.set_iaq_baseline(0x8973, 0x8AAE)
            # We'll set the calibration baseline from what we have in the config YAML
            # (which was detected by running the sensor outside for 10 mins or so, and reading what it reported)
            sgp30.set_iaq_baseline(
                config[CONFIG_CO2_BASELINE], config[CONFIG_TVOC_BASELINE]
            )

    scd30 = None
    if SENSOR_SCD30 in sensors:
        print("Starting SCD30 CO2 sensor")
        scd30 = SCD30(i2c)
        print("Found SCD30 sensor.")

    return pm25, sgp30, scd30


def run():
    """
    Main applicaiton loop. Initialize the sensors, initialize Honeycomb (if enabled), and begin taking readings. This
    logic will read the sensors every N seconds, where N can be configured in the sample_frequency parameter of the
    configuration file.
    :return: None
    """
    config = read_config()
    if config is None:
        print(f"No configuration found on: {CONFIG_PATH}")
        exit(1)

    # Setup the sensors and I²C bus
    pm25, sgp30, scd30 = init_electronics(config)

    # Setup Honeycomb reporting...
    honeycomb_enabled = False

    # Read some basic parameters from config, for our sensor loop...
    sample_freq = config.get(CONFIG_SAMPLE_FREQUENCY) or 60
    node_id = config.get(CONFIG_NODE_ID) or "unknown"
    quiet = config.get(CONFIG_QUIET_MODE) or False

    # NOTE: We use dict.get() here, not dict[key], since the keys may be missing!
    if (
            config.get(CONFIG_WRITE_KEY) is not None
            and config.get(CONFIG_DATASET) is not None
    ):
        libhoney.init(writekey=config[CONFIG_WRITE_KEY], dataset=config[CONFIG_DATASET], sample_rate=sample_freq)
        honeycomb_enabled = True

    baseline_counter = 0

    # Loop forever...
    while True:
        # If Honeycomb is available, we MAY initialize a new event for the sensors to use (see below)
        event = None

        # NOTE: This while loop will run every second, regardless of sampling frequency.
        # This means sampling frequency is actually DOWN-SAMPLING the basic loop.
        #
        # This means sampling_frequency is still specified in terms of seconds between reported samples,
        # even though we're pulling data from the sensor every second.
        if honeycomb_enabled:
            event = libhoney.new_event()
            event.add_field("node", node_id)

        # Read particulate data, then read CO₂ / VOC data...report it to screen and/or Honeycomb event
        if pm25 is not None:
            read_particulates(pm25, event, quiet)

        if sgp30 is not None:
            baseline_counter = read_volatiles(sgp30, baseline_counter, event, quiet)

        if scd30 is not None:
            read_real_co2(scd30, event, quiet)

        # If we're using Honeycomb, close/send the event.
        if event is not None:
            event.send()

            # This is going to be a low-frequency sensor, so let's avoid buffering.
            libhoney.flush()

        # Sleep for the number of seconds dictated by the sampling frequency, then we do another sensor pass
        time.sleep(1)


if __name__ == "__main__":
    run()
