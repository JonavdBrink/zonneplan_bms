from .base import ArbitrageStrategy
from .wave_heuristic import WhssStrategy
from .sliding_window import HswasStrategy
from ..const import (
    ALGORITHM_WHSS,
    ALGORITHM_HSWAS,
)

# Registry mapping configuration keys to strategy classes
STRATEGIES: dict[str, type[ArbitrageStrategy]] = {
    ALGORITHM_WHSS: WhssStrategy,
    ALGORITHM_HSWAS: HswasStrategy,
}

def get_arbitrage_strategy(algorithm_type: str) -> ArbitrageStrategy:
    """Polymorphically retrieves and instantiates the chosen BESS arbitrage strategy."""
    strategy_class = STRATEGIES.get(algorithm_type, WhssStrategy)
    return strategy_class()
