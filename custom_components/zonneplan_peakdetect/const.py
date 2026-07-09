from __future__ import annotations
from dataclasses import dataclass
from logging import Logger, getLogger
LOGGER: Logger = getLogger(__package__)

DOMAIN = "zonneplan_peakdetect"
PEAK_SENSOR = "zonneplan_peak_hour"
FORECAST_SENSOR = "sensor.zonneplan_current_quarter_hourly_electricity_tariff"

# Configuratiesleutels
CONF_CHARGE_HOURS = "charge_hours"  # Deprecated, fallback only
CONF_DISCHARGE_HOURS = "discharge_hours"  # Deprecated, fallback only
CONF_CHARGE_QUARTERS = "charge_quarters"
CONF_DISCHARGE_QUARTERS = "discharge_quarters"
CONF_RTE_PERCENT = "price_delta_percent"
CONF_MIN_PROFIT = "min_profit_c_kwh"
CONF_FORECAST_ENTITY = "forecast_entity"

# Standaardwaarden (optioneel)
DEFAULT_PERCENTAGE = 20
DEFAULT_CENTS = 6
DEFAULT_CHARGE_HOURS = 2  # Deprecated, fallback only
DEFAULT_DISCHARGE_HOURS = 2  # Deprecated, fallback only
DEFAULT_CHARGE_QUARTERS = 8
DEFAULT_DISCHARGE_QUARTERS = 8
DEFAULT_FORECAST_ENTITY = "sensor.zonneplan_current_quarter_hourly_electricity_tariff"

# State definitions
ACTION_CHARGE = "Charge"
ACTION_DISCHARGE = "Discharge"
ACTION_STOP = "Stop"
