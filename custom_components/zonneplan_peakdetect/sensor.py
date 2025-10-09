from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import callback
from .const import DOMAIN, PEAK_SENSOR, VALLEY_SENSOR, PRICE_SENSOR

import json

async def async_setup_entry(hass, config_entry, async_add_entities):
    sensors = [
        ZonneplanPeakSensor(hass, PEAK_SENSOR),
        ZonneplanValleySensor(hass, VALLEY_SENSOR)
    ]
    async_add_entities(sensors, True)

class BasePriceSensor(Entity):
    def __init__(self, hass, name):
        self.hass = hass
        self._attr_name = name.replace("_", " ").title()
        self._attr_unique_id = f"{DOMAIN}_{name}"
        self._state = None

    async def async_added_to_hass(self):
        async_track_state_change_event(self.hass, PRICE_SENSOR, self._handle_price_update)

    @callback
    def _handle_price_update(self, event):
        self._update_state()
        self.async_write_ha_state()

    def _parse_price_data(self):
        state = self.hass.states.get(PRICE_SENSOR)
        if not state or not state.attributes:
            return []

        prices = state.attributes.get("today")
        if not prices:
            return []

        try:
            return [float(p) for p in prices]
        except (ValueError, TypeError):
            return []

class ZonneplanPeakSensor(BasePriceSensor):
    def _update_state(self):
        prices = self._parse_price_data()
        if prices:
            self._state = prices.index(max(prices))

    @property
    def state(self):
        return self._state

class ZonneplanValleySensor(BasePriceSensor):
    def _update_state(self):
        prices = self._parse_price_data()
        if prices:
            self._state = prices.index(min(prices))

    @property
    def state(self):
        return self._state