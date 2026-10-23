# AI-Trader — application PC et Android

Une seule base de code Flet : un exécutable Windows/Linux et une APK Android,
posés sur le moteur de trading du dépôt.

La conception complète — pile retenue, modèle REA, schéma SQLite, permissions,
build, signature, pièges — est dans
[`docs/APP_PC_APK_FR.md`](../docs/APP_PC_APK_FR.md).

## Ce que l'application fait, et ce qu'elle ne fait pas

| | PC | Android |
|---|---|---|
| Analyser à la demande | oui | oui |
| Superviser un serveur | oui | oui |
| Coupe-circuit | oui | oui |
| Passer un ordre confirmé à la main | oui | oui |
| **Boucle automatique sans surveillance** | oui | **non** |

Android suspend les processus en arrière-plan. Un moteur qui doit poser un
stop juste après une entrée ne peut pas vivre dans un processus que le système
peut geler. Sur téléphone, l'application est un poste de pilotage ; la boucle
sans surveillance reste sur un serveur.

## Développement

```bash
python scripts/sync_engine.py      # recopier le moteur depuis service/server
cd app && pip install flet
flet run src/main.py               # lancer sur PC
```

Le moteur ne s'édite **jamais** dans `app/src/engine/` : il s'édite dans
`service/server/`, puis on relance `sync_engine.py`. Un test compare les deux
copies et échoue si elles divergent.

## Builds

```bash
flet build windows --build-version 1.0.0 --build-number 1
flet build apk --split-per-abi --build-version 1.0.0 --build-number 1
```
