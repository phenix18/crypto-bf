"""La base locale : le schéma REA de docs/APP_PC_APK_FR.md, en SQLite.

Rien d'autre que `sqlite3` et la bibliothèque standard. La base vit dans le
dossier privé de l'application, ce qui évite toute permission de stockage sur
Android.

Ce que ce module doit faire correctement, et qui n'est pas évident :

**La dualité.** Un trade n'est pas une ligne, c'est un couple d'événements —
l'entrée qui sort des liquidités et fait entrer une position, la clôture qui
fait l'inverse — relié au risque qui avait été consenti. `trade_duality` est
la table qui matérialise ce couple, et c'est la seule que la calibration lit.
Sans elle il n'y a pas de R, donc pas d'espérance, donc pas de portail calibré.

**L'idempotence.** `client_order_id` est unique. Un ordre renvoyé après un
délai d'attente ne peut pas créer un second engagement, ici comme chez
l'exchange.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS agent (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('trader','engine','ai_agent','exchange','model_provider')),
  name TEXT NOT NULL UNIQUE,
  internal INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS resource (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('cash','position','market_data','api_access','model_quota')),
  symbol TEXT, unit TEXT NOT NULL,
  UNIQUE (kind, symbol)
);

CREATE TABLE IF NOT EXISTS observation (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL, source TEXT NOT NULL,
  symbol TEXT NOT NULL, interval TEXT NOT NULL,
  bar_open_time INTEGER NOT NULL, candle_count INTEGER NOT NULL,
  UNIQUE (symbol, interval, bar_open_time, source)
);

CREATE TABLE IF NOT EXISTS proposal (
  id INTEGER PRIMARY KEY,
  observation_id INTEGER NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
  direction TEXT NOT NULL, regime TEXT NOT NULL,
  confluence REAL NOT NULL, participation REAL NOT NULL,
  entry REAL NOT NULL, stop_loss REAL, take_profit REAL, risk_reward REAL,
  actionable INTEGER NOT NULL, blockers TEXT,
  votes_json TEXT NOT NULL, readings_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposal(id) ON DELETE CASCADE,
  decided_at TEXT NOT NULL, stage TEXT NOT NULL, approved INTEGER NOT NULL,
  quantity REAL NOT NULL DEFAULT 0, risk_amount REAL NOT NULL DEFAULT 0,
  notional REAL NOT NULL DEFAULT 0,
  conviction REAL, probability REAL, basis TEXT, reasons TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_opinion (
  decision_id INTEGER NOT NULL REFERENCES decision(id) ON DELETE CASCADE,
  agent_id INTEGER NOT NULL REFERENCES agent(id),
  stance TEXT NOT NULL CHECK (stance IN ('confirm','abstain','veto')),
  confidence REAL NOT NULL, rationale TEXT, source TEXT,
  PRIMARY KEY (decision_id, agent_id)
);

CREATE TABLE IF NOT EXISTS order_commitment (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decision(id) ON DELETE CASCADE,
  client_order_id TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('entry','protective','unwind','manual_exit')),
  side TEXT NOT NULL, symbol TEXT NOT NULL, venue TEXT NOT NULL,
  quantity REAL NOT NULL, price REAL, stop_price REAL, take_profit REAL,
  sent_at TEXT NOT NULL, status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS economic_event (
  id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES order_commitment(id) ON DELETE SET NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('entry_fill','exit_fill','fee','deposit','withdrawal','adjustment')),
  resource_id INTEGER NOT NULL REFERENCES resource(id),
  direction TEXT NOT NULL CHECK (direction IN ('inflow','outflow')),
  quantity REAL NOT NULL, unit_price REAL, amount REAL NOT NULL,
  provider_agent_id INTEGER REFERENCES agent(id),
  receiver_agent_id INTEGER REFERENCES agent(id)
);

CREATE TABLE IF NOT EXISTS trade_duality (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decision(id) ON DELETE CASCADE,
  entry_event_id INTEGER NOT NULL REFERENCES economic_event(id),
  exit_event_id INTEGER REFERENCES economic_event(id),
  symbol TEXT NOT NULL, interval TEXT NOT NULL,
  direction TEXT NOT NULL, regime TEXT NOT NULL, confluence REAL NOT NULL,
  risk_committed REAL NOT NULL,
  r_multiple REAL, pnl REAL,
  outcome TEXT CHECK (outcome IN ('win','loss','scratch')),
  reason TEXT, closed_at TEXT
);

CREATE TABLE IF NOT EXISTS account_snapshot (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL,
  equity REAL NOT NULL, peak_equity REAL NOT NULL,
  realised_pnl_today REAL NOT NULL, open_positions INTEGER NOT NULL,
  consecutive_losses INTEGER NOT NULL, kill_switch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS halt_event (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL,
  halt TEXT NOT NULL, engaged INTEGER NOT NULL,
  by_agent_id INTEGER REFERENCES agent(id), reason TEXT
);

CREATE TABLE IF NOT EXISTS reglages (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);

-- Exactement la clé de compartiment que calibration.py interroge.
CREATE INDEX IF NOT EXISTS idx_trade_bucket ON trade_duality (regime, direction, confluence);
CREATE INDEX IF NOT EXISTS idx_event_at ON economic_event (at);
CREATE INDEX IF NOT EXISTS idx_decision_at ON decision (decided_at);
"""

AGENTS_DE_BASE = (
    ("trader", "Trader", 1),
    ("engine", "Moteur", 1),
    ("ai_agent", "analyst", 1),
    ("ai_agent", "risk", 1),
    ("ai_agent", "historian", 1),
    ("ai_agent", "macro", 1),
    ("exchange", "Binance", 0),
    ("model_provider", "OpenRouter", 0),
)


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Base:
    """La base locale. Une instance par application."""

    chemin: Path
    _conn: Optional[sqlite3.Connection] = None

    def __post_init__(self) -> None:
        self.chemin = Path(self.chemin)
        if str(self.chemin) != ":memory:":
            self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.chemin), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._amorcer()

    @property
    def conn(self) -> sqlite3.Connection:
        assert self._conn is not None
        return self._conn

    def fermer(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _amorcer(self) -> None:
        cur = self.conn.execute("SELECT version FROM schema_version")
        if cur.fetchone() is None:
            self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        for kind, name, internal in AGENTS_DE_BASE:
            self.conn.execute(
                "INSERT OR IGNORE INTO agent (kind, name, internal) VALUES (?, ?, ?)",
                (kind, name, internal),
            )
        for kind, symbol, unit in (("cash", "USDT", "USDT"), ("market_data", None, "bougie")):
            self.conn.execute(
                "INSERT OR IGNORE INTO resource (kind, symbol, unit) VALUES (?, ?, ?)",
                (kind, symbol, unit),
            )
        self.conn.commit()

    # -- agents et ressources --------------------------------------------

    def agent_id(self, nom: str) -> Optional[int]:
        ligne = self.conn.execute("SELECT id FROM agent WHERE name = ?", (nom,)).fetchone()
        return ligne["id"] if ligne else None

    def ressource_id(self, kind: str, symbol: Optional[str] = None) -> int:
        ligne = self.conn.execute(
            "SELECT id FROM resource WHERE kind = ? AND symbol IS ?", (kind, symbol)
        ).fetchone()
        if ligne:
            return ligne["id"]
        cur = self.conn.execute(
            "INSERT INTO resource (kind, symbol, unit) VALUES (?, ?, ?)",
            (kind, symbol, symbol or kind),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # -- enregistrement d'une décision -----------------------------------

    def enregistrer_decision(self, decision: dict, source: str = "binance") -> int:
        """Écrit l'observation, la proposition, la décision et les avis.

        Renvoie l'identifiant de la décision. Tout tient dans une transaction :
        une décision à moitié écrite est pire qu'une décision absente, parce
        qu'elle se relit comme une décision complète.
        """
        proposition = decision.get("proposal") or {}
        conseil = decision.get("council") or {}
        risque = decision.get("risk") or {}
        calibration = decision.get("calibration") or {}

        with self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO observation
                   (at, source, symbol, interval, bar_open_time, candle_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    decision.get("at") or maintenant(),
                    source,
                    decision.get("symbol", ""),
                    decision.get("interval", ""),
                    int(proposition.get("bar_open_time") or 0),
                    int(proposition.get("candle_count") or 0),
                ),
            )
            # `lastrowid` n'est PAS remis à zéro quand un INSERT OR IGNORE
            # est ignoré : il garde la valeur de la dernière insertion réussie.
            # S'y fier rattachait la proposition à une observation qui n'était
            # pas la sienne — ici la clé étrangère l'a attrapé, en production
            # elle aurait pu pointer sur une ligne existante et se taire.
            # `rowcount` dit la vérité : 1 si inséré, 0 si ignoré.
            observation_id = cur.lastrowid if cur.rowcount == 1 else None
            if observation_id is None:
                ligne = self.conn.execute(
                    """SELECT id FROM observation
                       WHERE symbol = ? AND interval = ? AND bar_open_time = ? AND source = ?""",
                    (
                        decision.get("symbol", ""),
                        decision.get("interval", ""),
                        int(proposition.get("bar_open_time") or 0),
                        source,
                    ),
                ).fetchone()
                observation_id = ligne["id"]

            cur = self.conn.execute(
                """INSERT INTO proposal
                   (observation_id, direction, regime, confluence, participation,
                    entry, stop_loss, take_profit, risk_reward, actionable,
                    blockers, votes_json, readings_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation_id,
                    proposition.get("direction", "flat"),
                    proposition.get("regime", "unknown"),
                    float(proposition.get("confluence") or 0.0),
                    float(proposition.get("participation") or 0.0),
                    float(proposition.get("entry") or 0.0),
                    proposition.get("stop_loss"),
                    proposition.get("take_profit"),
                    proposition.get("risk_reward"),
                    1 if proposition.get("actionable") else 0,
                    json.dumps(proposition.get("blockers") or [], ensure_ascii=False),
                    json.dumps(proposition.get("votes") or [], ensure_ascii=False),
                    json.dumps(proposition.get("readings") or {}, ensure_ascii=False),
                ),
            )
            proposal_id = int(cur.lastrowid)

            cur = self.conn.execute(
                """INSERT INTO decision
                   (proposal_id, decided_at, stage, approved, quantity, risk_amount,
                    notional, conviction, probability, basis, reasons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal_id,
                    decision.get("at") or maintenant(),
                    decision.get("stage", "strategy"),
                    1 if decision.get("approved") else 0,
                    float(decision.get("quantity") or 0.0),
                    float(risque.get("risk_amount") or 0.0),
                    float(risque.get("notional") or 0.0),
                    conseil.get("conviction"),
                    calibration.get("probability"),
                    calibration.get("basis"),
                    json.dumps(decision.get("reasons") or [], ensure_ascii=False),
                ),
            )
            decision_id = int(cur.lastrowid)

            for avis in conseil.get("opinions") or []:
                agent_id = self.agent_id(avis.get("role", ""))
                if agent_id is None:
                    continue
                self.conn.execute(
                    """INSERT OR REPLACE INTO decision_opinion
                       (decision_id, agent_id, stance, confidence, rationale, source)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        decision_id,
                        agent_id,
                        avis.get("stance", "abstain"),
                        float(avis.get("confidence") or 0.0),
                        avis.get("rationale"),
                        avis.get("source"),
                    ),
                )
        return decision_id

    # -- engagements et flux ---------------------------------------------

    def enregistrer_ordre(self, decision_id: int, ordre: dict, kind: str, venue: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO order_commitment
                   (decision_id, client_order_id, kind, side, symbol, venue,
                    quantity, price, stop_price, take_profit, sent_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    ordre.get("client_order_id", ""),
                    kind,
                    ordre.get("side", ""),
                    ordre.get("symbol", ""),
                    venue,
                    float(ordre.get("quantity") or 0.0),
                    ordre.get("average_price"),
                    ordre.get("stop_price"),
                    ordre.get("take_profit"),
                    maintenant(),
                    ordre.get("status", "UNKNOWN"),
                ),
            )
            if cur.rowcount == 1:
                return int(cur.lastrowid)
        # Déjà présent : l'identifiant d'ordre porte l'idempotence, donc un
        # renvoi après délai retrouve l'engagement au lieu d'en créer un second.
        # Même piège que ci-dessus : sans `rowcount`, un ordre renvoyé aurait
        # rendu l'identifiant d'un autre engagement.
        ligne = self.conn.execute(
            "SELECT id FROM order_commitment WHERE client_order_id = ?",
            (ordre.get("client_order_id", ""),),
        ).fetchone()
        return int(ligne["id"]) if ligne else 0

    def enregistrer_flux(
        self,
        *,
        order_id: Optional[int],
        kind: str,
        resource_kind: str,
        symbol: Optional[str],
        direction: str,
        quantity: float,
        amount: float,
        unit_price: Optional[float] = None,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO economic_event
                   (order_id, at, kind, resource_id, direction, quantity, unit_price, amount,
                    provider_agent_id, receiver_agent_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    maintenant(),
                    kind,
                    self.ressource_id(resource_kind, symbol),
                    direction,
                    quantity,
                    unit_price,
                    amount,
                    self.agent_id("Binance"),
                    self.agent_id("Trader"),
                ),
            )
        return int(cur.lastrowid)

    # -- la dualité --------------------------------------------------------

    def ouvrir_trade(
        self,
        *,
        decision_id: int,
        entry_event_id: int,
        symbol: str,
        interval: str,
        direction: str,
        regime: str,
        confluence: float,
        risk_committed: float,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO trade_duality
                   (decision_id, entry_event_id, symbol, interval, direction,
                    regime, confluence, risk_committed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    entry_event_id,
                    symbol,
                    interval,
                    direction,
                    regime,
                    confluence,
                    risk_committed,
                ),
            )
        return int(cur.lastrowid)

    def fermer_trade(
        self,
        trade_id: int,
        *,
        exit_event_id: Optional[int],
        r_multiple: float,
        pnl: float,
        outcome: str,
        reason: str,
    ) -> None:
        """Ferme le couple. C'est ici que le R devient lisible par la calibration."""
        with self.conn:
            self.conn.execute(
                """UPDATE trade_duality
                   SET exit_event_id = ?, r_multiple = ?, pnl = ?, outcome = ?,
                       reason = ?, closed_at = ?
                   WHERE id = ?""",
                (exit_event_id, r_multiple, pnl, outcome, reason, maintenant(), trade_id),
            )

    def trades_ouverts(self) -> list:
        return [
            dict(ligne)
            for ligne in self.conn.execute(
                "SELECT * FROM trade_duality WHERE closed_at IS NULL ORDER BY id"
            )
        ]

    def historique(self, limite: int = 1000) -> list:
        return [
            dict(ligne)
            for ligne in self.conn.execute(
                """SELECT * FROM trade_duality WHERE closed_at IS NOT NULL
                   ORDER BY closed_at DESC LIMIT ?""",
                (limite,),
            )
        ]

    def historique_calibration(self) -> list:
        """L'historique sous la forme que `calibration.gate` attend.

        C'est la boucle d'apprentissage de l'appareil : ce que le téléphone ou
        le PC a réellement fermé revient nourrir le portail, sans passer par un
        serveur.
        """
        from coeur.moteur import Outcome, TradeRecord

        enregistrements = []
        for ligne in self.conn.execute(
            """SELECT symbol, interval, regime, direction, confluence, outcome, r_multiple
               FROM trade_duality WHERE closed_at IS NOT NULL AND outcome IS NOT NULL"""
        ):
            try:
                issue = Outcome(ligne["outcome"])
            except ValueError:
                continue
            enregistrements.append(
                TradeRecord(
                    symbol=ligne["symbol"],
                    interval=ligne["interval"],
                    regime=ligne["regime"],
                    direction=ligne["direction"],
                    confluence=float(ligne["confluence"]),
                    outcome=issue,
                    r_multiple=float(ligne["r_multiple"] or 0.0),
                )
            )
        return enregistrements

    # -- compte et haltes --------------------------------------------------

    def enregistrer_compte(self, portefeuille: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO account_snapshot
                   (at, equity, peak_equity, realised_pnl_today, open_positions,
                    consecutive_losses, kill_switch)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    maintenant(),
                    float(portefeuille.get("equity") or 0.0),
                    float(portefeuille.get("peak_equity") or 0.0),
                    float(portefeuille.get("realised_pnl_today") or 0.0),
                    len(portefeuille.get("open_positions") or []),
                    int(portefeuille.get("consecutive_losses") or 0),
                    1 if portefeuille.get("kill_switch") else 0,
                ),
            )
        return int(cur.lastrowid)

    def enregistrer_halte(self, halt: str, engaged: bool, reason: str = "") -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO halt_event (at, halt, engaged, by_agent_id, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (maintenant(), halt, 1 if engaged else 0, self.agent_id("Trader"), reason),
            )

    def decisions_recentes(self, limite: int = 50) -> list:
        return [
            dict(ligne)
            for ligne in self.conn.execute(
                """SELECT d.*, p.direction, p.confluence, o.symbol, o.interval
                   FROM decision d
                   JOIN proposal p ON p.id = d.proposal_id
                   JOIN observation o ON o.id = p.observation_id
                   ORDER BY d.id DESC LIMIT ?""",
                (limite,),
            )
        ]

    # -- réglages ----------------------------------------------------------

    def lire_reglages(self) -> dict:
        ligne = self.conn.execute(
            "SELECT valeur FROM reglages WHERE cle = 'reglages'"
        ).fetchone()
        if not ligne:
            return {}
        try:
            return json.loads(ligne["valeur"])
        except json.JSONDecodeError:
            # Un fichier abîmé ne doit pas empêcher l'application de démarrer.
            return {}

    def ecrire_reglages(self, valeurs: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO reglages (cle, valeur) VALUES ('reglages', ?)",
                (json.dumps(valeurs, ensure_ascii=False),),
            )
