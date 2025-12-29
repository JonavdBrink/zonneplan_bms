from typing import Any, Dict, Optional, List

from homeassistant.helpers.entity import Entity
# from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from datetime import datetime, timezone, timedelta
from .const import DOMAIN, LOGGER, CONF_FORECAST_ENTITY, CONF_RTE_PERCENT, CONF_MIN_PROFIT, CONF_CHARGE_HOURS, CONF_DISCHARGE_HOURS

import json

from dataclasses import dataclass
from typing import TYPE_CHECKING

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Battery Optimizer Sensor."""
    config = hass.data[DOMAIN]
    
    forecast_entity_id = config.get(CONF_FORECAST_ENTITY)
    charge_hours = config.get(CONF_CHARGE_HOURS)
    discharge_hours = config.get(CONF_DISCHARGE_HOURS)
    price_delta_percent = config.get(CONF_RTE_PERCENT)
    min_profit_c_kwh = config.get(CONF_MIN_PROFIT)

    async_add_entities([
        BatteryOptimizerSensor(
            hass,
            forecast_entity_id,
            charge_hours,
            discharge_hours,
            price_delta_percent,
            min_profit_c_kwh
        )
    ], True)

DEFAULT_NAME = "Battery Optimizer"
ICON = "mdi:battery-sync"
SCAN_INTERVAL = timedelta(minutes=5)

# State definitions
ACTION_CHARGE = "Charge"
ACTION_DISCHARGE = "Discharge"
ACTION_STOP = "Stop"

class BatteryOptimizerSensor(SensorEntity, RestoreEntity):
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
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        return ICON

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes."""
        return self._attributes
    
    async def async_added_to_hass(self):
        """Register listeners when entity is added."""
        await super().async_added_to_hass()
        
        # Listen for forecast state changes
        self.async_on_remove(
            async_track_state_change_event(self._hass, self._forecast_entity_id, self._handle_forecast_update)
        )
        # Perform initial calculation
        await self.async_update()

    @callback
    def _handle_forecast_update(self, event):
        """Callback to force recalculation when forecast sensor changes."""
        self.async_schedule_update_ha_state(force_refresh=True)

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
                price_eur_kwh = self._convert_price(item['electricity_price'])
                prepared_data.append({
                    'datetime': item['datetime'],
                    'price_eur_kwh': price_eur_kwh,
                    'price_multiplier': 1.0,  # Default multiplier
                    'action': ACTION_STOP, # Default action
                    'interval_id': -1,
                })
            except KeyError as e:
                LOGGER.error(f"Missing key in forecast data: {e}")
                continue
        
        if not prepared_data:
            return []

        # 2. Interval Detection (Z - Price Delta Percentage)
        intervals = []
        current_interval = []
        interval_id_counter = 0
        current_min_price = prepared_data[0]['price_eur_kwh']
        threshold_multiplier = 1 + (self._price_delta_percent / 100.0)

        for i in range(len(prepared_data)):
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

        # Add the last interval
        if current_interval:
            interval_id_counter += 1
            for item in current_interval:
                item['interval_id'] = interval_id_counter
            intervals.append(current_interval)

        # 3. Validation & Action Assignment
        valid_intervals_count = 0
        for interval in intervals:
            prices = [h['price_eur_kwh'] for h in interval]
            if not prices: continue

            i_min = min(prices)
            i_max = max(prices)

            # Calculate multiplier for every hour in this interval relative to interval low
            for hour in interval:
                if i_min == 0:
                    # Offset by 1 cent (€0.01) to provide a realistic relative ratio
                    hour['price_multiplier'] = round(hour['price_eur_kwh'] / 0.01, 4)
                elif i_min > 0:
                    hour['price_multiplier'] = round(hour['price_eur_kwh'] / i_min, 4)
                else:
                    hour['price_multiplier'] = round(1.0 + (hour['price_eur_kwh'] / abs(i_min)), 4)
                    
            # Only assign actions if the interval meets the profit requirement
            if (i_max - i_min) >= self._min_profit_eur_kwh:
                valid_intervals_count += 1
                sorted_by_price = sorted(interval, key=lambda x: x['price_eur_kwh'])
                
                # Charge slots (cheapest)
                for slot in sorted_by_price[:self._charge_hours_per_interval]:
                    slot['action'] = ACTION_CHARGE
                
                # Discharge slots (most expensive)
                # Ensure we don't overwrite charge if interval is very short
                for slot in sorted_by_price[-self._discharge_hours_per_interval:]:
                    if slot['action'] != ACTION_CHARGE:
                        slot['action'] = ACTION_DISCHARGE

        self._attributes['intervals'] = valid_intervals_count        
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
            self._attributes['schedule'] = schedule
        except Exception as e:
            LOGGER.error(f"Error during schedule calculation: {e}")
            self._state = "Error"
            return
        
        # Determine the current action based on the nearest future hour
        now_dt = datetime.now(timezone.utc)
        current_action = ACTION_STOP

        for hour in schedule:
            try:
                # Re-parse to compare with current time
                h_dt = datetime.strptime(hour["datetime"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
                if h_dt <= now_dt < h_dt + timedelta(hours=1):
                    current_action = hour['action']
                    break
            except ValueError:
                LOGGER.warning(f"Could not parse datetime: {hour['datetime']}")
                continue
        
        self._state = current_action
        LOGGER.debug(f"Current BESS action set to: {self._state}")
