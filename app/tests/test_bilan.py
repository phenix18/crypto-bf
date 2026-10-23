"""Le bilan d'un historique.

Ce module ne fait que compter, et c'est exactement pour ça qu'il mérite des
tests : les deux dénominateurs — tranchés pour la réussite, tous pour
l'espérance — sont faciles à confondre, et les confondre flatte les chiffres.
"""

import math

from coeur.bilan import resume


def trade(issue, r=None, **extra):
    ligne = {"outcome": issue, "r_multiple": r}
    ligne.update(extra)
    return ligne


class TestComptage:
    def test_un_historique_vide_ne_prononce_rien(self):
        bilan = resume([])
        assert bilan.total == 0
        # None, pas 0 % : « aucun trade » n'est pas « que des pertes ».
        assert bilan.taux is None
        assert bilan.esperance_r is None
        assert bilan.meilleur is None

    def test_les_issues_sont_comptees_separement(self):
        bilan = resume([trade("win", 2.0), trade("loss", -1.0), trade("scratch", 0.0)])
        assert (bilan.gains, bilan.pertes, bilan.neutres) == (1, 1, 1)
        assert bilan.total == 3

    def test_une_issue_inconnue_compte_dans_le_total_sans_fausser_le_taux(self):
        bilan = resume([trade("win", 1.0), trade("annule", 0.0)])
        assert bilan.total == 2
        assert bilan.gains == 1 and bilan.pertes == 0
        assert bilan.taux == 100.0


class TestDenominateurs:
    def test_le_taux_ignore_les_neutres(self):
        # Un trade sorti à zéro n'a ni gagné ni perdu. Le compter comme une
        # perte rendrait un robot prudent artificiellement mauvais.
        bilan = resume([trade("win", 1.0), trade("loss", -1.0), trade("scratch", 0.0)])
        assert bilan.taux == 50.0

    def test_l_esperance_compte_les_neutres(self):
        # L'argent, lui, voit ces trades-là. Même définition que
        # calibration.summarise, pour que l'écran et le portail parlent du
        # même R.
        bilan = resume([trade("win", 2.0), trade("loss", -1.0), trade("scratch", 0.0)])
        assert math.isclose(bilan.somme_r, 1.0)
        assert math.isclose(bilan.esperance_r, 1.0 / 3)

    def test_un_taux_parfait_peut_accompagner_une_esperance_negative(self):
        # Le cas qu'il faut pouvoir afficher : deux petits gains, une grosse
        # perte neutre au sens de l'issue. Un écran qui ne montre que le taux
        # raconterait une stratégie gagnante.
        bilan = resume([trade("win", 0.2), trade("win", 0.2), trade("scratch", -3.0)])
        assert bilan.taux == 100.0
        assert bilan.esperance_r < 0


class TestLignesAbimees:
    def test_un_r_manquant_ne_vaut_pas_zero(self):
        # Ajouter un zéro à la place d'un R absent améliorerait l'espérance
        # affichée. Le trade compte dans son issue, pas dans les R.
        bilan = resume([trade("win", 3.0), trade("loss", None)])
        assert bilan.total == 2
        assert bilan.pertes == 1
        assert math.isclose(bilan.esperance_r, 3.0)

    def test_un_r_illisible_est_ecarte(self):
        bilan = resume([trade("win", "beaucoup"), trade("win", 1.5)])
        assert math.isclose(bilan.somme_r, 1.5)

    def test_un_nan_est_ecarte(self):
        bilan = resume([trade("win", float("nan")), trade("win", 2.0)])
        assert math.isclose(bilan.esperance_r, 2.0)

    def test_une_ligne_vide_ne_fait_pas_planter(self):
        assert resume([None, {}]).total == 2

    def test_les_extremes_sont_les_vrais_extremes(self):
        bilan = resume([trade("win", 4.0), trade("loss", -1.5), trade("scratch", 0.1)])
        assert bilan.meilleur == 4.0
        assert bilan.pire == -1.5
