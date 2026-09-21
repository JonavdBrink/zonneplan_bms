import pytest
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.zonneplan_peakdetect.const import (
    DOMAIN,
    ACTION_BUY,
    ACTION_SELL,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_FORECAST_ENTITY,
    CONF_ALGORITHM,
    CONF_SOLAR_BONUS_PERCENT,
    CONF_SOLAR_BONUS_FIXED_C_KWH,
    CONF_SOLAR_BONUS_IN_ARBITRAGE,
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
    # Check that 13:00 (index 96) is scheduled as Buy
    item_1300 = next(x for x in schedule if x["datetime"] == "2026-09-08T13:00:00+02:00")
    assert item_1300["action"] == ACTION_BUY

    # Check that 13:45 (index 99) is STOP (correctly skipped the midday hump!)
    item_1345 = next(x for x in schedule if x["datetime"] == "2026-09-08T13:45:00+02:00")
    assert item_1345["action"] == "Stop"


def test_mpes_strategy_raw():
    """Directly tests the MpesStrategy class on a simple mock price curve with a midday hump."""
    from custom_components.zonneplan_peakdetect.strategies.mpes import MpesStrategy
    from custom_components.zonneplan_peakdetect.const import ACTION_BUY, ACTION_SELL, ACTION_STOP
    import datetime

    strategy = MpesStrategy()
    
    # 24 hour mock dataset with a midday hump:
    # Valley 1: index 4 (0.05)
    # Midday hump: index 8 (0.28)
    # Midday dip: index 10 (0.23)
    # Evening Peak: index 18 (0.45)
    prices = [0.20] * 24
    for i in range(6, 18):
        prices[i] = 0.22
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

    # Valley 1 (0.05) should be scheduled as Buy (Interval 0)
    assert res[4]['action'] == ACTION_BUY
    assert res[4]['interval_id'] == 0
    assert res[5]['action'] == ACTION_BUY
    assert res[5]['interval_id'] == 0

    # Evening peak (0.45) should be scheduled as Sell (Interval 0)
    assert res[18]['action'] == ACTION_SELL
    assert res[18]['interval_id'] == 0
    assert res[19]['action'] == ACTION_SELL
    assert res[19]['interval_id'] == 0

    # Midday hump/dip (indices 8 and 10) should be Stop (correctly filtered out by hysteresis!)
    assert res[8]['action'] == ACTION_STOP
    assert res[10]['action'] == ACTION_STOP


async def test_sensor_algorithm_mpes_september20(hass, freezer, september20_forecast):
    """
    Test Live Sensor (MPES Strategy): Verifies behavior on September 20 dataset.
    Ensures that with our algorithmic pre-filtering of unprofitable cycles,
    MPES correctly schedules exactly 10 slots of charge and discharge on September 20.
    """
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.latitude = 52.3676
    hass.config.longitude = 4.9041
    hass.config.elevation = 0

    freezer.move_to("2026-09-20T17:30:00+02:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_MPES,
            "charge_quarters": 10,
            "discharge_quarters": 10,
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
            CONF_SOLAR_BONUS_PERCENT: 10.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 2.0,
        },
        entry_id="test_optimizer_entry_mpes_sept20",
    )
    config_entry.add_to_hass(hass)

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.35",
        {"forecast": september20_forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None

    schedule = state.attributes.get("schedule")
    assert len(schedule) == len(september20_forecast)

    # Count Buy and Sell actions on Sept 20th specifically
    # September 20th hours are before "2026-09-21T00:00:00+02:00"
    sept20_slots = [x for x in schedule if x["datetime"].startswith("2026-09-20")]
    buys = [x for x in sept20_slots if x["action"] == ACTION_BUY]
    sells = [x for x in sept20_slots if x["action"] == ACTION_SELL]

    # Verify that we now have the full optimal 10 quarters of Buy and 10 quarters of Sell scheduled!
    assert len(buys) == 10
    assert len(sells) == 10


async def test_sensor_algorithm_mpes_september20_disabled_solar_bonus(hass, freezer, september20_forecast):
    """
    Test Live Sensor (MPES Strategy): Verifies behavior on September 20 with solar bonus disabled in planning.
    """
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.latitude = 52.3676
    hass.config.longitude = 4.9041
    hass.config.elevation = 0

    freezer.move_to("2026-09-20T17:30:00+02:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_MPES,
            "charge_quarters": 13,
            "discharge_quarters": 11,
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
            CONF_SOLAR_BONUS_PERCENT: 10.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 2.0,
            CONF_SOLAR_BONUS_IN_ARBITRAGE: False,
        },
        entry_id="test_optimizer_entry_mpes_sept20_no_bonus",
    )
    config_entry.add_to_hass(hass)

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.35",
        {"forecast": september20_forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None

    schedule = state.attributes.get("schedule")
    assert len(schedule) == len(september20_forecast)

    # Count Buy and Sell actions on Sept 20th specifically
    # September 20th hours are before "2026-09-21T00:00:00+02:00"
    sept20_slots = [x for x in schedule if x["datetime"].startswith("2026-09-20")]
    buys = [x for x in sept20_slots if x["action"] == ACTION_BUY]
    sells = [x for x in sept20_slots if x["action"] == ACTION_SELL]

    # Verify that we still have the full optimal 11 quarters of Buy and 6 quarters of Sell scheduled on September 20th!
    assert len(buys) == 11
    assert len(sells) == 6

    # Verify that the morning peak of Sept 21st (between 07:45 and 09:15) has been correctly utilized for discharging by Cycle 0!
    sept21_morning = [x for x in schedule if x["datetime"].startswith("2026-09-21T07:45") or x["datetime"].startswith("2026-09-21T08") or x["datetime"].startswith("2026-09-21T09:00")]
    morning_sells = [x for x in sept21_morning if x["action"] == ACTION_SELL]
    assert len(morning_sells) == 4



