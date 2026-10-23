"""La session : ce que l'application sait faire, sans savoir comment l'afficher.

Deux modes, décidés par les réglages :

- **local** — le moteur embarqué tourne ici même, contre l'API publique de
  Binance (ou une source de démonstration). Aucun serveur.
- **relié** — tout passe par `/api/auto/*` d'un serveur.

Dans les deux cas, les décisions sont écrites dans la base locale, parce qu'une
décision qu'on ne peut pas relire plus tard n'a pas servi à grand-chose.

Et dans les deux cas, **rien ici ne lance de boucle automatique** : sur
téléphone c'est volontairement absent (Android suspend les processus), et sur
PC la boucle appartient à l'écran qui la pilote, pas à ce module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from coeur.config import MODE_LOCAL, MODE_RELIE, Reglages
from coeur.distant import Distant, ErreurDistante
from coeur.moteur import (
    AutoTrader,
    CalibrationConfig,
    PaperBroker,
    Portfolio,
    analyse,
    build_llm_client,
    build_source,
    indicators,
)
from coeur.stockage import Base


@dataclass
class Session:
    """L'état de l'application pour toute la durée de son exécution."""

    dossier: Path
    reglages: Reglages = field(default_factory=Reglages)
    base: Optional[Base] = None
    _trader: object = None
    _source_nom: str = "inconnue"

    @classmethod
    def ouvrir(cls, dossier) -> "Session":
        dossier = Path(dossier)
        base = Base(dossier / "aitrader.db")
        reglages = Reglages.from_dict(base.lire_reglages())
        return cls(dossier=dossier, reglages=reglages, base=base)

    def enregistrer_reglages(self, reglages: Reglages) -> None:
        self.reglages = reglages.valide()
        if self.base:
            self.base.ecrire_reglages(self.reglages.to_dict())
        # Le moteur est reconstruit à la prochaine demande : le capital, le
        # seuil et le testnet en dépendent.
        self._trader = None

    # -- mode local --------------------------------------------------------

    def trader_local(self):
        """Construit le moteur embarqué, et le réalimente avec l'historique local.

        Le `history=` est la boucle d'apprentissage de l'appareil : ce qui a
        été fermé ici revient nourrir le portail calibré, sans serveur.
        """
        if self._trader is not None:
            return self._trader

        # Pas de repli silencieux vers le flux de démonstration : si Binance
        # est injoignable, la lecture échoue et l'écran le dit. Afficher des
        # prix inventés parce que le marché ne répond pas serait pire que de
        # n'afficher rien.
        source = build_source(provider=self.reglages.source, testnet=self.reglages.testnet)
        self._source_nom = getattr(source, "name", self.reglages.source)

        historique = self.base.historique_calibration() if self.base else []
        self._trader = AutoTrader(
            source=source,
            broker=PaperBroker(quote_balance=self.reglages.capital_papier),
            portfolio=Portfolio(equity=self.reglages.capital_papier),
            calibration_config=CalibrationConfig(
                confluence_gate=self.reglages.seuil_confluence
            ),
            llm_client=build_llm_client(),
            history=list(historique),
        )
        return self._trader

    def _distant(self) -> Distant:
        return Distant(base_url=self.reglages.url_serveur)

    # -- ce que l'interface appelle ---------------------------------------

    def analyser(self, symbole: str, intervalle: Optional[str] = None) -> dict:
        """Analyse complète. Ne passe aucun ordre, quel que soit le verdict."""
        intervalle = intervalle or self.reglages.intervalle
        if self.reglages.mode == MODE_RELIE:
            decision = self._distant().analyser(symbole, intervalle)
        else:
            decision = self.trader_local().evaluate(symbole, intervalle).to_dict()
        if self.base:
            self.base.enregistrer_decision(decision, source=self._source_nom)
        return decision

    def graphique(self, symbole: str, intervalle: Optional[str] = None, limite: int = 300) -> dict:
        """Bougies et séries d'indicateurs, pour le dessin."""
        intervalle = intervalle or self.reglages.intervalle
        if self.reglages.mode == MODE_RELIE:
            return self._distant().graphique(symbole, intervalle, limite)

        trader = self.trader_local()
        bougies = trader.source.fetch(symbole, intervalle, limite)
        config = trader.strategy_config
        cours = indicators.closes(bougies)
        macd = indicators.macd(cours)
        bandes = indicators.bollinger(cours, config.bb_period)
        return {
            "symbol": symbole.upper(),
            "interval": intervalle,
            "source": getattr(trader.source, "name", "inconnue"),
            "candles": [
                {"t": c.open_time, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
                for c in bougies
            ],
            "indicators": {
                "rsi": indicators.rsi(cours, config.rsi_period),
                "ema_fast": indicators.ema(cours, config.ema_fast),
                "ema_slow": indicators.ema(cours, config.ema_slow),
                "ema_trend": indicators.ema(cours, config.ema_trend),
                "macd": macd.macd,
                "macd_signal": macd.signal,
                "macd_histogram": macd.histogram,
                "bb_upper": bandes.upper,
                "bb_lower": bandes.lower,
            },
            "proposal": analyse(symbole, intervalle, bougies, config).to_dict(),
        }

    def etat(self) -> dict:
        """Mode, portefeuille, positions. Jamais une exception vers l'écran."""
        if self.reglages.mode == MODE_RELIE:
            try:
                distant = self._distant().etat()
                distant["joignable"] = True
                return distant
            except ErreurDistante as exc:
                return {
                    "joignable": False,
                    "erreur": str(exc),
                    "mode": {"auto_trade": False, "data_source": "—", "broker": "—"},
                    "portfolio": {},
                }

        trader = self.trader_local()
        return {
            "joignable": True,
            "mode": {
                "data_source": self._source_nom,
                "broker": "paper",
                "paper": True,
                "testnet": self.reglages.testnet,
                "live": False,
                # Jamais vrai en local : l'appareil ne tient pas une boucle.
                "auto_trade": False,
                "model": getattr(trader.llm_client, "name", "none"),
                "model_available": bool(getattr(trader.llm_client, "available", False)),
            },
            "watchlist": list(self.reglages.watchlist),
            "interval": self.reglages.intervalle,
            "portfolio": trader.portfolio.to_dict(),
            "history_size": len(trader.history),
        }

    def coupe_circuit(self, engage: bool) -> dict:
        if self.reglages.mode == MODE_RELIE:
            resultat = self._distant().coupe_circuit(engage)
        else:
            trader = self.trader_local()
            trader.portfolio.kill_switch = bool(engage)
            resultat = trader.portfolio.to_dict()
        if self.base:
            self.base.enregistrer_halte("kill_switch", engage, "depuis l'application")
        return resultat

    def fermer(self) -> None:
        if self.base:
            self.base.fermer()
