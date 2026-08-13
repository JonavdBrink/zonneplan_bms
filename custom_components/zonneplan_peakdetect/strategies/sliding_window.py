from datetime import datetime, timedelta
from typing import Any
import homeassistant.util.dt as dt_util

from ..const import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_STOP,
)
from .base import ArbitrageStrategy

class HswasStrategy(ArbitrageStrategy):
    """
    Advanced (HSWAS) [β] - Hybrid SWA-Wave-Slot strategy.
    
    Uses sliding-window moving averages to robustly find wave boundaries,
    then optimizes slot selection allowing non-contiguous slots within those waves.
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
        """Calculates the BESS schedule using the Advanced (HSWAS) [β] Sliding Window."""
        prices = [item['price_eur_kwh'] for item in prepared_data]
        n = len(prepared_data)
        current_idx = 0
        interval_count = 0
        
        if charge_slots_count > 0 and discharge_slots_count > 0:
            while current_idx < n - (charge_slots_count + discharge_slots_count) + 1:
                best_profit = -float('inf')
                best_charge_idx = -1
                best_discharge_idx = -1
                
                # Limit search window to the next 24 hours (96 quarters) to localize cycles
                search_limit = min(n, current_idx + 96)
                
                for i in range(current_idx, search_limit - charge_slots_count + 1):
                    charge_slice = range(i, i + charge_slots_count)
                    avg_charge = sum(prices[k] for k in charge_slice) / charge_slots_count
                    
                    for j in range(i + charge_slots_count, search_limit - discharge_slots_count + 1):
                        discharge_slice = range(j, j + discharge_slots_count)
                        avg_discharge = sum(prices[k] for k in discharge_slice) / discharge_slots_count
                        
                        profit = avg_discharge * rte_factor - avg_charge
                        
                        if profit > best_profit:
                            best_profit = profit
                            best_charge_idx = i
                            best_discharge_idx = j
                            
                if best_profit >= min_profit_eur_kwh and best_charge_idx != -1 and best_discharge_idx != -1:
                    segment_start = best_charge_idx
                    segment_end = best_discharge_idx + discharge_slots_count
                    
                    segment = prepared_data[segment_start : segment_end]
                    segment_prices = [h['price_eur_kwh'] for h in segment]
                    
                    local_valley_val = min(segment_prices)
                    local_peak_val = max(segment_prices)
                    
                    charge_pool = prepared_data[segment_start : best_discharge_idx]
                    discharge_pool = prepared_data[best_discharge_idx : segment_end]
                    
                    charge_cands = [h for h in charge_pool if local_peak_val * rte_factor - h['price_eur_kwh'] >= min_profit_eur_kwh]
                    charge_cands.sort(key=lambda x: x['price_eur_kwh'])
                    charge_slots = charge_cands[:charge_slots_count]
                    
                    discharge_cands = [h for h in discharge_pool if h['price_eur_kwh'] * rte_factor - local_valley_val >= min_profit_eur_kwh]
                    discharge_cands.sort(key=lambda x: x['price_eur_kwh'], reverse=True)
                    discharge_slots = discharge_cands[:discharge_slots_count]
                    
                    if charge_slots or discharge_slots:
                        for s in segment:
                            s['interval_id'] = interval_count
                            
                        for s in charge_slots:
                            s['action'] = ACTION_CHARGE
                            
                        for s in discharge_slots:
                            s['action'] = ACTION_DISCHARGE
                            
                        interval_count += 1
                        
                    current_idx = segment_end
                else:
                    current_idx += 1

        return prepared_data
