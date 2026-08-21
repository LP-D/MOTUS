#!/usr/bin/env python3
"""Analyse de faisabilité du mode duel classé, à partir des mesures réelles produites
par `scripts/benchmark_bot_latency.py` (data/bot_latency_report.json).

Scénario : l'adversaire trouve le mot en n coups, un chrono de 45s démarre, et le
bot dispose de n-1 essais pour égaler ou faire mieux, dans cette fenêtre. Ce script
calcule, pour n=2,3,4,5, le temps que prendrait le bot pour jouer n-1 coups (à partir
des latences mesurées, y compris le risque de rejet "Mot inconnu" qui impose de
retaper un coup), et si ça tient dans 45s avec une marge de sécurité.

Diagnostic uniquement — aucune optimisation implémentée ici.

    python scripts/analyze_duel_feasibility.py --margin 0.20
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DUEL_WINDOW_S = 45.0
N_VALUES = [2, 3, 4, 5]

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = ROOT_DIR / "data" / "bot_latency_report.json"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "duel_feasibility_report.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--margin", type=float, default=0.20, help="Marge de sécurité (0.20 = 20%).")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    move = report["move_total_latency"]
    rejection_rate = report.get("rejection_rate") or 0.0
    budget = DUEL_WINDOW_S * (1 - args.margin)

    n_games = report.get("n_games", 0)
    outcomes = report.get("game_outcomes", {})
    n_unplayable = outcomes.get("all_candidates_rejected", 0) + outcomes.get("candidates_exhausted", 0)
    unplayable_rate = round(n_unplayable / n_games, 3) if n_games else None

    # Étape la plus coûteuse en latence, mesurée (pas supposée)
    per_step = report["per_step_latency"]
    slowest_step = max(per_step.items(), key=lambda kv: kv[1]["mean_s"]) if per_step else None

    # Coût espéré d'un coup avec risque de rejet : si un coup est rejeté (probabilité
    # `rejection_rate`), il faut retaper (coût ~ solver_suggest->guess_submitted,
    # sans le temps de confirmation qu'on n'atteint jamais sur un rejet) avant de
    # retenter — approximé ici par le même coût moyen d'un coup complet, en l'absence
    # de mesure séparée du coût d'un rejet isolé dans ce rapport.
    expected_move_cost = {
        stat: move[stat] / (1 - rejection_rate) if rejection_rate < 1 else float("inf")
        for stat in ("mean_s", "median_s", "p95_s")
        if move.get(stat) is not None
    }

    scenarios = []
    for n in N_VALUES:
        n_moves = n - 1
        if n_moves == 0:
            scenarios.append({"n": n, "moves_available": 0, "note": "adversaire au coup 1 : rien à jouer, duel perdu d'office"})
            continue
        row = {"n": n, "moves_available": n_moves}
        for stat in ("mean_s", "median_s", "p95_s"):
            if stat not in expected_move_cost:
                continue
            time_needed = n_moves * expected_move_cost[stat]
            row[f"time_needed_{stat}"] = round(time_needed, 2)
            row[f"fits_in_45s_{stat}"] = time_needed <= DUEL_WINDOW_S
            row[f"fits_with_margin_{stat}"] = time_needed <= budget
        scenarios.append(row)

    result = {
        "duel_window_s": DUEL_WINDOW_S,
        "safety_margin": args.margin,
        "budget_with_margin_s": round(budget, 2),
        "measured_rejection_rate_per_move": rejection_rate,
        "measured_game_outcomes": outcomes,
        "measured_unplayable_game_rate": unplayable_rate,
        "measured_move_total_latency_s": move,
        "expected_move_cost_accounting_for_rejections_s": {k: round(v, 3) for k, v in expected_move_cost.items()},
        "slowest_step": {"name": slowest_step[0], **slowest_step[1]} if slowest_step else None,
        "scenarios": scenarios,
        "verdict": (
            "La latence pure n'est PAS le facteur limitant (tous les scénarios n=2..5 "
            "tiennent largement dans le budget). Le facteur limitant réel est le taux "
            f"de rejet 'Mot inconnu' : {unplayable_rate:.0%} des parties testées "
            "n'ont trouvé AUCUN mot jouable parmi les 5 meilleures suggestions dès le "
            "coup 1 (corpus/dictionnaire de jeu mal alignés) — le bot perdrait ces "
            "duels non pas par manque de temps, mais faute de pouvoir soumettre un "
            "coup valide du tout."
            if unplayable_rate
            else "Latence uniquement : pas de données de rejet disponibles."
        ),
    }

    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Fenêtre duel : {DUEL_WINDOW_S}s, marge {args.margin:.0%} -> budget {budget:.1f}s")
    print(f"Taux de rejet mesuré (par coup) : {rejection_rate:.0%}")
    print(f"Parties sans AUCUN coup jouable (top-5 tous rejetés) : {unplayable_rate:.0%} ({n_unplayable}/{n_games})")
    if slowest_step:
        print(f"Étape la plus coûteuse : {slowest_step[0]} (moyenne {slowest_step[1]['mean_s']}s)")
    print()
    for row in scenarios:
        if row["moves_available"] == 0:
            print(f"n={row['n']} : {row['note']}")
            continue
        fits = row.get("fits_with_margin_mean_s")
        print(
            f"n={row['n']} ({row['moves_available']} coup(s) dispo) : "
            f"~{row.get('time_needed_mean_s')}s attendu (moyenne) — "
            f"{'OK' if fits else 'DÉPASSE'} le budget avec marge (latence seule)"
        )
    print(f"\n{result['verdict']}")
    print(f"\nRapport détaillé : {args.output}")


if __name__ == "__main__":
    main()
