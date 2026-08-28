
from __future__ import annotations

from datetime import datetime, timedelta
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
    CONF_CHARGE_QUARTERS,
    CONF_DISCHARGE_QUARTERS,
    CONF_FORECAST_ENTITY,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_ALGORITHM,
    DEFAULT_ALGORITHM,
    DOMAIN,
    LOGGER,
)
from .strategies import get_arbitrage_strategy

SENSOR_DESCRIPTION = SensorEntityDescription(
    key="Action",
    name="Battery Optimizer",
    icon="mdi:battery-sync",
)


async def async_setup_entry(hass: HomeAssistant, config_entry: Any, async_add_entities: Any) -> None:
    """Set up the Battery Optimizer Sensor."""
    config = config_entry.data
    
    forecast_entity_id = config.get(CONF_FORECAST_ENTITY)
    
    # Backwards compatibility fallback from charge_hours to charge_quarters
    charge_quarters = config.get(CONF_CHARGE_QUARTERS)
    if charge_quarters is None:
        charge_quarters = config.get("charge_hours", 2) * 4

    discharge_quarters = config.get(CONF_DISCHARGE_QUARTERS)
    if discharge_quarters is None:
        discharge_quarters = config.get("discharge_hours", 2) * 4

    price_delta_percent = config.get(CONF_RTE_PERCENT)
    min_profit_c_kwh = config.get(CONF_MIN_PROFIT)
    algorithm_type = config.get(CONF_ALGORITHM, DEFAULT_ALGORITHM)

    async_add_entities([
        BatteryOptimizerSensor(
            config_entry.entry_id,
            forecast_entity_id,
            charge_quarters,
            discharge_quarters,
            price_delta_percent,
            min_profit_c_kwh,
            algorithm_type,
            SENSOR_DESCRIPTION
        )
    ], True)


def _parse_datetime(val: Any) -> datetime | None:
    """Safely parse a datetime object or string."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return dt_util.parse_datetime(val)
    return None


class BatteryOptimizerSensor(SensorEntity, RestoreEntity):
    """Representation of the Battery Optimizer Sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        forecast_entity_id: str,
        charge_quarters: int,
        discharge_quarters: int,
        price_delta_percent: float,
        min_profit_c_kwh: float,
        algorithm_type: str,
        description: SensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_name = description.key
        self._forecast_entity_id = forecast_entity_id
        self._charge_quarters = charge_quarters
        self._discharge_quarters = discharge_quarters
        self._price_delta_percent = price_delta_percent
        self._algorithm_type = algorithm_type
        # Convert minimal profit from cents/kWh to €/kWh
        self._min_profit_eur_kwh = min_profit_c_kwh / 100.0
        self._attr_native_value = ACTION_STOP
        self._attr_extra_state_attributes: dict[str, Any] = {
            "schedule": [],
            "intervals": 0,
            "min_profit_required_eur_kwh": self._min_profit_eur_kwh,
            "charge_quarters": self._charge_quarters,
            "discharge_quarters": self._discharge_quarters,
            "price_delta_threshold_percent": self._price_delta_percent,
            "algorithm_type": self._algorithm_type,
            "current_price_multiplier": 1.0,
            "price_multiplier_quartiles": None,
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
        
        rte_factor = 1.0 - (self._price_delta_percent / 100.0)
        
        # 1. Prepare Data
        now = dt_util.now()
        prepared_data = []
        for idx, item in enumerate(forecast_data):
            # Backwards-compatible format extraction (supporting both old and new schema)
            raw_dt = item.get('start_date')
            if raw_dt is None:
                raw_dt = item.get('datetime')

            raw_price = None
            price_tax_included = item.get('price_tax_included')
            if isinstance(price_tax_included, dict):
                raw_price = price_tax_included.get('amount')
            if raw_price is None:
                raw_price = item.get('electricity_price')
            
            if raw_dt is None:
                LOGGER.warning("Incomplete forecast data (missing datetime) at index %d: %s", idx, item)
                continue
                
            if 'price_eur_kwh' in item:
                price = item['price_eur_kwh']
            elif raw_price is not None:
                price = self._convert_price(raw_price)
            else:
                LOGGER.warning("Incomplete forecast data (missing price) at index %d: %s", idx, item)
                continue
                
            dt = _parse_datetime(raw_dt)
            is_passed = dt < now if dt else False

            prepared_data.append({
                'datetime': raw_dt,
                'price_eur_kwh': price,
                'price_multiplier': 1.0,
                'action': ACTION_STOP,
                'interval_id': -1 if is_passed else 0,
                'sort_index': idx
            })

        # 2. Determine interval duration and slot counts from configured quarters
        interval_minutes = 60
        if len(prepared_data) > 1:
            dt1 = _parse_datetime(prepared_data[0]['datetime'])
            dt2 = _parse_datetime(prepared_data[1]['datetime'])
            if dt1 and dt2:
                diff = (dt2 - dt1).total_seconds() / 60.0
                if diff > 0:
                    interval_minutes = int(diff)

        if self._charge_quarters > 0:
            charge_slots_count = max(1, int(round(self._charge_quarters * 15.0 / interval_minutes)))
        else:
            charge_slots_count = 0

        if self._discharge_quarters > 0:
            discharge_slots_count = max(1, int(round(self._discharge_quarters * 15.0 / interval_minutes)))
        else:
            discharge_slots_count = 0

        # Execute the chosen algorithm strategy polymorphically
        strategy = get_arbitrage_strategy(self._algorithm_type)
        schedule = strategy.calculate_schedule(
            prepared_data,
            charge_slots_count,
            discharge_slots_count,
            rte_factor,
            self._min_profit_eur_kwh,
            now
        )
        
        # Recalculate price_multiplier using windows partitioned by the algorithm-found intervals
        n = len(schedule)
        if n > 0:
            # Find the end of each wave's active slots (Charge/Discharge)
            last_active_indices = {}
            for idx, item in enumerate(schedule):
                iid = item.get('interval_id', -1)
                if item.get('action') != ACTION_STOP:
                    if iid >= 0:
                        last_active_indices[iid] = idx

            active_waves = sorted(last_active_indices.keys())

            if not active_waves:
                # Fallback to absolute minimum of the entire forecast if no active intervals are found
                global_min = min(item['price_eur_kwh'] for item in schedule)
                for item in schedule:
                    p = item['price_eur_kwh']
                    item['price_multiplier'] = round(p / global_min, 2) if global_min > 0 else round(1.0 + p / abs(global_min), 2) if global_min != 0 else 1.0
            else:
                # Partition into windows anchored at the end of each active wave segment
                windows = []
                prev_end = 0
                for iid in active_waves:
                    end_idx = last_active_indices[iid] + 1
                    windows.append((prev_end, end_idx))
                    prev_end = end_idx
                
                if windows:
                    # Extend the last window to cover the trailing part of the day
                    last_start, _ = windows[-1]
                    windows[-1] = (last_start, n)

                # For each window, find its minimum price and calculate multipliers
                for start, end in windows:
                    window_slice = schedule[start:end]
                    if window_slice:
                        window_min = min(item['price_eur_kwh'] for item in window_slice)
                        for item in window_slice:
                            p = item['price_eur_kwh']
                            item['price_multiplier'] = round(p / window_min, 2) if window_min > 0 else round(1.0 + p / abs(window_min), 2) if window_min != 0 else 1.0

        # Read total interval count directly from scheduled data attributes
        intervals = len(set(h['interval_id'] for h in schedule if h.get('interval_id', -1) >= 0))
        self._attr_extra_state_attributes['intervals'] = intervals

        # Remove helper key before returning
        for item in schedule: item.pop('sort_index', None)
        return schedule

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
        interval_minutes = 60
        if len(schedule) > 1:
            dt1 = _parse_datetime(schedule[0]["datetime"])
            dt2 = _parse_datetime(schedule[1]["datetime"])
            if dt1 and dt2:
                diff = (dt2 - dt1).total_seconds() / 60.0
                if diff > 0:
                    interval_minutes = int(diff)

        current_multiplier = 1.0
        for i in schedule:
            dt = _parse_datetime(i["datetime"])
            if dt and dt <= now < dt + timedelta(minutes=interval_minutes):
                self._attr_native_value = i['action']
                current_multiplier = i.get('price_multiplier', 1.0)
                break

        self._attr_extra_state_attributes['current_price_multiplier'] = current_multiplier

        # Expose the statistical quartiles of the price multipliers over the entire scheduled forecast
        multipliers = [i.get('price_multiplier', 1.0) for i in schedule]
        if len(multipliers) >= 2:
            import statistics
            try:
                q = statistics.quantiles(multipliers, n=4)
                self._attr_extra_state_attributes['price_multiplier_quartiles'] = {
                    'min': round(min(multipliers), 2),
                    'q25': round(q[0], 2),
                    'q50': round(q[1], 2),
                    'q75': round(q[2], 2),
                    'max': round(max(multipliers), 2)
                }
            except Exception as e:
                LOGGER.warning("Failed to calculate price multiplier quartiles: %s", e)
                self._attr_extra_state_attributes['price_multiplier_quartiles'] = None
        else:
            self._attr_extra_state_attributes['price_multiplier_quartiles'] = None

        LOGGER.debug("Current BESS action set to: %s", self._attr_native_value)
