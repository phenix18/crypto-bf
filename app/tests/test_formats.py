"""La mise en forme française.

Ça paraît cosmétique et ça ne l'est pas : un R sans signe, un millier collé à
sa virgule, et le lecteur se fait une idée fausse de sa position en un coup
d'œil.
"""

import math

from coeur import formats as f


class TestNombre:
    def test_la_virgule_est_decimale_et_le_millier_separe(self):
        assert f.nombre(1234567.891) == "1 234 567,89"

    def test_les_petites_valeurs_restent_lisibles(self):
        assert f.nombre(0.5) == "0,50"

    def test_le_nombre_de_decimales_se_choisit(self):
        assert f.nombre(1.23456, 4) == "1,2346"

    def test_rien_a_afficher_se_dit_avec_un_tiret(self):
        # Pas "0,00" : un zéro est une valeur, l'absence n'en est pas une.
        assert f.nombre(None) == "—"
        assert f.nombre(math.nan) == "—"

    def test_une_valeur_illisible_ne_fait_pas_planter_l_ecran(self):
        assert f.nombre("beaucoup") == "—"


class TestSigne:
    def test_le_plus_est_explicite(self):
        assert f.signe(12.0) == "+12,00"

    def test_le_moins_est_typographique(self):
        assert f.signe(-3.5) == "−3,50"
        assert "-" not in f.signe(-3.5)

    def test_zero_compte_comme_positif(self):
        assert f.signe(0) == "+0,00"


class TestAutresFormats:
    def test_un_pourcentage_porte_son_symbole(self):
        assert f.pourcent(12.345) == "12,35 %"

    def test_un_r_se_lit_signe(self):
        assert f.multiple_r(2.0) == "+2,00 R"
        assert f.multiple_r(-1.0) == "−1,00 R"

    def test_un_prix_prend_les_decimales_qu_il_demande(self):
        assert f.prix(64250.5) == "64 250,50"
        assert f.prix(12.3456) == "12,3456"
        assert f.prix(0.00012345) == "0,000123"

    def test_un_horodatage_iso_devient_lisible(self):
        assert ":" in f.horodatage("2026-10-23T07:40:00Z")
        assert "/" in f.horodatage("2026-10-23T07:40:00Z", avec_date=True)

    def test_un_horodatage_en_millisecondes_aussi(self):
        assert f.horodatage(1_700_000_000_000) != "—"

    def test_un_horodatage_illisible_ne_plante_pas(self):
        assert f.horodatage("jamais") == "—"
        assert f.horodatage(None) == "—"


class TestVocabulaire:
    def test_les_etages_sont_traduits(self):
        assert f.etage("calibration") == "calibration"
        assert f.etage("executed") == "exécuté"

    def test_un_etage_inconnu_reste_lisible(self):
        # Brut plutôt que vide : un étage ajouté au moteur avant sa traduction
        # doit rester visible à l'écran.
        assert f.etage("quarantaine") == "quarantaine"

    def test_les_etages_couvrent_ceux_du_moteur(self):
        from auto_trader import STAGES

        manquants = set(STAGES) - set(f.ETAGES)
        assert manquants == set(), f"étages sans traduction : {sorted(manquants)}"

    def test_aucune_traduction_ne_traine_sans_etage(self):
        from auto_trader import STAGES

        assert set(f.ETAGES) == set(STAGES)

    def test_le_reste_du_vocabulaire(self):
        assert f.sens("long") == "achat"
        assert f.posture("veto") == "s'oppose"
        assert f.role("historian") == "historien"
        assert f.regime("ranging") == "range"
        assert f.sens("inconnu") == "inconnu"
