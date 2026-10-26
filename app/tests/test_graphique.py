"""La géométrie du graphique.

Le test qui porte : l'échelle des prix doit contenir le stop et la cible. Un
niveau dessiné hors de l'écran est un niveau que personne ne vérifie.
"""

from coeur.graphique import (
    Panneau,
    disposition,
    doit_cacher,
    echelle_prix,
    etiquettes_prix,
    extremes,
    largeur_corps,
    pas_horizontal,
    x_bougie,
)


def bougies(n=50, base=100.0):
    return [
        {"t": i, "o": base + i, "h": base + i + 1, "l": base + i - 1, "c": base + i, "v": 10.0}
        for i in range(n)
    ]


class TestDisposition:
    def test_les_trois_bandes_ne_se_chevauchent_pas(self):
        d = disposition(400, 600)
        assert d.prix.bas <= d.volume.haut
        assert d.volume.bas <= d.rsi.haut

    def test_tout_tient_dans_la_hauteur(self):
        d = disposition(400, 600)
        assert d.rsi.bas <= 600

    def test_une_hauteur_minuscule_ne_donne_pas_de_bande_negative(self):
        d = disposition(200, 20)
        for bande in (d.prix, d.volume, d.rsi):
            assert bande.hauteur > 0

    def test_la_gouttiere_de_l_axe_est_reservee(self):
        d = disposition(400, 600)
        assert d.largeur_trace == 400 - d.gauche - d.droite


class TestExtremes:
    def test_les_trous_de_chauffe_sont_ignores(self):
        assert extremes([None, None, 3.0, 7.0, None]) == (3.0, 7.0)

    def test_une_serie_vide_donne_des_bornes_utilisables(self):
        assert extremes([]) == (0.0, 1.0)
        assert extremes([None, None]) == (0.0, 1.0)

    def test_une_serie_plate_garde_une_epaisseur(self):
        bas, haut = extremes([5.0, 5.0, 5.0])
        assert bas < haut

    def test_les_valeurs_non_finies_sont_ecartees(self):
        assert extremes([float("inf"), 2.0, 4.0]) == (2.0, 4.0)


class TestEchellePrix:
    def test_le_stop_et_la_cible_entrent_dans_l_echelle(self):
        # Le bug que ce test existe pour empêcher : une échelle calculée sur
        # les seules bougies dessine un stop hors de l'écran.
        serie = bougies()
        echelle = echelle_prix(serie, niveaux=[80.0, 200.0])
        assert echelle.bas <= 80.0
        assert echelle.haut >= 200.0

    def test_les_bandes_de_bollinger_comptent_aussi(self):
        serie = bougies()
        echelle = echelle_prix(serie, series={"bb_upper": [500.0] * 50, "bb_lower": [None] * 50})
        assert echelle.haut >= 500.0

    def test_un_niveau_absent_ne_fait_rien_planter(self):
        echelle = echelle_prix(bougies(), niveaux=[None, None])
        assert echelle.haut > echelle.bas

    def test_le_haut_se_dessine_plus_haut_que_le_bas(self):
        # En pixels, « plus haut » veut dire une ordonnée plus petite.
        panneau = Panneau(10.0, 100.0)
        echelle = echelle_prix(bougies(), panneau=panneau)
        assert echelle.y(echelle.haut) < echelle.y(echelle.bas)

    def test_les_bornes_tombent_dans_le_panneau(self):
        panneau = Panneau(10.0, 100.0)
        echelle = echelle_prix(bougies(), panneau=panneau)
        assert abs(echelle.y(echelle.haut) - panneau.haut) < 0.001
        assert abs(echelle.y(echelle.bas) - panneau.bas) < 0.001


class TestPositions:
    def test_les_bougies_se_repartissent_sur_la_largeur(self):
        d = disposition(400, 600)
        premier = x_bougie(d, 0, 50)
        dernier = x_bougie(d, 49, 50)
        assert d.gauche < premier < dernier < d.largeur - d.droite

    def test_le_corps_ne_depasse_jamais_le_pas(self):
        d = disposition(400, 600)
        assert largeur_corps(d, 300) <= pas_horizontal(d, 300)

    def test_le_corps_reste_visible_meme_tres_serre(self):
        d = disposition(400, 600)
        assert largeur_corps(d, 5000) >= 1.0

    def test_le_corps_ne_devient_pas_un_pave(self):
        d = disposition(400, 600)
        assert largeur_corps(d, 3) <= 9.0


class TestAxe:
    def test_les_graduations_vont_du_bas_vers_le_haut(self):
        echelle = echelle_prix(bougies(), panneau=Panneau(0, 100))
        valeurs = etiquettes_prix(echelle, 4)
        assert len(valeurs) == 5
        assert valeurs == sorted(valeurs)

    def test_une_graduation_trop_proche_d_une_etiquette_se_tait(self):
        assert doit_cacher(100.0, [105.0]) is True
        assert doit_cacher(100.0, [140.0]) is False

    def test_sans_etiquette_rien_ne_se_cache(self):
        assert doit_cacher(100.0, []) is False
