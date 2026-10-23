# Version PC et APK — conception

Comment le robot sort du navigateur : une seule base de code Python/Flet qui
produit un exécutable Windows/Linux **et** une APK Android, posée sur le moteur
de trading déjà écrit.

---

## 1. Le choix de pile, en une phrase

**Flet** (interface Flutter, logique Python embarquée), parce que le moteur de
trading est déjà écrit en Python **n'utilisant que la bibliothèque standard** —
le réembarquer tel quel coûte 176 Ko, alors que le réécrire en Kotlin ou en
JavaScript le dupliquerait et le ferait diverger.

Vérifié, pas supposé :

```
$ du -sh engine/          # les 9 modules copiés seuls
176K
$ python -I -c "from auto_trader import build_auto_trader; ..."
isolated engine ok -> strategy 27.4
```

Les neuf modules (`indicators`, `strategy_engine`, `risk_manager`,
`calibration`, `market_data`, `broker`, `ai_agents`, `llm_client`,
`auto_trader`) n'importent que `dataclasses`, `enum`, `typing`, `math`,
`json`, `os`, `time`, `hashlib`, `hmac`, `random`, `datetime` et `urllib` —
plus eux-mêmes. Ni pandas, ni numpy, ni FastAPI.

C'est le point qui rend l'APK raisonnable. Le piège habituel de cette pile,
c'est pandas + numpy à 55 Mo ; ici le poids sera celui du moteur Flutter et de
CPython, pas celui de l'application.

### Ce qu'on écarte, et pourquoi

| Pile | Pourquoi pas |
|---|---|
| Kotlin natif | il faudrait réécrire les cinq étages de décision, et deux implémentations d'une règle de risque finissent toujours par diverger |
| Capacitor / PWA | pas de moteur local : l'application redevient un client qui ne sait rien faire sans serveur |
| Electron (PC seul) | règle le PC et pas Android, et pèse plus lourd que Flet pour le même résultat |

---

## 2. La décision de conception qui compte : le téléphone n'est pas le robot

**Android suspend les processus en arrière-plan.** Un moteur qui doit poser un
stop dans la seconde qui suit une entrée ne peut pas vivre dans un processus
que le système peut geler, et une position ouverte dont l'application a été
tuée est exactement l'état que tout le reste du projet s'efforce d'éviter.

Donc :

| Plateforme | Ce qu'elle a le droit de faire |
|---|---|
| **Serveur** (déjà là) | la boucle automatique sans surveillance, et l'exécution : seul |
| **PC** | analyse à la demande, supervision, coupe-circuit |
| **Android** | analyse à la demande, supervision, coupe-circuit |

L'application livrée est un **poste de pilotage**, sur téléphone comme sur PC.
Elle lit, elle montre, elle permet d'arrêter ; elle ne laisse pas une boucle
tourner seule et **ne passe aucun ordre elle-même**.

Le PC aurait pu tenir une boucle — un ordinateur de bureau ne gèle pas ses
processus comme un téléphone. Elle n'y est pas, pour deux raisons : la boucle
existe déjà dans `service/`, où elle tourne sans interface et survit à la
fermeture d'une fenêtre ; et une application qui ne passe aucun ordre n'a
aucune raison de détenir une clé d'échange, ce qui retire de l'APK le seul
secret qu'elle aurait pu contenir. La même décision sert les deux plateformes.

Ce n'est pas une limite technique qu'on contourne plus tard : c'est le
comportement voulu.

Deux modes de fonctionnement, choisis dans les réglages :

- **Local** — le moteur embarqué parle directement à l'API publique de
  Binance. Aucun serveur. Analyse et supervision du portefeuille papier.
- **Relié** — l'application interroge `/api/auto/*` d'un serveur que vous
  faites tourner. C'est le mode où la boucle automatique existe vraiment, et
  où le téléphone sert de hublot et de bouton d'arrêt.

---

## 3. Modèle REA

Le domaine est comptable avant d'être technique : chaque trade est un couple
d'événements qui déplacent des ressources entre des agents. Le modèle REA
donne le schéma local avant qu'on dessine le moindre écran.

### 3.1 Entités

**Ressources** — ce qui est transféré ou transformé

| Ressource | Nature | Mesure |
|---|---|---|
| Liquidités | fonds (USDT) | montant |
| Position | actif détenu (BTC, ETH…) | quantité |
| Budget de risque | engagement de fonds | montant, et R |
| Données de marché | ressource informationnelle | série OHLCV |
| Droit d'accès | clé API (trade, jamais retrait) | permissions |
| Quota de modèle | service consommable | appels restants |

**Événements** — ce qui augmente ou diminue une ressource

| Événement | Effet |
|---|---|
| Observation | acquiert la donnée de marché |
| Proposition | produit un candidat (pas encore économique) |
| Décision | engage le budget de risque, ou refuse |
| Entrée | −liquidités, +position |
| Protection | engage une sortie (OCO) |
| Clôture | −position, +liquidités |
| Frais | −liquidités |
| Halte | suspend tout engagement nouveau |

**Agents** — qui participe

| Agent | Interne ? |
|---|---|
| Trader (vous) | oui |
| Moteur (stratégie, risque, calibration) | oui |
| Conseil IA : analyste, risque, historien, macro | oui |
| Exchange (Binance) | non |
| Fournisseur de modèle (OpenRouter) | non |

### 3.2 Dualités

| Événement | Contrepartie | Ce que le couple mesure |
|---|---|---|
| Entrée | Clôture | le trade : liquidités sorties puis rentrées |
| Décision | Entrée | engagement → réalisation |
| Protection | Clôture | l'OCO est rempli, ou annulé |
| **Budget de risque consenti** | **R réalisé** | **ce que la calibration lit** |
| Observation | Proposition | dualité informationnelle, pas comptable |

La quatrième ligne est celle qui fait vivre l'apprentissage : sans apparier le
risque consenti à l'entrée au résultat mesuré à la clôture, il n'y a pas de R,
donc pas d'espérance, donc pas de portail calibré.

### 3.3 Schéma SQLite

```sql
CREATE TABLE agent (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('trader','engine','ai_agent','exchange','model_provider')),
  name TEXT NOT NULL,
  internal INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE resource (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('cash','position','market_data','api_access','model_quota')),
  symbol TEXT, unit TEXT NOT NULL
);

-- La donnée qui a servi à décider, pour pouvoir rejouer la décision
CREATE TABLE observation (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL, source TEXT NOT NULL,
  symbol TEXT NOT NULL, interval TEXT NOT NULL,
  bar_open_time INTEGER NOT NULL, candle_count INTEGER NOT NULL,
  UNIQUE (symbol, interval, bar_open_time, source)
);

CREATE TABLE proposal (
  id INTEGER PRIMARY KEY,
  observation_id INTEGER NOT NULL REFERENCES observation(id),
  direction TEXT NOT NULL, regime TEXT NOT NULL,
  confluence REAL NOT NULL, participation REAL NOT NULL,
  entry REAL NOT NULL, stop_loss REAL, take_profit REAL, risk_reward REAL,
  actionable INTEGER NOT NULL, blockers TEXT,
  votes_json TEXT NOT NULL, readings_json TEXT NOT NULL
);

CREATE TABLE decision (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposal(id),
  decided_at TEXT NOT NULL,
  stage TEXT NOT NULL,            -- cf. STAGES dans auto_trader.py
  approved INTEGER NOT NULL,
  quantity REAL NOT NULL DEFAULT 0,
  risk_amount REAL NOT NULL DEFAULT 0,
  notional REAL NOT NULL DEFAULT 0,
  conviction REAL, probability REAL, basis TEXT,
  reasons TEXT NOT NULL
);

-- Participation REA : qui a dit quoi, et sur quelle base
CREATE TABLE decision_opinion (
  decision_id INTEGER NOT NULL REFERENCES decision(id) ON DELETE CASCADE,
  agent_id INTEGER NOT NULL REFERENCES agent(id),
  stance TEXT NOT NULL CHECK (stance IN ('confirm','abstain','veto')),
  confidence REAL NOT NULL, rationale TEXT, source TEXT,
  PRIMARY KEY (decision_id, agent_id)
);

-- Engagement : un ordre envoyé. client_order_id porte l'idempotence.
CREATE TABLE order_commitment (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decision(id),
  client_order_id TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('entry','protective','unwind','manual_exit')),
  side TEXT NOT NULL, symbol TEXT NOT NULL, venue TEXT NOT NULL,
  quantity REAL NOT NULL, price REAL, stop_price REAL, take_profit REAL,
  sent_at TEXT NOT NULL, status TEXT NOT NULL
);

-- Flux réel de ressource
CREATE TABLE economic_event (
  id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES order_commitment(id),
  at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('entry_fill','exit_fill','fee','deposit','withdrawal','adjustment')),
  resource_id INTEGER NOT NULL REFERENCES resource(id),
  direction TEXT NOT NULL CHECK (direction IN ('inflow','outflow')),
  quantity REAL NOT NULL, unit_price REAL, amount REAL NOT NULL,
  provider_agent_id INTEGER REFERENCES agent(id),
  receiver_agent_id INTEGER REFERENCES agent(id)
);

-- La dualité, matérialisée : c'est cette table que lit la calibration
CREATE TABLE trade_duality (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decision(id),
  entry_event_id INTEGER NOT NULL REFERENCES economic_event(id),
  exit_event_id INTEGER REFERENCES economic_event(id),
  symbol TEXT NOT NULL, direction TEXT NOT NULL, regime TEXT NOT NULL,
  confluence REAL NOT NULL,
  risk_committed REAL NOT NULL,
  r_multiple REAL, pnl REAL,
  outcome TEXT CHECK (outcome IN ('win','loss','scratch')),
  reason TEXT, closed_at TEXT
);

CREATE TABLE account_snapshot (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL,
  equity REAL NOT NULL, peak_equity REAL NOT NULL,
  realised_pnl_today REAL NOT NULL, open_positions INTEGER NOT NULL,
  consecutive_losses INTEGER NOT NULL, kill_switch INTEGER NOT NULL
);

CREATE TABLE halt_event (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL,
  halt TEXT NOT NULL, engaged INTEGER NOT NULL, by_agent_id INTEGER REFERENCES agent(id),
  reason TEXT
);

CREATE INDEX idx_trade_bucket ON trade_duality (regime, direction, confluence);
CREATE INDEX idx_event_at ON economic_event (at);
CREATE INDEX idx_decision_at ON decision (decided_at);
```

`idx_trade_bucket` n'est pas décoratif : c'est exactement la clé de
compartiment que `calibration.py` interroge (régime, sens, bande de
confluence).

### 3.4 Diagramme

```
     [Trader] ────────────┐
                          │ participe
     [Moteur] ───────┐    │
                     ▼    ▼
 (Observation) ──> (Proposition) ──> (Décision) ──participation──> [Conseil IA]
      ▲                                   │
      │ fournit                           │ engage
 [Exchange]                               ▼
      │                            (Ordre d'entrée) ──> ⟨Liquidités −⟩ ⟨Position +⟩
      │                                   │
      │                              ▲ DUALITÉ ▼
      │                                   │
      └──────── exécute ──────────> (Clôture) ──────> ⟨Position −⟩ ⟨Liquidités +⟩
                                          │
                                          └──> risque consenti ⇄ R réalisé ──> calibration
```

---

## 4. Arborescence

```
app/                             # la nouvelle application, PC et Android
  pyproject.toml                 # configuration flet build
  HYPOTHESES.md
  src/
    main.py                      # point d'entrée Flet, thème, navigation
    engine/                      # LE MOTEUR, copié depuis service/server (cf. §5)
      indicators.py  strategy_engine.py  risk_manager.py  calibration.py
      market_data.py  broker.py  ai_agents.py  llm_client.py  auto_trader.py
    coeur/                       # logique applicative pure, sans flet
      config.py                  # nom, version, palette, réglages par défaut
      stockage.py                # SQLite : le schéma REA ci-dessus
      session.py                 # mode local/relié, clés, watchlist
      distant.py                 # client HTTP de /api/auto/*
      formats.py                 # nombres, dates, R, pourcentages en français
    vues/                        # une fonction par écran, aucune logique métier
      cockpit.py  analyse.py  positions.py  reglages.py  journal.py
    assets/                      # icon.png, splash.png
  tests/                         # pytest sur coeur/ et engine/, sans UI
scripts/
  sync_engine.py                 # recopie les 9 modules dans app/src/engine
```

Deux règles tenues partout : `coeur/` et `engine/` n'importent jamais `flet`,
et `vues/` ne contient aucune règle de trading. C'est ce qui permet de tester
l'essentiel avec pytest sur PC, sans émulateur.

---

## 5. Le moteur partagé, sans copier-coller à la main

Les neuf modules vivent dans `service/server/` et s'y importent à plat. Le
packageur Flet n'embarque que ce qui est sous `src/`, donc ils doivent y être
physiquement.

La réponse n'est ni un lien symbolique (que le packageur peut suivre ou pas
selon la plateforme) ni un copier-coller qu'on oublie de refaire :

- `scripts/sync_engine.py` recopie les neuf fichiers ;
- un test compare les deux copies octet par octet et **échoue** dès qu'elles
  divergent, avec le nom du fichier et la commande à lancer.

Une divergence silencieuse entre le moteur du serveur et celui du téléphone
donnerait deux robots qui ne décident pas pareil à partir des mêmes bougies.
Le test est là pour que ça ne puisse pas arriver discrètement.

---

## 6. Dépendances et poids attendu

```toml
dependencies = [
  "flet==0.86.5",
]
```

C'est tout. Le moteur n'a besoin de rien ; SQLite est dans la bibliothèque
standard ; les appels HTTP passent par `urllib`.

| Poste | Taille attendue |
|---|---|
| Moteur Flutter + CPython embarqué | ~25–30 Mo |
| Moteur de trading | 0,2 Mo |
| Interface et ressources | ~1 Mo |
| **APK (arm64-v8a)** | **~30 Mo** |

À comparer aux 61 Mo de FidAnalyst, dont 55 de pandas/numpy. Ne pas ajouter
pandas : tout ce dont l'application a besoin existe déjà en Python pur dans
`indicators.py`.

---

## 7. Permissions Android, et leur justification

| Permission | Pourquoi | Si refusée |
|---|---|---|
| `INTERNET` | lire les bougies Binance, joindre le serveur | l'application ne fonctionne qu'en rejeu |
| `ACCESS_NETWORK_STATE` | dire « hors ligne » au lieu d'afficher une erreur brute | message moins clair |

Et c'est tout. Pas de stockage externe : la base vit dans le dossier privé de
l'application, et l'export d'un rapport passe par le partage Android, qui ne
demande aucune permission. `MANAGE_EXTERNAL_STORAGE` est refusée par le Play
Store sauf cas précis, et rien ici n'en a besoin.

---

## 8. Builds

```bash
# PC
flet build windows --build-version 1.0.0 --build-number 1
flet build linux   --build-version 1.0.0 --build-number 1

# Android
flet build apk --split-per-abi --build-version 1.0.0 --build-number 1
# sortie : build/apk/app-arm64-v8a-release.apk
```

Prérequis : Flutter compatible avec la version de Flet épinglée, JDK 17,
Android SDK (platform 36, build-tools), Python 3.12+.

CI : un workflow GitHub Actions sur `ubuntu-latest` (`actions/setup-java@v4`
avec Java 17, `subosito/flutter-action@v2`, `pip install flet`) publie l'APK en
artefact à chaque tag. Le `.exe` Windows demande un runner `windows-latest`.

---

## 9. Signature

- Un keystore de release généré **une fois**, gardé hors du dépôt :
  ```bash
  keytool -genkeypair -v -keystore release.jks -alias aitrader \
          -keyalg RSA -keysize 4096 -validity 10000
  ```
- Mot de passe et alias par variables d'environnement ou secrets GitHub,
  jamais dans un fichier versionné.
- Le même keystore pour toutes les versions : en changer rend les mises à jour
  impossibles pour qui a déjà installé l'application.
- `versionCode` incrémenté à chaque livraison.
- La clé de debug suffit pour un test entre vos appareils ; elle ne convient
  pas pour une distribution, car elle diffère d'une machine à l'autre.

---

## 10. Pièges, appliqués à ce projet

| Piège | Ce qu'on en fait |
|---|---|
| **pandas/numpy à 55 Mo** | évité par construction : le moteur est en Python pur |
| **Processus suspendu par Android** | assumé : pas de boucle automatique sur téléphone (§2) |
| **Premier lancement lent** (extraction de la stdlib) | écran d'accueil avec indicateur, pas un écran blanc |
| **Secrets dans l'APK** | le `.pyc` ne protège rien : les clés API sont saisies par l'utilisateur et stockées dans le dossier privé, jamais livrées dans l'application |
| **Horloge du téléphone** | Binance rejette une requête signée dont l'horodatage dérive ; l'écart serveur est mesuré au démarrage et affiché s'il dépasse le seuil |
| **Wheels Android** | ne rien ajouter qui ait besoin de compilation native ; la liste de dépendances tient en une ligne, qu'il faut garder ainsi |
| **Encodage** | UTF-8 partout, dates et nombres au format français |
| **Opérations lourdes** | l'analyse de 300 bougies passe par `page.run_task`, jamais sur le fil de l'interface |

---

## 11. Checklist de test sur appareil

À faire avant de considérer une APK livrable — aucun de ces points ne se
vérifie depuis un émulateur de bureau :

- [ ] Installation sur un appareil neuf, sans l'application précédente
- [ ] Premier lancement : l'écran d'accueil s'affiche, pas un écran blanc
- [ ] Analyse d'une paire réelle en mode local, réseau mobile
- [ ] Même analyse hors ligne : message clair, pas une trace d'erreur
- [ ] Mode relié : l'application retrouve un serveur, affiche son état et son mode
- [ ] Coupe-circuit : engagé depuis le téléphone, visible dans l'interface web
- [ ] Rotation de l'écran pendant une analyse : rien ne se perd
- [ ] Mode sombre et mode clair
- [ ] Mise en arrière-plan pendant 10 minutes puis retour : l'état est cohérent
- [ ] Horloge décalée volontairement : l'application le détecte et le dit
- [ ] Désinstallation : aucune donnée laissée hors du dossier privé

---

## 12. Ce qui reste à décider

- **Nom et marque** de l'application (la compétence propose ANUBIS LABS par
  défaut ; à confirmer).
- **Distribution** : APK partagée directement, ou Play Store ? Le Play Store
  impose des contraintes supplémentaires sur une application qui touche à des
  comptes d'échange.
- **Licence hors-ligne** : le motif Ed25519 de FidAnalyst est disponible si
  l'application doit être commerciale ; inutile pour un usage personnel.
