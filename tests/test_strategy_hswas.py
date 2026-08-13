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
    ALGORITHM_HSWAS,
)

async def test_sensor_algorithm_hswas_august_extremes(hass, freezer, august_extremes_forecast):
    """
    Test Live Sensor (HSWAS): Verifies behavior on August 12/13 extreme-price dataset.
    
    Ensures that when configured to run the HSWAS algorithm:
    1. The entity calculates exactly 2 intervals.
    2. Correct independent (non-balanced) limits (13 charge / 11 discharge slots) are configured for Interval 2 (index 1).
    3. Chronological safety is maintained.
    """
    freezer.move_to("2026-08-12T05:59:00+00:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_HSWAS,
            "charge_hours": 3.25,      # 13 quarters
            "discharge_hours": 2.75,   # 11 quarters
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
        },
        entry_id="test_optimizer_entry",
    )
    config_entry.add_to_hass(hass)

    # Inject August 12/13 extremes forecast
    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.13",
        {"forecast": august_extremes_forecast}
    )

    # Set up custom component
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Find and verify our Sensor State
    entity_id = "sensor.battery_optimizer_action"
    for state in hass.states.async_all():
        if "battery_optimizer" in state.entity_id:
            entity_id = state.entity_id
            break

    state = hass.states.get(entity_id)
    assert state is not None
    
    # Assert correct number of segmented cycles and algorithm type
    intervals = state.attributes.get("intervals")
    schedule = state.attributes.get("schedule")
    assert intervals == 2
    assert state.attributes.get("algorithm_type") == ALGORITHM_HSWAS
    
    # Assert schedule is generated
    assert schedule is not None
    assert len(schedule) == len(august_extremes_forecast)
    
    # Analyze Interval 2 (August 13 cycle, index 1)
    interval_2_slots = [item for item in schedule if item.get("interval_id") == 1]
    charge_slots = [item for item in interval_2_slots if item["action"] == ACTION_CHARGE]
    discharge_slots = [item for item in interval_2_slots if item["action"] == ACTION_DISCHARGE]
    
    # Assert independent (unbalanced) slots are correct
    assert len(charge_slots) == 13
    assert len(discharge_slots) == 11
    
    # Assert chronological safety: Charge happens before Discharge
    charge_indices = [interval_2_slots.index(item) for item in charge_slots]
    discharge_indices = [interval_2_slots.index(item) for item in discharge_slots]
    assert max(charge_indices) < min(discharge_indices)

async def test_sensor_algorithm_hswas_july_baseline(hass, freezer, july_baseline_forecast):
    """
    Test Live Sensor (HSWAS): Verifies behavior on July 31 baseline dataset.
    
    Ensures that when configured to run HSWAS, exactly 1 highly profitable cycle is scheduled.
    """
    freezer.move_to("2026-07-30T12:59:00+00:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_HSWAS,
            "charge_hours": 3.25,      # 13 quarters
            "discharge_hours": 2.75,   # 11 quarters
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
        },
        entry_id="test_optimizer_entry",
    )
    config_entry.add_to_hass(hass)

    # Inject July 31 baseline forecast
    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.22",
        {"forecast": july_baseline_forecast}
    )

    # Set up custom component
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Find and verify our Sensor State
    entity_id = "sensor.battery_optimizer_action"
    for state in hass.states.async_all():
        if "battery_optimizer" in state.entity_id:
            entity_id = state.entity_id
            break

    state = hass.states.get(entity_id)
    assert state is not None
    
    # Assert correct number of segmented cycles and algorithm type (HSWAS correctly gets 1)
    assert state.attributes.get("intervals") == 1
    assert state.attributes.get("algorithm_type") == ALGORITHM_HSWAS
    
    # Assert schedule is generated
    schedule = state.attributes.get("schedule")
    assert schedule is not None
    assert len(schedule) == len(july_baseline_forecast)
    
    # Chronological safety checking for the scheduled interval (index 0)
    for interval_id in range(1):
        interval_slots = [item for item in schedule if item.get("interval_id") == interval_id]
        charge_slots = [item for item in interval_slots if item["action"] == ACTION_CHARGE]
        discharge_slots = [item for item in interval_slots if item["action"] == ACTION_DISCHARGE]
        
        if charge_slots and discharge_slots:
            charge_indices = [interval_slots.index(item) for item in charge_slots]
            discharge_indices = [interval_slots.index(item) for item in discharge_slots]
            assert max(charge_indices) < min(discharge_indices)

async def test_sensor_algorithm_hswas_july29(hass, freezer, july29_forecast):
    """
    Test Live Sensor (HSWAS): Verifies general arbitrage invariants on July 29 dataset.
    """
    freezer.move_to("2026-07-28T17:59:00+00:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_HSWAS,
            "charge_hours": 3.25,      # 13 quarters
            "discharge_hours": 2.75,   # 11 quarters
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
        },
        entry_id="test_optimizer_entry",
    )
    config_entry.add_to_hass(hass)

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.25",
        {"forecast": july29_forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "sensor.battery_optimizer_action"
    for state in hass.states.async_all():
        if "battery_optimizer" in state.entity_id:
            entity_id = state.entity_id
            break

    state = hass.states.get(entity_id)
    assert state is not None
    
    intervals = state.attributes.get("intervals")
    assert intervals >= 0
    
    schedule = state.attributes.get("schedule")
    assert len(schedule) == len(july29_forecast)
    
    # Chronological validation on all intervals found
    for interval_id in range(intervals):
        interval_slots = [item for item in schedule if item.get("interval_id") == interval_id]
        charge_slots = [item for item in interval_slots if item["action"] == ACTION_CHARGE]
        discharge_slots = [item for item in interval_slots if item["action"] == ACTION_DISCHARGE]
        
        if charge_slots and discharge_slots:
            charge_indices = [interval_slots.index(item) for item in charge_slots]
            discharge_indices = [interval_slots.index(item) for item in discharge_slots]
            assert max(charge_indices) < min(discharge_indices)

async def test_hswas_hourly_tariff(hass, freezer):
    """
    Test Live Sensor (HSWAS): Verifies correct slot scaling and scheduling under an hourly tariff.
    
    Ensures that when configured with 13 quarters of charge and 11 quarters of discharge,
    on an hourly tariff the limits scale down correctly to exactly 3 hours of charge and 3 hours of discharge.
    """
    freezer.move_to("2026-08-12T05:59:00+00:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_HSWAS,
            "charge_hours": 3.25,      # 13 quarters -> should scale to 3 hours
            "discharge_hours": 2.75,   # 11 quarters -> should scale to 3 hours
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
        },
        entry_id="test_optimizer_entry",
    )
    config_entry.add_to_hass(hass)

    # Construct mock hourly tariff forecast data (1-hour spacing)
    # Price dip in afternoon (12:00 - 15:00 at 0.10 EUR), Peak in evening (18:00 - 21:00 at 0.40 EUR)
    hourly_forecast = []
    for hour in range(24):
        price = 0.25
        if 12 <= hour < 15:
            price = 0.10
        elif 18 <= hour < 21:
            price = 0.40
        hourly_forecast.append({
            "datetime": f"2026-08-12T{hour:02d}:00:00+00:00",
            "price_eur_kwh": price
        })

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.25",
        {"forecast": hourly_forecast}
    )

    # Set up custom component
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Find and verify our Sensor State
    entity_id = "sensor.battery_optimizer_action"
    for state in hass.states.async_all():
        if "battery_optimizer" in state.entity_id:
            entity_id = state.entity_id
            break

    state = hass.states.get(entity_id)
    assert state is not None
    
    # Under hourly tariff, it should segment 1 profitable interval
    assert state.attributes.get("intervals") == 1
    
    schedule = state.attributes.get("schedule")
    assert len(schedule) == 24
    
    # Verify that slot counts scaled to exactly 3 hours of charge and 3 hours of discharge
    charge_slots = [item for item in schedule if item["action"] == ACTION_CHARGE]
    discharge_slots = [item for item in schedule if item["action"] == ACTION_DISCHARGE]
    
    assert len(charge_slots) == 3
    assert len(discharge_slots) == 3
