"""Constantes de l'application : nom, version, palette, réglages par défaut.

Aucun import de flet ici. Ce module est lu par l'interface comme par les
tests, et il doit rester lisible sans lancer quoi que ce soit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NOM = "AI-Trader"
APP_VERSION = "1.0.0"
APP_ORG = "com.aitrader"

# Palette reprise de l'interface web, pour que les deux se ressemblent : fond
# sombre, accent ambre, vert et rouge de marché. Le vert et le rouge ne portent
# jamais le sens tout seuls — un mot les accompagne toujours.
COULEUR_PRIMAIRE = "#d4a458"
COULEUR_HAUSSE = "#16c784"
COULEUR_BAISSE = "#ea3943"
COULEUR_NEUTRE = "#64748b"
COULEUR_FOND = "#070b10"
COULEUR_SURFACE = "#0f171e"
COULEUR_BORDURE = "#2a3540"
COULEUR_TEXTE = "#f2efe6"
COULEUR_TEXTE_DOUX = "#a3afb8"

INTERVALLES = ("1m", "5m", "15m", "1h", "4h")

# D'où viennent les bougies. « synthetic » est un flux généré, utile pour
# essayer l'application sans réseau — jamais pour juger une stratégie. Le choix
# est explicite : substituer des prix inventés au marché parce qu'il est
# injoignable serait le genre de silence que tout le reste du projet évite.
SOURCES = ("binance", "synthetic")

# Deux modes, décrits dans docs/APP_PC_APK_FR.md §2.
MODE_LOCAL = "local"
MODE_RELIE = "relie"
MODES = (MODE_LOCAL, MODE_RELIE)


def dossier_donnees(environnement=None, maison=None) -> Path:
    """Où l'application écrit sa base et ses réglages.

    Sur Android et sur les paquets de bureau, Flet pose `FLET_APP_STORAGE_DATA`
    sur un dossier privé que le système sauvegarde et nettoie avec
    l'application ; c'est là que les données doivent aller. En développement
    cette variable n'existe pas, et un dossier dans le répertoire personnel
    évite d'écrire une base dans le dépôt au premier lancement.
    """
    environnement = os.environ if environnement is None else environnement
    depuis_flet = (environnement.get("FLET_APP_STORAGE_DATA") or "").strip()
    if depuis_flet:
        return Path(depuis_flet)
    maison = Path.home() if maison is None else Path(maison)
    return maison / ".aitrader"


@dataclass
class Reglages:
    """Ce que l'utilisateur peut changer, et ce qui est repris au démarrage."""

    mode: str = MODE_LOCAL
    source: str = "binance"
    url_serveur: str = "http://127.0.0.1:8000"
    watchlist: list = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    intervalle: str = "5m"
    # 60 s et non 15 : sur téléphone, un rafraîchissement toutes les 15 s vide
    # la batterie pour une information qui change rarement autant.
    rafraichissement_s: int = 60
    capital_papier: float = 10_000.0
    seuil_confluence: float = 65.0
    testnet: bool = True
    theme_sombre: bool = True

    def valide(self) -> "Reglages":
        """Ramène les valeurs aberrantes dans leurs bornes, sans rien refuser.

        Des réglages viennent d'un fichier que l'utilisateur peut éditer ou
        qu'une version précédente a écrit. Planter au démarrage à cause d'un
        nombre hors bornes serait la pire réponse possible.
        """
        if self.mode not in MODES:
            self.mode = MODE_LOCAL
        if self.source not in SOURCES:
            self.source = "binance"
        if self.intervalle not in INTERVALLES:
            self.intervalle = "5m"
        self.rafraichissement_s = max(15, min(int(self.rafraichissement_s), 3600))
        self.capital_papier = max(1.0, float(self.capital_papier))
        self.seuil_confluence = max(0.0, min(float(self.seuil_confluence), 100.0))
        self.watchlist = [s.strip().upper() for s in self.watchlist if str(s).strip()] or [
            "BTCUSDT"
        ]
        return self

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "source": self.source,
            "url_serveur": self.url_serveur,
            "watchlist": list(self.watchlist),
            "intervalle": self.intervalle,
            "rafraichissement_s": self.rafraichissement_s,
            "capital_papier": self.capital_papier,
            "seuil_confluence": self.seuil_confluence,
            "testnet": self.testnet,
            "theme_sombre": self.theme_sombre,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Reglages":
        connus = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in connus}).valide()
