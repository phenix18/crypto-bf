"""Lecture des champs texte du formulaire de réglages.

Ces fonctions existent parce qu'un champ de saisie renvoie toujours une chaîne,
parfois vide, parfois avec une virgule décimale française, et qu'un réglage mal
lu se paie plus tard : un capital à zéro, c'est un moteur qui refuse tout sans
expliquer pourquoi.

La règle est la même partout : une saisie illisible garde la valeur précédente.
Jamais de zéro par défaut.
"""

from __future__ import annotations

from typing import Optional


def liste_symboles(texte: Optional[str]) -> list:
    """« btcusdt, eth usdt ; SOLUSDT » -> ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'].

    Virgule, point-virgule, saut de ligne et espace séparent tous. Les doublons
    disparaissent en gardant l'ordre de saisie : une liste surveillée où un
    symbole figure deux fois le ferait analyser deux fois.
    """
    brut = (texte or "").replace(";", ",").replace("\n", ",")
    symboles = []
    for morceau in brut.split(","):
        symbole = morceau.replace(" ", "").upper()
        if symbole and symbole not in symboles:
            symboles.append(symbole)
    return symboles


def nombre_saisi(texte: Optional[str], defaut: float) -> float:
    """Lit un nombre tapé à la main, virgule française comprise."""
    if texte is None:
        return defaut
    nettoye = str(texte).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not nettoye:
        return defaut
    try:
        valeur = float(nettoye)
    except ValueError:
        return defaut
    return defaut if valeur != valeur else valeur  # NaN -> on garde l'ancien
