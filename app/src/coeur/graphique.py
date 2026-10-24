"""La géométrie du graphique : où va chaque chose, en pixels.

Séparée du dessin pour la même raison que dans l'interface web : c'est ici que
vivent les bugs, et ici que les tests peuvent les attraper sans lancer la
moindre interface. Ce module ne connaît ni Flet, ni canvas, ni couleur — il
rend des nombres.

Le piège qu'il existe pour éviter : une échelle de prix calculée sans tenir
compte du stop ou de la cible dessine un niveau hors de l'écran. Un stop que
personne ne voit est un stop que personne ne vérifie.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Panneau:
    """Une bande horizontale du graphique."""

    haut: float
    hauteur: float

    @property
    def bas(self) -> float:
        return self.haut + self.hauteur


@dataclass(frozen=True)
class Disposition:
    largeur: float
    hauteur: float
    gauche: float
    droite: float
    prix: Panneau
    volume: Panneau
    rsi: Panneau

    @property
    def largeur_trace(self) -> float:
        return max(1.0, self.largeur - self.gauche - self.droite)


def disposition(largeur: float, hauteur: float, *, gauche: float = 6.0, droite: float = 58.0) -> Disposition:
    """Trois bandes : prix, volume, RSI. Le reste tiendrait mal sur un téléphone."""
    utile = max(60.0, hauteur - 16.0)
    prix = Panneau(8.0, utile * 0.66)
    volume = Panneau(prix.bas + 6.0, utile * 0.10)
    rsi = Panneau(volume.bas + 10.0, utile * 0.18)
    return Disposition(largeur, hauteur, gauche, droite, prix, volume, rsi)


@dataclass(frozen=True)
class Echelle:
    """Convertit une valeur en ordonnée, dans un panneau donné."""

    bas: float
    haut: float
    panneau: Panneau

    def y(self, valeur: float) -> float:
        etendue = self.haut - self.bas or 1.0
        part = (valeur - self.bas) / etendue
        return self.panneau.bas - part * self.panneau.hauteur


def extremes(valeurs: Sequence[Optional[float]]) -> tuple:
    """Minimum et maximum, en ignorant les trous de chauffe des indicateurs."""
    propres = [v for v in valeurs if v is not None and v == v and v not in (float("inf"), float("-inf"))]
    if not propres:
        return (0.0, 1.0)
    bas, haut = min(propres), max(propres)
    if bas == haut:
        return (bas * 0.999, haut * 1.001)
    return (bas, haut)


def echelle_prix(
    bougies: Sequence[dict],
    series: Optional[dict] = None,
    niveaux: Sequence[Optional[float]] = (),
    panneau: Optional[Panneau] = None,
    marge: float = 0.06,
) -> Echelle:
    """L'échelle du panneau des prix, **niveaux de la position compris**.

    Les bornes incluent l'entrée, le stop et la cible : les laisser de côté
    serait dessiner une position dont on ne voit pas la sortie.
    """
    valeurs: list = []
    for bougie in bougies:
        valeurs.extend((bougie.get("h"), bougie.get("l")))
    for nom in ("bb_upper", "bb_lower", "ema_trend"):
        valeurs.extend((series or {}).get(nom) or [])
    valeurs.extend(niveaux)

    bas, haut = extremes(valeurs)
    souffle = (haut - bas) * marge
    return Echelle(bas - souffle, haut + souffle, panneau or Panneau(8.0, 100.0))


def pas_horizontal(disp: Disposition, nombre: int) -> float:
    return disp.largeur_trace / max(1, nombre)


def x_bougie(disp: Disposition, index: int, nombre: int) -> float:
    """Le centre de la bougie d'indice donné."""
    return disp.gauche + pas_horizontal(disp, nombre) * (index + 0.5)


def largeur_corps(disp: Disposition, nombre: int) -> float:
    """Assez large pour se voir, assez étroit pour ne pas se chevaucher."""
    return max(1.0, min(9.0, pas_horizontal(disp, nombre) * 0.62))


def etiquettes_prix(echelle: Echelle, combien: int = 4) -> list:
    """Les valeurs de l'axe, du bas vers le haut."""
    etendue = echelle.haut - echelle.bas
    return [echelle.bas + etendue * i / combien for i in range(combien + 1)]


def doit_cacher(y: float, reserves: Sequence[float], marge: float = 11.0) -> bool:
    """Une graduation trop proche d'une étiquette de position se tait.

    Un nombre à moitié recouvert est pire qu'un nombre absent : le lecteur ne
    sait plus à quel niveau il appartient.
    """
    return any(abs(y - prise) < marge for prise in reserves)
