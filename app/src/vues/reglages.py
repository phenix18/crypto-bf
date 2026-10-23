"""Les réglages : mode, source des bougies, liste surveillée, seuil, capital.

Un seul bouton « Enregistrer ». Les valeurs ne s'appliquent pas au fur et à
mesure de la frappe : un seuil de confluence modifié à moitié (« 6 » avant de
taper « 65 ») serait appliqué entre-temps, et le moteur reconstruit pour rien.

Ce que cet écran *ne propose pas* est aussi délibéré : il n'y a aucun champ de
clé d'API. L'application ne passe jamais d'ordre réel elle-même — en mode local
elle simule, en mode relié c'est le serveur qui détient les clés. Un fichier
`.pyc` dans une APK ne protège rien, et une clé d'exchange n'a donc rien à faire
sur l'appareil.
"""

from __future__ import annotations

import asyncio

import flet as ft

from coeur import config, formats
from coeur.config import MODE_LOCAL, MODE_RELIE, Reglages
from coeur.saisie import liste_symboles, nombre_saisi
from vues import communs

LIBELLES_MODE = {MODE_LOCAL: "local (moteur embarqué)", MODE_RELIE: "relié à un serveur"}


def _champ(libelle: str, controle: ft.Control, aide: str | None = None) -> ft.Control:
    enfants = [
        ft.Text(libelle, size=11, color=config.COULEUR_TEXTE_DOUX),
        controle,
    ]
    if aide:
        enfants.append(ft.Text(aide, size=10, color=config.COULEUR_TEXTE_DOUX))
    return ft.Column(enfants, spacing=4, tight=True)


class Vue:
    """Le formulaire, et l'écriture en base au moment de l'enregistrement."""

    def __init__(self, session, page: ft.Page, sur_changement=None):
        self.session = session
        self.page = page
        # Appelé après un enregistrement réussi : le reste de l'application
        # doit relire l'état, le mode a pu changer.
        self.sur_changement = sur_changement
        self.corps = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.controle = ft.Container(content=self.corps, padding=12, expand=True)
        self._construire()

    # -- construction ------------------------------------------------------

    def _construire(self) -> None:
        reglages = self.session.reglages

        self.mode = ft.Dropdown(
            value=reglages.mode,
            options=[ft.DropdownOption(key=m, text=LIBELLES_MODE[m]) for m in LIBELLES_MODE],
            width=260,
        )
        self.url = ft.TextField(
            value=reglages.url_serveur, dense=True, width=300,
            hint_text="http://192.168.1.10:8000",
        )
        self.source = ft.Dropdown(
            value=reglages.source,
            options=[ft.DropdownOption(key=s, text=s) for s in config.SOURCES],
            width=180,
        )
        self.intervalle = ft.Dropdown(
            value=reglages.intervalle,
            options=[ft.DropdownOption(key=i, text=i) for i in config.INTERVALLES],
            width=120,
        )
        self.watchlist = ft.TextField(
            value=", ".join(reglages.watchlist), dense=True, multiline=True, min_lines=2,
        )
        self.rafraichissement = ft.TextField(
            value=str(reglages.rafraichissement_s), dense=True, width=120,
        )
        self.capital = ft.TextField(
            value=formats.nombre(reglages.capital_papier), dense=True, width=160,
        )
        self.seuil = ft.Slider(
            value=reglages.seuil_confluence, min=40, max=95, divisions=11,
            label="{value}%", active_color=config.COULEUR_PRIMAIRE,
        )
        self.testnet = ft.Switch(value=reglages.testnet, active_color=config.COULEUR_HAUSSE)
        self.theme = ft.Switch(value=reglages.theme_sombre, active_color=config.COULEUR_PRIMAIRE)
        self.retour = ft.Column(spacing=8, tight=True)

        self.corps.controls = [
            communs.carte(
                "Mode",
                _champ("Fonctionnement", self.mode,
                       "En local, le moteur tourne sur l'appareil et simule les ordres. "
                       "En relié, tout passe par le serveur."),
                _champ("Adresse du serveur", self.url,
                       "Utilisée uniquement en mode relié."),
            ),
            communs.carte(
                "Marché",
                _champ("Source des bougies", self.source,
                       "« synthetic » est un flux généré, pour essayer sans réseau. "
                       "Jamais pour juger une stratégie."),
                _champ("Unité de temps", self.intervalle),
                _champ("Liste surveillée", self.watchlist,
                       "Séparés par des virgules. Les doublons sont retirés."),
            ),
            communs.carte(
                "Moteur",
                _champ("Capital simulé", self.capital,
                       "Le solde de départ en mode local. N'engage aucun argent réel."),
                _champ(
                    "Seuil de confluence", self.seuil,
                    "En dessous, aucune entrée n'est proposée. 65 % est la valeur "
                    "mesurée ; au-delà de 75 % le moteur ne trade pratiquement plus.",
                ),
                ft.Row(
                    [
                        self.testnet,
                        ft.Text("Testnet", size=12, expand=True),
                    ],
                    spacing=8,
                ),
                _champ("Rafraîchissement (secondes)", self.rafraichissement,
                       "15 s minimum. Sur téléphone, 60 s suffisent et ménagent la batterie."),
            ),
            communs.carte(
                "Affichage",
                ft.Row([self.theme, ft.Text("Thème sombre", size=12, expand=True)], spacing=8),
            ),
            communs.message(
                "Aucune clé d'API n'est demandée ni stockée par l'application : en local "
                "les ordres sont simulés, en relié c'est le serveur qui les détient."
            ),
            ft.Row(
                [
                    communs.bouton("Enregistrer", self._enregistrer, ton="valider"),
                    communs.bouton("Annuler les modifications", lambda _e: self._recharger()),
                ],
                spacing=10,
                wrap=True,
            ),
            self.retour,
        ]

    def _recharger(self) -> None:
        self._construire()
        self.page.update()

    async def charger(self) -> None:
        """Rien à lire : le formulaire part toujours des réglages en mémoire."""
        self._recharger()

    # -- enregistrement ----------------------------------------------------

    def lire_formulaire(self) -> Reglages:
        """Le formulaire tel qu'il est, ramené dans ses bornes par `valide()`."""
        actuel = self.session.reglages
        return Reglages(
            mode=self.mode.value or actuel.mode,
            source=self.source.value or actuel.source,
            url_serveur=(self.url.value or actuel.url_serveur).strip(),
            watchlist=liste_symboles(self.watchlist.value) or list(actuel.watchlist),
            intervalle=self.intervalle.value or actuel.intervalle,
            rafraichissement_s=int(
                nombre_saisi(self.rafraichissement.value, actuel.rafraichissement_s)
            ),
            capital_papier=nombre_saisi(self.capital.value, actuel.capital_papier),
            seuil_confluence=float(self.seuil.value or actuel.seuil_confluence),
            testnet=bool(self.testnet.value),
            theme_sombre=bool(self.theme.value),
        ).valide()

    def _enregistrer(self, _event=None) -> None:
        self.page.run_task(self._appliquer)

    async def _appliquer(self) -> None:
        nouveaux = self.lire_formulaire()
        try:
            await asyncio.to_thread(self.session.enregistrer_reglages, nouveaux)
        except Exception as exc:  # noqa: BLE001
            self.retour.controls = [communs.message(f"Enregistrement impossible : {exc}", "erreur")]
            self.page.update()
            return
        # Le formulaire est reconstruit depuis ce qui a réellement été retenu :
        # si `valide()` a corrigé une valeur, l'utilisateur doit la voir.
        self._construire()
        self.retour.controls = [
            communs.message("Réglages enregistrés.", "info"),
        ]
        self.page.update()
        if self.sur_changement:
            self.sur_changement(self.session.reglages)
