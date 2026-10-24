"""Génère l'icône et les écrans d'accueil de l'application, sans dépendance.

Pourquoi un script plutôt qu'un fichier binaire déposé une fois : l'icône suit
la palette de `coeur/config.py`. Changer la couleur d'accent et relancer suffit,
au lieu d'ouvrir un éditeur d'images et d'espérer retomber sur la même teinte.

    python app/scripts/icone.py

Écrit `app/src/assets/icon.png`, `splash.png` et `splash_dark.png`. Ces trois
noms sont ceux que `flet build` cherche ; il met l'icône de Flet à la place
quand ils manquent.

Tout est en Python pur (`zlib` et `struct`) parce que Pillow n'est pas une
dépendance de ce projet et n'a aucune raison de le devenir pour trois images.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "src"))

from coeur import config  # noqa: E402

# Deux échantillons par axe : les bords arrondis et les mèches des bougies sont
# lissés sans bibliothèque de dessin.
SUR_ECHANTILLON = 2


def _couleur(hexa: str, alpha: int = 255) -> tuple:
    hexa = hexa.lstrip("#")
    return (int(hexa[0:2], 16), int(hexa[2:4], 16), int(hexa[4:6], 16), alpha)


def ecrire_png(chemin: Path, largeur: int, hauteur: int, pixels: list) -> None:
    """Un PNG RGBA sans filtre, le format le plus simple qui soit valide."""
    lignes = bytearray()
    for y in range(hauteur):
        lignes.append(0)  # type de filtre : aucun
        debut = y * largeur
        for pixel in pixels[debut : debut + largeur]:
            lignes.extend(pixel)

    def bloc(nom: bytes, donnees: bytes) -> bytes:
        entete = nom + donnees
        return (
            struct.pack(">I", len(donnees))
            + entete
            + struct.pack(">I", zlib.crc32(entete) & 0xFFFFFFFF)
        )

    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 6, 0, 0, 0))
        + bloc(b"IDAT", zlib.compress(bytes(lignes), 9))
        + bloc(b"IEND", b"")
    )


class Toile:
    """Un dessin en sur-échantillonnage, réduit à la fin."""

    def __init__(self, cote: int, fond=(0, 0, 0, 0)):
        self.cote = cote * SUR_ECHANTILLON
        self.echelle = SUR_ECHANTILLON
        self.cible = cote
        self.points = [fond] * (self.cote * self.cote)

    def rectangle(self, x: float, y: float, largeur: float, hauteur: float, couleur) -> None:
        e = self.echelle
        x0, y0 = int(x * e), int(y * e)
        x1, y1 = int((x + largeur) * e), int((y + hauteur) * e)
        for ligne in range(max(0, y0), min(self.cote, y1)):
            base = ligne * self.cote
            for colonne in range(max(0, x0), min(self.cote, x1)):
                self.points[base + colonne] = couleur

    def carre_arrondi(self, rayon: float, couleur) -> None:
        """Le fond de l'icône : un carré aux coins adoucis, hors-champ transparent."""
        e = self.echelle
        r = rayon * e
        for y in range(self.cote):
            for x in range(self.cote):
                dx = max(r - x, x - (self.cote - 1 - r), 0.0)
                dy = max(r - y, y - (self.cote - 1 - r), 0.0)
                if dx * dx + dy * dy <= r * r:
                    self.points[y * self.cote + x] = couleur

    def reduire(self) -> list:
        """Moyenne des blocs : c'est tout le lissage dont ces images ont besoin."""
        e = self.echelle
        sortie = []
        for y in range(self.cible):
            for x in range(self.cible):
                somme = [0, 0, 0, 0]
                for sy in range(e):
                    base = (y * e + sy) * self.cote + x * e
                    for sx in range(e):
                        pixel = self.points[base + sx]
                        for canal in range(4):
                            somme[canal] += pixel[canal]
                total = e * e
                sortie.append(bytes(valeur // total for valeur in somme))
        return sortie


def bougie(toile: Toile, x: float, largeur: float, haut: float, bas: float,
           corps_haut: float, corps_bas: float, couleur) -> None:
    """Une bougie : mèche fine, corps épais. Comme sur le graphique."""
    meche = max(1.0, largeur * 0.18)
    toile.rectangle(x + largeur / 2 - meche / 2, haut, meche, bas - haut, couleur)
    toile.rectangle(x, corps_haut, largeur, max(2.0, corps_bas - corps_haut), couleur)


def icone(cote: int = 512) -> list:
    """Trois bougies, la décisive en doré. Lisible jusqu'à 48 points.

    Pas de texte, pas de dégradé : à la taille d'un lanceur d'application, un
    sigle de trois lettres devient une tache.
    """
    toile = Toile(cote)
    u = cote / 100.0  # unités : tout se place en pourcentage du côté
    toile.carre_arrondi(22 * u, _couleur(config.COULEUR_SURFACE))

    largeur = 13 * u
    bougie(toile, 20 * u, largeur, 46 * u, 78 * u, 55 * u, 72 * u,
           _couleur(config.COULEUR_BAISSE))
    bougie(toile, 43 * u, largeur, 22 * u, 72 * u, 30 * u, 62 * u,
           _couleur(config.COULEUR_PRIMAIRE))
    bougie(toile, 66 * u, largeur, 30 * u, 66 * u, 36 * u, 52 * u,
           _couleur(config.COULEUR_HAUSSE))
    return toile.reduire()


def accueil(cote: int = 512, sombre: bool = True) -> list:
    """L'écran d'accueil : la même marque, sur le fond de l'application.

    Le premier lancement extrait la bibliothèque standard et le code ; c'est
    long, et un écran vide pendant ce temps-là ressemble à une panne.
    """
    toile = Toile(cote, _couleur(config.COULEUR_FOND if sombre else "#f4f1ea"))
    u = cote / 100.0
    largeur = 9 * u
    bougie(toile, 30 * u, largeur, 40 * u, 62 * u, 45 * u, 58 * u,
           _couleur(config.COULEUR_BAISSE))
    bougie(toile, 45 * u, largeur, 32 * u, 60 * u, 36 * u, 54 * u,
           _couleur(config.COULEUR_PRIMAIRE))
    bougie(toile, 60 * u, largeur, 36 * u, 58 * u, 40 * u, 48 * u,
           _couleur(config.COULEUR_HAUSSE))
    return toile.reduire()


def main() -> int:
    assets = RACINE / "src" / "assets"
    ecrire_png(assets / "icon.png", 512, 512, icone(512))
    # `splash.png` sert en thème clair, `splash_dark.png` en thème sombre.
    # Les écrire identiques ferait clignoter un fond noir sur un téléphone
    # réglé en clair, juste avant une interface qui, elle, est sombre.
    ecrire_png(assets / "splash.png", 512, 512, accueil(512, sombre=False))
    ecrire_png(assets / "splash_dark.png", 512, 512, accueil(512, sombre=True))
    print(f"icon.png, splash.png et splash_dark.png écrits dans {assets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
