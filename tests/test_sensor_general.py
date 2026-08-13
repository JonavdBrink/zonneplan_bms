import pytest
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.zonneplan_peakdetect.const import (
    DOMAIN,
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
