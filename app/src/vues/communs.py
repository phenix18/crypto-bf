"""Les briques d'interface réutilisées par tous les écrans.

Deux règles tenues partout, les mêmes que dans l'interface web :

- **La couleur ne porte jamais le sens toute seule.** Chaque vert et chaque
  rouge est accompagné d'un mot. Un écran de trading est exactement l'endroit
  où un lecteur daltonien ne doit pas avoir à deviner.
- **Rien ne clignote sur un changement de prix.** Un nombre qui s'anime est
  plus dur à lire au moment où il compte le plus.
"""

from __future__ import annotations

import flet as ft

from coeur import config, formats


def carte(titre: str | None, *contenus, marge: int = 12) -> ft.Container:
    """Une surface, un titre discret, du contenu."""
    enfants = []
    if titre:
        enfants.append(
            ft.Text(titre, size=12, weight=ft.FontWeight.W_600,
                    color=config.COULEUR_TEXTE_DOUX)
        )
    enfants.extend(contenus)
    return ft.Container(
        content=ft.Column(enfants, spacing=8, tight=True),
        bgcolor=config.COULEUR_SURFACE,
        border=ft.Border.all(1, config.COULEUR_BORDURE),
        border_radius=14,
        padding=marge,
    )


def badge(texte: str, couleur: str = config.COULEUR_TEXTE_DOUX) -> ft.Container:
    return ft.Container(
        content=ft.Text(texte.upper(), size=10, weight=ft.FontWeight.BOLD, color=couleur),
        border=ft.Border.all(1, couleur),
        border_radius=999,
        padding=ft.Padding.symmetric(vertical=3, horizontal=9),
    )


def statistique(libelle: str, valeur: str, couleur: str | None = None) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(libelle.upper(), size=9, color=config.COULEUR_TEXTE_DOUX),
                ft.Text(valeur, size=16, weight=ft.FontWeight.BOLD,
                        color=couleur or config.COULEUR_TEXTE),
            ],
            spacing=2,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.4, config.COULEUR_FOND),
        border=ft.Border.all(1, config.COULEUR_BORDURE),
        border_radius=10,
        padding=10,
        expand=True,
    )


def grille_statistiques(elements: list, colonnes: int = 2) -> ft.Column:
    """Des statistiques par deux : lisible sur téléphone comme sur écran."""
    lignes = []
    for debut in range(0, len(elements), colonnes):
        lignes.append(ft.Row(elements[debut : debut + colonnes], spacing=8))
    return ft.Column(lignes, spacing=8, tight=True)


def bouton(texte: str, au_clic, *, ton: str = "neutre", actif: bool = True,
           infobulle: str | None = None) -> ft.ElevatedButton:
    """Un bouton en relief.

    L'élévation et l'ombre portent le relief ; Flet gère l'enfoncement au
    toucher. Un bouton désactivé garde son infobulle, parce que « pourquoi
    est-il gris ? » est la première question qu'on se pose.
    """
    tons = {
        "valider": ("#0f9f6a", "#f0fff8"),
        "arreter": ("#b4303a", "#fff5f5"),
        "alerte": ("#c08a33", "#1a1206"),
        "neutre": ("#243141", config.COULEUR_TEXTE),
    }
    fond, texte_couleur = tons.get(ton, tons["neutre"])
    return ft.ElevatedButton(
        content=ft.Text(texte, weight=ft.FontWeight.W_600),
        on_click=au_clic if actif else None,
        disabled=not actif,
        tooltip=infobulle,
        bgcolor=fond,
        color=texte_couleur,
        elevation=4,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding.symmetric(vertical=14, horizontal=18),
        ),
    )


def couleur_sens(sens: str) -> str:
    return {
        "long": config.COULEUR_HAUSSE,
        "short": config.COULEUR_BAISSE,
    }.get(sens, config.COULEUR_NEUTRE)


def couleur_posture(posture: str) -> str:
    return {
        "confirm": config.COULEUR_HAUSSE,
        "veto": config.COULEUR_BAISSE,
    }.get(posture, config.COULEUR_NEUTRE)


def couleur_resultat(valeur: float | None) -> str:
    if valeur is None:
        return config.COULEUR_TEXTE
    return config.COULEUR_HAUSSE if valeur >= 0 else config.COULEUR_BAISSE


def message(texte: str, ton: str = "info") -> ft.Container:
    couleurs = {
        "info": config.COULEUR_TEXTE_DOUX,
        "alerte": "#d99d4c",
        "erreur": config.COULEUR_BAISSE,
    }
    couleur = couleurs.get(ton, couleurs["info"])
    return ft.Container(
        content=ft.Text(texte, size=12, color=couleur),
        bgcolor=ft.Colors.with_opacity(0.08, couleur),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.4, couleur)),
        border_radius=10,
        padding=10,
    )


def ligne_vote(vote: dict) -> ft.Column:
    """Un indicateur, sa force, et ce qu'il a vu — avec le mot à côté de la barre."""
    sens = vote.get("direction", "flat")
    force = max(0.0, min(1.0, float(vote.get("strength") or 0.0)))
    return ft.Column(
        [
            ft.Row(
                [
                    ft.Text(vote.get("name", "").upper(), size=11,
                            weight=ft.FontWeight.BOLD, color=couleur_sens(sens), width=86),
                    ft.Container(
                        content=ft.ProgressBar(
                            value=force, color=couleur_sens(sens), bgcolor="#1b2733", height=7
                        ),
                        expand=True,
                    ),
                    ft.Text(formats.sens(sens), size=10, color=config.COULEUR_TEXTE_DOUX, width=54),
                ],
                spacing=8,
            ),
            ft.Text(vote.get("detail", ""), size=10, color=config.COULEUR_TEXTE_DOUX),
        ],
        spacing=2,
        tight=True,
    )


def ligne_avis(avis: dict) -> ft.Row:
    """L'avis d'un agent : sa posture en toutes lettres, pas seulement en couleur."""
    posture = avis.get("stance", "abstain")
    return ft.Row(
        [
            ft.Container(
                content=ft.Text(formats.posture(posture).upper(), size=9,
                                weight=ft.FontWeight.BOLD, color=couleur_posture(posture)),
                border=ft.Border.all(1, couleur_posture(posture)),
                border_radius=8,
                padding=ft.Padding.symmetric(vertical=3, horizontal=6),
                width=92,
            ),
            ft.Column(
                [
                    ft.Text(formats.role(avis.get("role", "")).capitalize(), size=12,
                            weight=ft.FontWeight.W_600),
                    ft.Text(avis.get("rationale", ""), size=10,
                            color=config.COULEUR_TEXTE_DOUX),
                ],
                spacing=1,
                tight=True,
                expand=True,
            ),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )
