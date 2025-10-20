from homeassistant.helpers.entity import Entity
# from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import callback
from homeassistant.const import STATE_ON, STATE_OFF
from datetime import datetime
from .const import DOMAIN, PEAK_SENSOR, VALLEY_SENSOR, HIGHEST_SENSOR, LOWEST_SENSOR, PRICE_SENSOR
from homeassistant.helpers.device_registry import DeviceInfo

import json

async def async_setup_entry(hass, config_entry, async_add_entities):
    sensors = [
        ZonneplanPeakSensor(hass, PEAK_SENSOR),
        ZonneplanValleySensor(hass, VALLEY_SENSOR),
        IsPeakHourSensor(hass, HIGHEST_SENSOR),
        IsLowestHourSensor(hass, LOWEST_SENSOR)
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

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "zonneplan_bms")},
            name="Zonneplan BMS",
            manufacturer="Zonneplan",
            model="BMS",
            entry_type="service"
        )

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

class BaseBoolSensor(Entity):
    def __init__(self, hass, name):
        self.hass = hass
        self._attr_name = name.replace("_", " ").title()
        self._attr_unique_id = f"{DOMAIN}_{name}"
        self._state = STATE_OFF

    async def async_added_to_hass(self):
        async_track_state_change_event(self.hass, PEAK_SENSOR, self._handle_event)
        async_track_state_change_event(self.hass, "sensor.time", self._handle_event)

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

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "zonneplan_bms")},
            name="Zonneplan BMS",
            manufacturer="Zonneplan",
            model="BMS",
            entry_type="service"
        )

class IsPeakHourSensor(BaseBoolSensor):
    def _handle_event(self, event):
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self):
        peak_state = self.hass.states.get(PEAK_SENSOR)
        current_hour = datetime.now().hour
        if peak_state and peak_state.state.isdigit():
            self._state = STATE_ON if int(peak_state.state) == current_hour else STATE_OFF

    @property
    def state(self):
        return self._state

class IsLowestHourSensor(BaseBoolSensor):
    @callback
    def _handle_event(self, event):
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self):
        valley_state = self.hass.states.get(VALLEY_SENSOR)
        current_hour = datetime.now().hour
        if valley_state and valley_state.state.isdigit():
            self._state = STATE_ON if int(valley_state.state) == current_hour else STATE_OFF

    @property
    def state(self):
        return self._state