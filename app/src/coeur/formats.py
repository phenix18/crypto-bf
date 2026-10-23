"""Mise en forme des nombres, des dates et des verdicts, en français.

Séparé de l'interface parce que c'est testable et que ça se trompe facilement :
un R affiché sans signe, un pourcentage arrondi au mauvais endroit, et le
lecteur se fait une idée fausse de sa position.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

ESPACE_FINE = " "  # espace insécable étroite, séparateur de milliers français


def nombre(valeur: Optional[float], decimales: int = 2) -> str:
    """1234.5 -> '1 234,50'. Virgule décimale, espace fine pour les milliers."""
    if valeur is None or valeur != valeur:  # None ou NaN
        return "—"
    try:
        texte = f"{float(valeur):,.{decimales}f}"
    except (TypeError, ValueError, OverflowError):
        return "—"
    return texte.replace(",", ESPACE_FINE).replace(".", ",")


def signe(valeur: Optional[float], decimales: int = 2) -> str:
    """Toujours un signe explicite : '+12,00' ou '−3,50'.

    Le plus est là exprès. « 12,00 » et « +12,00 » se lisent pareil une fois
    qu'on sait, mais pas au premier coup d'œil sur un écran de gains et pertes.
    """
    if valeur is None or valeur != valeur:
        return "—"
    prefixe = "+" if valeur >= 0 else "−"  # moins typographique, pas un tiret
    return f"{prefixe}{nombre(abs(valeur), decimales)}"


def pourcent(valeur: Optional[float], decimales: int = 2) -> str:
    if valeur is None or valeur != valeur:
        return "—"
    return f"{nombre(valeur, decimales)}{ESPACE_FINE}%"


def multiple_r(valeur: Optional[float]) -> str:
    """Un R se lit signé et à deux décimales : '+2,00 R', '−1,00 R'."""
    if valeur is None or valeur != valeur:
        return "—"
    return f"{signe(valeur, 2)}{ESPACE_FINE}R"


def prix(valeur: Optional[float]) -> str:
    """Autant de décimales que le prix en demande, pas plus."""
    if valeur is None or valeur != valeur:
        return "—"
    absolu = abs(float(valeur))
    if absolu >= 1000:
        return nombre(valeur, 2)
    if absolu >= 1:
        return nombre(valeur, 4)
    return nombre(valeur, 6)


def horodatage(valeur, avec_date: bool = False) -> str:
    """Un ISO 8601, une date ou un entier en millisecondes -> heure locale."""
    moment = _vers_datetime(valeur)
    if moment is None:
        return "—"
    local = moment.astimezone()
    return local.strftime("%d/%m/%Y %H:%M") if avec_date else local.strftime("%H:%M:%S")


def _vers_datetime(valeur) -> Optional[datetime]:
    if valeur is None:
        return None
    if isinstance(valeur, datetime):
        return valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)
    if isinstance(valeur, (int, float)):
        try:
            return datetime.fromtimestamp(float(valeur) / 1000.0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        texte = str(valeur).replace("Z", "+00:00")
        moment = datetime.fromisoformat(texte)
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# Les étages sont les mêmes que dans auto_trader.STAGES ; un test le vérifie.
ETAGES = {
    "strategy": "stratégie",
    "risk": "risque",
    "calibration": "calibration",
    "council": "conseil",
    "approved": "validé",
    "executed": "exécuté",
    "execution": "exécution",
    "position": "position",
    "bar": "bougie",
    "error": "erreur",
}

SENS = {"long": "achat", "short": "vente", "flat": "neutre"}

POSTURES = {"confirm": "confirme", "abstain": "s'abstient", "veto": "s'oppose"}

ROLES = {
    "analyst": "analyste",
    "risk": "risque",
    "historian": "historien",
    "macro": "macro",
}

REGIMES = {
    "trending_up": "tendance haussière",
    "trending_down": "tendance baissière",
    "ranging": "range",
}


def etage(nom: str) -> str:
    """Le nom français d'un étage, ou le nom brut s'il est inconnu.

    Brut plutôt que vide : un étage ajouté au moteur avant sa traduction doit
    rester lisible, pas disparaître de l'écran.
    """
    return ETAGES.get(nom, nom)


def sens(nom: str) -> str:
    return SENS.get(nom, nom)


def posture(nom: str) -> str:
    return POSTURES.get(nom, nom)


def role(nom: str) -> str:
    return ROLES.get(nom, nom)


def regime(nom: str) -> str:
    return REGIMES.get(nom, nom)
