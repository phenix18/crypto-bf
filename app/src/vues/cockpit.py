"""Le poste : l'état du compte, la surveillance, le coupe-circuit.

C'est le premier écran, et il répond à trois questions dans cet ordre : où en
est le solde, qu'est-ce qui est ouvert, et comment tout arrêter. Le reste —
l'analyse détaillée, le journal — est à un onglet de distance.

Le bouton d'arrêt est toujours visible et toujours actif. Le relâcher demande
une confirmation ; l'engager n'en demande aucune, parce qu'un arrêt d'urgence
qui pose une question n'est pas un arrêt d'urgence.
"""

from __future__ import annotations

import asyncio

import flet as ft

from coeur import config, formats
from coeur.config import MODE_RELIE
from vues import communs


def entete(etat: dict, reglages) -> ft.Control:
    """Mode, source des bougies, modèle. Ce qui change le sens de l'écran."""
    mode = etat.get("mode") or {}
    badges = [
        communs.badge(
            "relié" if reglages.mode == MODE_RELIE else "local",
            config.COULEUR_PRIMAIRE,
        ),
        communs.badge(str(mode.get("data_source") or "—")),
    ]
    if mode.get("testnet"):
        badges.append(communs.badge("testnet", config.COULEUR_HAUSSE))
    if mode.get("live"):
        badges.append(communs.badge("réel", config.COULEUR_BAISSE))
    if mode.get("paper"):
        badges.append(communs.badge("papier"))
    modele = str(mode.get("model") or "none")
    if modele != "none":
        badges.append(
            communs.badge(
                modele,
                config.COULEUR_HAUSSE if mode.get("model_available") else config.COULEUR_NEUTRE,
            )
        )
    return ft.Row(badges, spacing=6, wrap=True)


def panneau_compte(etat: dict) -> ft.Control:
    """Le solde et ses limites. Les six nombres dont tout le reste dépend."""
    portefeuille = etat.get("portfolio") or {}
    return communs.carte(
        "Compte",
        communs.grille_statistiques(
            [
                communs.statistique("capital", formats.nombre(portefeuille.get("equity"))),
                communs.statistique(
                    "gain total",
                    formats.signe(portefeuille.get("total_pnl")),
                    communs.couleur_resultat(portefeuille.get("total_pnl")),
                ),
                communs.statistique(
                    "jour",
                    formats.signe(portefeuille.get("realised_pnl_today")),
                    communs.couleur_resultat(portefeuille.get("realised_pnl_today")),
                ),
                communs.statistique(
                    "repli",
                    formats.pourcent(portefeuille.get("drawdown_percent")),
                    config.COULEUR_BAISSE
                    if float(portefeuille.get("drawdown_percent") or 0.0) > 0
                    else None,
                ),
                communs.statistique(
                    "positions", str(len(portefeuille.get("open_positions") or []))
                ),
                communs.statistique(
                    "pertes d'affilée", str(portefeuille.get("consecutive_losses") or 0)
                ),
            ]
        ),
    )


def ligne_position(position: dict) -> ft.Control:
    """Une position ouverte : son sens écrit, ses trois niveaux, sa quantité."""
    sens = position.get("direction", "flat")
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(position.get("symbol", "—"), size=13,
                                weight=ft.FontWeight.BOLD),
                        communs.badge(formats.sens(sens), communs.couleur_sens(sens)),
                        ft.Container(expand=True),
                        ft.Text(f"× {formats.nombre(position.get('quantity'), 6)}",
                                size=11, color=config.COULEUR_TEXTE_DOUX),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Text(f"entrée {formats.prix(position.get('entry'))}", size=11),
                        ft.Text(f"SL {formats.prix(position.get('stop_loss'))}", size=11,
                                color=config.COULEUR_BAISSE),
                        ft.Text(f"TP {formats.prix(position.get('take_profit'))}", size=11,
                                color=config.COULEUR_HAUSSE),
                    ],
                    spacing=12,
                    wrap=True,
                ),
            ],
            spacing=4,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.4, config.COULEUR_FOND),
        border=ft.Border.all(1, config.COULEUR_BORDURE),
        border_radius=10,
        padding=10,
    )


def panneau_positions(etat: dict) -> ft.Control:
    positions = (etat.get("portfolio") or {}).get("open_positions") or []
    if not positions:
        return communs.carte(
            "Positions ouvertes",
            communs.message("Aucune position ouverte."),
        )
    return communs.carte("Positions ouvertes", *[ligne_position(p) for p in positions])


class Cockpit:
    """L'écran, et le peu d'état qu'il garde entre deux chargements."""

    def __init__(self, session, page: ft.Page, aller_analyse=None):
        self.session = session
        self.page = page
        self.aller_analyse = aller_analyse
        self.corps = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.etat: dict = {}
        self.controle = ft.Container(content=self.corps, padding=12, expand=True)

    # -- chargement --------------------------------------------------------

    async def charger(self) -> None:
        """Relit l'état. Une erreur s'affiche, elle ne remonte pas."""
        self.corps.controls = [communs.message("Lecture de l'état…")]
        self.page.update()
        try:
            # Hors du fil de l'interface : en mode relié c'est un appel réseau,
            # en mode local c'est la lecture des bougies.
            self.etat = await asyncio.to_thread(self.session.etat)
        except Exception as exc:  # noqa: BLE001 — un écran ne plante jamais
            self.etat = {}
            self.corps.controls = [
                communs.message(f"Lecture impossible : {exc}", "erreur"),
                self._barre_outils(),
            ]
            self.page.update()
            return
        self._redessiner()

    def _redessiner(self) -> None:
        etat = self.etat
        portefeuille = etat.get("portfolio") or {}
        enfants: list = [entete(etat, self.session.reglages)]

        if not etat.get("joignable", True):
            enfants.append(
                communs.message(
                    f"Serveur injoignable : {etat.get('erreur', 'sans détail')}", "erreur"
                )
            )
        if portefeuille.get("kill_switch"):
            enfants.append(
                communs.message(
                    "Coupe-circuit engagé : aucune entrée ne sera prise tant qu'il n'est "
                    "pas relâché.",
                    "alerte",
                )
            )

        enfants.append(panneau_compte(etat))
        enfants.append(self._panneau_surveillance())
        enfants.append(panneau_positions(etat))
        enfants.append(self._barre_outils())
        self.corps.controls = enfants
        self.page.update()

    # -- morceaux qui ont besoin des rappels -------------------------------

    def _panneau_surveillance(self) -> ft.Control:
        """La liste surveillée, chaque ligne menant à son analyse."""
        lignes = []
        for symbole in self.session.reglages.watchlist:
            lignes.append(
                ft.Row(
                    [
                        ft.Text(symbole, size=13, weight=ft.FontWeight.W_600, expand=True),
                        communs.bouton(
                            "Analyser",
                            lambda _e, s=symbole: self._analyser(s),
                            infobulle=f"Analyser {symbole} sans passer d'ordre",
                        ),
                    ],
                    spacing=8,
                )
            )
        return communs.carte(
            f"Surveillance · {self.session.reglages.intervalle}",
            *(lignes or [communs.message("Liste vide : à remplir dans les réglages.")]),
        )

    def _barre_outils(self) -> ft.Control:
        engage = bool((self.etat.get("portfolio") or {}).get("kill_switch"))
        return ft.Row(
            [
                communs.bouton("Rafraîchir", lambda _e: self.page.run_task(self.charger)),
                communs.bouton(
                    "Relâcher l'arrêt" if engage else "Tout arrêter",
                    self._demander_relache if engage else self._engager,
                    ton="alerte" if engage else "arreter",
                    infobulle="Relâcher demande une confirmation"
                    if engage
                    else "Engage le coupe-circuit : plus aucune entrée",
                ),
            ],
            spacing=10,
            wrap=True,
        )

    # -- actions -----------------------------------------------------------

    def _analyser(self, symbole: str) -> None:
        if self.aller_analyse:
            self.aller_analyse(symbole)

    def _engager(self, _event=None) -> None:
        self.page.run_task(self._appliquer_coupe_circuit, True)

    def _demander_relache(self, _event=None) -> None:
        dialogue = ft.AlertDialog(
            modal=True,
            title=ft.Text("Relâcher le coupe-circuit ?"),
            content=ft.Text(
                "Les entrées redeviendront possibles. À ne faire qu'après avoir "
                "compris pourquoi il s'est engagé."
            ),
            actions=[
                ft.TextButton("Annuler", on_click=lambda _e: self.page.pop_dialog()),
                ft.TextButton("Relâcher", on_click=self._relacher),
            ],
        )
        self.page.show_dialog(dialogue)

    def _relacher(self, _event=None) -> None:
        self.page.pop_dialog()
        self.page.run_task(self._appliquer_coupe_circuit, False)

    async def _appliquer_coupe_circuit(self, engage: bool) -> None:
        try:
            await asyncio.to_thread(self.session.coupe_circuit, engage)
        except Exception as exc:  # noqa: BLE001
            self.corps.controls.insert(
                0, communs.message(f"Coupe-circuit : {exc}", "erreur")
            )
            self.page.update()
            return
        await self.charger()
