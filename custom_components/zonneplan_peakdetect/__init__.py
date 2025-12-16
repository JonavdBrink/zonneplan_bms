
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_FORECAST_ENTITY, CONF_RTE_PERCENT, CONF_MIN_PROFIT, CONF_CHARGE_HOURS, CONF_DISCHARGE_HOURS

from .data import ZonneplanBmsConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .data import ZonneplanBmsConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
]

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZonneplanBmsConfigEntry
) -> bool:
    """Set up this integration using UI."""
    # Haal de opgeslagen waarden op uit de entry.data dictionary
    price_delta_percent = entry.data[CONF_RTE_PERCENT]
    min_profit_c_kwh = entry.data[CONF_MIN_PROFIT]
    charge_hours = entry.data[CONF_CHARGE_HOURS]
    discharge_hours = entry.data[CONF_DISCHARGE_HOURS]
    forecast_entity = entry.data[CONF_FORECAST_ENTITY]

    # Sla de configuratie op in het 'data' domein van de Home Assistant core
    hass.data[DOMAIN] = {
        CONF_RTE_PERCENT: price_delta_percent,
        CONF_MIN_PROFIT: min_profit_c_kwh,
        CONF_CHARGE_HOURS: charge_hours,
        CONF_DISCHARGE_HOURS: discharge_hours,
        CONF_FORECAST_ENTITY: forecast_entity
    }

    # Sensorplatform laden
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    )
    
    return True

async def async_unload_entry(
    hass: HomeAssistant,
    entry: ZonneplanBmsConfigEntry
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

async def async_reload_entry(
    hass: HomeAssistant,
    entry: ZonneplanBmsConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)