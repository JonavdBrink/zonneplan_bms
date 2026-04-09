from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_CHARGE_HOURS,
    CONF_DISCHARGE_HOURS,
    CONF_FORECAST_ENTITY,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    DEFAULT_CENTS,
    DEFAULT_CHARGE_HOURS,
    DEFAULT_DISCHARGE_HOURS,
    DEFAULT_FORECAST_ENTITY,
    DEFAULT_PERCENTAGE,
    DOMAIN,
)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zonneplan BMS."""

    VERSION = 1

    def _get_schema(self, user_input: dict[str, Any] | None = None) -> vol.Schema:
        """Get the data schema for user input."""
        if user_input is None:
            user_input = {}

        return vol.Schema({
            vol.Required(
                CONF_RTE_PERCENT, 
                default=user_input.get(CONF_RTE_PERCENT, DEFAULT_PERCENTAGE)
            ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=100.0)),
            vol.Required(
                CONF_MIN_PROFIT, 
                default=user_input.get(CONF_MIN_PROFIT, DEFAULT_CENTS)
            ): cv.positive_int,
            vol.Required(
                CONF_CHARGE_HOURS, 
                default=user_input.get(CONF_CHARGE_HOURS, DEFAULT_CHARGE_HOURS)
            ): cv.positive_int,
            vol.Required(
                CONF_DISCHARGE_HOURS, 
                default=user_input.get(CONF_DISCHARGE_HOURS, DEFAULT_DISCHARGE_HOURS)
            ): cv.positive_int,
            vol.Required(
                CONF_FORECAST_ENTITY, 
                default=user_input.get(CONF_FORECAST_ENTITY, DEFAULT_FORECAST_ENTITY)
            ): cv.string,
        })

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(
                title="Battery Optimizer settings", 
                data=user_input
            )

        return self.async_show_form(
            step_id="user", 
            data_schema=self._get_schema()
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reconfiguration."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry, 
                data=user_input
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._get_schema(entry.data),
        )