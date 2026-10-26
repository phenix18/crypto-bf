"""Le journal : ce qui a été décidé, ce qui a été fermé, et ce que ça a donné.

Ces deux listes sont volontairement séparées. Une décision n'est pas un trade :
la plupart s'arrêtent à un étage et ne touchent jamais le marché, et les
confondre donnerait l'impression d'un robot bien plus actif qu'il ne l'est.

Tout vient de la base locale. Le journal se lit donc sans réseau, et même après
une coupure : ce qui est écrit ici a été écrit au moment de la décision.
"""

from __future__ import annotations

import asyncio

import flet as ft

from coeur import config, formats
from coeur.bilan import Bilan, resume
from vues import communs


def panneau_bilan(bilan: Bilan) -> ft.Control:
    """Huit nombres, et aucun arrangement avec les dénominateurs."""
    return communs.carte(
        "Bilan des trades fermés",
        communs.grille_statistiques(
            [
                communs.statistique("fermés", str(bilan.total)),
                communs.statistique(
                    "réussite",
                    formats.pourcent(bilan.taux) if bilan.taux is not None else "—",
                ),
                communs.statistique(
                    "somme", formats.multiple_r(bilan.somme_r),
                    communs.couleur_resultat(bilan.somme_r),
                ),
                communs.statistique(
                    "espérance", formats.multiple_r(bilan.esperance_r),
                    communs.couleur_resultat(bilan.esperance_r),
                ),
                communs.statistique("meilleur", formats.multiple_r(bilan.meilleur),
                                    config.COULEUR_HAUSSE),
                communs.statistique("pire", formats.multiple_r(bilan.pire),
                                    config.COULEUR_BAISSE),
            ]
        ),
        ft.Text(
            f"{bilan.gains} gains · {bilan.pertes} pertes · {bilan.neutres} neutres. "
            "La réussite se compte sur les trades tranchés, l'espérance sur tous.",
            size=10,
            color=config.COULEUR_TEXTE_DOUX,
        ),
    )


def ligne_trade(trade: dict) -> ft.Control:
    """Un trade fermé : son issue en mot, son R signé, sa raison de sortie."""
    issue = str(trade.get("outcome") or "").lower()
    libelles = {"win": "gain", "loss": "perte", "scratch": "neutre"}
    couleurs = {
        "win": config.COULEUR_HAUSSE,
        "loss": config.COULEUR_BAISSE,
        "scratch": config.COULEUR_NEUTRE,
    }
    sens = trade.get("direction", "flat")
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(trade.get("symbol", "—"), size=12,
                                weight=ft.FontWeight.BOLD, width=88),
                        communs.badge(formats.sens(sens), communs.couleur_sens(sens)),
                        communs.badge(
                            libelles.get(issue, issue or "—"),
                            couleurs.get(issue, config.COULEUR_NEUTRE),
                        ),
                        ft.Container(expand=True),
                        ft.Text(
                            formats.multiple_r(trade.get("r_multiple")),
                            size=12,
                            weight=ft.FontWeight.BOLD,
                            color=communs.couleur_resultat(trade.get("r_multiple")),
                        ),
                    ],
                    spacing=6,
                    wrap=True,
                ),
                ft.Text(
                    f"{formats.horodatage(trade.get('closed_at'), avec_date=True)} · "
                    f"{trade.get('interval', '—')} · "
                    f"confluence {formats.pourcent(trade.get('confluence'))} · "
                    f"{trade.get('reason') or 'sans raison notée'}",
                    size=10,
                    color=config.COULEUR_TEXTE_DOUX,
                ),
            ],
            spacing=3,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.4, config.COULEUR_FOND),
        border=ft.Border.all(1, config.COULEUR_BORDURE),
        border_radius=10,
        padding=10,
    )


def ligne_decision(decision: dict) -> ft.Control:
    """Une décision : où elle s'est arrêtée, et avec quelle confluence."""
    valide = bool(decision.get("approved"))
    sens = decision.get("direction", "flat")
    return ft.Row(
        [
            ft.Text(formats.horodatage(decision.get("decided_at")), size=10,
                    color=config.COULEUR_TEXTE_DOUX, width=62),
            ft.Text(decision.get("symbol", "—"), size=12, weight=ft.FontWeight.W_600, width=82),
            communs.badge(formats.sens(sens), communs.couleur_sens(sens)),
            communs.badge(
                "validé" if valide else formats.etage(decision.get("stage", "")),
                config.COULEUR_HAUSSE if valide else config.COULEUR_NEUTRE,
            ),
            ft.Container(expand=True),
            ft.Text(formats.pourcent(decision.get("confluence")), size=11),
        ],
        spacing=6,
        wrap=True,
    )


class Journal:
    """Les deux listes, relues depuis la base à chaque affichage."""

    LIMITE_TRADES = 40
    LIMITE_DECISIONS = 40

    def __init__(self, session, page: ft.Page):
        self.session = session
        self.page = page
        self.corps = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.controle = ft.Container(content=self.corps, padding=12, expand=True)

    async def charger(self) -> None:
        if not self.session.base:
            self.corps.controls = [communs.message("Aucune base locale ouverte.", "erreur")]
            self.page.update()
            return
        try:
            trades, decisions = await asyncio.to_thread(self._lire)
        except Exception as exc:  # noqa: BLE001
            self.corps.controls = [communs.message(f"Journal illisible : {exc}", "erreur")]
            self.page.update()
            return

        enfants: list = [
            panneau_bilan(resume(trades)),
            communs.carte(
                "Trades fermés",
                *(
                    [ligne_trade(t) for t in trades[: self.LIMITE_TRADES]]
                    or [communs.message("Aucun trade fermé pour l'instant.")]
                ),
            ),
            communs.carte(
                "Décisions récentes",
                *(
                    [ligne_decision(d) for d in decisions]
                    or [communs.message("Aucune décision enregistrée.")]
                ),
            ),
            ft.Row(
                [communs.bouton("Rafraîchir", lambda _e: self.page.run_task(self.charger))],
            ),
        ]
        self.corps.controls = enfants
        self.page.update()

    def _lire(self) -> tuple:
        base = self.session.base
        return (
            base.historique(limite=self.LIMITE_TRADES),
            base.decisions_recentes(limite=self.LIMITE_DECISIONS),
        )
