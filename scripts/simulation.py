#!/usr/bin/env python3
"""Rejoue un historique bougie par bougie à travers le pipeline complet.

Ce que ce script mesure vraiment, et ce qu'il ne mesure pas.

Il mesure que la machine tient debout : les étages refusent ce qu'ils doivent
refuser, les positions se dénouent, les R se calculent, la calibration se
remplit. Il passe par `AutoTrader.trade()`, c'est-à-dire exactement le code qui
tourne en production — pas une imitation écrite pour l'occasion.

Il ne mesure **pas** une rentabilité, et la démonstration tient en deux
exécutions. Avec la dérive par défaut, le générateur alterne des phases de
tendance et le moteur les capte : environ +0,3 R par trade sur trois graines.
Avec `--derive 0`, c'est-à-dire du bruit sans tendance, la même configuration
tombe à **−0,23 R par trade** et perd 1,3 % du capital.

Autrement dit, le résultat positif par défaut mesure la dérive du générateur,
pas un avantage de marché. L'arithmétique le confirme : les sorties se font à
+1,67 R contre −1,00 R, donc sur une marche sans dérive il faudrait 37 % de
réussite pour être à l'équilibre, et la simulation en relève 29 %.

Ce que les chiffres servent à vérifier, c'est que la comptabilité est juste et
que les étages refusent ce qu'ils doivent refuser — pas à décider d'engager de
l'argent.

    python scripts/simulation.py
    python scripts/simulation.py --bougies 5000 --graine 11 --seuil 60
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "service" / "server"))

import calibration  # noqa: E402
from auto_trader import AutoTrader, Portfolio  # noqa: E402
from broker import PaperBroker  # noqa: E402
from calibration import CalibrationConfig  # noqa: E402
from llm_client import build_llm_client  # noqa: E402
from market_data import SyntheticSource  # noqa: E402

# Un horodatage fixe : deux exécutions du même scénario doivent donner la même
# chose, y compris les dates imprimées.
EPOQUE = 1_767_225_600.0  # 2026-01-01T00:00:00Z


def serie(symbole: str, intervalle: str, bougies: int, graine: int, derive: float):
    source = SyntheticSource(bars=bougies, seed=graine, drift=derive, clock=lambda: EPOQUE)
    return source.fetch(symbole, intervalle, bougies)


def pourcent(part: int, total: int) -> str:
    return f"{part / total * 100:5.1f} %" if total else "    — "


def titre(texte: str) -> None:
    print(f"\n{texte}\n" + "─" * len(texte))


def simuler(args) -> int:
    symboles = [s.strip().upper() for s in args.symboles.split(",") if s.strip()]
    series = {
        s: serie(s, args.intervalle, args.bougies, args.graine, args.derive) for s in symboles
    }

    trader = AutoTrader(
        source=SyntheticSource(bars=args.bougies, seed=args.graine),
        broker=PaperBroker(quote_balance=args.capital),
        portfolio=Portfolio(equity=args.capital),
        calibration_config=CalibrationConfig(confluence_gate=args.seuil),
        llm_client=build_llm_client(),
        candle_limit=args.fenetre,
    )

    etages = Counter()
    raisons = Counter()
    blocages = Counter()
    postures = defaultdict(Counter)
    convictions: list[float] = []
    capitaux = [trader.portfolio.equity]
    analyses = 0

    for index in range(args.fenetre, args.bougies):
        for symbole in symboles:
            fenetre = series[symbole][index - args.fenetre : index]
            instant = datetime.fromtimestamp(fenetre[-1].open_time / 1000, tz=timezone.utc)
            decision = trader.trade(symbole, args.intervalle, candles=fenetre, now=instant)
            analyses += 1
            etages[decision.stage] += 1
            for raison in decision.reasons:
                raisons[raison.split(":")[0][:72]] += 1
            for blocage in (decision.proposal or {}).get("blockers") or []:
                blocages[blocage.split(":")[0][:72]] += 1
            conseil = decision.council or {}
            for avis in conseil.get("opinions") or []:
                postures[avis["role"]][avis["stance"]] += 1
            if conseil.get("opinions"):
                convictions.append(float(conseil.get("conviction") or 0.0))
        capitaux.append(trader.portfolio.equity)

    # -- rapport -----------------------------------------------------------

    print(f"Simulation — {args.bougies} bougies × {len(symboles)} symboles, "
          f"intervalle {args.intervalle}, graine {args.graine}")
    print(f"Source : marche aléatoire ensemencée (pas un marché), dérive {args.derive:g}. "
          f"Seuil de confluence : {args.seuil:.0f} %.")
    modele = getattr(trader.llm_client, "name", "none")
    disponible = bool(getattr(trader.llm_client, "available", False))
    print(f"Modèle du conseil : {modele}"
          + ("" if disponible else "  ← aucun modèle joignable, avis de repli déterministes"))

    titre("Où les analyses se sont arrêtées")
    for etage, nombre in etages.most_common():
        print(f"  {etage:<12} {nombre:>6}  {pourcent(nombre, analyses)}")
    print(f"  {'TOTAL':<12} {analyses:>6}")

    if blocages:
        titre("Ce que la stratégie a bloqué")
        for blocage, nombre in blocages.most_common(8):
            print(f"  {nombre:>6}  {blocage}")

    if raisons:
        titre("Raisons de refus les plus fréquentes")
        for raison, nombre in raisons.most_common(8):
            print(f"  {nombre:>6}  {raison}")

    titre("Conseil d'agents")
    if not postures:
        print("  Le conseil n'a jamais siégé : aucune proposition n'a atteint cet étage.")
    else:
        for role, compte in sorted(postures.items()):
            total = sum(compte.values())
            detail = "  ".join(f"{p} {n:>4} ({pourcent(n, total).strip()})"
                               for p, n in compte.most_common())
            print(f"  {role:<10} {detail}")
        if convictions:
            print(f"  conviction : médiane {statistics.median(convictions):.3f}, "
                  f"max {max(convictions):.3f}")

    titre("Trades")
    historique = trader.history
    if not historique:
        print("  Aucun trade fermé.")
    else:
        issues = Counter(r.outcome.value for r in historique)
        multiples = [r.r_multiple for r in historique]
        tranches = issues["win"] + issues["loss"]
        print(f"  fermés        {len(historique)}")
        print(f"  gains/pertes/neutres  {issues['win']} / {issues['loss']} / {issues['scratch']}")
        if tranches:
            print(f"  réussite      {issues['win'] / tranches * 100:.1f} %  "
                  f"(sur les trades tranchés)")
        print(f"  somme         {sum(multiples):+.2f} R")
        print(f"  espérance     {statistics.fmean(multiples):+.3f} R par trade")
        print(f"  meilleur/pire {max(multiples):+.2f} R / {min(multiples):+.2f} R")

    ouvertes = trader.portfolio.positions
    if ouvertes:
        print(f"  encore ouvertes : {', '.join(sorted(ouvertes))}")

    titre("Capital")
    pic = capitaux[0]
    repli = 0.0
    for valeur in capitaux:
        pic = max(pic, valeur)
        repli = max(repli, (pic - valeur) / pic * 100 if pic else 0.0)
    depart, fin = capitaux[0], capitaux[-1]
    print(f"  départ {depart:,.2f}   fin {fin:,.2f}   "
          f"({(fin - depart) / depart * 100:+.2f} %)")
    print(f"  repli maximal {repli:.2f} %")

    titre("Calibration")
    paquets = defaultdict(list)
    for enregistrement in historique:
        paquets[enregistrement.bucket].append(enregistrement)
    config = CalibrationConfig()
    utilisables = 0
    for paquet, enregistrements in sorted(paquets.items(), key=lambda kv: -len(kv[1]))[:6]:
        stats = calibration.summarise(historique, paquet)
        pret = stats.wins + stats.losses >= config.min_sample
        utilisables += 1 if pret else 0
        print(f"  {paquet.key():<34} {stats.total:>4} trades  "
              f"réussite {stats.raw_hit_rate * 100:5.1f} %  "
              f"espérance {stats.expectancy_r:+.2f} R  "
              + ("calibré" if pret else f"apprend ({config.min_sample} requis)"))
    if paquets:
        print(f"  {utilisables} paquet(s) sur {len(paquets)} ont atteint "
              f"l'échantillon minimal de {config.min_sample}.")

    print("\nRappel : ces chiffres mesurent la mécanique, pas un avantage de marché.")
    return 0


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--bougies", type=int, default=3000)
    analyseur.add_argument("--fenetre", type=int, default=300,
                           help="bougies visibles à chaque décision")
    analyseur.add_argument("--symboles", default="BTCUSDT,ETHUSDT,SOLUSDT")
    analyseur.add_argument("--intervalle", default="5m")
    analyseur.add_argument("--graine", type=int, default=7)
    analyseur.add_argument("--capital", type=float, default=10_000.0)
    analyseur.add_argument("--seuil", type=float, default=65.0)
    analyseur.add_argument("--derive", type=float, default=0.0001,
                           help="dérive du générateur ; 0 pour du bruit pur")
    return simuler(analyseur.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
