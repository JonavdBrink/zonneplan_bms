from datetime import datetime, timedelta
from typing import Any
import homeassistant.util.dt as dt_util

from ..const import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_STOP,
)
from .base import ArbitrageStrategy

def _parse_datetime(val: Any) -> datetime | None:
    """Safely parse a datetime object or string."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return dt_util.parse_datetime(val)
    return None

class WhssStrategy(ArbitrageStrategy):
    """
    Standard (WHSS) - Wave Heuristic Slot Scheduler strategy.
    
    Segments the pricing timeline chronologically into waves,
    then performs slot-picking within each wave.
    """

    def calculate_schedule(
        self,
        prepared_data: list[dict[str, Any]],
        charge_slots_count: int,
        discharge_slots_count: int,
        rte_factor: float,
        min_profit_eur_kwh: float,
        now: datetime
    ) -> list[dict[str, Any]]:
        """Calculates the BESS schedule using the WHSS Wave Heuristic."""
        prices = [item['price_eur_kwh'] for item in prepared_data]
        n = len(prepared_data)
        current_idx = 0
        interval_count = 0
        
        while current_idx < n - 1:
            # Step A: Find the NEXT local valley (dip) relative to current position
            valley_idx = current_idx
            valley_min = prices[current_idx]
            
            for j in range(current_idx, n):
                valley_idx = j
                if prices[j] < valley_min:
                    valley_min = prices[j]
                # Break if price recovers significantly
                if prices[j] >= (valley_min + min_profit_eur_kwh):
                    break
            
            # Step B: Find the NEXT local peak (hump) AFTER that specific valley
            peak_idx = valley_idx
            peak_max = prices[valley_idx]
            
            for j in range(valley_idx, n):
                peak_idx = j
                if prices[j] > peak_max:
                    peak_max = prices[j]
                # Break if price drops significantly (indicating start of next wave)
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
            
            # Find the local minimum during the transition
            local_min_idx = peak_idx
            local_min_val = prices[peak_idx]
            for k in range(peak_idx, temp_min_idx):
                if prices[k] < local_min_val:
                    local_min_val = prices[k]
                    local_min_idx = k
            
            # Find the local maximum (shoulder) before the next descent
            boundary_idx = local_min_idx
            boundary_val = prices[local_min_idx]
            for k in range(local_min_idx, temp_min_idx + 1):
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

            # Define the current wave segment
            segment = prepared_data[current_idx : segment_end]
            if not segment:
                current_idx = segment_end
                continue

            # Process if profit threshold is met, taking round-trip efficiency into account
            if (peak_max * rte_factor - valley_min) >= min_profit_eur_kwh:                
                # CHARGE: Select cheapest hours in this wave before the valley
                charge_cands = [h for h in segment if h['sort_index'] < valley_idx and peak_max * rte_factor - h['price_eur_kwh'] >= min_profit_eur_kwh]
                charge_cands.sort(key=lambda x: x['price_eur_kwh'])
                if not charge_cands:
                    current_idx = segment_end
                    continue
                charge_slots = charge_cands[:charge_slots_count]
                                
                # DISCHARGE: Select most expensive hours in this wave after the valley
                discharge_cands = [h for h in segment if h['sort_index'] >= valley_idx and h['price_eur_kwh'] * rte_factor - valley_min >= min_profit_eur_kwh]
                discharge_cands.sort(key=lambda x: x['price_eur_kwh'], reverse=True)
                if not discharge_cands:
                    current_idx = segment_end
                    continue
                discharge_slots = discharge_cands[:discharge_slots_count]

                # Balance charge and discharge slots
                num_slots = min(len(charge_slots), len(discharge_slots))
                charge_slots = charge_slots[:num_slots]
                discharge_slots = discharge_slots[:num_slots]

                for s in segment:
                    s['interval_id'] = interval_count

                for s in charge_slots:
                    s['action'] = ACTION_CHARGE
                                
                for s in discharge_slots:
                    s['action'] = ACTION_DISCHARGE
                
                interval_count += 1
            
            # Move index forward to the end of this wave
            current_idx = segment_end

        return prepared_data
