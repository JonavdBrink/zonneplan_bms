from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "zonneplan_peakdetect"
PEAK_SENSOR = "zonneplan_peak_hour"
FORECAST_SENSOR = "sensor.zonneplan_current_electricity_tariff"
