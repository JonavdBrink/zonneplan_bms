from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "zonneplan_peakdetect"
PEAK_SENSOR = "zonneplan_peak_hour"
VALLEY_SENSOR = "zonneplan_valley_hour"
HIGHEST_SENSOR = "zonneplan_is_highest_hour"
LOWEST_SENSOR = "zonneplan_is_lowest_hour"
PRICE_SENSOR = "sensor.zonneplan_energy_prices"  # Update this to match your entity