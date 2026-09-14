import pytest
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.zonneplan_peakdetect.const import (
    DOMAIN,
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_FORECAST_ENTITY,
    CONF_ALGORITHM,
    CONF_SOLAR_BONUS_PERCENT,
    CONF_SOLAR_BONUS_FIXED_C_KWH,
    ALGORITHM_MPES,
)

async def test_sensor_algorithm_mpes_september8(hass, freezer, september8_forecast):
    """
    Test Live Sensor (MPES Strategy): Verifies behavior on September 8 duck-curve dataset.
    Ensures that MPES correctly captures the 13:00 dip and avoids the midday hump.
    """
    freezer.move_to("2026-09-08T05:59:00+02:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_MPES,
            "charge_quarters": 13,
            "discharge_quarters": 11,
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
            CONF_SOLAR_BONUS_PERCENT: 0.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 0.0,
        },
        entry_id="test_optimizer_entry_mpes",
    )
    config_entry.add_to_hass(hass)

    # Inject September 8 forecast data
    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.35",
        {"forecast": september8_forecast}
    )

    # Set up custom component
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Find and verify our Sensor State
    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None

    # Assert correct number of active segmented cycles (3 cycles)
    assert state.attributes.get("intervals") == 3

    schedule = state.attributes.get("schedule")
    assert len(schedule) == len(september8_forecast)

    # Find slots for September 8 midday (around index 96)
    # Check that 13:00 (index 96) is scheduled as Charge
    item_1300 = next(x for x in schedule if x["datetime"] == "2026-09-08T13:00:00+02:00")
    assert item_1300["action"] == ACTION_CHARGE

    # Check that 13:45 (index 99) is STOP (correctly skipped the midday hump!)
    item_1345 = next(x for x in schedule if x["datetime"] == "2026-09-08T13:45:00+02:00")
    assert item_1345["action"] == "Stop"


def test_mpes_strategy_raw():
    """Directly tests the MpesStrategy class on a simple mock price curve with a midday hump."""
    from custom_components.zonneplan_peakdetect.strategies.mpes import MpesStrategy
    from custom_components.zonneplan_peakdetect.const import ACTION_CHARGE, ACTION_DISCHARGE, ACTION_STOP
    import datetime

    strategy = MpesStrategy()
    
    # 24 hour mock dataset with a midday hump:
    # Valley 1: index 4 (0.05)
    # Midday hump: index 8 (0.28)
    # Midday dip: index 10 (0.23)
    # Evening Peak: index 18 (0.45)
    prices = [0.20] * 24
    prices[4] = 0.05
    prices[5] = 0.05
    prices[8] = 0.28
    prices[10] = 0.23
    prices[18] = 0.45
    prices[19] = 0.45

    prepared_data = []
    for idx, p in enumerate(prices):
        prepared_data.append({
            'datetime': f"2026-09-08T{idx:02d}:00:00+02:00",
            'price_eur_kwh': p,
            'action': ACTION_STOP,
            'interval_id': -1,
            'sort_index': idx
        })

    res = strategy.calculate_schedule(
        prepared_data,
        charge_slots_count=2,
        discharge_slots_count=2,
        rte_factor=0.8,
        min_profit_eur_kwh=0.06,
        now=datetime.datetime.now()
    )

    # Valley 1 (0.05) should be scheduled as Charge (Interval 0)
    assert res[4]['action'] == ACTION_CHARGE
    assert res[4]['interval_id'] == 0
    assert res[5]['action'] == ACTION_CHARGE
    assert res[5]['interval_id'] == 0

    # Evening peak (0.45) should be scheduled as Discharge (Interval 1)
    assert res[18]['action'] == ACTION_DISCHARGE
    assert res[18]['interval_id'] == 1
    assert res[19]['action'] == ACTION_DISCHARGE
    assert res[19]['interval_id'] == 1

    # Midday hump/dip (indices 8 and 10) should be Stop (correctly filtered out by hysteresis!)
    assert res[8]['action'] == ACTION_STOP
    assert res[10]['action'] == ACTION_STOP

