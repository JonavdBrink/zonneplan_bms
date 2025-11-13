"""Custom types for zonneplan_bms"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration


type ZonneplanBmsConfigEntry = ConfigEntry[ZonneplanBmsData]

@dataclass
class ZonneplanBmsData:
    """Data for the Zonneplan BMS integration"""

    Integration: Integration
