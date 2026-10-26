"""Le point d'entrée unique vers le moteur embarqué.

Les modules du moteur s'importent à plat entre eux, donc `src/engine` doit être
sur le chemin. Le faire ici, une fois, plutôt que dans chaque module qui en a
besoin, évite le piège qui a coûté un test : importer `engine.calibration`
d'un côté et `calibration` de l'autre donne **deux classes distinctes** pour
le même fichier, et `Outcome.WIN is Outcome.WIN` devient faux.
"""

from __future__ import annotations

import sys
from pathlib import Path

CHEMIN_MOTEUR = Path(__file__).resolve().parent.parent / "engine"
if str(CHEMIN_MOTEUR) not in sys.path:
    sys.path.insert(0, str(CHEMIN_MOTEUR))

from auto_trader import STAGES, AutoTrader, Portfolio  # noqa: E402
from broker import PaperBroker  # noqa: E402
from calibration import CalibrationConfig, Outcome, TradeRecord, gate  # noqa: E402
from llm_client import build_llm_client  # noqa: E402
from market_data import MarketDataError, build_source  # noqa: E402
from strategy_engine import StrategyConfig, analyse  # noqa: E402

import indicators  # noqa: E402

__all__ = [
    "AutoTrader", "CalibrationConfig", "MarketDataError", "Outcome", "PaperBroker",
    "Portfolio", "STAGES", "StrategyConfig", "TradeRecord", "analyse", "build_llm_client",
    "build_source", "gate", "indicators",
]
