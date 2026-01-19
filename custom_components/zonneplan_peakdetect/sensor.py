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
    config = config_entry.data
    
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
        return f"{DOMAIN}_{self._sensor_key}"

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
        
        self.async_on_remove(
            async_track_state_change_event(self._hass, self._forecast_entity_id, self._handle_forecast_update)
        )
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

        # 1. Prepare Data & Global Min for Multiplier
        prices = [self._convert_price(item['electricity_price']) for item in forecast_data]
        
        prepared_data = []
        for idx, item in enumerate(forecast_data):
            price = self._convert_price(item['electricity_price'])
            running_min = min(prices[:idx + 1])
            
            prepared_data.append({
                'datetime': item['datetime'],
                'price_eur_kwh': price,
                'price_multiplier': round(price / running_min, 2) if running_min > 0 else round(1.0 + price / abs(running_min), 2),
                'action': ACTION_STOP,
                'interval_id': 0,
                'sort_index': idx
            })

        # 2. Segment into Waves (Intervals)
        n = len(prepared_data)
        current_idx = 0
        interval_count = 0
        
        while current_idx < n - 1:
            # Step A: Find the NEXT local valley (dip) relative to current position
            # We track the minimum seen so far. We only 'break' the valley search if 
            # the price rises significantly (>= min_profit) above the current local minimum.
            valley_idx = current_idx
            valley_min = prices[current_idx]
            
            for j in range(current_idx, n):
                valley_idx = j
                if prices[j] < valley_min:
                    valley_min = prices[j]
                # Break if price recovers significantly
                if prices[j] >= (valley_min + self._min_profit_eur_kwh):
                    break
            
            # Step B: Find the NEXT local peak (hump) AFTER that specific valley
            # We track the maximum seen so far. We only 'break' the peak search if
            # the price drops significantly (>= min_profit) below the current local maximum.
            peak_idx = valley_idx
            peak_max = prices[valley_idx]
            
            for j in range(valley_idx, n):
                peak_idx = j
                if prices[j] > peak_max:
                    peak_max = prices[j]
                # Break if price drops significantly (indicating start of next wave)
                if prices[j] <= (peak_max - self._min_profit_eur_kwh):
                    break
            
            # Define the current wave segment
            segment = prepared_data[current_idx : peak_idx + 1]
            if not segment:
                current_idx += 1
                continue

            seg_min = min(h['price_eur_kwh'] for h in segment)
            seg_max = max(h['price_eur_kwh'] for h in segment if h['sort_index'] >= valley_idx)

            # Process if profit threshold is met
            if (seg_max - seg_min) >= self._min_profit_eur_kwh:
                interval_count += 1
                
                # CHARGE: Select cheapest hours in this specific wave
                charge_cands = [h for h in segment if h['sort_index'] < valley_idx ]
                charge_cands.sort(key=lambda x: (x['price_eur_kwh'], x['sort_index']))
                if not charge_cands:
                    break
                charge_slots = charge_cands[:self._charge_hours_per_interval]
                                
                discharge_cands = [h for h in segment if h['sort_index'] >= valley_idx]
                discharge_cands.sort(key=lambda x: (-x['price_eur_kwh'], x['sort_index']))
                if not discharge_cands:
                    break
                discharge_slots = discharge_cands[:self._discharge_hours_per_interval]
                
                for s in charge_slots:
                    s['action'] = ACTION_CHARGE
                    s['interval_id'] = interval_count
                                
                for s in discharge_slots:
                    s['action'] = ACTION_DISCHARGE
                    s['interval_id'] = interval_count
            
            # Move index forward to the end of this wave
            current_idx = peak_idx

        self._attributes['intervals'] = interval_count
        # Remove helper key before returning
        for item in prepared_data: item.pop('sort_index', None)
        return prepared_data

    async def async_update(self) -> None:
        """Get the latest forecast data and updates the state."""
        LOGGER.debug(f"Updating BESS Optimizer Sensor from {self._forecast_entity_id}")
        
        state = self._hass.states.get(self._forecast_entity_id)

        if not state or "forecast" not in state.attributes:
            LOGGER.error(f"Forecast entity {self._forecast_entity_id} not found.")
            return

        schedule = self._calculate_action_schedule(state.attributes.get("forecast"))
        self._attributes['schedule'] = schedule

        if len(schedule) <= 0:
            self._state = ACTION_STOP

        now = datetime.now(timezone.utc)
        for i in schedule:
            dt = datetime.strptime(i["datetime"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            if dt <= now < dt + timedelta(hours=1):
                self._state = i['action']
                break

        LOGGER.debug(f"Current BESS action set to: {self._state}")
