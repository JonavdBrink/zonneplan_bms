from datetime import datetime
from typing import Any

from ..const import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_STOP,
)
from .base import ArbitrageStrategy

class MpesStrategy(ArbitrageStrategy):
    """
    Midpoint-Partition Extrema Strategy (MPES).
    Uses a Schmitt-trigger swing filter to globally identify major valleys and peaks,
    then uses temporal midpoint partitioning to cleanly allocate optimal charge/discharge slots.
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
        n = len(prepared_data)
        if n < 2:
            return prepared_data

        prices = [item['price_eur_kwh'] for item in prepared_data]

        # 1. Schmitt-Trigger Swing Filter to find major valleys and peaks
        swings = self._find_major_swings(prices, min_profit_eur_kwh)

        # 2. Pair alternating valleys and peaks into distinct cycles
        cycles = []
        for i in range(len(swings) - 1):
            if swings[i]['type'] == 'valley' and swings[i+1]['type'] == 'peak':
                cycles.append({
                    'valley_idx': swings[i]['idx'],
                    'valley_price': swings[i]['price'],
                    'peak_idx': swings[i+1]['idx'],
                    'peak_price': swings[i+1]['price']
                })

        # 3. Schedule slot allocation per cycle
        interval_count = 0
        for cycle_id, c in enumerate(cycles):
            v_idx = c['valley_idx']
            p_idx = c['peak_idx']
            
            # Profitability Guard
            if c['peak_price'] * rte_factor - c['valley_price'] < min_profit_eur_kwh:
                continue

            midpoint = (v_idx + p_idx) // 2

            # Define search range bounds to prevent overlap with adjacent cycles
            start_search = 0 if cycle_id == 0 else (cycles[cycle_id-1]['peak_idx'] + v_idx) // 2
            end_search = n if cycle_id == len(cycles) - 1 else (p_idx + cycles[cycle_id+1]['valley_idx']) // 2

            # Separate into charge pool (before midpoint) and discharge pool (after midpoint)
            charge_segment_indices = list(range(start_search, midpoint))
            discharge_segment_indices = list(range(midpoint, end_search))

            # Select and sort candidates
            # Sort charge candidates ascending (cheapest first)
            charge_cands = sorted(charge_segment_indices, key=lambda x: prices[x])
            # Only keep charge candidates that are profitable relative to peak
            charge_cands = [x for x in charge_cands if c['peak_price'] * rte_factor - prices[x] >= min_profit_eur_kwh]
            charge_slots = charge_cands[:charge_slots_count]

            # Sort discharge candidates descending (most expensive first)
            discharge_cands = sorted(discharge_segment_indices, key=lambda x: prices[x], reverse=True)
            # Only keep discharge candidates that are profitable relative to valley
            discharge_cands = [x for x in discharge_cands if prices[x] * rte_factor - c['valley_price'] >= min_profit_eur_kwh]
            discharge_slots = discharge_cands[:discharge_slots_count]

            # Balance slots to ensure symmetric charge/discharge cycle
            num_slots = min(len(charge_slots), len(discharge_slots))
            charge_slots = charge_slots[:num_slots]
            discharge_slots = discharge_slots[:num_slots]

            if num_slots > 0:
                for x in charge_slots:
                    prepared_data[x]['action'] = ACTION_CHARGE
                    prepared_data[x]['interval_id'] = interval_count
                for x in discharge_slots:
                    prepared_data[x]['action'] = ACTION_DISCHARGE
                    prepared_data[x]['interval_id'] = interval_count
                
                interval_count += 1

        return prepared_data

    def _find_major_swings(self, prices: list[float], min_profit: float) -> list[dict[str, Any]]:
        swings = []
        n = len(prices)
        if n == 0:
            return swings

        # State: 1 = looking for peak, -1 = looking for valley, 0 = initializing
        state = 0
        curr_min_idx = 0
        curr_min_val = prices[0]
        curr_max_idx = 0
        curr_max_val = prices[0]

        # Use 1.2 * min_profit as hysteresis to filter out minor midday/noise fluctuations
        hysteresis = max(0.01, 1.2 * min_profit)

        for i in range(1, n):
            p = prices[i]
            if state == 0:
                if p < curr_min_val:
                    curr_min_val = p
                    curr_min_idx = i
                elif p > curr_max_val:
                    curr_max_val = p
                    curr_max_idx = i
                
                if p >= curr_min_val + hysteresis:
                    swings.append({'type': 'valley', 'idx': curr_min_idx, 'price': curr_min_val})
                    state = 1
                    curr_max_val = p
                    curr_max_idx = i
                elif p <= curr_max_val - hysteresis:
                    swings.append({'type': 'peak', 'idx': curr_max_idx, 'price': curr_max_val})
                    state = -1
                    curr_min_val = p
                    curr_min_idx = i
            elif state == 1: # Looking for peak
                if p > curr_max_val:
                    curr_max_val = p
                    curr_max_idx = i
                
                if p <= curr_max_val - hysteresis:
                    swings.append({'type': 'peak', 'idx': curr_max_idx, 'price': curr_max_val})
                    state = -1
                    curr_min_val = p
                    curr_min_idx = i
            elif state == -1: # Looking for valley
                if p < curr_min_val:
                    curr_min_val = p
                    curr_min_idx = i
                
                if p >= curr_min_val + hysteresis:
                    swings.append({'type': 'valley', 'idx': curr_min_idx, 'price': curr_min_val})
                    state = 1
                    curr_max_val = p
                    curr_max_idx = i

        # Add the final extrema
        if state == 1:
            swings.append({'type': 'peak', 'idx': curr_max_idx, 'price': curr_max_val})
        elif state == -1:
            swings.append({'type': 'valley', 'idx': curr_min_idx, 'price': curr_min_val})

        return swings
