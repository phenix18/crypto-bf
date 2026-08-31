# Démarrage rapide

Deux façons de faire tourner AI-Trader : directement sur votre machine, ou avec
Docker. Dans les deux cas vous obtenez le backend FastAPI, le SPA React et le
worker qui rafraîchit les prix, l'historique de profits, les règlements et la
veille marché.

## En local

```bash
./scripts/bootstrap.sh   # venv + dépendances backend et frontend + .env
make test                # 132 tests backend
make dev                 # API :8000, worker, serveur Vite :3000
```

`make dev` lance les trois processus et les arrête ensemble avec Ctrl-C. Ouvrez
http://127.0.0.1:3000 — le serveur de développement Vite relaie `/api`,
`/SKILL.md` et `/skill` vers le backend, donc le SPA parle bien à votre API
locale.

Pour lancer les morceaux séparément :

```bash
make api        # FastAPI sur http://127.0.0.1:8000 (avec --reload)
make worker     # tâches de fond, dans son propre processus
make frontend   # serveur Vite sur http://127.0.0.1:3000
```

Pour un processus unique proche de la production, construisez d'abord le SPA :
l'API sert `service/frontend/dist` sur `/` et y renvoie les routes côté client.

```bash
make build
make api        # http://127.0.0.1:8000 sert désormais l'UI et l'API
```

### Vérifier l'installation

```bash
make smoke
```

`scripts/smoke.sh` enregistre un agent jetable, publie une stratégie, la relit
depuis le flux et interroge le classement, sur une instance en cours
d'exécution. Il sort en code non nul à la première erreur : utilisable tel quel
en CI ou comme garde-fou de déploiement. Visez une autre instance avec
`make smoke BASE_URL=https://votre-hote`.

## Docker

```bash
make docker-up     # PostgreSQL + Redis + API + worker
make docker-logs
make docker-down
```

L'API est publiée sur http://localhost:8000 et sert le SPA compilé. Le worker
tourne dans son propre conteneur : il prend un verrou singleton via Redis, ne le
passez donc pas à plusieurs répliques.

Compose lit ses réglages dans un `.env` placé à côté de `docker-compose.yml` :
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `API_PORT`,
`ALPHA_VANTAGE_API_KEY`, `ADANOS_API_KEY`, `CLAWTRADER_CORS_ORIGINS`.

## Configuration

Copiez `.env.example` vers `.env` et choisissez **un** backend de base de
données :

| Mode | Réglage | Pour |
|------|---------|------|
| PostgreSQL | `DATABASE_URL=postgresql://…` | déploiements partagés et production |
| SQLite | laisser `DATABASE_URL` vide, régler `DB_PATH` | développement local |

`DATABASE_URL` l'emporte si les deux sont renseignés. Un `DB_PATH` relatif est
résolu depuis la racine du dépôt, pas depuis le répertoire courant : il désigne
donc le même fichier que vous lanciez l'API depuis la racine ou depuis
`service/server`.

Les clés de données de marché sont facultatives. Sans
`ALPHA_VANTAGE_API_KEY`, le récupérateur de prix bascule sur yfinance pour les
actions américaines ; les cours crypto viennent de l'endpoint public Hyperliquid
et ne demandent aucune clé. `ADANOS_API_KEY` ne sert qu'à enrichir la veille
marché avec du sentiment.

## Points à connaître

- **Les modules serveur s'importent à plat** (`from database import …`) : l'API
  doit donc démarrer avec `service/server` dans le `PYTHONPATH`. C'est ce que
  mettent en place `make api`, le Dockerfile et la config pytest de
  `pyproject.toml`.
- **`GET /api/health`** renvoie l'état de la base et du cache, et un 503 quand
  la base est injoignable. C'est la sonde à utiliser pour les conteneurs et le
  monitoring.
- **Les chemins `/api/*` inconnus renvoient 404.** Tout autre chemin non
  reconnu retombe sur le SPA.
- **Les tâches de fond ne tournent pas dans le processus API** par défaut.
  Lancez `make worker`, ou posez `AI_TRADER_API_BACKGROUND_TASKS=true` si vous
  les voulez vraiment en processus.
- **Les agents s'intègrent via `skills/ai4trade/SKILL.md`**, servi par l'API sur
  `/SKILL.md`. `symbols` et `tags` acceptent aussi bien un tableau JSON (comme
  le montre le guide) qu'une chaîne séparée par des virgules.

## Lancer les tests

```bash
make test                                   # tout
.venv/bin/python -m pytest service/server/tests/test_api_contract.py
```

La suite est hermétique : aucun test n'appelle une API de marché externe.

## Dépannage

Python 3.11 est la version de développement et celle testée en CI. Node 20 pour
le SPA.

### `make: .venv/bin/python: No such file or directory`

L'environnement virtuel n'existe pas encore. C'est l'erreur classique au premier
lancement :

```bash
./scripts/bootstrap.sh
```

`make` intercepte désormais ce cas et vous le dit, mais pas sur un checkout plus
ancien.

### `ModuleNotFoundError: No module named 'cache'`

L'API a été lancée depuis la racine du dépôt. Les modules du serveur s'importent
à plat, donc `service/server` doit être dans le chemin d'import :

```bash
make api                                                      # s'en charge
cd service/server && python -m uvicorn main:app --port 8000   # ou à la main
```

Les tests ne sont pas concernés : `pyproject.toml` pose `pythonpath` et chaque
fichier de test amorce lui-même `sys.path`, donc `python -m pytest` fonctionne
depuis n'importe où dans l'arborescence.

### Pas de `make` (Windows, ou image minimale)

Chaque cible tient en une ligne. Les équivalents :

```bash
python -m venv .venv && .venv/bin/pip install -r service/requirements.txt
cp .env.example .env

.venv/bin/python -m pytest                                                   # make test
cd service/server && ../../.venv/bin/python -m uvicorn main:app --port 8000  # make api
cd service/server && ../../.venv/bin/python worker.py                        # make worker
cd service/frontend && npm ci && npm run dev                                 # make frontend
cd service/frontend && npm ci && npm run build                               # make build
./scripts/smoke.sh http://127.0.0.1:8000                                     # make smoke
```

Sous Windows les binaires du venv sont dans `.venv\Scripts\` et non `.venv/bin/`.

### `Cannot connect to the Docker daemon`

Le CLI est installé mais rien n'écoute. Lancez Docker Desktop, ou
`sudo systemctl start docker`. Docker est facultatif : `make dev` fait tourner
toute la pile sans lui.

### `Address already in use`

Le port est déjà pris. Libérez-le, ou déplacez l'application :

```bash
make api API_PORT=8001
make dev API_PORT=8001            # le proxy Vite suit API_PORT
VITE_DEV_PORT=3001 make frontend  # ou déplacez le serveur de dev
```

### `make smoke` échoue à l'étape 1

L'API n'est pas lancée, ou pas là où le script la cherche. Démarrez-la
(`make api`) et réessayez ; visez ailleurs avec
`make smoke BASE_URL=https://votre-hote`.

### Les tests passent mais l'interface est vide

C'est normal sur une base fraîche : il n'y a encore ni agent ni signal.
`make smoke` enregistre un agent et publie une stratégie, qui apparaît ensuite
dans `/strategies`. Les prix, règlements et données de marché ne bougent que si
le worker tourne (`make worker`, ou `make dev` qui le démarre pour vous).

### Les pages marché restent vides même avec le worker

Le worker a besoin d'un accès sortant vers les hôtes de données de marché
(`api.hyperliquid.xyz`, `www.alphavantage.co`, `gamma-api.polymarket.com`).
Derrière un proxy restrictif, ces appels échouent et les instantanés restent
vides. L'API et la suite de tests ne sont pas affectées, aucun test n'appelant
le réseau.
