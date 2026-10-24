# AI-Trader — application PC et Android

Une seule base de code Flet : un exécutable Windows/Linux et une APK Android,
posés sur le moteur de trading du dépôt.

La conception complète — pile retenue, modèle REA, schéma SQLite, permissions,
build, signature, pièges — est dans
[`docs/APP_PC_APK_FR.md`](../docs/APP_PC_APK_FR.md).

## Ce que l'application fait, et ce qu'elle ne fait pas

| | PC | Android |
|---|---|---|
| Analyser à la demande, avec les quatre étages | oui | oui |
| Lire le graphique, les niveaux, le journal | oui | oui |
| Superviser un serveur | oui | oui |
| Coupe-circuit | oui | oui |
| **Passer un ordre** | non | non |
| **Boucle automatique** | non | non |

Les deux dernières lignes sont des décisions, pas des manques.

Android suspend les processus en arrière-plan : un moteur qui doit poser un
stop juste après une entrée ne peut pas vivre dans un processus que le système
peut geler. Et une application qui ne passe aucun ordre n'a aucune raison de
détenir une clé d'échange — donc elle n'en demande pas, et il n'y a rien à
extraire de l'APK.

L'exécution et la boucle restent sur le serveur (`service/`), que
l'application pilote en mode *relié* : état, verdicts, coupe-circuit. En mode
*local*, le moteur embarqué analyse et simule, sur un capital de papier.

## Les quatre écrans

| Écran | Ce qu'on y lit |
|---|---|
| **Poste** | capital, gain du jour, repli, positions ouvertes, liste surveillée, coupe-circuit |
| **Analyse** | graphique (bougies, EMA, bandes, volume, RSI, SL/TP), verdict, indicateurs, risque, calibration, conseil |
| **Journal** | bilan des trades fermés (réussite, espérance en R), trades, décisions récentes |
| **Réglages** | mode, source des bougies, liste surveillée, unité de temps, capital simulé, seuil de confluence, testnet, thème |

Le coupe-circuit s'engage sans question et se relâche avec une confirmation :
un arrêt d'urgence qui pose une question n'est pas un arrêt d'urgence.

## Développement

```bash
python scripts/sync_engine.py      # recopier le moteur depuis service/server
cd app && pip install flet
flet run src/main.py               # lancer sur PC
pytest tests -q                    # cœur, stockage, géométrie et écrans
```

Les tests des écrans se sautent si Flet n'est pas installé ; le reste de la
suite ne dépend que de la bibliothèque standard.

Le moteur ne s'édite **jamais** dans `app/src/engine/` : il s'édite dans
`service/server/`, puis on relance `sync_engine.py`. Un test compare les deux
copies et échoue si elles divergent.

## Builds

```bash
make app-apk                     # APK arm64-v8a
make app-desktop                 # exécutable pour cette plateforme
make app-icons                   # régénérer l'icône depuis la palette
```

Il faut Flutter **3.44.8** exactement (Flet 0.86.5 refuse un autre mineur),
plus JDK 17 et l'Android SDK pour l'APK. En CI, le workflow
`app-release.yml` s'en charge, au tag `app-v*` ou à la demande.

Keystore, secrets de signature, vérification de l'APK et installation sur le
téléphone : [`docs/APP_BUILD_FR.md`](../docs/APP_BUILD_FR.md).
