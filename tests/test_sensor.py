import pytest
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.zonneplan_peakdetect.const import (
    DOMAIN,
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_STOP,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_FORECAST_ENTITY,
)

async def test_sensor_empty_forecast(hass):
    """Test sensor behavior with an empty forecast dataset."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            "charge_hours": 3.25,      # 13 quarters
            "discharge_hours": 2.75,   # 11 quarters
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
        },
        entry_id="test_optimizer_entry",
    )
    config_entry.add_to_hass(hass)

    # Inject empty forecast attribute
    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.13",
        {"forecast": []}
    )

    # Set up the custom component
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Find the created sensor entity dynamically
    entity_id = "sensor.battery_optimizer_action"
    for state in hass.states.async_all():
        if "battery_optimizer" in state.entity_id:
            entity_id = state.entity_id
            break

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == ACTION_STOP
    assert state.attributes.get("intervals") == 0

async def test_sensor_algorithm_august_extremes(hass, freezer, august_extremes_forecast):
    """
    Test Live Sensor: Verifies Wave Heuristic behavior on August 12/13 extreme-price dataset.
    
    Ensures that when loaded inside Home Assistant:
    1. The entity calculates exactly 2 intervals.
    2. Correct balanced limits (11 charge / 11 discharge slots) are configured for Interval 2 (index 1).
    3. Chronological safety is maintained (no time-travel or overlaps).
    """
    freezer.move_to("2026-08-12T07:59:00+02:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            "charge_hours": 3.25,      # 13 quarters
            "discharge_hours": 2.75,   # 11 quarters
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
        },
        entry_id="test_optimizer_entry",
    )
    config_entry.add_to_hass(hass)

    # Inject August 12/13 extreme forecast data
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
    
    # Assert correct number of segmented cycles
    intervals = state.attributes.get("intervals")
    schedule = state.attributes.get("schedule")
    assert intervals == 2
    
    # Assert schedule is generated
    assert schedule is not None
    assert len(schedule) == len(august_extremes_forecast)
    
    # Analyze Interval 2 (August 13 cycle, which has zero-based index 1)
    interval_2_slots = [item for item in schedule if item.get("interval_id") == 1]
    charge_slots = [item for item in interval_2_slots if item["action"] == ACTION_CHARGE]
    discharge_slots = [item for item in interval_2_slots if item["action"] == ACTION_DISCHARGE]
    
    # Assert balanced slot counts are correct (scaled to min(13, 11) = 11)
    assert len(charge_slots) == 11
    assert len(discharge_slots) == 11
    
    # Assert chronological safety: Charge happens before Discharge
    charge_indices = [interval_2_slots.index(item) for item in charge_slots]
    discharge_indices = [interval_2_slots.index(item) for item in discharge_slots]
    assert max(charge_indices) < min(discharge_indices)

async def test_sensor_algorithm_july_baseline(hass, freezer, july_baseline_forecast):
    """
    Test Live Sensor: Verifies Wave Heuristic behavior on July 31 baseline dataset.
    
    Ensures that standard days have exactly 3 high-profit cycles scheduled under the wave heuristic.
    """
    freezer.move_to("2026-07-30T12:59:00+00:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            "charge_hours": 3.25,      # 13 quarters
            "discharge_hours": 2.75,   # 11 quarters
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
        },
        entry_id="test_optimizer_entry",
    )
    config_entry.add_to_hass(hass)

    # Inject July 31 baseline forecast data
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
    
    # Assert correct number of segmented cycles (Wave Heuristic correctly identifies 3 waves)
    assert state.attributes.get("intervals") == 3
    
    # Assert schedule is generated
    schedule = state.attributes.get("schedule")
    assert schedule is not None
    assert len(schedule) == len(july_baseline_forecast)
    
    # Chronological safety checking for all 3 scheduled intervals (index 0, 1, 2)
    for interval_id in range(3):
        interval_slots = [item for item in schedule if item.get("interval_id") == interval_id]
        charge_slots = [item for item in interval_slots if item["action"] == ACTION_CHARGE]
        discharge_slots = [item for item in interval_slots if item["action"] == ACTION_DISCHARGE]
        
        if charge_slots and discharge_slots:
            charge_indices = [interval_slots.index(item) for item in charge_slots]
            discharge_indices = [interval_slots.index(item) for item in discharge_slots]
            assert max(charge_indices) < min(discharge_indices)
