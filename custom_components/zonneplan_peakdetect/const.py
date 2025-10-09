from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "zonneplan_peakdetect"
PEAK_SENSOR = "zonneplan_peak_hour"
VALLEY_SENSOR = "zonneplan_valley_hour"
PRICE_SENSOR = "sensor.zonneplan_energy_prices"  # Update this to match your entity