## Installer

| Vous êtes sur | Fichier à prendre | Quoi en faire |
|---|---|---|
| Windows | `…-windows-setup.exe` | double-cliquer ; s'installe sans droits administrateur |
| Windows, sans installer | `…-windows-x64.zip` | décompresser où vous voulez, lancer l'exécutable |
| Linux | `…-linux-x64.tar.gz` | `tar xzf …`, puis lancer l'exécutable (GTK 3 requis) |
| Android | `…-arm64-v8a.apk` | autoriser les sources inconnues, ouvrir le fichier |

## À savoir avant de l'ouvrir

- L'application **ne contient aucun champ de clé API** et **ne passe aucun
  ordre**. Seule, elle analyse et simule ; reliée à un serveur, c'est le
  serveur qui détient les clés et qui décide.
- Elle **n'a jamais tourné sur un appareil réel** : ces binaires sortent de
  CI, c'est donc une préversion. La recette à faire est au §11 de
  [`docs/APP_PC_APK_FR.md`](../docs/APP_PC_APK_FR.md).
- L'APK ne porte votre signature que si les secrets Android du dépôt sont
  renseignés. Sinon c'est une signature de debug, bonne pour un test interne
  et rien de plus.
- Rien de tout cela n'est un conseil financier.
