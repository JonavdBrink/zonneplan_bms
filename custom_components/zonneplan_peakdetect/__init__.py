
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import DOMAIN, LOGGER
from .data import ZonneplanBmsConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .data import ZonneplanBmsConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    # Platform.BINARY_SENSOR,
    # Platform.SWITCH,
]

async def async_setup_entry(
        hass: HomeAssistant,
        entry: ZonneplanBmsConfigEntry
) -> bool:
    """Set up this integration using UI."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
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