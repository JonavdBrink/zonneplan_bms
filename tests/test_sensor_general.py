import pytest
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.zonneplan_peakdetect.const import (
    DOMAIN,
    ACTION_STOP,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_FORECAST_ENTITY,
    CONF_ALGORITHM,
    ALGORITHM_WHSS,
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


async def test_sensor_price_multiplier_fallback(hass):
    """Test price_multiplier fallback calculation when no active segments are scheduled."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            "charge_hours": 1.0,
            "discharge_hours": 1.0,
            CONF_RTE_PERCENT: 10.0,
            CONF_MIN_PROFIT: 10.0,  # 10 cents required, so no wave will be scheduled
        },
        entry_id="test_optimizer_entry_fallback",
    )
    config_entry.add_to_hass(hass)

    # Injected prices are very close (not profitable enough)
    forecast = [
        {"datetime": "2026-08-12T00:00:00+00:00", "price_eur_kwh": 0.10},
        {"datetime": "2026-08-12T01:00:00+00:00", "price_eur_kwh": 0.12},
        {"datetime": "2026-08-12T02:00:00+00:00", "price_eur_kwh": 0.15},
    ]

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.10",
        {"forecast": forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Retrieve sensor attributes
    entity_id = "sensor.battery_optimizer_action"
    for state in hass.states.async_all():
        if "battery_optimizer" in state.entity_id:
            entity_id = state.entity_id
            break

    state = hass.states.get(entity_id)
    assert state is not None
    schedule = state.attributes.get("schedule")
    assert schedule is not None

    # No waves scheduled
    assert state.attributes.get("intervals") == 0

    # Multipliers should be relative to the absolute global minimum (0.10)
    assert schedule[0]["price_multiplier"] == 1.0   # 0.10 / 0.10
    assert schedule[1]["price_multiplier"] == 1.2   # 0.12 / 0.10
    assert schedule[2]["price_multiplier"] == 1.5   # 0.15 / 0.10


async def test_sensor_price_multiplier_windowed(hass, freezer):
    """Test price_multiplier recalculation windowed by the algorithm found intervals."""
    freezer.move_to("2026-08-12T05:59:00+00:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_WHSS,
            "charge_hours": 1.0,       # 4 quarters (1 hour)
            "discharge_hours": 1.0,    # 4 quarters (1 hour)
            CONF_RTE_PERCENT: 0.0,     # No efficiency loss to keep math simple
            CONF_MIN_PROFIT: 6.0,      # 6 cents (0.06 EUR)
        },
        entry_id="test_optimizer_entry_windowed",
    )
    config_entry.add_to_hass(hass)

    # 2 waves:
    # Wave 1 (indices 0 to 11): valley of 0.05, peak of 0.30 (profit = 0.25 >= 0.06)
    # Wave 2 (indices 12 to 23): valley of 0.10, peak of 0.35 (profit = 0.25 >= 0.06)
    forecast = []
    for h in range(24):
        price = 0.20 if h < 12 else 0.25
        if h in (4, 5):
            price = 0.05
        elif h in (8, 9):
            price = 0.30
        elif h in (14, 15):
            price = 0.10
        elif h in (18, 19):
            price = 0.35
            
        forecast.append({
            "datetime": f"2026-08-12T{h:02d}:00:00+00:00",
            "price_eur_kwh": price
        })

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.20",
        {"forecast": forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Retrieve sensor attributes
    entity_id = "sensor.battery_optimizer_action"
    for state in hass.states.async_all():
        if "battery_optimizer" in state.entity_id:
            entity_id = state.entity_id
            break

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get("intervals") == 2

    schedule = state.attributes.get("schedule")
    assert len(schedule) == 24

    # Wave 1 window (indices 0 to 11): min price = 0.05
    # Verify index 0 (0.20): multiplier = 0.20 / 0.05 = 4.0
    assert schedule[0]["price_multiplier"] == 4.0
    # Verify index 4 (0.05): multiplier = 0.05 / 0.05 = 1.0
    assert schedule[4]["price_multiplier"] == 1.0
    # Verify index 8 (0.30): multiplier = 0.30 / 0.05 = 6.0
    assert schedule[8]["price_multiplier"] == 6.0

    # Wave 2 window (indices 12 to 23): min price = 0.10
    # Verify index 12 (0.25): multiplier = 0.25 / 0.10 = 2.5
    assert schedule[12]["price_multiplier"] == 2.5
    # Verify index 14 (0.10): multiplier = 0.10 / 0.10 = 1.0
    assert schedule[14]["price_multiplier"] == 1.0
    # Verify index 18 (0.35): multiplier = 0.35 / 0.10 = 3.5
    assert schedule[18]["price_multiplier"] == 3.5

