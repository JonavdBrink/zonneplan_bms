from typing import Any, Dict, Optional, List

from homeassistant.helpers.entity import Entity
# from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import STATE_ON, STATE_OFF
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from datetime import datetime, timezone, timedelta
from .const import DOMAIN, FORECAST_SENSOR, PEAK_SENSOR, LOGGER, CONF_FORECAST_ENTITY, CONF_RTE_PERCENT, CONF_MIN_PROFIT, CONF_CHARGE_HOURS, CONF_DISCHARGE_HOURS
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType

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
    """Set up the Battery Optimizer Sensor."""
    config = hass.data[DOMAIN]
    
    forecast_entity_id = config.get(CONF_FORECAST_ENTITY)
    charge_hours = config.get(CONF_CHARGE_HOURS)
    discharge_hours = config.get(CONF_DISCHARGE_HOURS)
    price_delta_percent = config.get(CONF_RTE_PERCENT)
    min_profit_c_kwh = config.get(CONF_MIN_PROFIT)

    async_add_entities([
        PeakSensor(
            hass,
            PEAK_SENSOR,
            price_delta_percent,
            min_profit_c_kwh,
            charge_hours,
            discharge_hours
        ),
        BatteryOptimizerSensor(
            hass,
            forecast_entity_id,
            charge_hours,
            discharge_hours,
            price_delta_percent,
            min_profit_c_kwh
        )
    ], True)

class PeakSensor(Entity):
    _attr_icon = "mdi:code-array"
    def __init__(self, hass, name, percentage, cents, charge_hours, discharge_hours):
        self.hass = hass
        self._attr_name = name.replace("_", " ").title()
        self._attr_unique_id = f"{DOMAIN}_{name}"
        self._state = None
        self._peaks = {}
        self._attrs = {}
        self._rte = 0.75
        self._rte_minimal_percentage = percentage
        self._rte_minimal_cents = cents
        self._charge_hours = charge_hours
        self._discharge_hours = discharge_hours

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
            entry_type=DeviceEntryType.SERVICE,
        )

DEFAULT_NAME = "Battery Optimizer"
ICON = "mdi:battery-sync"
SCAN_INTERVAL = timedelta(minutes=5)

# State definitions
ACTION_CHARGE = "Charge"
ACTION_DISCHARGE = "Discharge"
ACTION_STOP = "Stop"
ACTION_SUPER_DISCHARGE = "Super Discharge"
ACTION_SELF_CONSUME = "Self Consumption"


class BatteryOptimizerSensor(SensorEntity):
    """Representation of the Battery Optimizer Sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        forecast_entity_id: str,
        charge_hours: int,
        discharge_hours: int,
        price_delta_percent: float,
        min_profit_c_kwh: float
    ):
        """Initialize the sensor."""
        self._hass = hass
        self._sensor_key = "battery_optimizer"
        self._name = "Battery Optimizer"
        self._forecast_entity_id = forecast_entity_id
        self._charge_hours_per_interval = charge_hours
        self._discharge_hours_per_interval = discharge_hours
        self._price_delta_percent = price_delta_percent
        # Convert minimal profit from cents/kWh to €/kWh
        self._min_profit_eur_kwh = min_profit_c_kwh / 100.0
        self._state = ACTION_STOP
        self._attributes: Dict[str, Any] = {
            "schedule": [],
            "intervals": 0,
            "min_profit_required_eur_kwh": self._min_profit_eur_kwh,
            "charge_hours_per_interval": self._charge_hours_per_interval,
            "discharge_hours_per_interval": self._discharge_hours_per_interval,
            "price_delta_threshold_percent": self._price_delta_percent,
        }
    
    @property
    def unique_id(self) -> Optional[str]:
        """Return a unique ID."""
        return DOMAIN + "_" + self._sensor_key

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, DOMAIN)},
            name=DEFAULT_NAME,
            manufacturer="Custom BESS Optimization",
            model="Energy Arbitrage Scheduler",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self) -> str:
        """Return the state of the sensor (current action)."""
        return self._state

    @property
    def unit_of_measurement(self) -> Optional[str]:
        """Return the unit of measurement."""
        return None

    @property
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        return ICON

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes."""
        return self._attributes

    def _convert_price(self, price_int: int) -> float:
        """
        Converts the raw integer price to €/kWh.
        
        The raw integer is typically in a scaled unit (e.g., deci-micro-euro)
        and must be divided by 10,000,000.0 to get the price in Euro/kWh (€/kWh).
        """
        return price_int / 10_000_000.0

    def _calculate_action_schedule(self, forecast_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Main logic to segment and determine the optimal action schedule."""
        if not forecast_data:
            return []

        # 1. Price Conversion and Data Preparation
        prepared_data = []
        for item in forecast_data:
            try:
                price_int = item['electricity_price']
                price_eur_kwh = self._convert_price(price_int)
                
                # Check for solar yield to flag potential self-consumption
                has_solar_yield = item.get('solar_yield', 0) > 0 or item.get('solar_percentage', 0) > 0
                
                prepared_data.append({
                    'datetime': item['datetime'],
                    'price_eur_kwh': price_eur_kwh,
                    'is_solar_hour': has_solar_yield,
                    'action': ACTION_STOP, # Default action
                    'interval_id': -1,
                })
            except KeyError as e:
                LOGGER.error(f"Missing key in forecast data: {e}")
                continue

        # Check for overall profit margin (Q)
        prices = [item['price_eur_kwh'] for item in prepared_data]
        if not prices:
            return []

        # 2. Interval Detection (Z - Price Delta Percentage)
        intervals = []
        current_interval = []
        interval_id_counter = 0
        
        # Start the first interval based on the first data point
        current_interval.append(prepared_data[0])
        current_min_price = prepared_data[0]['price_eur_kwh']
        
        # Threshold calculation based on the low point
        threshold_multiplier = 1 + (self._price_delta_percent / 100.0)

        for i in range(1, len(prepared_data)):
            current_data = prepared_data[i]
            current_price = current_data['price_eur_kwh']
            
            # 1. Update minimum price for the current running interval
            if current_price < current_min_price:
                current_min_price = current_price
            
            # 2. Check if the price has exceeded the threshold to signal the start of a new peak cycle
            threshold = current_min_price * threshold_multiplier

            if current_price > threshold and len(current_interval) > 0:
                # The current price signals a significant rise, so the previous interval ends here
                # Finalize the existing interval
                interval_id_counter += 1
                for item in current_interval:
                    item['interval_id'] = interval_id_counter
                intervals.append(current_interval)
                
                # Start a new interval with the current data point
                current_interval = [current_data]
                current_min_price = current_price # New minimum for the new interval cycle
            
            else:
                # Continue the current interval
                current_interval.append(current_data)

        # 3. Add the last interval
        if current_interval:
            interval_id_counter += 1
            for item in current_interval:
                item['interval_id'] = interval_id_counter
            intervals.append(current_interval)
            
        self._attributes['intervals'] = len(intervals)

        # 3.5. Enforce Minimal Profit Margin (Q) Per Interval
        valid_intervals = []
        for i, interval in enumerate(intervals):
            interval_prices = [h['price_eur_kwh'] for h in interval]
            
            # This check should ideally not fail if interval detection worked, but included for robustness.
            if not interval_prices:
                continue

            interval_min_price = min(interval_prices)
            interval_max_price = max(interval_prices)
            interval_profit = interval_max_price - interval_min_price

            if interval_profit >= self._min_profit_eur_kwh:
                valid_intervals.append(interval)
            else:
                LOGGER.debug(f"Interval {i+1} skipped: Profit (€{interval_profit:.4f}) < Required (€{self._min_profit_eur_kwh:.4f}). Arbitrage suspended for this period.")

        # 4. Action Assignment (Laden / Ontladen / Super Ontladen)
        
        # Determine global super discharge hour (highest price overall)
        super_discharge_hour = max(prepared_data, key=lambda x: x['price_eur_kwh'])
        
        for interval in valid_intervals:
            # Sort by price for charging and discharging decisions within the interval
            sorted_by_price = sorted(interval, key=lambda x: x['price_eur_kwh'])
            
            # Find the X cheapest hours for LADEN
            charge_slots = sorted_by_price[:self._charge_hours_per_interval]
            
            # Find the Y most expensive hours for ONTLADEN
            # Use max(1, ...) to ensure a positive slice, and max(0, ...) to not exceed list length
            discharge_slots = sorted_by_price[-min(self._discharge_hours_per_interval, len(sorted_by_price)):]
            
            # Assign actions based on rank
            for slot in charge_slots:
                slot['action'] = ACTION_CHARGE
                
            for slot in discharge_slots:
                slot['action'] = ACTION_DISCHARGE

        # 5. Final Pass: Solar / Super Discharge (Overrides Laden/Ontladen if applicable)
        for hour in prepared_data:
            # Self-Consumption Override (If solar is present, prioritize using/selling solar power)
            if hour['is_solar_hour']:
                hour['action'] = ACTION_SELF_CONSUME
            
            # Super Discharge Override (The single most expensive hour)
            if hour == super_discharge_hour:
                hour['action'] = ACTION_SUPER_DISCHARGE
        
        return prepared_data

    async def async_update(self) -> None:
        """Get the latest forecast data and updates the state."""
        LOGGER.debug(f"Updating BESS Optimizer Sensor from {self._forecast_entity_id}")
        
        forecast_state = self._hass.states.get(self._forecast_entity_id)

        if not forecast_state:
            LOGGER.error(f"Forecast entity {self._forecast_entity_id} not found.")
            self._state = "Error"
            return

        # The forecast data is usually in the 'forecast' attribute of the source entity
        forecast_data = forecast_state.attributes.get("forecast")
        
        if not isinstance(forecast_data, list):
            LOGGER.error("Forecast data attribute 'forecast' is missing or not a list.")
            self._state = "Error"
            return

        # Calculate the new schedule
        try:
            schedule = self._calculate_action_schedule(forecast_data)
        except Exception as e:
            LOGGER.error(f"Error during schedule calculation: {e}")
            self._state = "Error"
            return
        
        # Update attributes with the full schedule
        self._attributes['schedule'] = schedule

        # Determine the current action based on the nearest future hour
        now_dt = datetime.now(timezone.utc)
        current_action = ACTION_STOP

        for hour in schedule:
            try:
                # Use datetime component to parse the timestamp
                hour_dt = datetime.strptime(hour["datetime"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            except ValueError:
                LOGGER.warning(f"Could not parse datetime: {hour['datetime']}")
                continue
            
            # Check if this hour is the current or next hour
            # The forecast is hourly, so we check if 'now' is within that hour block
            if hour_dt <= now_dt < hour_dt + timedelta(hours=1):
                current_action = hour['action']
                break
        
        self._state = current_action
        LOGGER.debug(f"Current BESS action set to: {self._state}")
