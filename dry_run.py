#!/usr/bin/env python3
import json
import sys
import yaml
from datetime import datetime, timedelta

# Action constants
ACTION_CHARGE = "Charge"
ACTION_DISCHARGE = "Discharge"
ACTION_STOP = "Stop"

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

def calculate_action_schedule(
    forecast_data,
    charge_quarters=13,
    discharge_quarters=11,
    min_profit_eur_kwh=0.06,
    price_delta_percent=20.0
):
    """Refactored core scheduling algorithm exactly as defined in sensor.py."""
    if not forecast_data:
        return []
    
    rte_factor = 1.0 - (price_delta_percent / 100.0)
    
    first_dt = None
    if forecast_data:
        first_raw = forecast_data[0].get('datetime') or forecast_data[0].get('start_date')
        if first_raw:
            first_dt = _parse_datetime(first_raw)
            
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
        is_passed = dt < now if dt else False

        prepared_data.append({
            'datetime': raw_dt,
            'price_eur_kwh': price,
            'price_multiplier': 1.0,
            'action': ACTION_STOP,
            'interval_id': -1,
            'sort_index': idx
        })

    # 2. Determine interval duration and slot counts
    interval_minutes = 15  # For 15-min quarters
    charge_slots_count = charge_quarters
    discharge_slots_count = discharge_quarters

    # 3. Segment into Waves (Intervals)
    prices = [item['price_eur_kwh'] for item in prepared_data]
    n = len(prepared_data)
    current_idx = 0
    interval_count = 0

    while current_idx < n - 1:
        # Step A: Find local valley
        valley_idx = current_idx
        valley_min = prices[current_idx]
        
        for j in range(current_idx, n):
            valley_idx = j
            if prices[j] < valley_min:
                valley_min = prices[j]
            if prices[j] >= (valley_min + min_profit_eur_kwh):
                break
        
        # Step B: Find local peak
        peak_idx = valley_idx
        peak_max = prices[valley_idx]
        
        for j in range(valley_idx, n):
            peak_idx = j
            if prices[j] > peak_max:
                peak_max = prices[j]
            if prices[j] <= (peak_max - min_profit_eur_kwh):
                break
        
        # Step C: Find the next valley index where the next wave starts
        temp_min_idx = peak_idx
        temp_min = prices[peak_idx]
        wave_height = peak_max - valley_min
        for j in range(peak_idx, n):
            if prices[j] < temp_min:
                temp_min = prices[j]
                temp_min_idx = j
            # Break if price recovers by 1/3 of min_profit, but only after dropping by at least 40% of wave height
            # to avoid breaking prematurely during high evening peak variations
            if temp_min <= (peak_max - 0.40 * wave_height) and prices[j] >= temp_min + (min_profit_eur_kwh * 0.33):
                # Lookahead to verify if the recovery is sustained (at least 2 periods) to filter out transient spikes
                if j + 1 < n and prices[j+1] < temp_min + (min_profit_eur_kwh * 0.33):
                    continue
                break
        
        # Find the local minimum during the transition night
        night_min_idx = peak_idx
        night_min_val = prices[peak_idx]
        for k in range(peak_idx, temp_min_idx):
            if prices[k] < night_min_val:
                night_min_val = prices[k]
                night_min_idx = k
        
        # Find the local maximum (shoulder) before the next descent
        boundary_idx = night_min_idx
        boundary_val = prices[night_min_idx]
        for k in range(night_min_idx, temp_min_idx + 1):
            if prices[k] > boundary_val:
                boundary_val = prices[k]
                boundary_idx = k
        
        segment_end = boundary_idx
        # Guard: If segment_end points to the last element of the dataset, extend it to n (exclusive)
        # so that the last element is included in the current segment and not orphaned.
        if segment_end >= n - 1:
            segment_end = n
        # Guard: Prevent infinite loops by ensuring current_idx always advances by at least 1.
        if segment_end == current_idx:
            segment_end = current_idx + 1

        # Define current wave segment
        segment = prepared_data[current_idx : segment_end]
        if not segment:
            current_idx = segment_end
            continue

        # Process if profit threshold is met, taking round-trip efficiency into account
        is_profitable = False
        if (peak_max * rte_factor - valley_min) >= min_profit_eur_kwh:                
            # CHARGE: Select cheapest hours before valley
            charge_cands = [h for h in segment if h['sort_index'] < valley_idx and peak_max * rte_factor - h['price_eur_kwh'] >= min_profit_eur_kwh ]
            charge_cands.sort(key=lambda x: x['price_eur_kwh'])
            if charge_cands:
                charge_slots = charge_cands[:charge_slots_count]
                                
                # DISCHARGE: Select most expensive hours after valley
                discharge_cands = [h for h in segment if h['sort_index'] >= valley_idx and h['price_eur_kwh'] * rte_factor - valley_min >= min_profit_eur_kwh]
                discharge_cands.sort(key=lambda x: x['price_eur_kwh'], reverse=True)
                if discharge_cands:
                    discharge_slots = discharge_cands[:discharge_slots_count]

                    # Balance charge and discharge slots
                    num_slots = min(len(charge_slots), len(discharge_slots))
                    charge_slots = charge_slots[:num_slots]
                    discharge_slots = discharge_slots[:num_slots]

                    if len(charge_slots) > 0 and len(discharge_slots) > 0:
                        for s in segment:
                            s['interval_id'] = interval_count

                        for s in charge_slots:
                            s['action'] = ACTION_CHARGE
                                        
                        for s in discharge_slots:
                            s['action'] = ACTION_DISCHARGE
                        
                        interval_count += 1
        
        current_idx = segment_end

    # Recalculate price_multiplier using continuous linear interpolation of divisors between wave valleys
    n = len(prepared_data)
    if n > 0:
        # 1. Find the valley index and price for each active wave (interval_id >= 0)
        valleys = {}
        for idx, item in enumerate(prepared_data):
            iid = item.get('interval_id', -1)
            if item.get('action') != ACTION_STOP:
                if iid >= 0:
                    price = item['price_eur_kwh']
                    if iid not in valleys or price < valleys[iid]['price']:
                        valleys[iid] = {'idx': idx, 'price': price}
        
        # Sort waves to ensure correct chronological sequence
        active_wave_ids = sorted(valleys.keys())
        
        if not active_wave_ids:
            # Fallback to absolute minimum of the entire forecast if no active waves are found
            global_min = min(item['price_eur_kwh'] for item in prepared_data)
            for item in prepared_data:
                p = item['price_eur_kwh']
                item['price_multiplier'] = round(p / global_min, 2) if global_min > 0 else round(1.0 + p / abs(global_min), 2) if global_min != 0 else 1.0
                item['interval_id'] = -1
        else:
            # 2. Assign interval_id to all items based on closest wave's midpoint partition
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
                    assigned_id = active_wave_ids[-1]
                    for k in range(len(midpoints)):
                        if idx <= midpoints[k]:
                            assigned_id = active_wave_ids[k]
                            break
                    item['interval_id'] = assigned_id

            # 3. Calculate divisor for each interval in the timeline
            anchors = [(valleys[iid]['idx'], valleys[iid]['price']) for iid in active_wave_ids]
            
            for idx, item in enumerate(prepared_data):
                p = item['price_eur_kwh']
                
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

        # Assign price_multiplier_quartile (1, 2, 3, 4) to each interval
        multipliers = [item.get('price_multiplier', 1.0) for item in prepared_data]
        if len(multipliers) >= 2:
            import statistics
            try:
                q = statistics.quantiles(multipliers, n=4)
                for item in prepared_data:
                    m = item.get('price_multiplier', 1.0)
                    if m <= q[0]:
                        item['price_multiplier_quartile'] = 1
                    elif m <= q[1]:
                        item['price_multiplier_quartile'] = 2
                    elif m <= q[2]:
                        item['price_multiplier_quartile'] = 3
                    else:
                        item['price_multiplier_quartile'] = 4
            except Exception:
                pass

    # Cleanup internal keys
    for item in prepared_data:
        item.pop('sort_index', None)
    return prepared_data, interval_count

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

    if len(sys.argv) > 1:
        filepath = sys.argv[1]
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
        price_delta_percent=price_delta_percent
    )
    
    active_intervals = len(set(item['interval_id'] for item in schedule if item.get('interval_id', -1) >= 0 and item.get('action') != ACTION_STOP))

    # Calculate quartiles fixed per interval_id
    from collections import defaultdict
    iid_multipliers = defaultdict(list)
    for item in schedule:
        iid = item.get('interval_id', -1)
        iid_multipliers[iid].append(item.get('price_multiplier', 1.0))
        
    iid_quartiles = {}
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
        
        iid_quartiles[iid] = f"[{min_val:.2f}, {q25:.2f}, {q50:.2f}, {q75:.2f}, {max_val:.2f}]"

    print("\n" + "=" * 120)
    print(" BESS ARBITRAGE SCHEDULER - ALGORITHM TEST RESULTS")
    print("=" * 120)
    print(f"Total Intervals Segmented: {active_intervals}")
    print(f"Configuration: charge_quarters={charge_quarters}, discharge_quarters={discharge_quarters}, min_profit={min_profit}, price_delta_percent={price_delta_percent}")
    print("-" * 120)
    print(f"{'Datetime':<30} | {'Price (€/kWh)':<14} | {'Multiplier':<11} | {'Quartiles (Min-Max)':<30} | {'Action':<10} | {'Interval ID':<11}")
    print("-" * 120)

    for item in schedule:
        action = item["action"]
        if action == ACTION_CHARGE:
            action_str = f"\033[92m{action:<10}\033[0m"  # Green
        elif action == ACTION_DISCHARGE:
            action_str = f"\033[91m{action:<10}\033[0m"  # Red
        else:
            action_str = f"{action:<10}"

        iid = item.get('interval_id', -1)
        q_str = iid_quartiles.get(iid, "[1.00, 1.00, 1.00, 1.00, 1.00]")

        print(
            f"{item['datetime']:<30} | {item['price_eur_kwh']:<14.7f} | {item['price_multiplier']:<11.2f} | {q_str:<30} | {action_str} | {item['interval_id']:<11}"
        )
    print("=" * 120 + "\n")

if __name__ == "__main__":
    main()
