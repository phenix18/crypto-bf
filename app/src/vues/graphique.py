"""Le dessin du graphique sur un canvas Flet.

La géométrie est dans `coeur.graphique`, qui ne connaît pas Flet et se teste
sans interface. Ici il ne reste que la traduction en formes et en couleurs.

Les niveaux de la position — entrée, stop, cible — sont dessinés sur la même
échelle que les bougies, et l'étiquette de l'entrée s'efface quand les trois
se chevauchent : un nombre à moitié recouvert est pire qu'un nombre absent.
"""

from __future__ import annotations

from typing import Optional, Sequence

import flet as ft
import flet.canvas as cv

from coeur import config, formats
from coeur.graphique import (
    Echelle,
    Panneau,
    disposition,
    doit_cacher,
    echelle_prix,
    etiquettes_prix,
    extremes,
    largeur_corps,
    x_bougie,
)

COULEUR_GRILLE = "#1b2733"
COULEUR_AXE = "#6b7a88"
COULEUR_EMA_RAPIDE = "#38bdf8"
COULEUR_EMA_LENTE = "#a78bfa"
COULEUR_EMA_FOND = "#f59e0b"
COULEUR_BANDE = "#60a5fa"
COULEUR_RSI = "#f472b6"


def _trait(couleur: str, epaisseur: float = 1.0) -> ft.Paint:
    return ft.Paint(color=couleur, stroke_width=epaisseur, style=ft.PaintingStyle.STROKE)


def _plein(couleur: str) -> ft.Paint:
    return ft.Paint(color=couleur, style=ft.PaintingStyle.FILL)


def _courbe(serie: Sequence[Optional[float]], disp, echelle: Echelle, couleur: str,
            nombre: int, epaisseur: float = 1.4) -> list:
    """Une série, coupée là où l'indicateur n'avait pas encore de lecture."""
    formes = []
    precedent = None
    for index, valeur in enumerate(serie or []):
        if valeur is None or valeur != valeur:
            precedent = None
            continue
        point = (x_bougie(disp, index, nombre), echelle.y(valeur))
        if precedent is not None:
            formes.append(
                cv.Line(precedent[0], precedent[1], point[0], point[1],
                        paint=_trait(couleur, epaisseur))
            )
        precedent = point
    return formes


def _niveau(disp, y: float, couleur: str, etiquette: str | None) -> list:
    formes = [cv.Line(disp.gauche, y, disp.largeur - disp.droite, y, paint=_trait(couleur, 1.0))]
    if etiquette:
        formes.append(
            cv.Text(disp.largeur - disp.droite + 4, y - 7, etiquette,
                    ft.TextStyle(size=10, color=couleur, weight=ft.FontWeight.BOLD))
        )
    return formes


def formes(payload: dict, largeur: float, hauteur: float) -> list:
    """Toutes les formes du graphique, prêtes pour un `cv.Canvas`."""
    bougies = payload.get("candles") or []
    if not bougies:
        return [
            cv.Text(16, hauteur / 2, "Aucune donnée",
                    ft.TextStyle(size=13, color=config.COULEUR_TEXTE_DOUX))
        ]

    disp = disposition(largeur, hauteur)
    nombre = len(bougies)
    series = payload.get("indicators") or {}
    proposition = payload.get("proposal") or {}
    niveaux = [proposition.get("entry"), proposition.get("stop_loss"), proposition.get("take_profit")]

    echelle = echelle_prix(bougies, series, niveaux, disp.prix)
    corps = largeur_corps(disp, nombre)
    dessins: list = []

    # Les étiquettes de la position réservent leur place avant les graduations.
    reserves = [echelle.y(v) for v in niveaux if isinstance(v, (int, float))]
    for valeur in etiquettes_prix(echelle):
        y = echelle.y(valeur)
        dessins.append(cv.Line(disp.gauche, y, disp.largeur - disp.droite, y,
                               paint=_trait(COULEUR_GRILLE, 1.0)))
        if not doit_cacher(y, reserves):
            dessins.append(
                cv.Text(disp.largeur - disp.droite + 4, y - 6, formats.prix(valeur),
                        ft.TextStyle(size=9, color=COULEUR_AXE))
            )

    for nom in ("bb_upper", "bb_lower"):
        dessins.extend(_courbe(series.get(nom), disp, echelle, COULEUR_BANDE, nombre, 1.0))

    # Les bougies
    for index, bougie in enumerate(bougies):
        ouverture = float(bougie.get("o") or 0.0)
        cloture = float(bougie.get("c") or 0.0)
        hausse = cloture >= ouverture
        couleur = config.COULEUR_HAUSSE if hausse else config.COULEUR_BAISSE
        x = x_bougie(disp, index, nombre)
        dessins.append(
            cv.Line(x, echelle.y(float(bougie.get("h") or 0.0)),
                    x, echelle.y(float(bougie.get("l") or 0.0)), paint=_trait(couleur, 1.0))
        )
        haut = min(echelle.y(ouverture), echelle.y(cloture))
        epaisseur = max(1.0, abs(echelle.y(cloture) - echelle.y(ouverture)))
        dessins.append(
            cv.Rect(x - corps / 2, haut, corps, epaisseur, paint=_plein(couleur))
        )

    # Les moyennes mobiles, par-dessus les bougies
    for nom, couleur in (
        ("ema_fast", COULEUR_EMA_RAPIDE),
        ("ema_slow", COULEUR_EMA_LENTE),
        ("ema_trend", COULEUR_EMA_FOND),
    ):
        dessins.extend(_courbe(series.get(nom), disp, echelle, couleur, nombre, 1.5))

    # La position : entrée discrète, stop et cible en évidence
    entree, stop, cible = niveaux
    etiquettes_prises = []
    if isinstance(stop, (int, float)):
        y = echelle.y(stop)
        etiquettes_prises.append(y)
        dessins.extend(_niveau(disp, y, config.COULEUR_BAISSE, f"SL {formats.prix(stop)}"))
    if isinstance(cible, (int, float)):
        y = echelle.y(cible)
        etiquettes_prises.append(y)
        dessins.extend(_niveau(disp, y, config.COULEUR_HAUSSE, f"TP {formats.prix(cible)}"))
    if isinstance(entree, (int, float)):
        y = echelle.y(entree)
        # L'étiquette de l'entrée cède la place : le stop et la cible comptent
        # davantage, et trois nombres superposés n'en font lire aucun.
        serre = doit_cacher(y, etiquettes_prises, marge=17.0)
        dessins.extend(
            _niveau(disp, y, config.COULEUR_TEXTE, None if serre else formats.prix(entree))
        )

    # Le volume
    volumes = [float(b.get("v") or 0.0) for b in bougies]
    _, volume_haut = extremes(volumes)
    echelle_volume = Echelle(0.0, volume_haut, disp.volume)
    for index, bougie in enumerate(bougies):
        hausse = float(bougie.get("c") or 0.0) >= float(bougie.get("o") or 0.0)
        couleur = config.COULEUR_HAUSSE if hausse else config.COULEUR_BAISSE
        y = echelle_volume.y(float(bougie.get("v") or 0.0))
        dessins.append(
            cv.Rect(x_bougie(disp, index, nombre) - corps / 2, y, corps,
                    max(1.0, disp.volume.bas - y),
                    paint=ft.Paint(color=ft.Colors.with_opacity(0.35, couleur),
                                   style=ft.PaintingStyle.FILL))
        )

    # Le RSI, avec les bornes qui le rendent lisible d'un coup d'oeil
    echelle_rsi = Echelle(0.0, 100.0, disp.rsi)
    for borne, couleur in ((70.0, config.COULEUR_BAISSE), (30.0, config.COULEUR_HAUSSE)):
        y = echelle_rsi.y(borne)
        dessins.append(cv.Line(disp.gauche, y, disp.largeur - disp.droite, y,
                               paint=_trait(ft.Colors.with_opacity(0.5, couleur), 1.0)))
        dessins.append(
            cv.Text(disp.largeur - disp.droite + 4, y - 6, f"{borne:.0f}",
                    ft.TextStyle(size=9, color=COULEUR_AXE))
        )
    dessins.extend(_courbe(series.get("rsi"), disp, echelle_rsi, COULEUR_RSI, nombre, 1.6))
    dessins.append(
        cv.Text(disp.gauche + 2, disp.rsi.haut - 2, "RSI",
                ft.TextStyle(size=9, color=COULEUR_AXE))
    )

    return dessins


def canevas(payload: dict, largeur: float, hauteur: float) -> cv.Canvas:
    """Le graphique complet, prêt à être posé dans une vue."""
    return cv.Canvas(shapes=formes(payload, largeur, hauteur), expand=True)
