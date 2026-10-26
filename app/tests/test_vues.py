"""Les écrans : qu'ils se construisent, et qu'une panne s'affiche.

Ces tests ne regardent pas à quoi l'écran ressemble — un test ne juge pas une
couleur. Ils vérifient les deux choses qui cassent réellement une interface :

- un panneau construit avec des données partielles ou vides, parce que c'est
  l'état d'une application au premier lancement et après une coupure ;
- une erreur du moteur ou du réseau qui *s'affiche* au lieu de remonter. Un
  écran de trading qui disparaît sur une exception est la pire issue possible.

Flet n'est pas une dépendance des tests du cœur ; ce fichier se saute si la
bibliothèque n'est pas installée.
"""

import asyncio

import pytest

flet = pytest.importorskip("flet")

from coeur.config import Reglages  # noqa: E402
from coeur.session import Session  # noqa: E402
from vues import analyse as vue_analyse  # noqa: E402
from vues import cockpit as vue_cockpit  # noqa: E402
from vues import journal as vue_journal  # noqa: E402
from vues import reglages as vue_reglages  # noqa: E402


class PageFactice:
    """Le minimum qu'un écran demande à sa page, et un journal des appels."""

    def __init__(self, largeur=900, hauteur=800):
        self.width = largeur
        self.height = hauteur
        self.mises_a_jour = 0
        self.taches = []
        self.dialogues = []

    def update(self):
        self.mises_a_jour += 1

    def run_task(self, fonction, *args):
        # Les tests pilotent les chargements eux-mêmes ; ici on note seulement
        # que l'écran a bien demandé un travail hors du fil de l'interface.
        self.taches.append((fonction, args))

    def show_dialog(self, dialogue):
        self.dialogues.append(dialogue)

    def pop_dialog(self):
        self.dialogues.pop() if self.dialogues else None


class SessionCassee:
    """Une session dont tout échoue, pour voir ce que l'écran en fait."""

    def __init__(self):
        self.reglages = Reglages(source="synthetic")
        self.base = None

    def _echoue(self, *_a, **_k):
        raise RuntimeError("marché injoignable")

    etat = _echoue
    graphique = _echoue
    analyser = _echoue
    coupe_circuit = _echoue


@pytest.fixture
def page():
    return PageFactice()


@pytest.fixture
def session(tmp_path):
    s = Session.ouvrir(tmp_path)
    s.enregistrer_reglages(
        Reglages(source="synthetic", capital_papier=5000.0, seuil_confluence=20.0)
    )
    yield s
    s.fermer()


def textes(controle, trouves=None):
    """Tous les textes d'un arbre de contrôles, pour chercher un message."""
    trouves = [] if trouves is None else trouves
    for attribut in ("value", "label"):
        valeur = getattr(controle, attribut, None)
        if isinstance(valeur, str):
            trouves.append(valeur)
    for nom in ("controls", "actions", "shapes", "destinations"):
        for enfant in getattr(controle, nom, None) or []:
            textes(enfant, trouves)
    for nom in ("content", "control"):
        enfant = getattr(controle, nom, None)
        if enfant is not None and not isinstance(enfant, str):
            textes(enfant, trouves)
    return trouves


class TestCockpit:
    def test_il_se_construit_depuis_l_etat_local(self, session, page):
        ecran = vue_cockpit.Cockpit(session, page)
        asyncio.run(ecran.charger())
        assert ecran.corps.controls
        assert any("Compte" in t for t in textes(ecran.controle))

    def test_une_panne_s_affiche_au_lieu_de_remonter(self, page):
        ecran = vue_cockpit.Cockpit(SessionCassee(), page)
        asyncio.run(ecran.charger())  # ne doit pas lever
        assert any("injoignable" in t for t in textes(ecran.controle))

    def test_le_coupe_circuit_engage_est_annonce_en_clair(self, session, page):
        session.coupe_circuit(True)
        ecran = vue_cockpit.Cockpit(session, page)
        asyncio.run(ecran.charger())
        assert any("Coupe-circuit engagé" in t for t in textes(ecran.controle))

    def test_relacher_demande_une_confirmation(self, session, page):
        session.coupe_circuit(True)
        ecran = vue_cockpit.Cockpit(session, page)
        asyncio.run(ecran.charger())
        ecran._demander_relache()
        # Engager est immédiat, relâcher passe par une question : un arrêt
        # d'urgence qui pose une question n'est pas un arrêt d'urgence.
        assert len(page.dialogues) == 1

    def test_le_bouton_analyser_renvoie_le_symbole_demande(self, session, page):
        recus = []
        ecran = vue_cockpit.Cockpit(session, page, aller_analyse=recus.append)
        asyncio.run(ecran.charger())
        ecran._analyser("ETHUSDT")
        assert recus == ["ETHUSDT"]

    def test_une_position_ouverte_dit_son_sens_en_mots(self):
        ligne = vue_cockpit.ligne_position(
            {"symbol": "BTCUSDT", "direction": "short", "entry": 100.0,
             "stop_loss": 105.0, "take_profit": 90.0, "quantity": 0.5}
        )
        assert any("VENTE" in t.upper() for t in textes(ligne))


class TestAnalyse:
    def test_le_graphique_se_dessine_avant_tout_verdict(self, session, page):
        ecran = vue_analyse.Analyse(session, page)
        asyncio.run(ecran.charger())
        assert ecran._graphique["candles"]
        assert any("synthetic" in t for t in textes(ecran.controle))

    def test_l_analyse_ajoute_les_etages(self, session, page):
        ecran = vue_analyse.Analyse(session, page)
        asyncio.run(ecran.charger())
        asyncio.run(ecran.analyser())
        affiche = textes(ecran.controle)
        assert any("Indicateurs" in t for t in affiche)
        assert any("Calibration" in t for t in affiche)

    def test_un_marche_illisible_s_affiche(self, page):
        ecran = vue_analyse.Analyse(SessionCassee(), page)
        asyncio.run(ecran.charger())
        assert any("illisible" in t for t in textes(ecran.controle))

    def test_un_conseil_vide_ne_fait_pas_de_panneau(self):
        # `{}` est faux mais présent : le même piège a fait planter
        # l'interface web, qui affichait le panneau puis lisait `opinions`.
        assert vue_analyse.a_conseil({}) is False
        assert vue_analyse.a_conseil(None) is False
        assert vue_analyse.a_conseil({"opinions": []}) is False
        assert vue_analyse.a_conseil({"opinions": [{"role": "analyst"}]}) is True
        assert vue_analyse.panneau_conseil({}) is None

    def test_un_conseil_qui_a_siege_est_affiche(self):
        panneau = vue_analyse.panneau_conseil(
            {
                "approved": True,
                "conviction": 0.8,
                "opinions": [
                    {"role": "risk", "stance": "veto", "rationale": "repli trop profond"}
                ],
                "reasons": [],
            }
        )
        affiche = textes(panneau)
        assert any("S'OPPOSE" in t.upper() for t in affiche)

    def test_la_hauteur_du_graphique_reste_dans_ses_bornes(self):
        # Trop plat les bougies ne se lisent plus ; trop haut le verdict passe
        # sous la ligne de flottaison.
        _, courte = vue_analyse.dimensions_graphique(400, 300)
        _, haute = vue_analyse.dimensions_graphique(2000, 4000)
        assert courte == vue_analyse.HAUTEUR_GRAPHIQUE_MIN
        assert haute == vue_analyse.HAUTEUR_GRAPHIQUE_MAX

    def test_une_page_sans_dimensions_a_quand_meme_un_graphique(self):
        largeur, hauteur = vue_analyse.dimensions_graphique(None, None)
        assert largeur > 0 and hauteur > 0


class TestJournal:
    def test_une_base_vierge_le_dit(self, session, page):
        ecran = vue_journal.Journal(session, page)
        asyncio.run(ecran.charger())
        affiche = textes(ecran.controle)
        assert any("Aucun trade fermé" in t for t in affiche)
        assert any("Aucune décision" in t for t in affiche)

    def test_les_decisions_enregistrees_apparaissent(self, session, page):
        session.analyser("BTCUSDT")
        ecran = vue_journal.Journal(session, page)
        asyncio.run(ecran.charger())
        assert any("BTCUSDT" in t for t in textes(ecran.controle))

    def test_un_trade_ferme_dit_son_issue_en_mot(self):
        ligne = vue_journal.ligne_trade(
            {"symbol": "ETHUSDT", "direction": "long", "outcome": "loss",
             "r_multiple": -1.0, "interval": "5m", "confluence": 70.0,
             "closed_at": "2026-01-01T12:00:00+00:00", "reason": "stop touché"}
        )
        assert any("PERTE" in t.upper() for t in textes(ligne))

    def test_le_bilan_distingue_aucun_trade_de_zero_pour_cent(self):
        from coeur.bilan import resume

        affiche = textes(vue_journal.panneau_bilan(resume([])))
        assert "—" in affiche


class TestReglages:
    def test_le_formulaire_part_des_reglages_en_memoire(self, session, page):
        ecran = vue_reglages.Vue(session, page)
        assert ecran.source.value == "synthetic"
        assert "BTCUSDT" in ecran.watchlist.value

    def test_la_saisie_est_relue_et_bornee(self, session, page):
        ecran = vue_reglages.Vue(session, page)
        ecran.watchlist.value = "btcusdt; eth usdt, BTCUSDT"
        ecran.capital.value = "12 500,50"
        ecran.rafraichissement.value = "1"
        lus = ecran.lire_formulaire()
        assert lus.watchlist == ["BTCUSDT", "ETHUSDT"]
        assert lus.capital_papier == 12500.50
        # Borne de Reglages.valide(), pas de la lecture.
        assert lus.rafraichissement_s == 15

    def test_une_saisie_illisible_garde_l_ancienne_valeur(self, session, page):
        ecran = vue_reglages.Vue(session, page)
        ecran.capital.value = "beaucoup"
        assert ecran.lire_formulaire().capital_papier == 5000.0

    def test_l_enregistrement_est_persistant(self, session, page, tmp_path):
        ecran = vue_reglages.Vue(session, page)
        ecran.watchlist.value = "adausdt"
        asyncio.run(ecran._appliquer())
        assert session.reglages.watchlist == ["ADAUSDT"]
        # Relu depuis la base : c'est ce que verra le prochain démarrage.
        assert Session.ouvrir(tmp_path).reglages.watchlist == ["ADAUSDT"]

    def test_aucun_champ_ne_demande_de_cle_d_api(self, session, page):
        # Une clé d'exchange n'a rien à faire sur l'appareil : un .pyc dans
        # une APK ne protège rien.
        ecran = vue_reglages.Vue(session, page)
        affiche = " ".join(textes(ecran.controle)).lower()
        assert "aucune clé d'api" in affiche
        assert not hasattr(ecran, "cle_api")


class TestEntree:
    """Le point d'entrée, monté sur une page factice.

    C'est le test qui attrape le plus de bêtises d'un coup : un nom de contrôle
    qui a changé de version, un rappel branché sur un écran pas encore
    construit, un onglet qui ne charge jamais.
    """

    @pytest.fixture
    def demarre(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path / "donnees"))
        import main as entree

        page = PageFactice()
        page.window = type("Fenetre", (), {})()
        page.ajoutes = []
        page.add = page.ajoutes.append
        entree.main(page)
        return entree, page

    def test_l_application_se_monte(self, demarre):
        _entree, page = demarre
        assert page.ajoutes
        assert page.title

    def test_les_quatre_destinations_sont_la(self, demarre):
        entree, page = demarre
        affiche = textes(page.ajoutes[0])
        for titre in entree.TITRES:
            assert any(titre in t for t in affiche), titre

    def test_le_premier_ecran_charge_hors_du_fil_de_l_interface(self, demarre):
        # Une lecture de bougies qui prend deux secondes ne doit pas figer le
        # défilement : l'écran demande son chargement, il ne le fait pas ici.
        _entree, page = demarre
        assert page.taches, "le poste devrait avoir demandé son chargement"

    def test_la_base_est_ecrite_dans_le_dossier_prive(self, demarre, tmp_path):
        # Sur Android c'est le dossier que le système sauvegarde et nettoie
        # avec l'application ; jamais le dépôt ni le dossier courant.
        assert (tmp_path / "donnees" / "aitrader.db").exists()
