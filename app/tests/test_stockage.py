"""La base locale, et la dualité qu'elle existe pour tenir.

Le test qui compte est celui qui ferme un couple entrée/clôture et vérifie
qu'il ressort sous la forme que la calibration attend. Sans ça, pas de R, pas
d'espérance, pas de portail calibré — et rien ne le signalerait.
"""

import pytest

from coeur.stockage import Base


def decision(stage="executed", approuvee=True, confluence=72.0, bar=1):
    return {
        "symbol": "BTCUSDT",
        "interval": "5m",
        "at": "2026-10-23T07:00:00Z",
        "stage": stage,
        "approved": approuvee,
        "quantity": 0.5,
        "proposal": {
            "direction": "long", "regime": "trending_up",
            "confluence": confluence, "participation": 80.0,
            "entry": 100.0, "stop_loss": 98.0, "take_profit": 104.0,
            "risk_reward": 2.0, "actionable": True, "blockers": [],
            "votes": [{"name": "rsi", "direction": "long"}],
            "readings": {"rsi": 32.0}, "bar_open_time": bar,
        },
        "risk": {"risk_amount": 50.0, "notional": 500.0},
        "calibration": {"basis": "confluence", "probability": None},
        "council": {
            "conviction": 0.72,
            "opinions": [
                {"role": "risk", "stance": "confirm", "confidence": 0.9,
                 "rationale": "taille dans les limites", "source": "fallback"},
                {"role": "macro", "stance": "abstain", "confidence": 0.3,
                 "rationale": "pas de donnée", "source": "fallback"},
            ],
        },
        "reasons": ["le conseil valide"],
    }


@pytest.fixture
def base():
    b = Base(":memory:")
    yield b
    b.fermer()


class TestSchema:
    def test_les_agents_de_base_sont_la(self, base):
        for nom in ("Trader", "Moteur", "analyst", "risk", "historian", "macro", "Binance"):
            assert base.agent_id(nom) is not None, nom

    def test_une_ressource_inconnue_est_creee_a_la_demande(self, base):
        premier = base.ressource_id("position", "ETHUSDT")
        assert premier == base.ressource_id("position", "ETHUSDT")

    def test_ouvrir_deux_fois_ne_duplique_rien(self, tmp_path):
        chemin = tmp_path / "x.db"
        Base(chemin).fermer()
        b = Base(chemin)
        lignes = b.conn.execute("SELECT COUNT(*) AS n FROM agent").fetchone()["n"]
        assert lignes == 8
        b.fermer()


class TestDecision:
    def test_une_decision_s_ecrit_avec_sa_proposition_et_ses_avis(self, base):
        did = base.enregistrer_decision(decision())
        assert did > 0
        lignes = base.decisions_recentes()
        assert lignes[0]["stage"] == "executed"
        assert lignes[0]["symbol"] == "BTCUSDT"
        avis = base.conn.execute(
            "SELECT COUNT(*) AS n FROM decision_opinion WHERE decision_id = ?", (did,)
        ).fetchone()["n"]
        assert avis == 2

    def test_deux_decisions_sur_la_meme_bougie_partagent_l_observation(self, base):
        base.enregistrer_decision(decision(bar=7))
        base.enregistrer_decision(decision(bar=7))
        n = base.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
        assert n == 1

    def test_une_decision_refusee_s_ecrit_aussi(self, base):
        # Les refus sont la majorité de ce que le moteur produit, et c'est eux
        # qu'on relit quand on se demande pourquoi rien ne s'est passé.
        base.enregistrer_decision(decision(stage="calibration", approuvee=False))
        assert base.decisions_recentes()[0]["approved"] == 0

    def test_un_avis_d_un_role_inconnu_est_ignore_sans_planter(self, base):
        d = decision()
        d["council"]["opinions"].append({"role": "astrologue", "stance": "confirm",
                                         "confidence": 1.0})
        did = base.enregistrer_decision(d)
        n = base.conn.execute(
            "SELECT COUNT(*) AS n FROM decision_opinion WHERE decision_id = ?", (did,)
        ).fetchone()["n"]
        assert n == 2


class TestIdempotence:
    def test_le_meme_identifiant_d_ordre_ne_cree_pas_un_second_engagement(self, base):
        did = base.enregistrer_decision(decision())
        ordre = {"client_order_id": "at-1", "side": "BUY", "symbol": "BTCUSDT",
                 "quantity": 0.5, "status": "FILLED"}
        premier = base.enregistrer_ordre(did, ordre, "entry", "paper")
        second = base.enregistrer_ordre(did, ordre, "entry", "paper")
        assert premier == second
        n = base.conn.execute("SELECT COUNT(*) AS n FROM order_commitment").fetchone()["n"]
        assert n == 1

    def test_l_idempotence_ne_rend_pas_l_identifiant_d_un_autre_engagement(self, base):
        """Le test précédent passait pour une mauvaise raison.

        `lastrowid` n'est pas remis à zéro quand un INSERT OR IGNORE est
        ignoré : il garde la dernière valeur insérée. Avec un seul ordre en
        base, cette valeur était justement la bonne. Il en faut un autre entre
        les deux pour que la différence se voie.
        """
        did = base.enregistrer_decision(decision())
        attendu = base.enregistrer_ordre(
            did, {"client_order_id": "at-1", "quantity": 1}, "entry", "paper")
        autre = base.enregistrer_ordre(
            did, {"client_order_id": "at-2", "quantity": 1}, "protective", "paper")
        renvoye = base.enregistrer_ordre(
            did, {"client_order_id": "at-1", "quantity": 1}, "entry", "paper")
        assert renvoye == attendu
        assert renvoye != autre

    def test_deux_identifiants_distincts_donnent_deux_engagements(self, base):
        did = base.enregistrer_decision(decision())
        a = base.enregistrer_ordre(did, {"client_order_id": "a", "quantity": 1}, "entry", "paper")
        b = base.enregistrer_ordre(did, {"client_order_id": "b", "quantity": 1}, "protective", "paper")
        assert a != b


class TestDualite:
    def ouvrir(self, base):
        did = base.enregistrer_decision(decision())
        oid = base.enregistrer_ordre(
            did, {"client_order_id": "e1", "side": "BUY", "symbol": "BTCUSDT",
                  "quantity": 0.5, "status": "FILLED"}, "entry", "paper")
        ev = base.enregistrer_flux(
            order_id=oid, kind="entry_fill", resource_kind="position", symbol="BTCUSDT",
            direction="inflow", quantity=0.5, amount=50.0, unit_price=100.0)
        tid = base.ouvrir_trade(
            decision_id=did, entry_event_id=ev, symbol="BTCUSDT", interval="5m",
            direction="long", regime="trending_up", confluence=72.0, risk_committed=50.0)
        return tid

    def test_un_trade_ouvert_n_est_pas_dans_l_historique(self, base):
        self.ouvrir(base)
        assert len(base.trades_ouverts()) == 1
        assert base.historique() == []
        assert base.historique_calibration() == []

    def test_fermer_le_couple_le_fait_entrer_dans_l_historique(self, base):
        tid = self.ouvrir(base)
        base.fermer_trade(tid, exit_event_id=None, r_multiple=2.0, pnl=100.0,
                          outcome="win", reason="target")
        assert base.trades_ouverts() == []
        assert len(base.historique()) == 1

    def test_le_couple_ferme_ressort_sous_la_forme_que_la_calibration_attend(self, base):
        from calibration import Outcome

        tid = self.ouvrir(base)
        base.fermer_trade(tid, exit_event_id=None, r_multiple=2.0, pnl=100.0,
                          outcome="win", reason="target")
        historique = base.historique_calibration()
        assert len(historique) == 1
        enregistrement = historique[0]
        assert enregistrement.outcome is Outcome.WIN
        assert enregistrement.r_multiple == 2.0
        assert enregistrement.regime == "trending_up"
        assert enregistrement.confluence == 72.0

    def test_l_historique_local_ferme_vraiment_le_portail(self, base):
        """La boucle d'apprentissage, de bout en bout, sans serveur."""
        from calibration import CalibrationConfig, gate

        for _ in range(30):
            tid = self.ouvrir(base)
            base.fermer_trade(tid, exit_event_id=None, r_multiple=-1.0, pnl=-50.0,
                              outcome="loss", reason="stop")
        verdict = gate(
            confluence=72.0, regime="trending_up", direction="long",
            history=base.historique_calibration(), config=CalibrationConfig(),
        )
        assert verdict.basis == "calibrated"
        assert verdict.allowed is False

    def test_une_issue_inconnue_est_refusee_par_le_schema(self, base):
        """Le garde-fou est dans la base, pas seulement dans le code.

        Écrit en voulant tester le filtrage côté Python : la contrainte CHECK
        a parlé la première, ce qui est l'endroit où elle doit parler.
        """
        import sqlite3

        tid = self.ouvrir(base)
        with pytest.raises(sqlite3.IntegrityError):
            base.conn.execute(
                "UPDATE trade_duality SET outcome = 'peut-etre' WHERE id = ?", (tid,)
            )


class TestReglagesEtCompte:
    def test_les_reglages_font_un_aller_retour(self, base):
        base.ecrire_reglages({"mode": "local", "capital_papier": 42.0})
        assert base.lire_reglages()["capital_papier"] == 42.0

    def test_des_reglages_abimes_ne_bloquent_pas_le_demarrage(self, base):
        base.conn.execute(
            "INSERT OR REPLACE INTO reglages (cle, valeur) VALUES ('reglages', '{pas du json')"
        )
        base.conn.commit()
        assert base.lire_reglages() == {}

    def test_un_instantane_de_compte_s_ecrit(self, base):
        base.enregistrer_compte(
            {"equity": 1000.0, "peak_equity": 1200.0, "realised_pnl_today": -20.0,
             "open_positions": [{}], "consecutive_losses": 2, "kill_switch": True}
        )
        ligne = base.conn.execute("SELECT * FROM account_snapshot").fetchone()
        assert ligne["kill_switch"] == 1
        assert ligne["open_positions"] == 1

    def test_une_halte_est_tracee(self, base):
        base.enregistrer_halte("kill_switch", True, "depuis l'application")
        ligne = base.conn.execute("SELECT * FROM halt_event").fetchone()
        assert ligne["engaged"] == 1
        assert ligne["by_agent_id"] == base.agent_id("Trader")
