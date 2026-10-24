"""Le résumé d'un historique de trades, calculé sans interface.

Deux dénominateurs différents, et c'est volontaire, parce que les confondre est
la façon la plus courante de se raconter une histoire :

- le **taux de réussite** se compte sur les trades tranchés (gain ou perte).
  Un trade sorti à zéro n'a ni gagné ni perdu, et le compter comme une perte
  rend un robot prudent artificiellement mauvais ;
- l'**espérance** se compte sur *tous* les trades, neutres compris, parce que
  l'argent, lui, voit bien ces trades-là. C'est la même définition que
  `calibration.summarise`, pour que l'écran et le portail parlent du même R.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Bilan:
    """Ce qu'un historique dit, en huit nombres."""

    total: int = 0
    gains: int = 0
    pertes: int = 0
    neutres: int = 0
    # None plutôt que 0 quand rien n'a été tranché : « 0 % de réussite » et
    # « pas encore de trade tranché » ne se lisent pas pareil.
    taux: Optional[float] = None
    somme_r: float = 0.0
    esperance_r: Optional[float] = None
    meilleur: Optional[float] = None
    pire: Optional[float] = None


def _reel(valeur) -> Optional[float]:
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return None
    return None if nombre != nombre else nombre  # écarte les NaN


def resume(trades: Iterable[dict]) -> Bilan:
    """Replie l'historique. Tolère les lignes incomplètes sans rien inventer.

    Une ligne sans `r_multiple` compte dans son issue mais pas dans les R :
    additionner un zéro à sa place améliorerait l'espérance affichée, ce qui
    est exactement le genre de mensonge qu'on ne veut pas ici.
    """
    gains = pertes = neutres = 0
    multiples: list = []
    total = 0

    for trade in trades or []:
        total += 1
        issue = str((trade or {}).get("outcome") or "").lower()
        if issue == "win":
            gains += 1
        elif issue == "loss":
            pertes += 1
        elif issue == "scratch":
            neutres += 1
        multiple = _reel((trade or {}).get("r_multiple"))
        if multiple is not None:
            multiples.append(multiple)

    tranches = gains + pertes
    return Bilan(
        total=total,
        gains=gains,
        pertes=pertes,
        neutres=neutres,
        taux=(gains / tranches * 100.0) if tranches else None,
        somme_r=sum(multiples),
        esperance_r=(sum(multiples) / len(multiples)) if multiples else None,
        meilleur=max(multiples) if multiples else None,
        pire=min(multiples) if multiples else None,
    )
