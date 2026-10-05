import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.zonneplan_peakdetect.const import (
    DOMAIN,
    ACTION_STOP,
    ACTION_SAVE,
    ACTION_CONSUME,
    ACTION_BUY,
    ACTION_SELL,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_FORECAST_ENTITY,
    CONF_ALGORITHM,
    CONF_MULTIPLIER_ALGORITHM,
    CONF_SOLAR_BONUS_PERCENT,
    CONF_SOLAR_BONUS_FIXED_C_KWH,
    CONF_CHARGE_QUANTILE,
    CONF_DISCHARGE_QUANTILE,
    MULTIPLIER_CPWL,
    MULTIPLIER_BLOCK,
    ALGORITHM_WHSS,
    ALGORITHM_MPES,
)
from custom_components.zonneplan_peakdetect.sensor import BatteryOptimizerSensor


def test_solar_bonus_applies_between_sunrise_and_sunset():
    """Solar bonus applies throughout daylight, but not at sunset."""
    sensor = BatteryOptimizerSensor.__new__(BatteryOptimizerSensor)
    sensor.hass = MagicMock()
    sensor._solar_bonus_percent = 10.0
    sunrise = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)
    sunset = datetime(2026, 8, 12, 21, 0, tzinfo=timezone.utc)

    def astral_event(_hass, event, _date):
        return sunrise if event == "sunrise" else sunset

    with patch(
        "custom_components.zonneplan_peakdetect.sensor.get_astral_event_date",
        side_effect=astral_event,
    ):
        windows = sensor._get_solar_bonus_windows([sunrise])

    assert sensor._solar_bonus_applies(
        datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc), windows
    )
    assert not sensor._solar_bonus_applies(
        datetime(2026, 8, 12, 21, 0, tzinfo=timezone.utc), windows
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
            CONF_SOLAR_BONUS_PERCENT: 0.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 0.0,
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
            CONF_SOLAR_BONUS_PERCENT: 0.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 0.0,
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
            CONF_MULTIPLIER_ALGORITHM: MULTIPLIER_CPWL,
            "charge_hours": 1.0,       # 4 quarters (1 hour)
            "discharge_hours": 1.0,    # 4 quarters (1 hour)
            CONF_RTE_PERCENT: 0.0,     # No efficiency loss to keep math simple
            CONF_MIN_PROFIT: 6.0,      # 6 cents (0.06 EUR)
            CONF_SOLAR_BONUS_PERCENT: 0.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 0.0,
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
    # Verify index 0 (0.20): multiplier = 0.20 / 0.05 = 4.0 (locked to Valley 1 before center)
    assert schedule[0]["price_multiplier"] == 4.0
    # Verify index 4 (0.05): multiplier = 0.05 / 0.05 = 1.0 (Valley 1 center)
    assert schedule[4]["price_multiplier"] == 1.0
    # Verify index 8 (0.30): multiplier = 0.30 / 0.07 = 4.29 (linearly interpolated divisor 0.07 between 0.05 and 0.10)
    assert schedule[8]["price_multiplier"] == 4.29

    # Wave 2 window (indices 12 to 23): min price = 0.10
    # Verify index 12 (0.25): multiplier = 0.25 / 0.09 = 2.78 (linearly interpolated divisor 0.09 between 0.05 and 0.10)
    assert schedule[12]["price_multiplier"] == 2.78
    # Verify index 14 (0.10): multiplier = 0.10 / 0.10 = 1.0 (Valley 2 center)
    assert schedule[14]["price_multiplier"] == 1.0
    # Verify index 18 (0.35): multiplier = 0.35 / 0.10 = 3.5 (locked to Valley 2 after center)
    assert schedule[18]["price_multiplier"] == 3.5

    # Verify new sensor attributes for automation
    assert state.attributes.get("current_price_multiplier") == 0.91  # multiplier of current interval (index 5)
    quantiles = state.attributes.get("price_multiplier_quantiles")
    assert quantiles is not None
    assert "min" in quantiles
    assert "q25" in quantiles
    assert "q50" in quantiles
    assert "q75" in quantiles
    assert "max" in quantiles
    assert quantiles["min"] == 0.91
    assert quantiles["max"] == 4.29


async def test_sensor_self_consumption_charge_discharge(hass, freezer):
    """Test self-consumption Charge and Discharge scheduling based on multiplier quantiles."""
    freezer.move_to("2026-08-12T05:59:00+00:00")
    
    # Configure the sensor with specific charge and discharge quantiles
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_WHSS,
            CONF_MULTIPLIER_ALGORITHM: MULTIPLIER_CPWL,
            "charge_hours": 1.0,       # 4 quarters (1 hour)
            "discharge_hours": 1.0,    # 4 quarters (1 hour)
            CONF_RTE_PERCENT: 0.0,
            CONF_MIN_PROFIT: 6.0,      # 6 cents
            CONF_SOLAR_BONUS_PERCENT: 0.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 0.0,
            CONF_CHARGE_QUANTILE: 50.0,
            CONF_DISCHARGE_QUANTILE: 75.0,
        },
        entry_id="test_optimizer_entry_self_consumption",
    )
    config_entry.add_to_hass(hass)

    # Mock sunrise/sunset: 6:00 to 21:00 is daytime.
    # We construct a 24-hour forecast
    forecast = []
    for h in range(24):
        # Base price is 0.20
        price = 0.20
        # Valley 1: 4:00 (0.05) - night
        if h == 4:
            price = 0.05
        # Daytime valley: 12:00 (0.08) - day
        elif h == 12:
            price = 0.08
        # Daytime cheap slot (not valley): 13:00 (0.12) - day
        elif h == 13:
            price = 0.12
        # Peak 1: 8:00 (0.30) - day
        elif h == 8:
            price = 0.30
        # Peak 2: 20:00 (0.35) - day/sunset
        elif h == 20:
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

    # Mock get_astral_event_date for daylight window checks
    sunrise = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)
    sunset = datetime(2026, 8, 12, 21, 0, tzinfo=timezone.utc)

    def astral_event(_hass, event, _date):
        return sunrise if event == "sunrise" else sunset

    with patch(
        "custom_components.zonneplan_peakdetect.sensor.get_astral_event_date",
        side_effect=astral_event,
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Retrieve sensor state
    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None
    
    # Assert extra config is correctly read
    assert state.attributes.get("charge_multiplier_quantile") == 50.0
    assert state.attributes.get("discharge_multiplier_quantile") == 75.0

    schedule = state.attributes.get("schedule")
    assert schedule is not None

    # Let's inspect specific items in the schedule:
    # 1. Daytime valley (12:00, index 12): Should be ACTION_BUY (the high priority arbitrage slot)
    item_12 = schedule[12]
    assert item_12["action"] == ACTION_BUY

    # 2. Daytime cheap slot (13:00, index 13): Should be ACTION_SAVE (self-consumption Save)
    # as price multiplier is low and sun is up.
    item_13 = schedule[13]
    assert item_13["action"] == ACTION_SAVE

    # 3. Night-time valley (4:00, index 4): Should be ACTION_BUY (the arbitrage slot)
    item_4 = schedule[4]
    assert item_4["action"] == ACTION_BUY

    # 4. Peak 2 (20:00, index 20): Should be ACTION_SELL (the arbitrage slot)
    item_20 = schedule[20]
    assert item_20["action"] == ACTION_SELL

    # 5. Night-time high price (3:00, index 3): Should be ACTION_CONSUME (self-consumption Consume)
    # because price multiplier is high (>= q75).
    item_3 = schedule[3]
    assert item_3["action"] == ACTION_CONSUME


async def test_config_flow_quantile_validation(hass):
    """Test that config flow correctly validates quantile parameters and RTE guard band."""
    from custom_components.zonneplan_peakdetect.const import (
        DOMAIN,
        CONF_CHARGE_QUANTILE,
        CONF_DISCHARGE_QUANTILE,
        CONF_RTE_PERCENT,
    )
    
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {}

    # 1. Test order validation error: discharge < charge
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "algorithm_type": "whss",
            "multiplier_type": "block",
            CONF_RTE_PERCENT: 20.0,
            "min_profit_c_kwh": 6,
            "charge_quarters": 8,
            "discharge_quarters": 8,
            "forecast_entity": "sensor.zonneplan_forecast",
            "solar_bonus_percent": 10.0,
            "solar_bonus_fixed_c_kwh": 2.0,
            CONF_CHARGE_QUANTILE: 60.0,
            CONF_DISCHARGE_QUANTILE: 50.0, # lower than charge!
        },
    )
    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "quantile_order_error"}

    # 2. Test order validation error when equal: discharge <= charge (e.g. 60.0 <= 60.0)
    result3 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "algorithm_type": "whss",
            "multiplier_type": "block",
            CONF_RTE_PERCENT: 20.0,
            "min_profit_c_kwh": 6,
            "charge_quarters": 8,
            "discharge_quarters": 8,
            "forecast_entity": "sensor.zonneplan_forecast",
            "solar_bonus_percent": 10.0,
            "solar_bonus_fixed_c_kwh": 2.0,
            CONF_CHARGE_QUANTILE: 60.0,
            CONF_DISCHARGE_QUANTILE: 60.0, # 60 <= 60
        },
    )
    assert result3["type"] == "form"
    assert result3["errors"] == {"base": "quantile_order_error"}

    # 3. Test successful validation when discharge > charge (e.g. 50 < 60, no rigid guard band required)
    result4 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "algorithm_type": "whss",
            "multiplier_type": "block",
            CONF_RTE_PERCENT: 20.0,
            "min_profit_c_kwh": 6,
            "charge_quarters": 8,
            "discharge_quarters": 8,
            "forecast_entity": "sensor.zonneplan_forecast",
            "solar_bonus_percent": 10.0,
            "solar_bonus_fixed_c_kwh": 2.0,
            CONF_CHARGE_QUANTILE: 50.0,
            CONF_DISCHARGE_QUANTILE: 60.0, # 60 > 50 -> OK!
        },
    )
    assert result4["type"] == "create_entry"


async def test_sensor_self_consumption_economic_guardrails(hass, freezer):
    """Test that economic guardrails prevent premature Consume and unprofitable Save."""
    freezer.move_to("2026-08-12T05:59:00+00:00")
    
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_WHSS,
            CONF_MULTIPLIER_ALGORITHM: MULTIPLIER_BLOCK,
            "charge_quarters": 0,       # No arbitrage to isolate self-consumption
            "discharge_quarters": 0,    # No arbitrage to isolate self-consumption
            CONF_RTE_PERCENT: 20.0,     # 20% loss (rte_factor = 0.8)
            CONF_MIN_PROFIT: 6.0,       # 6 cents required
            CONF_SOLAR_BONUS_PERCENT: 0.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 0.0,
            CONF_CHARGE_QUANTILE: 50.0,
            CONF_DISCHARGE_QUANTILE: 75.0,
        },
        entry_id="test_optimizer_entry_economic_guardrails",
    )
    config_entry.add_to_hass(hass)

    # 1. Scenario A: Small spread (0.19 to 0.22 EUR/kWh). 
    # Spread is 0.22 * 0.8 - 0.19 = -0.014 < 0.06 -> Not economically viable!
    forecast_flat = [
        {"datetime": "2026-08-12T04:00:00+00:00", "price_eur_kwh": 0.20},
        {"datetime": "2026-08-12T12:00:00+00:00", "price_eur_kwh": 0.19},
        {"datetime": "2026-08-12T18:00:00+00:00", "price_eur_kwh": 0.22},
    ]

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.20",
        {"forecast": forecast_flat}
    )

    # Mock sunrise/sunset so 06:00 to 21:00 is daytime
    sunrise = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)
    sunset = datetime(2026, 8, 12, 21, 0, tzinfo=timezone.utc)

    def astral_event(_hass, event, _date):
        return sunrise if event == "sunrise" else sunset

    with patch(
        "custom_components.zonneplan_peakdetect.sensor.get_astral_event_date",
        side_effect=astral_event,
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None
    schedule = state.attributes.get("schedule")

    # On this flat day, even though 18:00 has highest multiplier and 12:00 has lowest,
    # neither clears the 6-cent hurdle after 20% RTE loss -> both stay STOP!
    assert schedule[1]["action"] == ACTION_STOP
    assert schedule[2]["action"] == ACTION_STOP


async def test_sensor_self_consumption_september30_economic_dispatch(hass, freezer, september30_forecast):
    """Test that economic dispatch prevents morning Consume and prioritizes true evening peak on Sept 30."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.latitude = 52.3676
    hass.config.longitude = 4.9041
    hass.config.elevation = 0
    freezer.move_to("2026-09-30T18:00:00+02:00")

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_MPES,
            CONF_MULTIPLIER_ALGORITHM: MULTIPLIER_BLOCK,
            "charge_quarters": 10,
            "discharge_quarters": 10,
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,
            CONF_SOLAR_BONUS_PERCENT: 10.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 2.0,
            CONF_CHARGE_QUANTILE: 50.0,
            CONF_DISCHARGE_QUANTILE: 75.0,
        },
        entry_id="test_optimizer_entry_sept30_economic",
    )
    config_entry.add_to_hass(hass)

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.39",
        {"forecast": september30_forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None
    schedule = state.attributes.get("schedule")

    # 1. Verify clean 1.00 baseline at the 12:45 valley on Sept 30
    slot_1245 = next(x for x in schedule if x["datetime"] == "2026-09-30T12:45:00+02:00")
    assert slot_1245["price_multiplier"] == 1.0

    # 2. Verify morning slots (09:30 - 10:30) are NOT prematurely discharged as Consume
    morning_consume = [
        x for x in schedule
        if (x["datetime"].startswith("2026-09-30T09:30") or x["datetime"].startswith("2026-09-30T10:"))
        and x["action"] == ACTION_CONSUME
    ]
    assert len(morning_consume) == 0

    # 3. Verify evening peak slots (20:15 - 21:30) with €0.38 - €0.43 are scheduled as Consume
    evening_consume = [
        x for x in schedule
        if (x["datetime"].startswith("2026-09-30T20:") or x["datetime"].startswith("2026-09-30T21:"))
        and x["action"] == ACTION_CONSUME
    ]
    assert len(evening_consume) > 0


async def test_sensor_solar_bonus_calculated_on_pre_tax_price(hass, freezer, october4_forecast):
    """Test that solar bonus is computed from price_tax_excluded (kale marktprijs) when present."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.latitude = 52.3676
    hass.config.longitude = 4.9041
    hass.config.elevation = 0
    freezer.move_to("2026-10-04T19:00:00+02:00")

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_MPES,
            CONF_MULTIPLIER_ALGORITHM: MULTIPLIER_BLOCK,
            "charge_quarters": 10,
            "discharge_quarters": 10,
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,
            CONF_SOLAR_BONUS_PERCENT: 10.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 2.0,
        },
        entry_id="test_optimizer_entry_oct4_pre_tax",
    )
    config_entry.add_to_hass(hass)

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.41",
        {"forecast": october4_forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None
    schedule = state.attributes.get("schedule")

    # Find slot at 19:00 on Oct 4:
    # raw tax-included price = 0.4121004
    # raw pre-tax price = 0.3012523
    # With 2c fixed + 10%:
    # True pre-tax bonus = (0.3012523 + 0.02) * 1.10 - 0.3012523 = 0.0521252
    slot_1900 = next(x for x in schedule if x["datetime"] == "2026-10-04T19:00:00+02:00")
    assert round(slot_1900["price_bonus_eur_kwh"], 5) == 0.05213


async def test_sensor_consume_and_save_quarters_budgeting(hass, freezer, september30_forecast):
    """Test that consume_quarters and save_quarters strictly enforce capacity budgets."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.latitude = 52.3676
    hass.config.longitude = 4.9041
    hass.config.elevation = 0
    freezer.move_to("2026-09-30T18:00:00+02:00")

    # Configure strictly 4 consume quarters and 4 save quarters
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FORECAST_ENTITY: "sensor.zonneplan_forecast",
            CONF_ALGORITHM: ALGORITHM_MPES,
            CONF_MULTIPLIER_ALGORITHM: MULTIPLIER_BLOCK,
            "charge_quarters": 10,
            "discharge_quarters": 10,
            "consume_quarters": 4,
            "save_quarters": 4,
            CONF_RTE_PERCENT: 20.0,
            CONF_MIN_PROFIT: 6.0,
            CONF_SOLAR_BONUS_PERCENT: 10.0,
            CONF_SOLAR_BONUS_FIXED_C_KWH: 2.0,
            CONF_CHARGE_QUANTILE: 50.0,
            CONF_DISCHARGE_QUANTILE: 75.0,
        },
        entry_id="test_optimizer_entry_budgeting",
    )
    config_entry.add_to_hass(hass)

    hass.states.async_set(
        "sensor.zonneplan_forecast",
        "0.39",
        {"forecast": september30_forecast}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None
    assert state.attributes.get("consume_quarters") == 4
    assert state.attributes.get("save_quarters") == 4

    schedule = state.attributes.get("schedule")
    consume_slots = [x for x in schedule if x["action"] == ACTION_CONSUME]
    assert len(consume_slots) <= 4





