import pytest

async def test_hello_world(hass):
    """Test a simple Home Assistant state change to verify the test setup works."""
    hass.states.async_set("sensor.hello_world", "online")
    await hass.async_block_till_done()

    state = hass.states.get("sensor.hello_world")
    assert state is not None
    assert state.state == "online"
