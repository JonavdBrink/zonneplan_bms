from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .data import ZonneplanBmsConfigEntry, ZonneplanBmsData

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
]

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZonneplanBmsConfigEntry
) -> bool:
    """Set up this integration using UI."""
    entry.runtime_data = ZonneplanBmsData(
        integration=entry.version, # Placeholder or actual integration object if needed
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(
    hass: HomeAssistant,
    entry: ZonneplanBmsConfigEntry
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)