# Hypothèses

Les choix faits sans vous attendre, pour que le projet avance. Chacun se
défait en une ligne si vous en décidez autrement.

## Produit

| Sujet | Choix | Pourquoi |
|---|---|---|
| Nom | `AI-Trader`, organisation `com.aitrader` | reprend le nom du dépôt plutôt que la marque ANUBIS LABS proposée par défaut dans la compétence : ce projet a déjà un nom |
| Langue | français d'abord, puis anglais et chinois | c'est la langue dans laquelle ce projet est lu |
| Licence hors-ligne | **pas** de verrou Ed25519 | le motif existe dans la compétence et reste disponible, mais il n'a de sens que pour une application commerciale ; ici l'usage est personnel |
| Distribution | APK partagée directement | le Play Store impose des contraintes supplémentaires à une application qui touche à des comptes d'échange ; à rouvrir si vous visez une publication |

## Technique

| Sujet | Choix | Pourquoi |
|---|---|---|
| Pile | Flet (Flutter + Python embarqué) | le moteur est déjà en Python pur : le réembarquer coûte 176 Ko, le réécrire le ferait diverger |
| Boucle automatique sur Android | **non** | Android suspend les processus en arrière-plan ; une position ouverte dont le processus a été tué est l'état que tout le reste du projet évite |
| Boucle automatique sur PC | **non** dans l'application | l'application est un poste de lecture et de pilotage ; la boucle vit dans `service/`, qui tourne déjà sans interface et que l'application pilote en mode relié |
| Partage du moteur | copie vérifiée par un test | ni lien symbolique (le packageur peut ne pas le suivre) ni copier-coller manuel (qu'on oublie) |
| Stockage | SQLite dans le dossier privé | aucune permission de stockage nécessaire ; l'export passe par le partage Android |
| ABI | `arm64-v8a` seule | tous les téléphones courants depuis 2017 ; une APK universelle doublerait le poids |
| `minSdk` | 24 (Android 7) | valeur de la compétence, suffisante et courante |
| Clés API | **aucun champ** dans l'application | elle ne passe aucun ordre : en local elle simule, en relié c'est le serveur qui détient les clés. Il n'y a donc rien à extraire de l'APK — un `.pyc` ne protégerait rien |

## Ce que je n'ai pas tranché

- **Le mode par défaut au premier lancement** : local (moteur embarqué) ou
  relié (serveur) ? Posé actuellement sur *local*, parce qu'il fonctionne sans
  rien installer d'autre.
- **La fréquence de rafraîchissement** sur mobile : 15 s comme l'interface
  web vide une batterie ; 60 s est proposé, à ajuster à l'usage.
