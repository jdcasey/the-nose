#!/usr/bin/env python
"""
Read air quality data from various Adafruit sensors using CircuitPython, and emit events
over OpenTelemetry OTLP-HTTP protocol for datalogging.
"""

import os
import sys
import time

import busio
from adafruit_ms8607 import MS8607
from adafruit_pm25.i2c import PM25_I2C
from adafruit_scd30 import SCD30
from adafruit_sgp30 import Adafruit_SGP30
from board import SDA, SCL
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from ruamel.yaml import YAML

# These are configuration keys, to be found in your config.yaml file.
CONFIG_SAMPLE_FREQUENCY = "sample_frequency"
CONFIG_CO2_BASELINE = "co2_baseline"
CONFIG_TVOC_BASELINE = "tvoc_baseline"
CONFIG_LOCATION = "location"
CONFIG_QUIET_MODE = "quiet"
CONFIG_SENSORS = "sensors"

SENSOR_PM25 = "pm25"
SENSOR_SGP30 = "sgp30"
SENSOR_SCD30 = "scd30"
SENSOR_MS8607 = "ms8607"
DEFAULT_SENSORS = [SENSOR_PM25, SENSOR_SGP30]

BASELINE_FREQUENCY = 60

# Setup OpenTelemetry tracing for emitting data events
provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)


# Require the config-file to be present as a command-line arg. This works best with Systemd.
if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} [CONFIG_YAML_PATH]")
    sys.exit(1)

# Path to the configuration file, which is referenced in a couple of places.
CONFIG_PATH = sys.argv[1]


def read_config():
    """
    Read the configuration YAML file into a dict.
    :return: The dict containing the configuration
    """
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return YAML().load(f)

    return None


def read_particulates(sensor, quiet=False):
    """
    Read the PMSA003I air quality sensor to get particulate data.
    :param sensor: The sensor object to read from
    :param quiet: If True (or the Honeycomb event is unavailable), STDOUT is suppressed
    :return: None
    """
    try:
        aqdata = sensor.read()
        # print(aqdata)
    except RuntimeError:
        print("Unable to read from sensor, retrying...")
        return

    if quiet is False:
        # NOTE: This output format was copied from the Adafruit example for this sensor, at:
        # https://github.com/adafruit/Adafruit_CircuitPython_PM25/blob/main/examples/pm25_simpletest.py#L60-L80
        print()
        print("Concentration Units (standard)")
        print("---------------------------------------")
        print(
            f"PM 1.0: {aqdata['pm10 standard']}\tPM2.5: {aqdata['pm25 standard']}\tPM10: {aqdata['pm100 standard']}"
        )
        print("Concentration Units (environmental)")
        print("---------------------------------------")
        print(
            f"PM 1.0: {aqdata['pm10 env']}\tPM2.5: {aqdata['pm25 env']}\tPM10: {aqdata['pm100 env']}"
        )
        print("---------------------------------------")
        print("Particles > 0.3um / 0.1L air:", aqdata["particles 03um"])
        print("Particles > 0.5um / 0.1L air:", aqdata["particles 05um"])
        print("Particles > 1.0um / 0.1L air:", aqdata["particles 10um"])
        print("Particles > 2.5um / 0.1L air:", aqdata["particles 25um"])
        print("Particles > 5.0um / 0.1L air:", aqdata["particles 50um"])
        print("Particles > 10 um / 0.1L air:", aqdata["particles 100um"])
        print("---------------------------------------")

    for key, value in aqdata.items():
        trace.get_current_span().set_attribute(key.replace(" ", "_"), value)


def read_volatiles(sensor, baseline_counter, quiet=False):
    """
    Read the SGP30 MOX sensor to get CO₂ and VOC data
    :param sensor: The SGP30 sensor to read
    :param baseline_counter: The last time we collected / printed candidate baseline settings
    (useful for calibrating)
    :param quiet: If True (or the Honeycomb event is unavailable), STDOUT is suppressed
    :return: The new since_baseline counter value
    """
    baseline_counter += 1

    if quiet is False:
        print(f"eCO2 = {sensor.eCO2} ppm \t TVOC = {sensor.TVOC} ppb")

    trace.get_current_span().set_attributes({
        "eCO2_ppm": sensor.eCO2,
        "TVOC_ppb": sensor.TVOC,
    })

    # Print / report a new candidate baseline every 10th measurement.
    # This helps us calibrate the sensor when needed.
    if baseline_counter > BASELINE_FREQUENCY:
        baseline_counter = 0

        if quiet is False:
            print(
                f"Baseline values: eCO2 = 0x{sensor.baseline_eCO2:x}, TVOC = 0x{sensor.baseline_TVOC:x}"
            )

        trace.get_current_span().set_attributes({
            "baseline_eCO2": f"0x{sensor.baseline_eCO2:x}",
            "baseline_TVOC": f"0x{sensor.baseline_TVOC:x}",
        })

    return baseline_counter


def read_real_co2(sensor, quiet=False):
    """
    Read the SCD30 sensor to get a direct measurement of CO₂ ppm
    :param sensor: The SCD30 sensor to read
    :param quiet: If True (or the Honeycomb event is unavailable), STDOUT is suppressed
    :return: None
    """
    for _i in range(30):
        if sensor.data_available:
            if quiet is False:
                print(
                    f"CO₂: {sensor.CO2:.3g} PPM / "
                    f"Temp: {sensor.temperature:.3g}\N{DEGREE SIGN} C / "
                    f"Humidity: {sensor.relative_humidity:.3g}%"
                )

            trace.get_current_span().set_attributes({
                "scd30.actual_CO2": sensor.CO2,
                "scd30.temp_C": sensor.temperature,
                "scd30.temp_F": ((9 / 5) * sensor.temperature) + 32,
                "scd30.rel_humidity": round(sensor.relative_humidity)
            })

            return None

        time.sleep(0.5)

    return None


def read_pht(sensor, quiet=False):
    """
    Read the MS8607 PHT sensor to get direct measurements of pressure, relative humidity, and temp.
    :param sensor: The MS8607 sensor to read
    :param quiet: If True (or the Honeycomb event is unavailable), STDOUT is suppressed
    :return: None
    """
    if quiet is False:
        print(f"Pressure: {sensor.pressure:.2f} hPa")
        print(f"Temperature: {sensor.temperature:.2f} C")
        print(f"Humidity: {sensor.relative_humidity:.2f} % rH")

    trace.get_current_span().set_attributes({
        "ms8607.pressure_hPa": sensor.pressure,
        "ms8607.temp_C": sensor.temperature,
        "ms8607.temp_F": ((9 / 5) * sensor.temperature) + 32,
        "ms8607.rel_humidity": round(sensor.relative_humidity),
    })


def init_electronics(config):
    """
    Setup the sensors and the I²C bus. If we have a baseline configuration for the SGP30 sensor,
    set that here too.
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
            # (which was detected by running the sensor outside for 10 mins or so, and reading
            # what it reported)
            sgp30.set_iaq_baseline(
                config[CONFIG_CO2_BASELINE], config[CONFIG_TVOC_BASELINE]
            )

    scd30 = None
    if SENSOR_SCD30 in sensors:
        print("Starting SCD30 CO2 sensor")
        scd30 = SCD30(i2c)
        print("Found SCD30 CO2 sensor.")

    ms8607 = None
    if SENSOR_MS8607 in sensors:
        print("Starting MS8607 Pressure, Humidity, and Temp (PHT) sensor")
        ms8607 = MS8607(i2c)
        print("Found MS8607 PHT sensor")

    return pm25, sgp30, scd30, ms8607


@tracer.start_as_current_span("sniff")
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
        sys.exit(1)

    # Setup the sensors and I²C bus
    pm25, sgp30, scd30, ms8607 = init_electronics(config)

    # Read some basic parameters from config, for our sensor loop...
    location = config.get(CONFIG_LOCATION) or "unknown"
    quiet = config.get(CONFIG_QUIET_MODE) or False
    sleep_seconds = config.get(CONFIG_SAMPLE_FREQUENCY) or BASELINE_FREQUENCY # seconds

    baseline_counter = 0

    # Loop forever...
    while True:
        start = time.time()

        # NOTE: This while loop will run every second, regardless of sampling frequency.
        # This means sampling frequency is actually DOWN-SAMPLING the basic loop.
        #
        # This means sampling_frequency is still specified in terms of seconds between reported
        # samples, even though we're pulling data from the sensor every second.
        trace.get_current_span().set_attributes({"location": location})

        # Read particulate data, then read CO₂ / VOC data...report it to screen and OTel event
        if pm25 is not None:
            read_particulates(pm25, quiet)

        if sgp30 is not None:
            baseline_counter = read_volatiles(sgp30, baseline_counter, quiet)

        if scd30 is not None:
            read_real_co2(scd30, quiet)

        if ms8607 is not None:
            read_pht(ms8607, quiet)

        # Sleep for the number of seconds dictated by the sampling frequency,
        # then we do another sensor pass
        sleep = max(sleep_seconds - (time.time()-start), 1)
        time.sleep(sleep)


if __name__ == "__main__":
    run()
