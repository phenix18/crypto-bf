"""La lecture des champs texte des réglages.

Un réglage mal lu se paie plus tard et ailleurs : un capital tombé à zéro, et
le moteur refuse tout sans que l'écran sache dire pourquoi.
"""

from coeur.saisie import liste_symboles, nombre_saisi


class TestListeSymboles:
    def test_les_separateurs_usuels_sont_acceptes(self):
        assert liste_symboles("btcusdt, ethusdt; solusdt\nxrpusdt") == [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "XRPUSDT",
        ]

    def test_les_espaces_internes_disparaissent(self):
        assert liste_symboles("btc usdt") == ["BTCUSDT"]

    def test_les_doublons_sont_retires_en_gardant_l_ordre(self):
        # Un symbole saisi deux fois serait analysé deux fois.
        assert liste_symboles("btcusdt, BTCUSDT, ethusdt") == ["BTCUSDT", "ETHUSDT"]

    def test_un_champ_vide_rend_une_liste_vide(self):
        assert liste_symboles("") == []
        assert liste_symboles(None) == []
        assert liste_symboles(" , ; ") == []


class TestNombreSaisi:
    def test_la_virgule_francaise_est_lue(self):
        assert nombre_saisi("1234,50", 0.0) == 1234.5

    def test_les_separateurs_de_milliers_sont_lus(self):
        # C'est exactement ce que `formats.nombre` écrit dans le champ.
        assert nombre_saisi("10 000,00", 0.0) == 10000.0

    def test_une_saisie_illisible_garde_la_valeur_precedente(self):
        # Jamais zéro par défaut : un capital à zéro bloque tout le moteur.
        assert nombre_saisi("beaucoup", 10_000.0) == 10_000.0
        assert nombre_saisi("", 60) == 60
        assert nombre_saisi(None, 60) == 60

    def test_un_nan_garde_la_valeur_precedente(self):
        assert nombre_saisi("nan", 42.0) == 42.0

    def test_un_negatif_est_lu_tel_quel(self):
        # Les bornes sont l'affaire de Reglages.valide(), pas de la lecture.
        assert nombre_saisi("-5", 1.0) == -5.0
