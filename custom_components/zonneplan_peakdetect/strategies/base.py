from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

class ArbitrageStrategy(ABC):
    """Abstract base class for all BESS arbitrage scheduling strategies."""

    @abstractmethod
    def calculate_schedule(
        self,
        prepared_data: list[dict[str, Any]],
        charge_slots_count: int,
        discharge_slots_count: int,
        rte_factor: float,
        min_profit_eur_kwh: float,
        now: datetime
    ) -> list[dict[str, Any]]:
        """
        Calculates the action schedule and returns the updated prepared_data list
        with assigned 'action' and 'interval_id' keys.
        """
        pass
