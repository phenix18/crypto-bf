# Le robot de trading — guide d'exploitation

Ce document explique ce que le robot décide, comment le lancer, et ce qu'il
faut avoir vérifié avant de le laisser toucher à de l'argent réel.

---

## 1. Comment une décision est prise

Cinq étages, dans cet ordre. Chacun ne peut que **refuser** ce que le
précédent a laissé passer — aucun étage ne peut créer une position que celui
d'avant n'avait pas proposée.

```
bougies → stratégie → risque → calibration → conseil d'IA → ordres
```

| Étage | Question posée | Refuse quand |
|---|---|---|
| **Stratégie** | Le graphique propose-t-il quelque chose ? | pas de sens net, volatilité hors bornes, ratio gain/risque sous 1,3, contre-tendance |
| **Risque** | Le compte peut-il se le permettre, et pour quelle taille ? | coupe-circuit, perte du jour atteinte, pause après série perdante, trop de positions, pas de stop |
| **Calibration** | Ce type de configuration a-t-il déjà fonctionné ? | confluence sous le seuil (phase d'apprentissage), ou taux mesuré / espérance insuffisants (phase calibrée) |
| **Conseil** | Les quatre agents sont-ils d'accord ? | veto du risque ou de l'historien, deux vetos consultatifs, conviction sous le plancher |
| **Exécution** | L'ordre peut-il partir proprement ? | filtres de l'exchange, position déjà ouverte, bougie déjà tradée |

Chaque refus est écrit en clair dans la décision et dans le journal d'audit.
Un écran qui dit « rien » sans dire pourquoi est inutilisable.

---

## 2. Démarrer en local, sans réseau ni clés

```bash
make bootstrap                      # venv + dépendances
MARKET_DATA_PROVIDER=synthetic make api
```

Puis ouvrez `/auto`. Le bandeau indique **PAPER**, **SYNTHETIC**, **AUTO OFF** :
courtier papier, flux de démonstration, aucun ordre possible. C'est le mode
dans lequel tout le monde devrait commencer.

Pour voir la boucle tourner toute seule :

```bash
MARKET_DATA_PROVIDER=synthetic AUTO_TRADE_ENABLED=true \
AUTO_TRADE_PERIOD_SECONDS=30 make api
```

> Le flux `synthetic` est une marche aléatoire générée. Il sert à exercer la
> chaîne et l'interface, pas à juger une stratégie. Son nom voyage dans chaque
> réponse de l'API et s'affiche à l'écran, précisément pour qu'on ne le
> confonde jamais avec le marché.

---

## 3. L'échelle vers l'argent réel

Quatre barreaux. **Oublier un réglage fait toujours tomber du côté le plus
prudent**, jamais l'inverse.

| Barreau | Réglage | Effet |
|---|---|---|
| 0 | *(rien)* | courtier papier, données testnet, aucun ordre |
| 1 | `PAPER_TRADING=false` | ordres envoyés au **testnet** Binance |
| 2 | `BINANCE_TESTNET=false` | **refusé seul** — le barreau 3 est obligatoire |
| 3 | `LIVE_TRADING_ARMED=true` | carnet réel |

Au premier ordre réel, le courtier demande à Binance ce que la clé a le droit
de faire et **refuse si les retraits sont activés**. Si la permission ne peut
pas être lue, il refuse aussi : un jeu de permissions inconnu n'est pas un jeu
de permissions sûr.

Et indépendamment de tout ça, `AUTO_TRADE_ENABLED=false` (le défaut) interdit
le moindre ordre : toute l'analyse tourne et rapporte, rien ne part.

---

## 4. Lire les quatre nombres

On les confond facilement. Ils ne disent pas la même chose.

- **Confluence** (0–100) — *l'accord*. Parmi les contrôles qui ont une lecture,
  quelle proportion tire dans le même sens. Une tendance de manuel vaut ~69 ;
  rien n'a jamais atteint 90.
- **Participation** (0–100 %) — *combien ont parlé*. Deux contrôles d'accord,
  ce n'est pas une confluence. Sous 45 %, la proposition est bloquée.
- **Conviction** (0–1) — *le verdict du conseil*. Plafonnée par la confluence :
  l'accord des agents peut la faire baisser, jamais monter. Un modèle qui
  hallucine peut donc refuser un bon trade, mais pas en inventer un mauvais.
- **Probabilité** (%) — *la seule vraie*. Le taux de réussite mesuré sur les
  trades réellement fermés dans ce contexte, en borne basse de Wilson. Absente
  tant qu'il n'y a pas 30 trades décidés dans le compartiment.

### Sur « 90 % de chance »

Deux choses, souvent mélangées :

1. **Confluence 90 = ne jamais trader.** Mesuré sur 300 séries : rien
   d'exploitable n'a dépassé 49 avant correction de l'échelle, et ~74 après.
   Le seuil est réglable (`CONFLUENCE_GATE`, défaut 65), mais le mettre à 90
   revient à éteindre le robot — et un robot qui ne trade jamais ne remplit
   jamais un compartiment, donc n'atteint jamais la phase où une vraie
   probabilité devient mesurable.
2. **90 % de taux de réussite à un ratio 1,3:1 n'existe pas.** Ce qui fait
   gagner de l'argent, c'est l'espérance : 8 gains de 0,15R contre 2 pertes de
   1R, c'est 80 % de réussite et −0,08R par trade. Le portail exige donc les
   deux : un taux au-dessus de 55 % **et** une espérance positive.

---

## 5. Réglages qui comptent

Tous documentés dans `.env.example`. Les plus utiles :

| Variable | Défaut | À quoi ça sert |
|---|---|---|
| `AUTO_TRADE_ENABLED` | `false` | autorise les ordres |
| `CONFLUENCE_GATE` | `65` | sélectivité pendant l'apprentissage |
| `WATCHLIST` | `BTCUSDT,ETHUSDT,SOLUSDT` | ce que la boucle surveille |
| `AUTO_TRADE_INTERVAL` | `5m` | unité de temps des bougies |
| `AUTO_TRADE_PERIOD_SECONDS` | `60` | fréquence de balayage (plancher 15 s) |
| `OPENROUTER_API_KEY` | *(vide)* | active les agents IA ; sans clé ils tournent sur leur raisonnement déterministe |

---

## 6. Avant d'armer en réel

- [ ] Tourné plusieurs jours en **papier** sur le flux Binance réel, et relu le
      journal d'audit : les refus sont-ils ceux que vous auriez faits ?
- [ ] Tourné sur **testnet** : les ordres partent, l'OCO se pose, les filtres
      de l'exchange passent.
- [ ] Clé API **sans droit de retrait**, créée pour ça et pour rien d'autre.
- [ ] Les limites de `risk_manager.py` relues et acceptées : 0,5 % du capital
      par trade, 3 % de perte journalière, 15 % de drawdown pour le
      coupe-circuit.
- [ ] Vous savez où est le bouton d'arrêt (**启用熔断 / Engage kill switch** sur
      l'écran, ou `POST /api/auto/kill-switch`) et que le relâcher demande
      `confirm: "release"`.

---

## 7. Rejouer un historique

```bash
make simulation                              # 3 000 bougies × 3 symboles
python scripts/simulation.py --graine 11     # un autre tirage
python scripts/simulation.py --derive 0      # du bruit sans tendance
```

Le script passe par `AutoTrader.trade()`, donc par le code de production, et
imprime où les analyses se sont arrêtées, ce que le conseil a dit, les trades,
le capital et l'état des paquets de calibration.

**Lisez-le pour la mécanique, pas pour la rentabilité.** Mesuré le 24/10/2026
sur trois graines : environ 1 % des analyses aboutissent à un trade, et
l'espérance ressort à +0,3 R. Mais avec `--derive 0`, la même configuration
tombe à **−0,23 R par trade**. Le résultat positif par défaut mesure la dérive
du générateur, pas un avantage : les sorties se font à +1,67 R contre −1,00 R,
il faudrait donc 37 % de réussite pour être à l'équilibre sur une marche sans
dérive, et la simulation en relève 29 %.

Ce que la simulation prouve vraiment : les portails refusent, les positions se
dénouent, les R se calculent, et un paquet de calibration qui atteint 30 trades
tranchés avec 39 % de réussite se ferme bien tout seul. C'est le sujet.

---

## 8. Ce qui n'est pas fait

À savoir avant de compter dessus :

- **Le spot ne peut pas shorter.** Un signal short est refusé avec cette raison
  plutôt que transformé en vente de vos avoirs. Futures ou marge = autre
  chantier.
- **Les positions sont réglées sur les bougies**, pas sur le statut réel des
  ordres chez l'exchange. Quand une bougie couvre à la fois le stop et la
  cible, elle est comptée comme une perte — la bougie ne dit pas lequel est
  venu en premier, et supposer le bon rend la calibration optimiste. Réconcilier
  avec l'exchange est l'étape suivante.
- **Les exécutions du courtier papier sont optimistes** : ni slippage ni
  exécution partielle. Les résultats papier flattent la réalité.
- **Aucun de ces chiffres n'est un conseil financier**, et un robot qui a bien
  marché sur l'historique peut perdre demain.
