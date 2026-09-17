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
CONF_ALGORITHM = "algorithm_type"
CONF_MULTIPLIER_ALGORITHM = "multiplier_type"
CONF_SOLAR_BONUS_PERCENT = "solar_bonus_percent"
CONF_SOLAR_BONUS_FIXED_C_KWH = "solar_bonus_fixed_c_kwh"
CONF_CHARGE_QUANTILE = "charge_multiplier_quantile"
CONF_DISCHARGE_QUANTILE = "discharge_multiplier_quantile"

# Algorithm types
ALGORITHM_WHSS = "whss"
ALGORITHM_HSWAS = "hswas"
ALGORITHM_MPES = "mpes"

# Multiplier calculation types
MULTIPLIER_CPWL = "cpwl"
MULTIPLIER_BLOCK = "block"
DEFAULT_MULTIPLIER_ALGORITHM = MULTIPLIER_BLOCK

# Standaardwaarden (optioneel)
DEFAULT_PERCENTAGE = 20
DEFAULT_CENTS = 6
DEFAULT_CHARGE_HOURS = 2  # Deprecated, fallback only
DEFAULT_DISCHARGE_HOURS = 2  # Deprecated, fallback only
DEFAULT_CHARGE_QUARTERS = 8
DEFAULT_DISCHARGE_QUARTERS = 8
DEFAULT_FORECAST_ENTITY = "sensor.zonneplan_current_quarter_hourly_electricity_tariff"
DEFAULT_ALGORITHM = ALGORITHM_WHSS
DEFAULT_SOLAR_BONUS_PERCENT = 10.0
DEFAULT_SOLAR_BONUS_FIXED_C_KWH = 2.0
DEFAULT_CHARGE_QUANTILE = 50.0
DEFAULT_DISCHARGE_QUANTILE = 75.0

# State definitions
ACTION_SAVE = "Save"
ACTION_CONSUME = "Consume"
ACTION_BUY = "Buy"
ACTION_SELL = "Sell"
ACTION_STOP = "Stop"
