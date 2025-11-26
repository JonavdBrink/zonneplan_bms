from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "zonneplan_peakdetect"
PEAK_SENSOR = "zonneplan_peak_hour"
FORECAST_SENSOR = "sensor.zonneplan_current_electricity_tariff"

# Configuratiesleutels
CONF_PERCENTAGE = "rte_minimal_percentage"
CONF_CENTS = "minimal_profit_in_cents"
CONF_CHARGE_HOURS = "charge_hours"
CONF_DISCHARGE_HOURS = "discharge_hours"

# Standaardwaarden (optioneel)
DEFAULT_PERCENTAGE = 20
DEFAULT_CENTS = 6
DEFAULT_CHARGE_HOURS = 2
DEFAULT_DISCHARGE_HOURS = 2