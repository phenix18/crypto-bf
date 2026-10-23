"""Les réglages, et surtout leur validation.

Ils viennent d'un fichier que l'utilisateur peut éditer et qu'une version
précédente a pu écrire autrement. Planter au démarrage à cause d'un nombre
hors bornes serait la pire réponse possible.
"""

from coeur.config import MODE_LOCAL, SOURCES, Reglages


class TestValidation:
    def test_les_defauts_sont_les_prudents(self):
        r = Reglages()
        assert r.mode == MODE_LOCAL
        assert r.testnet is True
        assert r.source == "binance"

    def test_un_mode_inconnu_retombe_en_local(self):
        assert Reglages(mode="nimporte").valide().mode == MODE_LOCAL

    def test_une_source_inconnue_retombe_sur_binance(self):
        assert Reglages(source="inventee").valide().source == "binance"

    def test_un_rafraichissement_trop_court_est_releve(self):
        # Une seconde viderait la batterie et ferait bannir l'adresse IP.
        assert Reglages(rafraichissement_s=1).valide().rafraichissement_s == 15

    def test_un_rafraichissement_absurde_est_plafonne(self):
        assert Reglages(rafraichissement_s=99999).valide().rafraichissement_s == 3600

    def test_un_seuil_hors_bornes_est_ramene(self):
        assert Reglages(seuil_confluence=400).valide().seuil_confluence == 100.0
        assert Reglages(seuil_confluence=-5).valide().seuil_confluence == 0.0

    def test_un_capital_nul_est_refuse(self):
        assert Reglages(capital_papier=0).valide().capital_papier == 1.0

    def test_une_watchlist_vide_garde_au_moins_une_paire(self):
        assert Reglages(watchlist=[]).valide().watchlist == ["BTCUSDT"]

    def test_la_watchlist_est_normalisee(self):
        assert Reglages(watchlist=[" btcusdt ", "", "eth"]).valide().watchlist == [
            "BTCUSDT",
            "ETH",
        ]

    def test_un_intervalle_inconnu_retombe_sur_cinq_minutes(self):
        assert Reglages(intervalle="7m").valide().intervalle == "5m"


class TestSerialisation:
    def test_un_aller_retour_conserve_les_valeurs(self):
        r = Reglages(mode="relie", source="synthetic", capital_papier=250.0).valide()
        assert Reglages.from_dict(r.to_dict()).to_dict() == r.to_dict()

    def test_une_cle_inconnue_est_ignoree(self):
        # Un réglage d'une version future ne doit pas empêcher le démarrage.
        r = Reglages.from_dict({"capital_papier": 500.0, "couleur_preferee": "bleu"})
        assert r.capital_papier == 500.0

    def test_un_dictionnaire_vide_donne_les_defauts(self):
        assert Reglages.from_dict({}).mode == MODE_LOCAL
        assert Reglages.from_dict(None).source in SOURCES


class TestDossierDonnees:
    def test_flet_decide_quand_il_le_dit(self):
        # Sur Android et dans les paquets de bureau, c'est le dossier privé
        # que le système sauvegarde et nettoie avec l'application.
        from coeur.config import dossier_donnees

        chemin = dossier_donnees({"FLET_APP_STORAGE_DATA": "/var/data/aitrader"})
        assert str(chemin) == "/var/data/aitrader"

    def test_sans_flet_on_ecrit_dans_le_repertoire_personnel(self):
        # Jamais le dossier courant : un `flet run` depuis le dépôt y
        # déposerait une base de trading.
        from coeur.config import dossier_donnees

        chemin = dossier_donnees({}, maison="/home/essai")
        assert str(chemin) == "/home/essai/.aitrader"

    def test_une_variable_vide_compte_comme_absente(self):
        from coeur.config import dossier_donnees

        chemin = dossier_donnees({"FLET_APP_STORAGE_DATA": "  "}, maison="/home/essai")
        assert str(chemin) == "/home/essai/.aitrader"
