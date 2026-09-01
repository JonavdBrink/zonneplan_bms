#!/usr/bin/env python3
import json
import sys
import yaml
from datetime import datetime

# Action constants
ACTION_CHARGE = "Charge"
ACTION_DISCHARGE = "Discharge"
ACTION_STOP = "Stop"

def _parse_datetime(val):
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        if val.endswith("+00:00"):
            val = val[:-6]
        return datetime.fromisoformat(val)
    return None

def calculate_hybrid_schedule(
    forecast_data,
    charge_quarters=13,
    discharge_quarters=11,
    min_profit_eur_kwh=0.06,
    price_delta_percent=20.0
):
    """
    Hybrid SWA-Wave-Slot Algorithm (HSWAS) for BESS Arbitrage.
    
    1. ALGORITHM A (Wave Finder): Uses Chronological Sliding Window averages to safely 
       and robustly identify macro-wave boundaries, completely immune to transient spikes.
    2. ALGORITHM B (Slot Selector): Discards the rigid contiguous window constraint 
       within the identified wave boundaries, selecting the absolute cheapest quarters 
       to charge and most expensive quarters to discharge (allowing gaps/non-contiguous slots).
    """
    if not forecast_data:
        return [], 0

    first_dt = None
    if forecast_data:
        first_raw = forecast_data[0].get('datetime') or forecast_data[0].get('start_date')
        if first_raw:
            first_dt = _parse_datetime(first_raw)
            
    now = first_dt if first_dt else datetime.fromisoformat("2026-07-28T18:00:00")
    prepared_data = []
    
    for idx, item in enumerate(forecast_data):
        raw_dt = item.get('start_date') or item.get('datetime')
        raw_price = item.get('electricity_price')
        if raw_price is None and 'price_tax_included' in item:
            raw_price = item['price_tax_included'].get('amount')
        
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

    n = len(prepared_data)
    rte_factor = 1.0 - (price_delta_percent / 100.0)
    
    current_idx = 0
    interval_count = 1
    
    # Forward sequential scan to find waves (Algorithm A)
    while current_idx < n - (charge_quarters + discharge_quarters) + 1:
        best_profit = -float('inf')
        best_charge_idx = -1
        best_discharge_idx = -1
        
        # Lookahead horizon to find the optimal next wave boundary
        search_limit = min(n, current_idx + 96)  # limit search window to next 24 hours
        
        for i in range(current_idx, search_limit - charge_quarters + 1):
            charge_slice = range(i, i + charge_quarters)
            avg_charge = sum(prepared_data[k]['price_eur_kwh'] for k in charge_slice) / charge_quarters
            
            for j in range(i + charge_quarters, search_limit - discharge_quarters + 1):
                discharge_slice = range(j, j + discharge_quarters)
                avg_discharge = sum(prepared_data[k]['price_eur_kwh'] for k in discharge_slice) / discharge_quarters
                
                # Check sliding window profit under RTE
                profit = avg_discharge * rte_factor - avg_charge
                
                if profit > best_profit:
                    best_profit = profit
                    best_charge_idx = i
                    best_discharge_idx = j
                    
        # If a profitable wave segment is found, boundary-lock it and run Slot Selector (Algorithm B)
        if best_profit >= min_profit_eur_kwh and best_charge_idx != -1 and best_discharge_idx != -1:
            segment_start = best_charge_idx
            segment_end = best_discharge_idx + discharge_quarters
            
            segment = prepared_data[segment_start : segment_end]
            segment_prices = [h['price_eur_kwh'] for h in segment]
            
            # Find local valley (minimum price in this wave segment)
            local_valley_val = min(segment_prices)
            
            # Find local peak (maximum price in this wave segment)
            local_peak_val = max(segment_prices)
            
            # Define pools based on the optimal SWA windows:
            # - Charge pool: from segment_start up to best_discharge_idx (exclusive)
            # - Discharge pool: from best_discharge_idx to segment_end
            charge_pool = prepared_data[segment_start : best_discharge_idx]
            discharge_pool = prepared_data[best_discharge_idx : segment_end]
            
            # Select charge/discharge candidates within the wave boundaries (Algorithm B)
            charge_cands = [h for h in charge_pool if local_peak_val * rte_factor - h['price_eur_kwh'] >= min_profit_eur_kwh]
            charge_cands.sort(key=lambda x: x['price_eur_kwh'])
            charge_slots = charge_cands[:charge_quarters]
            
            discharge_cands = [h for h in discharge_pool if h['price_eur_kwh'] * rte_factor - local_valley_val >= min_profit_eur_kwh]
            discharge_cands.sort(key=lambda x: x['price_eur_kwh'], reverse=True)
            discharge_slots = discharge_cands[:discharge_quarters]
            
            if charge_slots or discharge_slots:
                # Assign actions and interval IDs
                for s in segment:
                    s['interval_id'] = interval_count
                    
                for s in charge_slots:
                    s['action'] = ACTION_CHARGE
                    
                for s in discharge_slots:
                    s['action'] = ACTION_DISCHARGE
                    
                interval_count += 1
                
            # Advance our sequential timeline past the end of this wave
            current_idx = segment_end
        else:
            # Advance timeline by 1 step if no waves are found
            current_idx += 1

    # Recalculate price_multiplier using continuous linear interpolation of divisors between wave valleys
    n = len(prepared_data)
    if n > 0:
        # 1. Find the valley index and price for each active wave (interval_id >= 0)
        # Note: HSWAS dry-run wave count starts at 1, so active waves have interval_id >= 1
        valleys = {}
        for idx, item in enumerate(prepared_data):
            iid = item.get('interval_id', -1)
            if iid >= 1:
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
        else:
            # Build anchor points list: [(idx, price), ...]
            anchors = [(valleys[iid]['idx'], valleys[iid]['price']) for iid in active_wave_ids]
            
            # Calculate divisor for each interval in the timeline
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

    # Clean up temporary internal keys
    for item in prepared_data:
        item.pop('sort_index', None)
        
    return prepared_data, interval_count - 1


# Default forecast data fallback
forecast_data = [
    {"datetime": "2026-07-28T18:00:00+00:00", "price_eur_kwh": 0.3703554},
    {"datetime": "2026-07-28T18:15:00+00:00", "price_eur_kwh": 0.3803137},
    {"datetime": "2026-07-28T18:30:00+00:00", "price_eur_kwh": 0.395596},
    {"datetime": "2026-07-28T18:45:00+00:00", "price_eur_kwh": 0.4176664},
    {"datetime": "2026-07-28T19:00:00+00:00", "price_eur_kwh": 0.3970359},
    {"datetime": "2026-07-28T19:15:00+00:00", "price_eur_kwh": 0.3896791},
    {"datetime": "2026-07-28T19:30:00+00:00", "price_eur_kwh": 0.3797571},
    {"datetime": "2026-07-28T19:45:00+00:00", "price_eur_kwh": 0.372969},
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
            
            parsed = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = yaml.safe_load(content)
            
            if parsed is None:
                raise ValueError("Failed to parse file as JSON or YAML.")
            
            if isinstance(parsed, dict):
                if 'attributes' in parsed and isinstance(parsed['attributes'], dict):
                    attrs = parsed['attributes']
                    if 'schedule' in attrs:
                        parsed = attrs['schedule']
                    elif 'forecast' in attrs:
                        parsed = attrs['forecast']
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
                    parsed = [parsed]
            
            if not isinstance(parsed, list):
                raise ValueError("Expected a list of items.")
            
            data = parsed
            print(f"Successfully loaded {len(data)} items from {filepath}")
        except Exception as e:
            print(f"Error loading {filepath}: {e}")
            print("Falling back to built-in forecast_data.")
            data = forecast_data

    schedule, intervals = calculate_hybrid_schedule(
        data, 
        charge_quarters=charge_quarters, 
        discharge_quarters=discharge_quarters, 
        min_profit_eur_kwh=min_profit,
        price_delta_percent=price_delta_percent
    )
    
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
    print(" BESS HYBRID SWA-WAVE-SLOT (HSWAS) - TEST RESULTS")
    print("=" * 120)
    print(f"Total Intervals Segmented: {intervals}")
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
