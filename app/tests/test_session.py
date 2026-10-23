"""La session : les deux modes, et ce qu'elle refuse de faire.

Le test qui porte le plus est celui sur l'absence de boucle automatique. C'est
une décision de conception, pas un oubli, et sans test elle se perdra au
premier qui trouvera pratique d'ajouter un minuteur.
"""

import pytest

from coeur.config import MODE_LOCAL, MODE_RELIE, Reglages
from coeur.distant import Distant, ErreurDistante
from coeur.session import Session


@pytest.fixture
def session(tmp_path):
    s = Session.ouvrir(tmp_path)
    s.enregistrer_reglages(
        Reglages(source="synthetic", capital_papier=5000.0, seuil_confluence=20.0)
    )
    yield s
    s.fermer()


class TestModeLocal:
    def test_l_etat_decrit_un_poste_de_pilotage(self, session):
        etat = session.etat()
        assert etat["joignable"] is True
        assert etat["mode"]["paper"] is True
        assert etat["mode"]["data_source"] == "synthetic"
        assert etat["portfolio"]["equity"] == 5000.0

    def test_la_boucle_automatique_est_absente_sur_l_appareil(self, session):
        # Android suspend les processus en arrière-plan ; une position dont le
        # processus a été tué entre l'entrée et son stop est exactement l'état
        # que le reste du projet évite. Ce n'est pas une limite à contourner.
        assert session.etat()["mode"]["auto_trade"] is False
        assert not hasattr(session, "demarrer_boucle")
        assert not hasattr(session, "run_once")

    def test_analyser_ne_passe_aucun_ordre(self, session):
        session.analyser("BTCUSDT")
        courtier = session.trader_local().broker
        assert courtier.placed == []

    def test_une_analyse_est_ecrite_en_base(self, session):
        session.analyser("BTCUSDT")
        lignes = session.base.decisions_recentes()
        assert len(lignes) == 1
        assert lignes[0]["symbol"] == "BTCUSDT"

    def test_le_graphique_aligne_ses_series_sur_ses_bougies(self, session):
        graphique = session.graphique("BTCUSDT", limite=80)
        assert len(graphique["candles"]) == 80
        for nom, serie in graphique["indicators"].items():
            assert len(serie) == 80, nom

    def test_le_graphique_porte_le_nom_de_sa_source(self, session):
        # Pour qu'un écran nourri par le flux de démonstration puisse le dire
        # au lieu de faire passer des prix inventés pour le marché.
        assert session.graphique("BTCUSDT", limite=60)["source"] == "synthetic"

    def test_le_coupe_circuit_s_engage_et_se_trace(self, session):
        assert session.coupe_circuit(True)["kill_switch"] is True
        haltes = session.base.conn.execute("SELECT * FROM halt_event").fetchall()
        assert len(haltes) == 1
        assert haltes[0]["engaged"] == 1

    def test_le_coupe_circuit_arrete_vraiment_le_moteur(self, session):
        session.coupe_circuit(True)
        assert session.trader_local().portfolio.kill_switch is True


class TestApprentissageLocal:
    def test_le_moteur_est_amorce_avec_l_historique_de_l_appareil(self, session):
        """La boucle d'apprentissage sans serveur."""
        from coeur.moteur import Outcome, TradeRecord

        assert session.trader_local().history == []

        did = session.base.enregistrer_decision(
            {
                "symbol": "BTCUSDT", "interval": "5m", "stage": "executed",
                "approved": True, "quantity": 1.0,
                "proposal": {"direction": "long", "regime": "trending_up",
                             "confluence": 72.0, "entry": 100.0, "actionable": True},
                "reasons": [],
            }
        )
        ordre = session.base.enregistrer_ordre(
            did, {"client_order_id": "x", "quantity": 1.0}, "entry", "paper")
        flux = session.base.enregistrer_flux(
            order_id=ordre, kind="entry_fill", resource_kind="position",
            symbol="BTCUSDT", direction="inflow", quantity=1.0, amount=100.0)
        trade = session.base.ouvrir_trade(
            decision_id=did, entry_event_id=flux, symbol="BTCUSDT", interval="5m",
            direction="long", regime="trending_up", confluence=72.0, risk_committed=10.0)
        session.base.fermer_trade(trade, exit_event_id=None, r_multiple=-1.0,
                                  pnl=-10.0, outcome="loss", reason="stop")

        # Les réglages changent : le moteur est reconstruit, donc relit l'historique.
        session.enregistrer_reglages(session.reglages)
        historique = session.trader_local().history
        assert len(historique) == 1
        assert isinstance(historique[0], TradeRecord)
        assert historique[0].outcome is Outcome.LOSS


class TestModeRelie:
    def test_un_serveur_injoignable_donne_un_etat_lisible(self, session):
        session.enregistrer_reglages(
            Reglages(mode=MODE_RELIE, url_serveur="http://127.0.0.1:9")
        )
        etat = session.etat()
        assert etat["joignable"] is False
        assert "injoignable" in etat["erreur"]
        # Et surtout : pas d'exception vers l'écran, et aucun mode inventé.
        assert etat["mode"]["auto_trade"] is False

    def test_les_reglages_choisissent_le_mode(self, session):
        assert session.reglages.mode == MODE_LOCAL
        session.enregistrer_reglages(Reglages(mode=MODE_RELIE))
        assert session.reglages.mode == MODE_RELIE


class TestErreurDistante:
    def test_un_409_se_distingue_d_un_503(self):
        # Les deux se règlent autrement : l'un dans la configuration, l'autre
        # en attendant. L'écran doit pouvoir les dire différemment.
        desarme = ErreurDistante("auto-trading is off", 409)
        injoignable = ErreurDistante("feed down", 503)
        assert desarme.est_desarme and not desarme.est_injoignable
        assert injoignable.est_injoignable and not injoignable.est_desarme

    def test_une_absence_de_reponse_compte_comme_injoignable(self):
        assert ErreurDistante("serveur injoignable", None).est_injoignable

    def test_l_url_est_construite_sans_double_barre(self):
        assert Distant("http://x:8000/")._url("/api/auto/status") == "http://x:8000/api/auto/status"

    def test_les_parametres_sont_encodes(self):
        url = Distant("http://x")._url("/api/auto/chart", {"symbol": "BTC/USDT"})
        assert "BTC%2FUSDT" in url
