from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zonneplan_peakdetect.config_flow import ConfigFlow
from custom_components.zonneplan_peakdetect.const import (
    ACTION_BUY,
    ACTION_SELL,
    ACTION_STOP,
    ALGORITHM_HSWAS,
    ALGORITHM_MPES,
    ALGORITHM_WHSS,
    CONF_ALGORITHM,
    CONF_CHARGE_QUARTERS,
    CONF_DISCHARGE_QUARTERS,
    CONF_FORECAST_ENTITY,
    CONF_GREEDY_CHARGE,
    CONF_MIN_PROFIT,
    CONF_RTE_PERCENT,
    CONF_SOLAR_BONUS_FIXED_C_KWH,
    CONF_SOLAR_BONUS_PERCENT,
    DOMAIN,
)
from custom_components.zonneplan_peakdetect.strategies import get_arbitrage_strategy

BUY_PRICES = [0.17, 0.16, 0.15, 0.14, 0.13, 0.12, 0.11, 0.10]
SELL_PRICES = [0.40, 0.41, 0.42, 0.43, 0.44, 0.45, 0.46, 0.47]


def prepare_data(prices, minutes=15):
    start = datetime(2026, 10, 30, tzinfo=timezone.utc)
    return [
        {
            "datetime": (start + timedelta(minutes=minutes * idx)).isoformat(),
            "price_eur_kwh": price,
            "action": ACTION_STOP,
            "interval_id": -1,
            "sort_index": idx,
        }
        for idx, price in enumerate(prices)
    ]


@pytest.mark.parametrize("algorithm", [ALGORITHM_WHSS, ALGORITHM_MPES])
@pytest.mark.parametrize("greedy_charge", [None, False, True])
@pytest.mark.parametrize(
    ("charge_quarters", "discharge_quarters", "prices", "balanced", "greedy"),
    [
        (8, 4, BUY_PRICES + SELL_PRICES[:5], (4, 4), (8, 4)),
        (8, 4, BUY_PRICES + SELL_PRICES[:2], (2, 2), (8, 2)),
        (4, 8, BUY_PRICES + SELL_PRICES, (4, 4), (4, 4)),
        (8, 0, BUY_PRICES + SELL_PRICES[:4], (0, 0), (0, 0)),
        (0, 4, BUY_PRICES + SELL_PRICES[:4], (0, 0), (0, 0)),
        (8, 4, [0.10] * 8 + [0.11] * 4, (0, 0), (0, 0)),
    ],
)
def test_greedy_charge_strategy(
    algorithm, greedy_charge, charge_quarters, discharge_quarters, prices, balanced, greedy
):
    kwargs = {} if greedy_charge is None else {"greedy_charge": greedy_charge}
    schedule = get_arbitrage_strategy(algorithm).calculate_schedule(
        prepare_data(prices),
        charge_quarters,
        discharge_quarters,
        0.8,
        0.06,
        datetime(2026, 10, 30, tzinfo=timezone.utc),
        **kwargs,
    )
    buys = [i for i, item in enumerate(schedule) if item["action"] == ACTION_BUY]
    sells = [i for i, item in enumerate(schedule) if item["action"] == ACTION_SELL]
    assert (len(buys), len(sells)) == (greedy if greedy_charge else balanced)
    if buys and sells:
        assert max(buys) < min(sells)
        assert {schedule[i]["interval_id"] for i in buys + sells} == {0}


@pytest.mark.parametrize("greedy_charge", [False, True])
def test_hswas_keeps_independent_quotas(greedy_charge):
    schedule = get_arbitrage_strategy(ALGORITHM_HSWAS).calculate_schedule(
        prepare_data([0.10] * 8 + [0.40] * 4),
        8,
        4,
        0.8,
        0.06,
        datetime(2026, 10, 30, tzinfo=timezone.utc),
        greedy_charge=greedy_charge,
    )
    assert sum(item["action"] == ACTION_BUY for item in schedule) == 8
    assert sum(item["action"] == ACTION_SELL for item in schedule) == 4


@pytest.mark.parametrize("configured", [None, False, True])
def test_greedy_charge_schema_default(configured):
    data = {} if configured is None else {CONF_GREEDY_CHARGE: configured}
    validated = ConfigFlow()._get_schema(data)(data)
    assert validated[CONF_GREEDY_CHARGE] is (configured is True)


@pytest.mark.parametrize("algorithm", [ALGORITHM_WHSS, ALGORITHM_MPES])
@pytest.mark.parametrize("configured", [None, False, True])
@pytest.mark.parametrize("minutes", [15, 60])
async def test_greedy_charge_live_sensor(hass, freezer, algorithm, configured, minutes):
    freezer.move_to("2026-10-29T23:59:00+00:00")
    data = {
        CONF_FORECAST_ENTITY: "sensor.test_forecast",
        CONF_ALGORITHM: algorithm,
        CONF_CHARGE_QUARTERS: 8,
        CONF_DISCHARGE_QUARTERS: 4,
        CONF_RTE_PERCENT: 20,
        CONF_MIN_PROFIT: 6,
        CONF_SOLAR_BONUS_PERCENT: 0,
        CONF_SOLAR_BONUS_FIXED_C_KWH: 0,
    }
    if configured is not None:
        data[CONF_GREEDY_CHARGE] = configured
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    forecast = prepare_data(BUY_PRICES + SELL_PRICES[:5], minutes)
    hass.states.async_set("sensor.test_forecast", "0.10", {"forecast": forecast})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.battery_optimizer_action")
    assert state is not None
    assert state.attributes[CONF_GREEDY_CHARGE] is (configured is True)
    schedule = state.attributes["schedule"]
    factor = minutes // 15
    assert sum(item["action"] == ACTION_BUY for item in schedule) == (
        (8 if configured else 4) // factor
    )
    assert sum(item["action"] == ACTION_SELL for item in schedule) == 4 // factor


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("source", ["user", "reconfigure"])
async def test_greedy_charge_config_flow(hass, configured, source):
    context = {"source": source}
    if source == "reconfigure":
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_GREEDY_CHARGE: not configured},
        )
        entry.add_to_hass(hass)
        context["entry_id"] = entry.entry_id
    result = await hass.config_entries.flow.async_init(DOMAIN, context=context)
    assert result["type"] == "form"
    expected_default = not configured if source == "reconfigure" else False
    assert result["data_schema"]({})[CONF_GREEDY_CHARGE] is expected_default
    data = ConfigFlow()._get_schema()({CONF_GREEDY_CHARGE: configured})

    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    if source == "reconfigure":
        assert result["type"] == "abort"
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_GREEDY_CHARGE] is configured
    else:
        assert result["type"] == "create_entry"
        assert result["data"][CONF_GREEDY_CHARGE] is configured


@pytest.mark.parametrize("algorithm", [ALGORITHM_WHSS, ALGORITHM_MPES])
@pytest.mark.parametrize("configured", [False, True])
def test_greedy_charge_dry_run(algorithm, configured):
    from dry_run import calculate_action_schedule

    schedule, intervals = calculate_action_schedule(
        prepare_data(BUY_PRICES + SELL_PRICES[:5]),
        charge_quarters=8,
        discharge_quarters=4,
        algorithm_type=algorithm,
        solar_bonus_percent=0,
        solar_bonus_fixed_c_kwh=0,
        greedy_charge=configured,
    )
    assert intervals == 1
    assert sum(item["action"] == ACTION_BUY for item in schedule) == (8 if configured else 4)
    assert sum(item["action"] == ACTION_SELL for item in schedule) == 4
