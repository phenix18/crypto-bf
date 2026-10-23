"""L'écran d'analyse : le graphique, le verdict, et ce qui l'a produit.

L'ordre est voulu : le graphique d'abord, puis le verdict en une ligne, puis le
détail des étages qui y ont mené. Un verdict sans ses raisons n'apprend rien,
et des raisons sans verdict ne se lisent pas.

Rien ici ne passe d'ordre. L'analyse est consultative, sur PC comme sur
téléphone, et le bouton qui exécuterait n'existe pas dans cette application.
"""

from __future__ import annotations

import asyncio

import flet as ft

from coeur import config, formats
from vues import communs
from vues.graphique import canevas

HAUTEUR_GRAPHIQUE_MIN = 300.0
HAUTEUR_GRAPHIQUE_MAX = 560.0


def a_conseil(conseil: dict | None) -> bool:
    """Un conseil vide est un dictionnaire vide, et `{}` est faux mais présent.

    Le même piège a fait planter l'interface web : `council` renvoyé vide quand
    aucun modèle n'est configuré, le panneau affiché quand même, et `opinions`
    indéfini au moment du rendu.
    """
    return bool(conseil) and bool(conseil.get("opinions"))


def dimensions_graphique(largeur: float | None, hauteur: float | None) -> tuple:
    """La place qu'on donne au graphique, bornée aux deux bouts.

    Trop plat, les bougies ne se lisent plus ; trop haut sur un téléphone en
    paysage, le verdict passe sous la ligne de flottaison.
    """
    l = max(320.0, float(largeur or 900.0) - 24.0)
    h = float(hauteur or 800.0) * 0.52
    return l, max(HAUTEUR_GRAPHIQUE_MIN, min(h, HAUTEUR_GRAPHIQUE_MAX))


def panneau_verdict(decision: dict) -> ft.Control:
    """Validé ou arrêté, où, et pourquoi — en haut, en clair."""
    proposition = decision.get("proposal") or {}
    valide = bool(decision.get("approved"))
    sens = proposition.get("direction", "flat")
    titre = ft.Row(
        [
            communs.badge(
                "validé" if valide else f"arrêté · {formats.etage(decision.get('stage', ''))}",
                config.COULEUR_HAUSSE if valide else config.COULEUR_NEUTRE,
            ),
            communs.badge(formats.sens(sens), communs.couleur_sens(sens)),
            communs.badge(formats.regime(proposition.get("regime", "")), config.COULEUR_PRIMAIRE),
        ],
        spacing=6,
        wrap=True,
    )
    chiffres = communs.grille_statistiques(
        [
            communs.statistique("confluence", formats.pourcent(proposition.get("confluence"))),
            communs.statistique("participation", formats.pourcent(proposition.get("participation"))),
            communs.statistique("entrée", formats.prix(proposition.get("entry"))),
            communs.statistique(
                "R/R",
                formats.nombre(proposition.get("risk_reward"), 2),
            ),
            communs.statistique("stop", formats.prix(proposition.get("stop_loss")),
                                config.COULEUR_BAISSE),
            communs.statistique("cible", formats.prix(proposition.get("take_profit")),
                                config.COULEUR_HAUSSE),
        ]
    )
    enfants = [titre, chiffres]
    for raison in decision.get("reasons") or []:
        enfants.append(communs.message(raison, "info" if valide else "alerte"))
    for blocage in proposition.get("blockers") or []:
        enfants.append(communs.message(f"Bloqué : {blocage}", "alerte"))
    return communs.carte(
        f"{decision.get('symbol', '—')} · {decision.get('interval', '—')} · "
        f"{formats.horodatage(decision.get('at'))}",
        *enfants,
    )


def panneau_votes(proposition: dict) -> ft.Control:
    votes = proposition.get("votes") or []
    if not votes:
        return communs.carte("Indicateurs", communs.message("Aucune lecture."))
    return communs.carte("Indicateurs", *[communs.ligne_vote(v) for v in votes])


def panneau_risque(risque: dict) -> ft.Control:
    if not risque:
        return communs.carte("Risque", communs.message("Étage non atteint."))
    enfants = [
        communs.grille_statistiques(
            [
                communs.statistique("quantité", formats.nombre(risque.get("quantity"), 6)),
                communs.statistique("notionnel", formats.nombre(risque.get("notional"))),
                communs.statistique("risque engagé", formats.nombre(risque.get("risk_amount"))),
                communs.statistique("halte", str(risque.get("halt") or "none")),
            ]
        )
    ]
    for raison in risque.get("reasons") or []:
        enfants.append(communs.message(raison, "alerte" if not risque.get("approved") else "info"))
    return communs.carte("Risque", *enfants)


def panneau_calibration(calibration: dict) -> ft.Control:
    if not calibration:
        return communs.carte("Calibration", communs.message("Étage non atteint."))
    base = calibration.get("basis") or "—"
    enfants = [
        ft.Row(
            [
                communs.badge(
                    "autorisé" if calibration.get("allowed") else "refusé",
                    config.COULEUR_HAUSSE
                    if calibration.get("allowed")
                    else config.COULEUR_BAISSE,
                ),
                communs.badge(base),
                ft.Text(
                    f"probabilité {formats.pourcent(calibration.get('probability'))}",
                    size=11,
                    color=config.COULEUR_TEXTE_DOUX,
                ),
            ],
            spacing=6,
            wrap=True,
        )
    ]
    for raison in calibration.get("reasons") or []:
        enfants.append(communs.message(raison))
    return communs.carte("Calibration", *enfants)


def panneau_conseil(conseil: dict) -> ft.Control | None:
    """Le conseil, et seulement s'il a réellement siégé."""
    if not a_conseil(conseil):
        return None
    enfants = [
        ft.Row(
            [
                communs.badge(
                    "validé" if conseil.get("approved") else "refusé",
                    config.COULEUR_HAUSSE
                    if conseil.get("approved")
                    else config.COULEUR_BAISSE,
                ),
                ft.Text(
                    f"conviction {formats.pourcent(float(conseil.get('conviction') or 0.0) * 100)}",
                    size=11,
                    color=config.COULEUR_TEXTE_DOUX,
                ),
            ],
            spacing=8,
        )
    ]
    enfants.extend(communs.ligne_avis(avis) for avis in conseil.get("opinions") or [])
    for raison in conseil.get("reasons") or []:
        enfants.append(communs.message(raison))
    return communs.carte("Conseil", *enfants)


class Analyse:
    """Le graphique et le verdict d'un symbole, à la demande."""

    def __init__(self, session, page: ft.Page):
        self.session = session
        self.page = page
        self.symbole = (session.reglages.watchlist or ["BTCUSDT"])[0]
        self.intervalle = session.reglages.intervalle
        self.corps = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.controle = ft.Container(content=self.corps, padding=12, expand=True)
        self._graphique: dict = {}
        self._decision: dict = {}

    def viser(self, symbole: str) -> None:
        """Change de symbole sans charger : le cockpit appelle avant de naviguer."""
        self.symbole = symbole.upper()
        self._graphique = {}
        self._decision = {}

    # -- chargement --------------------------------------------------------

    async def charger(self) -> None:
        self.corps.controls = [self._selecteurs(), communs.message("Lecture du marché…")]
        self.page.update()
        try:
            self._graphique = await asyncio.to_thread(
                self.session.graphique, self.symbole, self.intervalle
            )
        except Exception as exc:  # noqa: BLE001
            self._graphique = {}
            self.corps.controls = [
                self._selecteurs(),
                communs.message(f"Marché illisible : {exc}", "erreur"),
            ]
            self.page.update()
            return
        self._redessiner()

    async def analyser(self) -> None:
        """Le verdict complet, conseil compris. Ne passe aucun ordre."""
        if not self._graphique:
            await self.charger()
        self.corps.controls.append(communs.message("Analyse en cours…"))
        self.page.update()
        try:
            self._decision = await asyncio.to_thread(
                self.session.analyser, self.symbole, self.intervalle
            )
        except Exception as exc:  # noqa: BLE001
            self._decision = {}
            self.corps.controls = [
                self._selecteurs(),
                communs.message(f"Analyse impossible : {exc}", "erreur"),
            ]
            self.page.update()
            return
        self._redessiner()

    def _redessiner(self) -> None:
        largeur, hauteur = dimensions_graphique(self.page.width, self.page.height)
        # Le dessin suit les niveaux du verdict quand il y en a un : c'est le
        # même graphique, mais avec le stop et la cible réellement décidés.
        payload = dict(self._graphique)
        if self._decision.get("proposal"):
            payload["proposal"] = self._decision["proposal"]

        enfants: list = [self._selecteurs()]
        enfants.append(
            communs.carte(
                f"{payload.get('symbol', self.symbole)} · {payload.get('interval', self.intervalle)}"
                f" · {payload.get('source', '—')}",
                ft.Container(
                    content=canevas(payload, largeur, hauteur),
                    height=hauteur,
                    bgcolor=config.COULEUR_FOND,
                    border_radius=10,
                ),
                marge=8,
            )
        )
        if self._decision:
            enfants.append(panneau_verdict(self._decision))
            enfants.append(panneau_votes(self._decision.get("proposal") or {}))
            enfants.append(panneau_risque(self._decision.get("risk") or {}))
            enfants.append(panneau_calibration(self._decision.get("calibration") or {}))
            conseil = panneau_conseil(self._decision.get("council") or {})
            if conseil:
                enfants.append(conseil)
        else:
            enfants.append(panneau_votes((payload.get("proposal") or {})))
            enfants.append(
                communs.message(
                    "« Analyser » déroule les quatre étages : stratégie, risque, "
                    "calibration, conseil."
                )
            )
        self.corps.controls = enfants
        self.page.update()

    # -- barre de sélection ------------------------------------------------

    def _selecteurs(self) -> ft.Control:
        symboles = list(self.session.reglages.watchlist)
        if self.symbole not in symboles:
            symboles.insert(0, self.symbole)
        choix_symbole = ft.Dropdown(
            value=self.symbole,
            options=[ft.DropdownOption(key=s, text=s) for s in symboles],
            width=150,
            on_select=self._changer_symbole,
        )
        choix_intervalle = ft.Dropdown(
            value=self.intervalle,
            options=[ft.DropdownOption(key=i, text=i) for i in config.INTERVALLES],
            width=100,
            on_select=self._changer_intervalle,
        )
        return ft.Row(
            [
                choix_symbole,
                choix_intervalle,
                communs.bouton("Lire", lambda _e: self.page.run_task(self.charger),
                               infobulle="Recharge les bougies"),
                communs.bouton("Analyser", lambda _e: self.page.run_task(self.analyser),
                               ton="valider", infobulle="Déroule les étages, sans passer d'ordre"),
            ],
            spacing=8,
            wrap=True,
        )

    def _changer_symbole(self, event) -> None:
        valeur = getattr(event.control, "value", None)
        if valeur:
            self.viser(valeur)
            self.page.run_task(self.charger)

    def _changer_intervalle(self, event) -> None:
        valeur = getattr(event.control, "value", None)
        if valeur:
            self.intervalle = valeur
            self._decision = {}
            self.page.run_task(self.charger)
