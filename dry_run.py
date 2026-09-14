#!/usr/bin/env python3
import json
import sys
import yaml
from datetime import datetime, timedelta

from custom_components.zonneplan_peakdetect.const import (
    ALGORITHM_WHSS,
    ALGORITHM_HSWAS,
    ALGORITHM_MPES,
    MULTIPLIER_CPWL,
    MULTIPLIER_BLOCK,
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_STOP,
)

# Minimal raw datetime parsing
def _parse_datetime(val):
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        # Handle +00:00 timezone format safely
        if val.endswith("+00:00"):
            val = val[:-6]
        return datetime.fromisoformat(val)
    return None

try:
    from astral import LocationInfo
    from astral.sun import sun
    HAS_ASTRAL = True
except ImportError:
    HAS_ASTRAL = False

def _get_solar_bonus_windows(forecast_datetimes):
    """Return daylight windows covered by the forecast."""
    if not forecast_datetimes:
        return []

    first_date = min(item.date() for item in forecast_datetimes)
    last_date = max(item.date() for item in forecast_datetimes)
    windows = []
    
    if HAS_ASTRAL:
        city = LocationInfo("Amsterdam", "Netherlands", "Europe/Amsterdam", 52.3676, 4.9041)
        current_date = first_date - timedelta(days=1)
        while current_date <= last_date + timedelta(days=1):
            try:
                s = sun(city.observer, date=current_date)
                sunrise = s['sunrise']
                sunset = s['sunset']
                if sunrise < sunset:
                    windows.append((sunrise, sunset))
            except Exception:
                pass
            current_date += timedelta(days=1)
    else:
        # Fallback: standard daily sunrise/sunset in UTC (Amsterdam DST is roughly 4am to 8pm UTC)
        current_date = first_date - timedelta(days=1)
        while current_date <= last_date + timedelta(days=1):
            from datetime import timezone
            sunrise = datetime(current_date.year, current_date.month, current_date.day, 4, 0, tzinfo=timezone.utc)
            sunset = datetime(current_date.year, current_date.month, current_date.day, 20, 0, tzinfo=timezone.utc)
            windows.append((sunrise, sunset))
            current_date += timedelta(days=1)
            
    return windows

def _solar_bonus_applies(slot_time, solar_windows):
    """Check whether a forecast slot is between sunrise and sunset."""
    from datetime import timezone
    def to_naive_utc(dt):
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    slot_naive = to_naive_utc(slot_time)
    
    for sunrise, sunset in solar_windows:
        sunrise_naive = to_naive_utc(sunrise)
        sunset_naive = to_naive_utc(sunset)
        if sunrise_naive <= slot_naive < sunset_naive:
            return True
    return False

def calculate_action_schedule(
    forecast_data,
    charge_quarters=13,
    discharge_quarters=11,
    min_profit_eur_kwh=0.06,
    price_delta_percent=20.0,
    algorithm_type="whss",
    multiplier_type="block",
    solar_bonus_percent=10.0,
    solar_bonus_fixed_c_kwh=2.0
):
    """Polymorphically runs the chosen BESS Arbitrage strategy and post-processes multipliers."""
    if not forecast_data:
        return [], 0
    
    rte_factor = 1.0 - (price_delta_percent / 100.0)
    solar_bonus_fixed_eur_kwh = solar_bonus_fixed_c_kwh / 100.0
    
    first_dt = None
    if forecast_data:
        first_raw = forecast_data[0].get('datetime') or forecast_data[0].get('start_date')
        if first_raw:
            first_dt = _parse_datetime(first_raw)
            
    # Prepare datetimes list for daylight window calculations
    forecast_datetimes = []
    for item in forecast_data:
        raw_dt = item.get('start_date') or item.get('datetime')
        if raw_dt:
            dt = _parse_datetime(raw_dt)
            if dt:
                forecast_datetimes.append(dt)
                
    solar_bonus_windows = _get_solar_bonus_windows(forecast_datetimes)
            
    # 1. Prepare Data
    now = first_dt if first_dt else datetime.fromisoformat("2026-07-28T18:00:00")  # Mock current time to start of dataset
    prepared_data = []
    for idx, item in enumerate(forecast_data):
        raw_dt = item.get('start_date') or item.get('datetime')
        raw_price = item.get('electricity_price')
        if raw_price is None and 'price_tax_included' in item:
            raw_price = item['price_tax_included'].get('amount')
        
        # If input has direct float price_eur_kwh, use it, else convert
        if 'price_eur_kwh' in item:
            price = item['price_eur_kwh']
        elif raw_price is not None:
            price = raw_price / 10_000_000.0
        else:
            continue
            
        dt = _parse_datetime(raw_dt)

        solar_bonus_applied = bool(
            dt
            and (
                solar_bonus_percent > 0
                or solar_bonus_fixed_eur_kwh > 0
            )
            and _solar_bonus_applies(dt, solar_bonus_windows)
        )
        effective_price = price
        if solar_bonus_applied:
            effective_price = (
                price + solar_bonus_fixed_eur_kwh
            ) * (1.0 + solar_bonus_percent / 100.0)

        prepared_data.append({
            'datetime': raw_dt,
            'price_eur_kwh': price,
            'buy_price_eur_kwh': price,
            'sell_price_eur_kwh': effective_price,
            'forecast_price_eur_kwh': price,
            'solar_bonus_applied': solar_bonus_applied,
            'solar_bonus_multiplier': round(
                effective_price / price, 4
            ) if price != 0 else 1.0,
            'price_multiplier': 1.0,
            'action': ACTION_STOP,
            'interval_id': -1,
            'sort_index': idx
        })

    # 2. Execute selected polymorphic strategy
    charge_slots_count = charge_quarters
    discharge_slots_count = discharge_quarters

    from custom_components.zonneplan_peakdetect.strategies import get_arbitrage_strategy
    strategy = get_arbitrage_strategy(algorithm_type)
    prepared_data = strategy.calculate_schedule(
        prepared_data,
        charge_slots_count,
        discharge_slots_count,
        rte_factor,
        min_profit_eur_kwh,
        now
    )

    # 3. Post-Process Multipliers using the same logic as sensor.py
    n = len(prepared_data)
    if n > 0:
        valleys = {}
        for idx, item in enumerate(prepared_data):
            iid = item.get('interval_id', -1)
            if item.get('action') != ACTION_STOP:
                if iid >= 0:
                    price = item.get('buy_price_eur_kwh', item['price_eur_kwh'])
                    if iid not in valleys or price < valleys[iid]['price']:
                        valleys[iid] = {'idx': idx, 'price': price}

        active_wave_ids = sorted(valleys.keys())

        if not active_wave_ids:
            # Fallback to absolute minimum
            global_min = min(item.get('buy_price_eur_kwh', item['price_eur_kwh']) for item in prepared_data)
            for item in prepared_data:
                p = item.get('sell_price_eur_kwh', item['price_eur_kwh'])
                item['price_multiplier'] = round(p / global_min, 2) if global_min > 0 else round(1.0 + p / abs(global_min), 2) if global_min != 0 else 1.0
                item['interval_id'] = -1
        else:
            # Reassign close midpoints
            if len(active_wave_ids) == 1:
                single_id = active_wave_ids[0]
                for item in prepared_data:
                    item['interval_id'] = single_id
            else:
                midpoints = []
                for k in range(len(active_wave_ids) - 1):
                    idx_a = valleys[active_wave_ids[k]]['idx']
                    idx_b = valleys[active_wave_ids[k+1]]['idx']
                    midpoints.append((idx_a + idx_b) // 2)

                for idx, item in enumerate(prepared_data):
                    assigned_id = active_wave_ids[0]
                    for k, mid in enumerate(midpoints):
                        if idx > mid:
                            assigned_id = active_wave_ids[k+1]
                    item['interval_id'] = assigned_id

            # Calculate divisor for each interval in the timeline
            if multiplier_type == MULTIPLIER_BLOCK:
                # Partition into windows anchored at the end of each active wave segment
                windows = []
                prev_end = 0
                for iid in active_wave_ids:
                    active_indices = [idx for idx, item in enumerate(prepared_data) if item.get('interval_id', -1) == iid and item.get('action') != ACTION_STOP]
                    end_idx = (max(active_indices) + 1) if active_indices else n
                    windows.append((prev_end, end_idx))
                    prev_end = end_idx
                
                if windows:
                    # Extend the last window to cover the trailing part of the day
                    last_start, _ = windows[-1]
                    windows[-1] = (last_start, n)

                # For each window, find its minimum price and calculate multipliers
                for start, end in windows:
                    window_slice = prepared_data[start:end]
                    if window_slice:
                        window_min = min(item.get('buy_price_eur_kwh', item['price_eur_kwh']) for item in window_slice)
                        for item in window_slice:
                            p = item.get('sell_price_eur_kwh', item['price_eur_kwh'])
                            item['price_multiplier'] = round(p / window_min, 2) if window_min > 0 else round(1.0 + p / abs(window_min), 2) if window_min != 0 else 1.0
            else: # MULTIPLIER_CPWL
                anchors = [(valleys[iid]['idx'], valleys[iid]['price']) for iid in active_wave_ids]
                
                for idx, item in enumerate(prepared_data):
                    p = item.get('sell_price_eur_kwh', item['price_eur_kwh'])
                    
                    # If before the first valley, lock to first valley price
                    if idx <= anchors[0][0]:
                        divisor = anchors[0][1]
                    # If after the last valley, lock to last valley price
                    elif idx >= anchors[-1][0]:
                        divisor = anchors[-1][1]
                    # If in between two valleys, linearly interpolate
                    else:
                        # Find the two bounding valleys
                        for k in range(len(anchors) - 1):
                            idx_a, price_a = anchors[k]
                            idx_b, price_b = anchors[k+1]
                            if idx_a <= idx <= idx_b:
                                # Interpolation fraction
                                f = (idx - idx_a) / (idx_b - idx_a)
                                divisor = (1.0 - f) * price_a + f * price_b
                                break
                    
                    # Compute multiplier
                    item['price_multiplier'] = round(p / divisor, 2) if divisor > 0 else round(1.0 + p / abs(divisor), 2) if divisor != 0 else 1.0

        # Assign price_multiplier_quantile (1, 2, 3, 4) to each interval
        multipliers = [item.get('price_multiplier', 1.0) for item in prepared_data]
        if len(multipliers) >= 2:
            import statistics
            try:
                q = statistics.quantiles(multipliers, n=4)
                for item in prepared_data:
                    m = item.get('price_multiplier', 1.0)
                    if m <= q[0]:
                        item['price_multiplier_quantile'] = 1
                    elif m <= q[1]:
                        item['price_multiplier_quantile'] = 2
                    elif m <= q[2]:
                        item['price_multiplier_quantile'] = 3
                    else:
                        item['price_multiplier_quantile'] = 4
            except Exception:
                pass

    interval_count = len(set(item['interval_id'] for item in prepared_data if item.get('interval_id', -1) >= 0 and item.get('action') != ACTION_STOP))

    # Format output keys
    formatted_data = []
    for item in prepared_data:
        bonus_amount = round(item['sell_price_eur_kwh'] - item['price_eur_kwh'], 7) if item.get('solar_bonus_applied') else 0.0
        formatted_item = {
            'datetime': item['datetime'],
            'price_eur_kwh': item['price_eur_kwh'],
            'price_bonus_eur_kwh': bonus_amount,
            'price_multiplier': item['price_multiplier'],
            'action': item['action'],
            'interval_id': item['interval_id'],
        }
        if 'price_multiplier_quantile' in item:
            formatted_item['price_multiplier_quantile'] = item['price_multiplier_quantile']
        formatted_data.append(formatted_item)
        
    return formatted_data, interval_count

# Your original forecast dataset
forecast_data = [
    {"datetime": "2026-07-28T18:00:00+00:00", "price_eur_kwh": 0.3703554},
    {"datetime": "2026-07-28T18:15:00+00:00", "price_eur_kwh": 0.3803137},
    {"datetime": "2026-07-28T18:30:00+00:00", "price_eur_kwh": 0.395596},
    {"datetime": "2026-07-28T18:45:00+00:00", "price_eur_kwh": 0.4176664},
    {"datetime": "2026-07-28T19:00:00+00:00", "price_eur_kwh": 0.3970359},
    {"datetime": "2026-07-28T19:15:00+00:00", "price_eur_kwh": 0.3896791},
    {"datetime": "2026-07-28T19:30:00+00:00", "price_eur_kwh": 0.3797571},
    {"datetime": "2026-07-28T19:45:00+00:00", "price_eur_kwh": 0.372969},
    {"datetime": "2026-07-28T20:00:00+00:00", "price_eur_kwh": 0.3859039},
    {"datetime": "2026-07-28T20:15:00+00:00", "price_eur_kwh": 0.3692906},
    {"datetime": "2026-07-28T20:30:00+00:00", "price_eur_kwh": 0.3577835},
    {"datetime": "2026-07-28T20:45:00+00:00", "price_eur_kwh": 0.3491683},
    {"datetime": "2026-07-28T21:00:00+00:00", "price_eur_kwh": 0.3569728},
    {"datetime": "2026-07-28T21:15:00+00:00", "price_eur_kwh": 0.3315023},
    {"datetime": "2026-07-28T21:30:00+00:00", "price_eur_kwh": 0.3316474},
    {"datetime": "2026-07-28T21:45:00+00:00", "price_eur_kwh": 0.3253918},
    {"datetime": "2026-07-28T22:00:00+00:00", "price_eur_kwh": 0.3257669},
    {"datetime": "2026-07-28T22:15:00+00:00", "price_eur_kwh": 0.3041078},
    {"datetime": "2026-07-28T22:30:00+00:00", "price_eur_kwh": 0.3148526},
    {"datetime": "2026-07-28T22:45:00+00:00", "price_eur_kwh": 0.3050638},
    {"datetime": "2026-07-28T23:00:00+00:00", "price_eur_kwh": 0.3189787},
    {"datetime": "2026-07-28T23:15:00+00:00", "price_eur_kwh": 0.3085364},
    {"datetime": "2026-07-28T23:30:00+00:00", "price_eur_kwh": 0.3062133},
    {"datetime": "2026-07-28T23:45:00+00:00", "price_eur_kwh": 0.299159},
    {"datetime": "2026-07-29T00:00:00+00:00", "price_eur_kwh": 0.301337},
    {"datetime": "2026-07-29T00:15:00+00:00", "price_eur_kwh": 0.2982152},
    {"datetime": "2026-07-29T00:30:00+00:00", "price_eur_kwh": 0.292504},
    {"datetime": "2026-07-29T00:45:00+00:00", "price_eur_kwh": 0.2911609},
    {"datetime": "2026-07-29T01:00:00+00:00", "price_eur_kwh": 0.2942101},
    {"datetime": "2026-07-29T01:15:00+00:00", "price_eur_kwh": 0.2943795},
    {"datetime": "2026-07-29T01:30:00+00:00", "price_eur_kwh": 0.2942343},
    {"datetime": "2026-07-29T01:45:00+00:00", "price_eur_kwh": 0.2939802},
    {"datetime": "2026-07-29T02:00:00+00:00", "price_eur_kwh": 0.2932542},
    {"datetime": "2026-07-29T02:15:00+00:00", "price_eur_kwh": 0.2979732},
    {"datetime": "2026-07-29T02:30:00+00:00", "price_eur_kwh": 0.2986023},
    {"datetime": "2026-07-29T02:45:00+00:00", "price_eur_kwh": 0.3047613},
    {"datetime": "2026-07-29T03:00:00+00:00", "price_eur_kwh": 0.2980336},
    {"datetime": "2026-07-29T03:15:00+00:00", "price_eur_kwh": 0.2999576},
    {"datetime": "2026-07-29T03:30:00+00:00", "price_eur_kwh": 0.3123359},
    {"datetime": "2026-07-29T03:45:00+00:00", "price_eur_kwh": 0.3250287},
    {"datetime": "2026-07-29T04:00:00+00:00", "price_eur_kwh": 0.3238793},
    {"datetime": "2026-07-29T04:15:00+00:00", "price_eur_kwh": 0.3347935},
    {"datetime": "2026-07-29T04:30:00+00:00", "price_eur_kwh": 0.3333173},
    {"datetime": "2026-07-29T04:45:00+00:00", "price_eur_kwh": 0.3330873},
    {"datetime": "2026-07-29T05:00:00+00:00", "price_eur_kwh": 0.3358825},
    {"datetime": "2026-07-29T05:15:00+00:00", "price_eur_kwh": 0.3339223},
    {"datetime": "2026-07-29T05:30:00+00:00", "price_eur_kwh": 0.322512},
    {"datetime": "2026-07-29T05:45:00+00:00", "price_eur_kwh": 0.3031157},
    {"datetime": "2026-07-29T06:00:00+00:00", "price_eur_kwh": 0.3331963},
    {"datetime": "2026-07-29T06:15:00+00:00", "price_eur_kwh": 0.3148043},
    {"datetime": "2026-07-29T06:30:00+00:00", "price_eur_kwh": 0.3053421},
    {"datetime": "2026-07-29T06:45:00+00:00", "price_eur_kwh": 0.2809727},
    {"datetime": "2026-07-29T07:00:00+00:00", "price_eur_kwh": 0.3169338},
    {"datetime": "2026-07-29T07:15:00+00:00", "price_eur_kwh": 0.2843607},
    {"datetime": "2026-07-29T07:30:00+00:00", "price_eur_kwh": 0.2648434},
    {"datetime": "2026-07-29T07:45:00+00:00", "price_eur_kwh": 0.2572203},
    {"datetime": "2026-07-29T08:00:00+00:00", "price_eur_kwh": 0.2647708},
    {"datetime": "2026-07-29T08:15:00+00:00", "price_eur_kwh": 0.2536025},
    {"datetime": "2026-07-29T08:30:00+00:00", "price_eur_kwh": 0.2280594},
    {"datetime": "2026-07-29T08:45:00+00:00", "price_eur_kwh": 0.191469},
    {"datetime": "2026-07-29T09:00:00+00:00", "price_eur_kwh": 0.2061462},
    {"datetime": "2026-07-29T09:15:00+00:00", "price_eur_kwh": 0.1705359},
    {"datetime": "2026-07-29T09:30:00+00:00", "price_eur_kwh": 0.1461908},
    {"datetime": "2026-07-29T09:45:00+00:00", "price_eur_kwh": 0.1410483},
    {"datetime": "2026-07-29T10:00:00+00:00", "price_eur_kwh": 0.1372852},
    {"datetime": "2026-07-29T10:15:00+00:00", "price_eur_kwh": 0.1311263},
    {"datetime": "2026-07-29T10:30:00+00:00", "price_eur_kwh": 0.1310657},
    {"datetime": "2026-07-29T10:45:00+00:00", "price_eur_kwh": 0.1310053},
    {"datetime": "2026-07-29T11:00:00+00:00", "price_eur_kwh": 0.1311868},
    {"datetime": "2026-07-29T11:15:00+00:00", "price_eur_kwh": 0.1311989},
    {"datetime": "2026-07-29T11:30:00+00:00", "price_eur_kwh": 0.1311626},
    {"datetime": "2026-07-29T11:45:00+00:00", "price_eur_kwh": 0.1311384},
    {"datetime": "2026-07-29T12:00:00+00:00", "price_eur_kwh": 0.1315982},
    {"datetime": "2026-07-29T12:15:00+00:00", "price_eur_kwh": 0.1318402},
    {"datetime": "2026-07-29T12:30:00+00:00", "price_eur_kwh": 0.1318644},
    {"datetime": "2026-07-29T12:45:00+00:00", "price_eur_kwh": 0.1321064},
    {"datetime": "2026-07-29T13:00:00+00:00", "price_eur_kwh": 0.1319249},
    {"datetime": "2026-07-29T13:15:00+00:00", "price_eur_kwh": 0.1325661},
    {"datetime": "2026-07-29T13:30:00+00:00", "price_eur_kwh": 0.1381201},
    {"datetime": "2026-07-29T13:45:00+00:00", "price_eur_kwh": 0.1559918},
    {"datetime": "2026-07-29T14:00:00+00:00", "price_eur_kwh": 0.158678},
    {"datetime": "2026-07-29T14:15:00+00:00", "price_eur_kwh": 0.2157053},
    {"datetime": "2026-07-29T14:30:00+00:00", "price_eur_kwh": 0.2397601},
    {"datetime": "2026-07-29T14:45:00+00:00", "price_eur_kwh": 0.2560951},
    {"datetime": "2026-07-29T15:00:00+00:00", "price_eur_kwh": 0.2479639},
    {"datetime": "2026-07-29T15:15:00+00:00", "price_eur_kwh": 0.2663317},
    {"datetime": "2026-07-29T15:30:00+00:00", "price_eur_kwh": 0.2920926},
    {"datetime": "2026-07-29T15:45:00+00:00", "price_eur_kwh": 0.3124689},
    {"datetime": "2026-07-29T16:00:00+00:00", "price_eur_kwh": 0.2851351},
    {"datetime": "2026-07-29T16:15:00+00:00", "price_eur_kwh": 0.3090689},
    {"datetime": "2026-07-29T16:30:00+00:00", "price_eur_kwh": 0.3311272},
    {"datetime": "2026-07-29T16:45:00+00:00", "price_eur_kwh": 0.3758972},
    {"datetime": "2026-07-29T17:00:00+00:00", "price_eur_kwh": 0.3398876},
    {"datetime": "2026-07-29T17:15:00+00:00", "price_eur_kwh": 0.368371},
    {"datetime": "2026-07-29T17:30:00+00:00", "price_eur_kwh": 0.4259428},
    {"datetime": "2026-07-29T17:45:00+00:00", "price_eur_kwh": 0.5410501},
    {"datetime": "2026-07-29T18:00:00+00:00", "price_eur_kwh": 0.4281571},
    {"datetime": "2026-07-29T18:15:00+00:00", "price_eur_kwh": 0.443028},
    {"datetime": "2026-07-29T18:30:00+00:00", "price_eur_kwh": 0.4573665},
    {"datetime": "2026-07-29T18:45:00+00:00", "price_eur_kwh": 0.4389745},
    {"datetime": "2026-07-29T19:00:00+00:00", "price_eur_kwh": 0.4334448},
    {"datetime": "2026-07-29T19:15:00+00:00", "price_eur_kwh": 0.4032069},
    {"datetime": "2026-07-29T19:30:00+00:00", "price_eur_kwh": 0.393781},
    {"datetime": "2026-07-29T19:45:00+00:00", "price_eur_kwh": 0.375268},
    {"datetime": "2026-07-29T20:00:00+00:00", "price_eur_kwh": 0.3813059},
    {"datetime": "2026-07-29T20:15:00+00:00", "price_eur_kwh": 0.370791},
    {"datetime": "2026-07-29T20:30:00+00:00", "price_eur_kwh": 0.3612198},
    {"datetime": "2026-07-29T20:45:00+00:00", "price_eur_kwh": 0.3438322},
    {"datetime": "2026-07-29T21:00:00+00:00", "price_eur_kwh": 0.3465184},
    {"datetime": "2026-07-29T21:15:00+00:00", "price_eur_kwh": 0.3405894},
    {"datetime": "2026-07-29T21:30:00+00:00", "price_eur_kwh": 0.3370078},
    {"datetime": "2026-07-29T21:45:00+00:00", "price_eur_kwh": 0.307629},
    {"datetime": "2026-07-29T22:00:00+00:00", "price_eur_kwh": 0.3270616},
    {"datetime": "2026-07-29T22:15:00+00:00", "price_eur_kwh": 0.314768},
    {"datetime": "2026-07-29T22:30:00+00:00", "price_eur_kwh": 0.3034666},
    {"datetime": "2026-07-29T22:45:00+00:00", "price_eur_kwh": 0.30291},
    {"datetime": "2026-07-29T23:00:00+00:00", "price_eur_kwh": 0.3051485},
    {"datetime": "2026-07-29T23:15:00+00:00", "price_eur_kwh": 0.3025228},
    {"datetime": "2026-07-29T23:30:00+00:00", "price_eur_kwh": 0.3070361},
    {"datetime": "2026-07-29T23:45:00+00:00", "price_eur_kwh": 0.297828},
    {"datetime": "2026-07-30T00:00:00+00:00", "price_eur_kwh": 0.3038779},
    {"datetime": "2026-07-30T00:15:00+00:00", "price_eur_kwh": 0.3034303},
    {"datetime": "2026-07-30T00:30:00+00:00", "price_eur_kwh": 0.2978159},
    {"datetime": "2026-07-30T00:45:00+00:00", "price_eur_kwh": 0.2958557},
    {"datetime": "2026-07-30T01:00:00+00:00", "price_eur_kwh": 0.2953354},
    {"datetime": "2026-07-30T01:15:00+00:00", "price_eur_kwh": 0.2931332},
    {"datetime": "2026-07-30T01:30:00+00:00", "price_eur_kwh": 0.2907858},
    {"datetime": "2026-07-30T01:45:00+00:00", "price_eur_kwh": 0.2922861},
    {"datetime": "2026-07-30T02:00:00+00:00", "price_eur_kwh": 0.2906043},
    {"datetime": "2026-07-30T02:15:00+00:00", "price_eur_kwh": 0.2903744},
    {"datetime": "2026-07-30T02:30:00+00:00", "price_eur_kwh": 0.2907979},
    {"datetime": "2026-07-30T02:45:00+00:00", "price_eur_kwh": 0.2885231},
    {"datetime": "2026-07-30T03:00:00+00:00", "price_eur_kwh": 0.290568},
    {"datetime": "2026-07-30T03:15:00+00:00", "price_eur_kwh": 0.2902049},
    {"datetime": "2026-07-30T03:30:00+00:00", "price_eur_kwh": 0.2987355},
    {"datetime": "2026-07-30T03:45:00+00:00", "price_eur_kwh": 0.3040716},
    {"datetime": "2026-07-30T04:00:00+00:00", "price_eur_kwh": 0.3044709},
    {"datetime": "2026-07-30T04:15:00+00:00", "price_eur_kwh": 0.3134007},
    {"datetime": "2026-07-30T04:30:00+00:00", "price_eur_kwh": 0.3131466},
    {"datetime": "2026-07-30T04:45:00+00:00", "price_eur_kwh": 0.3100489},
    {"datetime": "2026-07-30T05:00:00+00:00", "price_eur_kwh": 0.3314176},
    {"datetime": "2026-07-30T05:15:00+00:00", "price_eur_kwh": 0.3172},
    {"datetime": "2026-07-30T05:30:00+00:00", "price_eur_kwh": 0.303999},
    {"datetime": "2026-07-30T05:45:00+00:00", "price_eur_kwh": 0.2868896},
    {"datetime": "2026-07-30T06:00:00+00:00", "price_eur_kwh": 0.330014},
    {"datetime": "2026-07-30T06:15:00+00:00", "price_eur_kwh": 0.3092625},
    {"datetime": "2026-07-30T06:30:00+00:00", "price_eur_kwh": 0.2903139},
    {"datetime": "2026-07-30T06:45:00+00:00", "price_eur_kwh": 0.2715952},
    {"datetime": "2026-07-30T07:00:00+00:00", "price_eur_kwh": 0.3017121},
    {"datetime": "2026-07-30T07:15:00+00:00", "price_eur_kwh": 0.2817108},
    {"datetime": "2026-07-30T07:30:00+00:00", "price_eur_kwh": 0.2652306},
    {"datetime": "2026-07-30T07:45:00+00:00", "price_eur_kwh": 0.2530096},
    {"datetime": "2026-07-30T08:00:00+00:00", "price_eur_kwh": 0.2667794},
    {"datetime": "2026-07-30T08:15:00+00:00", "price_eur_kwh": 0.245193},
    {"datetime": "2026-07-30T08:30:00+00:00", "price_eur_kwh": 0.2294993},
    {"datetime": "2026-07-30T08:45:00+00:00", "price_eur_kwh": 0.2016088},
    {"datetime": "2026-07-30T09:00:00+00:00", "price_eur_kwh": 0.2221788},
    {"datetime": "2026-07-30T09:15:00+00:00", "price_eur_kwh": 0.1802159},
    {"datetime": "2026-07-30T09:30:00+00:00", "price_eur_kwh": 0.161098},
    {"datetime": "2026-07-30T09:45:00+00:00", "price_eur_kwh": 0.1496272},
    {"datetime": "2026-07-30T10:00:00+00:00", "price_eur_kwh": 0.1495909},
    {"datetime": "2026-07-30T10:15:00+00:00", "price_eur_kwh": 0.1476428},
    {"datetime": "2026-07-30T10:30:00+00:00", "price_eur_kwh": 0.1580004},
    {"datetime": "2026-07-30T10:45:00+00:00", "price_eur_kwh": 0.1538622},
    {"datetime": "2026-07-30T11:00:00+00:00", "price_eur_kwh": 0.1418106},
    {"datetime": "2026-07-30T11:15:00+00:00", "price_eur_kwh": 0.159525},
    {"datetime": "2026-07-30T11:30:00+00:00", "price_eur_kwh": 0.170778},
    {"datetime": "2026-07-30T11:45:00+00:00", "price_eur_kwh": 0.1670391},
    {"datetime": "2026-07-30T12:00:00+00:00", "price_eur_kwh": 0.1530878},
    {"datetime": "2026-07-30T12:15:00+00:00", "price_eur_kwh": 0.1652846},
    {"datetime": "2026-07-30T12:30:00+00:00", "price_eur_kwh": 0.1758479},
    {"datetime": "2026-07-30T12:45:00+00:00", "price_eur_kwh": 0.2249981},
    {"datetime": "2026-07-30T13:00:00+00:00", "price_eur_kwh": 0.1925579},
    {"datetime": "2026-07-30T13:15:00+00:00", "price_eur_kwh": 0.217847},
    {"datetime": "2026-07-30T13:30:00+00:00", "price_eur_kwh": 0.2372675},
    {"datetime": "2026-07-30T13:45:00+00:00", "price_eur_kwh": 0.2588055},
    {"datetime": "2026-07-30T14:00:00+00:00", "price_eur_kwh": 0.2341336},
    {"datetime": "2026-07-30T14:15:00+00:00", "price_eur_kwh": 0.2443823},
    {"datetime": "2026-07-30T14:30:00+00:00", "price_eur_kwh": 0.2603422},
    {"datetime": "2026-07-30T14:45:00+00:00", "price_eur_kwh": 0.2832959},
    {"datetime": "2026-07-30T15:00:00+00:00", "price_eur_kwh": 0.2706514},
    {"datetime": "2026-07-30T15:15:00+00:00", "price_eur_kwh": 0.2689695},
    {"datetime": "2026-07-30T15:30:00+00:00", "price_eur_kwh": 0.2878092},
    {"datetime": "2026-07-30T15:45:00+00:00", "price_eur_kwh": 0.3146833},
    {"datetime": "2026-07-30T16:00:00+00:00", "price_eur_kwh": 0.2858248},
    {"datetime": "2026-07-30T16:15:00+00:00", "price_eur_kwh": 0.3048823},
    {"datetime": "2026-07-30T16:30:00+00:00", "price_eur_kwh": 0.322391},
    {"datetime": "2026-07-30T16:45:00+00:00", "price_eur_kwh": 0.3257427},
    {"datetime": "2026-07-30T17:00:00+00:00", "price_eur_kwh": 0.311138},
    {"datetime": "2026-07-30T17:15:00+00:00", "price_eur_kwh": 0.3389438},
    {"datetime": "2026-07-30T17:30:00+00:00", "price_eur_kwh": 0.3506324},
    {"datetime": "2026-07-30T17:45:00+00:00", "price_eur_kwh": 0.3621758},
    {"datetime": "2026-07-30T18:00:00+00:00", "price_eur_kwh": 0.3521933},
    {"datetime": "2026-07-30T18:15:00+00:00", "price_eur_kwh": 0.3587394},
    {"datetime": "2026-07-30T18:30:00+00:00", "price_eur_kwh": 0.3710935},
    {"datetime": "2026-07-30T18:45:00+00:00", "price_eur_kwh": 0.3936479},
    {"datetime": "2026-07-30T19:00:00+00:00", "price_eur_kwh": 0.3899816},
    {"datetime": "2026-07-30T19:15:00+00:00", "price_eur_kwh": 0.3852747},
    {"datetime": "2026-07-30T19:30:00+00:00", "price_eur_kwh": 0.3766232},
    {"datetime": "2026-07-30T19:45:00+00:00", "price_eur_kwh": 0.3613046},
    {"datetime": "2026-07-30T20:00:00+00:00", "price_eur_kwh": 0.374421},
    {"datetime": "2026-07-30T20:15:00+00:00", "price_eur_kwh": 0.3656969},
    {"datetime": "2026-07-30T20:30:00+00:00", "price_eur_kwh": 0.3574205},
    {"datetime": "2026-07-30T20:45:00+00:00", "price_eur_kwh": 0.3468935},
    {"datetime": "2026-07-30T21:00:00+00:00", "price_eur_kwh": 0.3502088},
    {"datetime": "2026-07-30T21:15:00+00:00", "price_eur_kwh": 0.3397545},
    {"datetime": "2026-07-30T21:30:00+00:00", "price_eur_kwh": 0.3362818},
    {"datetime": "2026-07-30T21:45:00+00:00", "price_eur_kwh": 0.323964},
]

def main():
    data = forecast_data
    charge_quarters = 13
    discharge_quarters = 11
    min_profit = 0.06
    price_delta_percent = 20.0
    solar_bonus_percent = 10.0
    solar_bonus_fixed_c_kwh = 2.0
    algorithm_type = ALGORITHM_WHSS
    multiplier_type = MULTIPLIER_BLOCK

    filepath = None
    for arg in sys.argv[1:]:
        if arg in (ALGORITHM_WHSS, ALGORITHM_HSWAS, ALGORITHM_MPES):
            algorithm_type = arg
        elif arg in (MULTIPLIER_CPWL, MULTIPLIER_BLOCK):
            multiplier_type = arg
        else:
            filepath = arg

    if filepath is not None:
        try:
            with open(filepath, 'r') as f:
                content = f.read()
            
            # Try to parse as JSON first, then YAML
            parsed = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = yaml.safe_load(content)
            
            if parsed is None:
                raise ValueError("Failed to parse file as JSON or YAML.")
            
            # Extract list from potential nested Home Assistant states / dict wrapper structures
            if isinstance(parsed, dict):
                # Check for attributes, state, etc.
                if 'attributes' in parsed and isinstance(parsed['attributes'], dict):
                    attrs = parsed['attributes']
                    if 'schedule' in attrs:
                        parsed = attrs['schedule']
                    elif 'forecast' in attrs:
                        parsed = attrs['forecast']
                    # Load parameters if present
                    if 'charge_quarters' in attrs:
                        charge_quarters = attrs['charge_quarters']
                    if 'discharge_quarters' in attrs:
                        discharge_quarters = attrs['discharge_quarters']
                    if 'min_profit_required_eur_kwh' in attrs:
                        min_profit = attrs['min_profit_required_eur_kwh']
                    if 'price_delta_threshold_percent' in attrs:
                        price_delta_percent = attrs['price_delta_threshold_percent']
                    if 'multiplier_type' in attrs:
                        multiplier_type = attrs['multiplier_type']
                    if 'solar_bonus_percent' in attrs:
                        solar_bonus_percent = attrs['solar_bonus_percent']
                    if 'solar_bonus_fixed_c_kwh' in attrs:
                        solar_bonus_fixed_c_kwh = attrs['solar_bonus_fixed_c_kwh']
                elif 'schedule' in parsed:
                    parsed = parsed['schedule']
                elif 'forecast' in parsed:
                    parsed = parsed['forecast']
                else:
                    # Maybe it's a single dictionary of item?
                    parsed = [parsed]
            
            if not isinstance(parsed, list):
                raise ValueError("Expected a list of schedule/forecast items.")
            
            data = parsed
            print(f"Successfully loaded {len(data)} items from {filepath}")
        except Exception as e:
            print(f"Error loading {filepath}: {e}")
            print("Falling back to built-in forecast_data.")
            data = forecast_data

    schedule, intervals = calculate_action_schedule(
        data, 
        charge_quarters=charge_quarters, 
        discharge_quarters=discharge_quarters, 
        min_profit_eur_kwh=min_profit,
        price_delta_percent=price_delta_percent,
        algorithm_type=algorithm_type,
        multiplier_type=multiplier_type,
        solar_bonus_percent=solar_bonus_percent,
        solar_bonus_fixed_c_kwh=solar_bonus_fixed_c_kwh
    )
    
    active_intervals = len(set(item['interval_id'] for item in schedule if item.get('interval_id', -1) >= 0 and item.get('action') != ACTION_STOP))

    # Calculate quantiles fixed per interval_id
    from collections import defaultdict
    iid_multipliers = defaultdict(list)
    for item in schedule:
        iid = item.get('interval_id', -1)
        iid_multipliers[iid].append(item.get('price_multiplier', 1.0))
        
    iid_quantiles = {}
    for iid, mults in iid_multipliers.items():
        min_val, max_val = 1.0, 1.0
        q25, q50, q75 = 1.0, 1.0, 1.0
        if len(mults) >= 2:
            import statistics
            try:
                q = statistics.quantiles(mults, n=4)
                min_val = min(mults)
                max_val = max(mults)
                q25 = q[0]
                q50 = q[1]
                q75 = q[2]
            except Exception:
                pass
        elif len(mults) == 1:
            min_val = max_val = q25 = q50 = q75 = mults[0]
        
        iid_quantiles[iid] = f"[{min_val:.2f}, {q25:.2f}, {q50:.2f}, {q75:.2f}, {max_val:.2f}]"

    print("\n" + "=" * 110)
    print(" BESS ARBITRAGE SCHEDULER - ALGORITHM TEST RESULTS")
    print("=" * 110)
    print(f"Total Intervals Segmented: {active_intervals}")
    print(f"Configuration: strategy={algorithm_type}, multiplier={multiplier_type}, charge_quarters={charge_quarters}, discharge_quarters={discharge_quarters}, min_profit={min_profit}, price_delta_percent={price_delta_percent}, solar_bonus_percent={solar_bonus_percent}, solar_bonus_fixed_c_kwh={solar_bonus_fixed_c_kwh}")
    print("-" * 110)
    print(f"{'Datetime':<30} | {'Price (€/kWh)':<14} | {'Zonnebonus (€/kWh)':<19} | {'Multiplier':<11} | {'Action':<10} | {'Interval ID':<11}")
    print("-" * 110)

    last_interval_id = None
    for item in schedule:
        iid = item.get('interval_id', -1)
        if iid != last_interval_id:
            if iid == -1:
                print(f"\n=== INTERVAL ID: -1 (Unassigned / Gaps) ========================================================================")
            else:
                q_str = iid_quantiles.get(iid, "[1.00, 1.00, 1.00, 1.00, 1.00]")
                print(f"\n=== INTERVAL ID: {iid} (Quantiles: {q_str}) ========================================================================")
            last_interval_id = iid

        action = item["action"]
        if action == ACTION_CHARGE:
            action_str = f"\033[92m{action:<10}\033[0m"  # Green
        elif action =="ACTION_DISCHARGE" or action == ACTION_DISCHARGE:
            action_str = f"\033[91m{action:<10}\033[0m"  # Red
        else:
            action_str = f"{action:<10}"

        bonus_val = f"+{item['price_bonus_eur_kwh']:.7f}" if item.get('price_bonus_eur_kwh', 0.0) > 0.0 else "-"

        print(
            f"{item['datetime']:<30} | {item['price_eur_kwh']:<14.7f} | {bonus_val:<19} | {item['price_multiplier']:<11.2f} | {action_str} | {item['interval_id']:<11}"
        )
    print("=" * 110 + "\n")

if __name__ == "__main__":
    main()
