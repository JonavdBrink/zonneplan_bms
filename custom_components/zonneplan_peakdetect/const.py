from __future__ import annotations
from dataclasses import dataclass
from logging import Logger, getLogger
LOGGER: Logger = getLogger(__package__)

DOMAIN = "zonneplan_peakdetect"
PEAK_SENSOR = "zonneplan_peak_hour"
FORECAST_SENSOR = "sensor.zonneplan_current_electricity_tariff"

# Configuratiesleutels
CONF_CHARGE_HOURS = "charge_hours"
CONF_DISCHARGE_HOURS = "discharge_hours"
CONF_RTE_PERCENT = "price_delta_percent"
CONF_MIN_PROFIT = "min_profit_c_kwh"
CONF_FORECAST_ENTITY = "forecast_entity"

# Standaardwaarden (optioneel)
DEFAULT_PERCENTAGE = 20
DEFAULT_CENTS = 6
DEFAULT_CHARGE_HOURS = 2
DEFAULT_DISCHARGE_HOURS = 2
DEFAULT_FORECAST_ENTITY = "sensor.zonneplan_current_electricity_tariff"
