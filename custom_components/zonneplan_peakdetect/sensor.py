
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_STOP,
    CONF_CHARGE_HOURS,
    CONF_DISCHARGE_HOURS,
    CONF_FORECAST_ENTITY,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    DOMAIN,
    LOGGER,
)

SENSOR_DESCRIPTION = SensorEntityDescription(
    key="Action",
    name="Battery Optimizer",
    icon="mdi:battery-sync",
)


async def async_setup_entry(hass: HomeAssistant, config_entry: Any, async_add_entities: Any) -> None:
    """Set up the Battery Optimizer Sensor."""
    config = config_entry.data
    
    forecast_entity_id = config.get(CONF_FORECAST_ENTITY)
    charge_hours = config.get(CONF_CHARGE_HOURS)
    discharge_hours = config.get(CONF_DISCHARGE_HOURS)
    price_delta_percent = config.get(CONF_RTE_PERCENT)
    min_profit_c_kwh = config.get(CONF_MIN_PROFIT)

    async_add_entities([
        BatteryOptimizerSensor(
            config_entry.entry_id,
            forecast_entity_id,
            charge_hours,
            discharge_hours,
            price_delta_percent,
            min_profit_c_kwh,
            SENSOR_DESCRIPTION
        )
    ], True)


class BatteryOptimizerSensor(SensorEntity, RestoreEntity):
    """Representation of the Battery Optimizer Sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        forecast_entity_id: str,
        charge_hours: int,
        discharge_hours: int,
        price_delta_percent: float,
        min_profit_c_kwh: float,
        description: SensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_name = description.key
        self._forecast_entity_id = forecast_entity_id
        self._charge_hours_per_interval = charge_hours
        self._discharge_hours_per_interval = discharge_hours
        self._price_delta_percent = price_delta_percent
        # Convert minimal profit from cents/kWh to €/kWh
        self._min_profit_eur_kwh = min_profit_c_kwh / 100.0
        self._attr_native_value = ACTION_STOP
        self._attr_extra_state_attributes: dict[str, Any] = {
            "schedule": [],
            "intervals": 0,
            "min_profit_required_eur_kwh": self._min_profit_eur_kwh,
            "charge_hours_per_interval": self._charge_hours_per_interval,
            "discharge_hours_per_interval": self._discharge_hours_per_interval,
            "price_delta_threshold_percent": self._price_delta_percent,
        }
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=description.name,
            manufacturer="Custom BESS Optimization",
            model="Energy Arbitrage Scheduler",
            entry_type=DeviceEntryType.SERVICE,
        )
    
    async def async_added_to_hass(self) -> None:
        """Register listeners when entity is added."""
        await super().async_added_to_hass()
        
        self.async_on_remove(
            async_track_state_change_event(self.hass, self._forecast_entity_id, self._handle_forecast_update)
        )
        await self.async_update()

    @callback
    def _handle_forecast_update(self, event: Any) -> None:
        """Callback to force recalculation when forecast sensor changes."""
        self.async_schedule_update_ha_state(force_refresh=True)

    def _convert_price(self, price_int: int) -> float:
        """
        Converts the raw integer price to €/kWh.
        
        The raw integer is typically in a scaled unit (e.g., deci-micro-euro)
        and must be divided by 10,000,000.0 to get the price in Euro/kWh (€/kWh).
        """
        return price_int / 10_000_000.0

    def _calculate_action_schedule(self, forecast_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Main logic to segment and determine the optimal action schedule."""
        if not forecast_data:
            return []
        
        # 1. Prepare Data
        now = dt_util.now()
        prepared_data = []
        running_min = float('inf')
        for idx, item in enumerate(forecast_data):
            raw_price = item.get('electricity_price')
            raw_dt = item.get('datetime')
            
            if raw_price is None or raw_dt is None:
                LOGGER.warning("Incomplete forecast data at index %d: %s", idx, item)
                continue
                
            price = self._convert_price(raw_price)
            if price < running_min:
                running_min = price
            
            dt = dt_util.parse_datetime(raw_dt)
            is_passed = dt < now if dt else False

            prepared_data.append({
                'datetime': raw_dt,
                'price_eur_kwh': price,
                'price_multiplier': round(price / running_min, 2) if running_min > 0 else round(1.0 + price / abs(running_min), 2) if running_min != 0 else 1.0,
                'action': ACTION_STOP,
                'interval_id': -1 if is_passed else 0,
                'sort_index': idx
            })

        # 2. Segment into Waves (Intervals)
        prices = [item['price_eur_kwh'] for item in prepared_data]
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

            # Process if profit threshold is met
            if (peak_max - valley_min) >= self._min_profit_eur_kwh:                
                # CHARGE: Select cheapest hours in this wave before the valley
                charge_cands = [h for h in segment if h['sort_index'] < valley_idx and peak_max - h['price_eur_kwh'] > self._min_profit_eur_kwh ]
                charge_cands.sort(key=lambda x: x['price_eur_kwh'])
                if not charge_cands:
                    current_idx = peak_idx
                    continue
                charge_slots = charge_cands[:self._charge_hours_per_interval]
                                
                # DISCHARGE: Select most expensive hours in this wave after the valley
                discharge_cands = [h for h in segment if h['sort_index'] >= valley_idx and h['price_eur_kwh'] - valley_min > self._min_profit_eur_kwh]
                discharge_cands.sort(key=lambda x: x['price_eur_kwh'], reverse=True)
                if not discharge_cands:
                    current_idx = peak_idx
                    continue
                discharge_slots = discharge_cands[:self._discharge_hours_per_interval]

                # Balance charge and discharge slots
                num_slots = min(len(charge_slots), len(discharge_slots))
                charge_slots = charge_slots[:num_slots]
                discharge_slots = discharge_slots[:num_slots]

                for s in segment:
                    s['interval_id'] = interval_count

                for s in charge_slots:
                    s['action'] = ACTION_CHARGE
                                
                for s in discharge_slots:
                    s['action'] = ACTION_DISCHARGE
                
                interval_count += 1
            
            # Move index forward to the end of this wave
            current_idx = peak_idx

        self._attr_extra_state_attributes['intervals'] = interval_count
        # Remove helper key before returning
        for item in prepared_data: item.pop('sort_index', None)
        return prepared_data

    async def async_update(self) -> None:
        """Get the latest forecast data and update the state."""
        LOGGER.debug("Updating BESS Optimizer Sensor from %s", self._forecast_entity_id)
        
        state = self.hass.states.get(self._forecast_entity_id)

        if not state or "forecast" not in state.attributes:
            LOGGER.warning("Forecast entity %s or its forecast attribute not found", self._forecast_entity_id)
            return

        schedule = self._calculate_action_schedule(state.attributes.get("forecast"))
        self._attr_extra_state_attributes['schedule'] = schedule

        if len(schedule) <= 0:
            self._attr_native_value = ACTION_STOP

        now = dt_util.now()
        for i in schedule:
            dt = dt_util.parse_datetime(i["datetime"])
            if dt and dt <= now < dt + timedelta(hours=1):
                self._attr_native_value = i['action']
                break

        LOGGER.debug("Current BESS action set to: %s", self._attr_native_value)
