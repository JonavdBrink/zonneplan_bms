from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_CHARGE_QUARTERS,
    CONF_DISCHARGE_QUARTERS,
    CONF_FORECAST_ENTITY,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_ALGORITHM,
    CONF_MULTIPLIER_ALGORITHM,
    ALGORITHM_WHSS,
    ALGORITHM_HSWAS,
    ALGORITHM_MPES,
    MULTIPLIER_CPWL,
    MULTIPLIER_BLOCK,
    DEFAULT_CENTS,
    DEFAULT_CHARGE_QUARTERS,
    DEFAULT_DISCHARGE_QUARTERS,
    DEFAULT_FORECAST_ENTITY,
    DEFAULT_PERCENTAGE,
    DEFAULT_ALGORITHM,
    DEFAULT_MULTIPLIER_ALGORITHM,
    CONF_SOLAR_BONUS_PERCENT,
    CONF_SOLAR_BONUS_FIXED_C_KWH,
    CONF_SOLAR_BONUS_IN_ARBITRAGE,
    DEFAULT_SOLAR_BONUS_PERCENT,
    DEFAULT_SOLAR_BONUS_FIXED_C_KWH,
    DEFAULT_SOLAR_BONUS_IN_ARBITRAGE,
    CONF_CHARGE_QUANTILE,
    CONF_DISCHARGE_QUANTILE,
    DEFAULT_CHARGE_QUANTILE,
    DEFAULT_DISCHARGE_QUANTILE,
    DOMAIN,
)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zonneplan BMS."""

    VERSION = 1

    def _get_schema(self, user_input: dict[str, Any] | None = None) -> vol.Schema:
        """Get the data schema for user input."""
        if user_input is None:
            user_input = {}

        charge_default = user_input.get(CONF_CHARGE_QUARTERS)
        if charge_default is None:
            if "charge_hours" in user_input:
                charge_default = user_input["charge_hours"] * 4
            else:
                charge_default = DEFAULT_CHARGE_QUARTERS

        discharge_default = user_input.get(CONF_DISCHARGE_QUARTERS)
        if discharge_default is None:
            if "discharge_hours" in user_input:
                discharge_default = user_input["discharge_hours"] * 4
            else:
                discharge_default = DEFAULT_DISCHARGE_QUARTERS

        return vol.Schema({
            vol.Required(
                CONF_ALGORITHM,
                default=user_input.get(CONF_ALGORITHM, DEFAULT_ALGORITHM)
            ): vol.In([ALGORITHM_WHSS, ALGORITHM_HSWAS, ALGORITHM_MPES]),
            vol.Required(
                CONF_MULTIPLIER_ALGORITHM,
                default=user_input.get(CONF_MULTIPLIER_ALGORITHM, DEFAULT_MULTIPLIER_ALGORITHM)
            ): vol.In([MULTIPLIER_CPWL, MULTIPLIER_BLOCK]),
            vol.Required(
                CONF_RTE_PERCENT, 
                default=user_input.get(CONF_RTE_PERCENT, DEFAULT_PERCENTAGE)
            ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=100.0)),
            vol.Required(
                CONF_MIN_PROFIT, 
                default=user_input.get(CONF_MIN_PROFIT, DEFAULT_CENTS)
            ): cv.positive_int,
            vol.Required(
                CONF_CHARGE_QUARTERS, 
                default=charge_default
            ): cv.positive_int,
            vol.Required(
                CONF_DISCHARGE_QUARTERS, 
                default=discharge_default
            ): cv.positive_int,
            vol.Required(
                CONF_FORECAST_ENTITY, 
                default=user_input.get(CONF_FORECAST_ENTITY, DEFAULT_FORECAST_ENTITY)
            ): cv.string,
            vol.Required(
                CONF_SOLAR_BONUS_PERCENT,
                default=user_input.get(CONF_SOLAR_BONUS_PERCENT, DEFAULT_SOLAR_BONUS_PERCENT)
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0)),
            vol.Required(
                CONF_SOLAR_BONUS_FIXED_C_KWH,
                default=user_input.get(
                    CONF_SOLAR_BONUS_FIXED_C_KWH,
                    DEFAULT_SOLAR_BONUS_FIXED_C_KWH,
                )
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0)),
            vol.Required(
                CONF_CHARGE_QUANTILE,
                default=user_input.get(CONF_CHARGE_QUANTILE, DEFAULT_CHARGE_QUANTILE)
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0)),
            vol.Required(
                CONF_DISCHARGE_QUANTILE,
                default=user_input.get(CONF_DISCHARGE_QUANTILE, DEFAULT_DISCHARGE_QUANTILE)
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0)),
            vol.Required(
                CONF_SOLAR_BONUS_IN_ARBITRAGE,
                default=user_input.get(CONF_SOLAR_BONUS_IN_ARBITRAGE, DEFAULT_SOLAR_BONUS_IN_ARBITRAGE)
            ): bool,
        })

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            charge = user_input.get(CONF_CHARGE_QUANTILE)
            discharge = user_input.get(CONF_DISCHARGE_QUANTILE)
            rte = user_input.get(CONF_RTE_PERCENT)
            
            if charge is not None and discharge is not None and rte is not None:
                if discharge < charge:
                    errors["base"] = "quantile_order_error"
                elif discharge < charge + rte:
                    errors["base"] = "quantile_guard_band_error"
            
            if not errors:
                return self.async_create_entry(
                    title="Battery Optimizer settings", 
                    data=user_input
                )

        return self.async_show_form(
            step_id="user", 
            data_schema=self._get_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reconfiguration."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            charge = user_input.get(CONF_CHARGE_QUANTILE)
            discharge = user_input.get(CONF_DISCHARGE_QUANTILE)
            rte = user_input.get(CONF_RTE_PERCENT)
            
            if charge is not None and discharge is not None and rte is not None:
                if discharge < charge:
                    errors["base"] = "quantile_order_error"
                elif discharge < charge + rte:
                    errors["base"] = "quantile_guard_band_error"
            
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, 
                    data=user_input
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._get_schema(user_input or entry.data),
            errors=errors,
        )