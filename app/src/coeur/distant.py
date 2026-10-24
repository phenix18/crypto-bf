"""Client de l'API /api/auto/* d'un serveur AI-Trader.

`urllib` et rien d'autre : ajouter une dépendance HTTP ici coûterait une roue
Android pour ce que la bibliothèque standard fait déjà.

Les erreurs remontent en `ErreurDistante` avec le message du serveur quand il
y en a un. Un 409 « auto-trading is off » et un 503 « feed injoignable » ne
doivent pas se présenter pareil à l'écran : l'un se règle dans la
configuration, l'autre en attendant.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


class ErreurDistante(RuntimeError):
    """Le serveur a refusé, ou n'a pas répondu."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code

    @property
    def est_desarme(self) -> bool:
        """409 : le trading automatique est éteint côté serveur."""
        return self.code == 409

    @property
    def est_injoignable(self) -> bool:
        """503 ou pas de réponse : la donnée de marché manque."""
        return self.code in (503, None)


@dataclass
class Distant:
    base_url: str = "http://127.0.0.1:8000"
    timeout: float = 15.0

    def _url(self, chemin: str, params: Optional[dict] = None) -> str:
        url = f"{self.base_url.rstrip('/')}{chemin}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return url

    def _appel(self, chemin: str, params=None, corps: Optional[dict] = None) -> dict:
        donnees = None if corps is None else json.dumps(corps).encode("utf-8")
        requete = urllib.request.Request(
            self._url(chemin, params),
            data=donnees,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST" if corps is not None else "GET",
        )
        try:
            with urllib.request.urlopen(requete, timeout=self.timeout) as reponse:
                return json.loads(reponse.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            texte = exc.read().decode("utf-8", "replace")
            detail = texte
            try:
                detail = json.loads(texte).get("detail", texte)
            except json.JSONDecodeError:
                pass
            raise ErreurDistante(str(detail)[:300], exc.code) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ErreurDistante(f"serveur injoignable : {exc}") from exc

    # -- lectures ---------------------------------------------------------

    def etat(self) -> dict:
        return self._appel("/api/auto/status")

    def graphique(self, symbole: str, intervalle: str, limite: int = 0) -> dict:
        params = {"symbol": symbole, "interval": intervalle}
        if limite:
            params["limit"] = limite
        return self._appel("/api/auto/chart", params)

    def calibration(self) -> dict:
        return self._appel("/api/auto/calibration")

    def journal(self, limite: int = 50) -> dict:
        return self._appel("/api/auto/audit", {"limit": limite})

    # -- écritures --------------------------------------------------------

    def analyser(self, symbole: str, intervalle: str) -> dict:
        return self._appel("/api/auto/analyse", corps={"symbol": symbole, "interval": intervalle})

    def trader(self, symbole: str, intervalle: str) -> dict:
        return self._appel("/api/auto/trade", corps={"symbol": symbole, "interval": intervalle})

    def fermer(self, symbole: str, prix: float) -> dict:
        return self._appel("/api/auto/close", corps={"symbol": symbole, "price": prix})

    def coupe_circuit(self, engage: bool) -> dict:
        corps = {"engaged": engage}
        if not engage:
            # Le serveur exige cette confirmation pour relâcher la halte.
            corps["confirm"] = "release"
        return self._appel("/api/auto/kill-switch", corps=corps)
