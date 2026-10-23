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
| Boucle automatique sur PC | oui, tant que la fenêtre est ouverte | un ordinateur de bureau ne gèle pas ses processus comme un téléphone |
| Partage du moteur | copie vérifiée par un test | ni lien symbolique (le packageur peut ne pas le suivre) ni copier-coller manuel (qu'on oublie) |
| Stockage | SQLite dans le dossier privé | aucune permission de stockage nécessaire ; l'export passe par le partage Android |
| ABI | `arm64-v8a` seule | tous les téléphones courants depuis 2017 ; une APK universelle doublerait le poids |
| `minSdk` | 24 (Android 7) | valeur de la compétence, suffisante et courante |
| Clés API | saisies par l'utilisateur, jamais livrées | un `.pyc` ne protège rien ; un secret dans une APK est un secret public |

## Ce que je n'ai pas tranché

- **Le mode par défaut au premier lancement** : local (moteur embarqué) ou
  relié (serveur) ? Posé actuellement sur *local*, parce qu'il fonctionne sans
  rien installer d'autre.
- **La fréquence de rafraîchissement** sur mobile : 15 s comme l'interface
  web vide une batterie ; 60 s est proposé, à ajuster à l'usage.
