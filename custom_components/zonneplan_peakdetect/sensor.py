from typing import Any, Dict, Optional, List

from homeassistant.helpers.entity import Entity
# from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import callback
from homeassistant.const import STATE_ON, STATE_OFF
from homeassistant.components.sensor import SensorEntity
from datetime import datetime, timezone
from .const import DOMAIN, FORECAST_SENSOR, PEAK_SENSOR, LOGGER
from homeassistant.helpers.device_registry import DeviceInfo

import json

from dataclasses import dataclass
from typing import TYPE_CHECKING

@dataclass
class ForecastEntry:
    price: float
    datetime: datetime

def map_dict_to_entry(data: dict) -> ForecastEntry:
    time = datetime.strptime(data["datetime"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return ForecastEntry(
        price=data["electricity_price"],
        datetime=time
    )

async def async_setup_entry(hass, config_entry, async_add_entities):
    sensors = [
        PeakSensor(hass, PEAK_SENSOR)
    ]
    async_add_entities(sensors, True)

class PeakSensor(Entity):
    _attr_icon = "mdi:code-array"
    def __init__(self, hass, name):
        self.hass = hass
        self._attr_name = name.replace("_", " ").title()
        self._attr_unique_id = f"{DOMAIN}_{name}"
        self._state = None
        self._peaks = {}
        self._attrs = {}
        self._charge_hours = 2
        self._discharge_hours = 2
        self._rte = 0.75

    async def async_added_to_hass(self):
        async_track_state_change_event(self.hass, FORECAST_SENSOR, self._async_state_changed)

    @callback
    def _async_state_changed(self, event):
        self._process_forecast()
        self.async_write_ha_state()

    def _process_forecast(self):
        state = self.hass.states.get(FORECAST_SENSOR)
        if not state or not state.attributes:
            return

        forecast = state.attributes.get("forecast")
        if not forecast:
            return
        
        now = datetime.now(timezone.utc)

        forecast_data: Optional[List[Dict[str, Any]]] = state.attributes.get("forecast")

        LOGGER.debug("forecast_data '%s'", forecast_data)

        min_price_item = None
        max_price_item = None
        
        min_price_value = float("inf")
        max_price_value = float("-inf")

        # The mapping operation using Pydantic
        forecast_entries = [
            map_dict_to_entry(data)
            for data in forecast_data
        ]

        for item in forecast_entries:
            if item.datetime < now:
                continue

            if item.price < min_price_value:
                min_price_value = item.price
                min_price_item = item
            
            if item.price > max_price_value:
                max_price_value = item.price
                max_price_item = item
        
        LOGGER.debug("min_price_item '%s'", min_price_item)
        LOGGER.debug("max_price_item '%s'", max_price_item)
        self._state = now
        if min_price_item and max_price_item:
            # Scale the raw prices for display and round to 4 decimal places
            # lowest_price_scaled = round(min_price_value / self._scale_factor, 4)
            # highest_price_scaled = round(max_price_value / self._scale_factor, 4)
            
            # Extract and store time strings (ISO format)
            # lowest_price_time_raw = min_price_item.get("datetime")
            # highest_price_time_raw = max_price_item.get("datetime")

            self._peaks = {
                "min_hour": min_price_item,
                "max_hour": max_price_item,
            }

    @property
    def state(self):
        return self._state
    
    @property
    def extra_state_attributes(self):
        attrs = {}

        attrs["peaks"] = self._peaks
        
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "zonneplan_bms")},
            name="Zonneplan BMS",
            manufacturer="Zonneplan",
            model="BMS",
            entry_type="service"
        )
