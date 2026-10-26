# Construire, signer et livrer l'application

Ce document est la procédure. La conception — pourquoi Flet, pourquoi pas de
boucle sur téléphone, le modèle de données — est dans
[`APP_PC_APK_FR.md`](APP_PC_APK_FR.md).

> **Où en sont les builds.** Les trois cibles sortent de CI, vérifiées le
> 23/10/2026 sur le run 39994518717 :
>
> | Cible | Poids |
> |---|---|
> | APK `arm64-v8a` | 25,8 Mio |
> | Linux | 33,2 Mio |
> | Windows | 37,0 Mio |
>
> Les 25,8 Mio de l'APK confirment le pari de départ : un moteur en Python pur
> tient sous les 30 Mo, là où pandas et numpy en auraient coûté 55 de plus.
>
> Rien n'a été construit dans le conteneur qui a écrit ce code — il n'a ni
> Flutter, ni Android SDK, ni JDK 17 — et **l'application n'a jamais tourné sur
> un appareil**. La checklist du §11 du document de conception reste entière.

---

## 1. Prérequis

| Outil | Version | Pourquoi cette version |
|---|---|---|
| Python | 3.12+ | exigé par `app/pyproject.toml` |
| Flet | 0.86.5 | épinglé ; l'API de Flet bouge d'une version à l'autre |
| Flutter | **3.44.8** | Flet 0.86.5 refuse de construire sur un autre mineur |
| JDK | 17 | APK seulement |
| Android SDK | platform 36, build-tools | APK seulement |

La version de Flutter exigée n'est pas à deviner :

```bash
python -c "import flet.version; print(flet.version.flutter_version)"
```

`flet build` sait télécharger Flutter lui-même s'il ne le trouve pas. En CI on
l'installe explicitement, pour que le cache serve à quelque chose.

---

## 2. Construire en local

```bash
pip install 'flet[cli]==0.86.5'

make app-run                      # lancer l'application sur cette machine
make app-apk                      # APK arm64-v8a, signature de debug
make app-desktop                  # exécutable pour cette plateforme
FLET_TARGET=windows make app-desktop
BUILD_NUMBER=7 make app-apk
```

Sorties : `app/build/apk/`, `app/build/linux/`, `app/build/windows/`.

L'icône et les écrans d'accueil se régénèrent depuis la palette :

```bash
make app-icons                    # écrit src/assets/icon.png, splash*.png
```

---

## 3. Le keystore de release

À faire **une seule fois**, sur une machine à vous.

```bash
keytool -genkeypair -v -keystore aitrader-release.jks -alias aitrader \
        -keyalg RSA -keysize 4096 -validity 10000
```

Ce fichier et son mot de passe sont la **seule** chose qui permette de publier
une mise à jour de cette application. Les perdre oblige tous ceux qui l'ont
installée à désinstaller puis réinstaller, en perdant leur base locale. Donc :

- il ne va **jamais** dans le dépôt — `.gitignore` couvre déjà `*.jks` ;
- il se sauvegarde ailleurs que sur la machine qui construit ;
- le même keystore sert pour toutes les versions, à vie.

### Les quatre secrets GitHub

Dépôt → Settings → Secrets and variables → Actions :

| Secret | Contenu |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | le fichier `.jks` encodé en base64 |
| `ANDROID_KEYSTORE_PASSWORD` | le mot de passe du keystore |
| `ANDROID_KEY_PASSWORD` | le mot de passe de la clé (souvent le même) |
| `ANDROID_KEY_ALIAS` | `aitrader`, ou l'alias choisi à la création |

```bash
base64 -w0 aitrader-release.jks > keystore.b64   # Linux
base64 -i aitrader-release.jks | tr -d '\n' > keystore.b64   # macOS
```

Coller le contenu de `keystore.b64`, puis **supprimer ce fichier**.

Sans ces secrets, le workflow construit quand même : l'APK porte alors la
signature de debug et l'artefact s'appelle `…-UNSIGNED`. C'est bon pour un
essai entre vos appareils, pas pour une distribution — la clé de debug diffère
d'une machine à l'autre, et une mise à jour signée autrement sera refusée par
Android.

---

## 4. Livrer depuis CI

Workflow : [`.github/workflows/app-release.yml`](../.github/workflows/app-release.yml).

Deux façons de le déclencher :

```bash
git tag app-v1.0.0 && git push origin app-v1.0.0   # construit tout
```

ou l'onglet **Actions → Application build (PC and Android) → Run workflow**,
qui demande la cible (`apk`, `desktop`, `all`), un `versionCode`, et un
`release_tag`.

Avant de construire, le workflow vérifie que la copie du moteur dans
`app/src/engine/` est identique à `service/server/` et relance les tests de
l'application. Un moteur qui a dérivé livrerait des décisions fausses sur un
téléphone, là où c'est le plus difficile à remarquer.

### Le `versionCode`

Android compare ce nombre entier pour décider si un APK est une mise à jour.
Il doit **croître strictement** d'une livraison à l'autre. Par défaut le
workflow utilise `github.run_number`, qui augmente tout seul ; l'entrée
`build_number` permet de le forcer.

Le `versionName` affiché vient de `version` dans `app/pyproject.toml`.

### Publier une release téléchargeable

Laissé vide, `release_tag` ne produit que des artefacts d'Actions : visibles
uniquement connecté au dépôt, et effacés au bout de 30 jours. Rempli — par
exemple `app-v1.0.0` — le workflow crée en plus **une release GitHub** sur le
commit construit, avec un lien de téléchargement direct par plateforme :

| Fichier attaché | Pour qui |
|---|---|
| `ai-trader-<version>-windows-setup.exe` | Windows, installation en deux clics, sans droits administrateur |
| `ai-trader-<version>-windows-x64.zip` | Windows, version portable |
| `ai-trader-<version>-linux-x64.tar.gz` | Linux, GTK 3 requis |
| `app-arm64-v8a-release.apk` | Android |

C'est la release qui crée le tag, pas l'inverse : un `git push --tags` n'est
pas nécessaire — et depuis certains environnements, il est refusé.

Le texte de la release est [`.github/notes_publication_fr.md`](../.github/notes_publication_fr.md),
le programme d'installation Windows est engendré depuis
[`.github/installateur.iss.in`](../.github/installateur.iss.in). Les releases
sortent en **préversion** tant que la recette sur appareil du §11 du document
de conception n'est pas faite.

> Inno Setup est présent sur les images Windows de GitHub, mais une image peut
> changer. S'il manque, le build ne tombe pas : il émet un avertissement et la
> release ne contient que l'archive portable.

---

## 5. Vérifier l'APK avant de le diffuser

```bash
# Qui l'a signé ? « CN=Android Debug » = artefact non signé.
apksigner verify --print-certs app-arm64-v8a-release.apk

# Version affichée, versionCode, permissions, ABI
aapt2 dump badging app-arm64-v8a-release.apk | head -20
```

Trois choses à regarder :

1. **le certificat** — le vôtre, pas celui de debug ;
2. **les permissions** — `INTERNET` et `ACCESS_NETWORK_STATE`, et rien d'autre.
   Toute permission supplémentaire signale un plugin entré par la bande ;
3. **le poids** — autour de 25 à 30 Mo. Au-delà de 45 Mo, une dépendance
   native s'est glissée dans le paquet : le moteur, lui, est en Python pur et
   pèse 176 Ko.

---

## 6. Installer

### Sur un PC

Depuis la release GitHub :

- **Windows** — `ai-trader-<version>-windows-setup.exe`, puis suivre
  l'assistant. L'application s'installe dans le dossier de l'utilisateur, donc
  sans élévation. Windows peut afficher un avertissement SmartScreen tant que
  l'exécutable n'est pas signé par un certificat de confiance : « Informations
  complémentaires » → « Exécuter quand même ».
- **Windows, sans installer** — décompresser le `.zip` et lancer l'exécutable.
- **Linux** — `tar xzf ai-trader-<version>-linux-x64.tar.gz`, puis lancer
  l'exécutable. Il faut GTK 3 (`libgtk-3-0`), présent sur toute installation
  de bureau récente.

Depuis les sources, sans rien télécharger :

```bash
pip install 'flet[cli]==0.86.5'
make app-run
```

### Sur le téléphone

```bash
adb install -r app-arm64-v8a-release.apk
```

Ou par transfert de fichier, en autorisant les sources inconnues pour le
gestionnaire de fichiers. Le premier lancement est lent — l'application extrait
la bibliothèque standard et son code — d'où l'écran d'accueil.

Ensuite, la checklist de §11 du document de conception : installation, premier
lancement, analyse d'une paire réelle, rotation, mode sombre, coupure réseau.

---

## 7. Ce qui peut mal se passer

| Symptôme | Cause habituelle |
|---|---|
| `Flutter SDK … is required` | mauvais mineur de Flutter : prendre exactement celui qu'imprime `flet.version.flutter_version` |
| Le build Linux échoue sur `gtk/gtk.h` | dépendances manquantes : `ninja-build libgtk-3-dev clang cmake pkg-config` |
| L'APK s'installe mais remplace mal la précédente | `versionCode` non croissant, ou signature différente |
| « Application non installée » sans autre message | APK signée avec une autre clé que celle déjà installée |
| L'artefact s'appelle `…-UNSIGNED` | les secrets de signature ne sont pas en place (§3) |
| L'APK dépasse 45 Mo | une dépendance native est entrée dans `pyproject.toml` |
| `JniVersionCheck` / `Constant evaluation error` | les liaisons générées par `jnigen` attendent `jni` 1.1 et la résolution en retient une plus ancienne. `app/pyproject.toml` épingle `jni = "1.1.0"`. Épingler le greffon fautif ne sert à rien : le suivant prend sa place |
