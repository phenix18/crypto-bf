"""Point d'entrée de l'application AI-Trader (PC et Android).

Quatre écrans dans une barre basse : poste, analyse, journal, réglages. La
barre basse plutôt que des onglets hauts parce que sur téléphone le pouce est
en bas, et parce que les quatre destinations tiennent sans se chevaucher.

Deux règles de structure tenues ici :

- **tout le travail lourd sort du fil de l'interface.** Chaque écran charge
  dans `page.run_task`, et les appels bloquants partent dans un thread. Une
  lecture de bougies qui prend deux secondes ne doit pas figer le défilement ;
- **aucune boucle automatique.** L'application analyse quand on le demande.
  Android suspend les processus en arrière-plan, et un robot qui croit tourner
  pendant que le téléphone dort est plus dangereux qu'un robot à l'arrêt.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Lancé par `flet run` ou par la coque Android, le dossier du fichier n'est pas
# toujours dans le chemin d'import.
RACINE = Path(__file__).resolve().parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

import flet as ft  # noqa: E402

from coeur import config  # noqa: E402
from coeur.session import Session  # noqa: E402
from vues import analyse as vue_analyse  # noqa: E402
from vues import cockpit as vue_cockpit  # noqa: E402
from vues import communs  # noqa: E402
from vues import journal as vue_journal  # noqa: E402
from vues import reglages as vue_reglages  # noqa: E402

TITRES = ("Poste", "Analyse", "Journal", "Réglages")


def theme() -> ft.Theme:
    """Une seule graine de couleur : Material en déduit le reste.

    Le clair et le sombre se choisissent par `page.theme_mode`, pas ici.
    """
    return ft.Theme(color_scheme_seed=config.COULEUR_PRIMAIRE)


def barre_titre(reglages) -> ft.Control:
    """Le nom, la version, et le mode : ce qui situe l'écran d'un coup d'œil."""
    return ft.Container(
        content=ft.Row(
            [
                ft.Text(config.APP_NOM, size=16, weight=ft.FontWeight.BOLD,
                        color=config.COULEUR_PRIMAIRE),
                ft.Text(f"v{config.APP_VERSION}", size=10,
                        color=config.COULEUR_TEXTE_DOUX),
                ft.Container(expand=True),
                communs.badge(
                    "relié" if reglages.mode == config.MODE_RELIE else "local",
                    config.COULEUR_PRIMAIRE,
                ),
            ],
            spacing=8,
        ),
        padding=ft.Padding.symmetric(vertical=10, horizontal=12),
        bgcolor=config.COULEUR_SURFACE,
        border=ft.Border.only(bottom=ft.BorderSide(1, config.COULEUR_BORDURE)),
    )


def barre_navigation(au_changement) -> ft.NavigationBar:
    return ft.NavigationBar(
        selected_index=0,
        bgcolor=config.COULEUR_SURFACE,
        indicator_color=ft.Colors.with_opacity(0.25, config.COULEUR_PRIMAIRE),
        on_change=au_changement,
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.SPEED, label=TITRES[0]),
            ft.NavigationBarDestination(icon=ft.Icons.CANDLESTICK_CHART, label=TITRES[1]),
            ft.NavigationBarDestination(icon=ft.Icons.RECEIPT_LONG, label=TITRES[2]),
            ft.NavigationBarDestination(icon=ft.Icons.TUNE, label=TITRES[3]),
        ],
    )


def main(page: ft.Page) -> None:
    dossier = config.dossier_donnees()
    dossier.mkdir(parents=True, exist_ok=True)
    session = Session.ouvrir(dossier)

    page.title = config.APP_NOM
    page.theme = theme()
    page.theme_mode = (
        ft.ThemeMode.DARK if session.reglages.theme_sombre else ft.ThemeMode.LIGHT
    )
    page.bgcolor = config.COULEUR_FOND
    page.padding = 0
    # Un téléphone en portrait tient dans 360 points de large ; en dessous,
    # c'est une fenêtre de bureau réduite à la main, et les cartes passent en
    # colonne unique sans réglage particulier.
    page.window.min_width = 360
    page.window.min_height = 520

    corps = ft.Container(expand=True)
    entete = ft.Container()
    deja_chargees: set = set()

    analyse = vue_analyse.Analyse(session, page)
    journal = vue_journal.Journal(session, page)

    def aller_analyse(symbole: str) -> None:
        """Le cockpit envoie un symbole : on vise, on bascule, on charge."""
        analyse.viser(symbole)
        deja_chargees.discard(1)
        navigation.selected_index = 1
        afficher(1)

    cockpit = vue_cockpit.Cockpit(session, page, aller_analyse=aller_analyse)

    def apres_reglages(nouveaux) -> None:
        """Les réglages ont changé : le thème suit, et tout sera relu."""
        page.theme_mode = (
            ft.ThemeMode.DARK if nouveaux.theme_sombre else ft.ThemeMode.LIGHT
        )
        entete.content = barre_titre(nouveaux)
        # Les trois autres écrans parlent peut-être d'un autre serveur
        # maintenant ; ils rechargeront à leur prochain affichage.
        deja_chargees.clear()
        deja_chargees.add(3)
        analyse.viser((nouveaux.watchlist or ["BTCUSDT"])[0])
        analyse.intervalle = nouveaux.intervalle
        page.update()

    reglages = vue_reglages.Vue(session, page, sur_changement=apres_reglages)
    ecrans = (cockpit, analyse, journal, reglages)

    def afficher(index: int) -> None:
        ecran = ecrans[index]
        corps.content = ecran.controle
        page.update()
        if index not in deja_chargees:
            deja_chargees.add(index)
            page.run_task(ecran.charger)

    def au_changement(event) -> None:
        afficher(int(getattr(event.control, "selected_index", 0) or 0))

    navigation = barre_navigation(au_changement)
    entete.content = barre_titre(session.reglages)

    # La barre de navigation est posée dans la colonne plutôt que sur la page :
    # `Page.navigation_bar` n'existe pas dans cette version de Flet, et un
    # enfant de colonne se comporte de la même façon à l'écran.
    page.add(
        ft.SafeArea(
            ft.Column([entete, corps, navigation], spacing=0, expand=True),
            expand=True,
        )
    )
    afficher(0)


if __name__ == "__main__":
    ft.run(main)
