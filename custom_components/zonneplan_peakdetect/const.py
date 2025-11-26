from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "zonneplan_peakdetect"
PEAK_SENSOR = "zonneplan_peak_hour"
FORECAST_SENSOR = "sensor.zonneplan_current_electricity_tariff"

# Configuratiesleutels
CONF_PERCENTAGE = "rte_minimal_percentage"
CONF_CENTS = "minimal_profit_in_cents"

# Standaardwaarden (optioneel)
DEFAULT_PERCENTAGE = 20
DEFAULT_CENTS = 6