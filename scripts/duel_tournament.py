#!/usr/bin/env python3
"""Tournoi local de duels classés simulés (motus_solver.duel), sans aucune requête au site.

Chaque paire de modèles joue les mêmes mots, tirés parmi les solutions réelles connues.
Sorties :
- bilan victoires / nulles / défaites par paire ;
- par modèle : taux de victoire, mots trouvés, essais moyens, temps moyen pour trouver ;
- classement Elo (Bradley-Terry sur tous les duels, nulle = demi-victoire).

    python scripts/duel_tournament.py --duels 300
    python scripts/duel_tournament.py --agents entropy_pure,composite --speed rapide --duels 1000
    python scripts/duel_tournament.py --agents entropy_pure@instantane,entropy_pure@humain --duels 500

`modele@vitesse` fixe la vitesse d'un modèle (sinon --speed pour tous). Modèles et
vitesses disponibles : motus_solver.agents.AGENTS et SPEEDS.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import multiprocessing as mp
import random
import statistics
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from motus_solver.agents import AGENTS, SPEEDS, SolverContext, make_agent, play_duel  # noqa: E402

DATA = ROOT_DIR / "data"
OUT_DIR = DATA / "duel_runs"

_ctx: SolverContext | None = None


def known_solutions() -> list[str]:
    words = {json.loads(line)["answer"].upper()
             for line in (DATA / "revealed_solutions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    stats = DATA / "dashboard_stats.json"
    if stats.exists():
        words |= {g["solution"].upper() for g in json.loads(stats.read_text(encoding="utf-8")) if g.get("solution")}
    return sorted(words)


def parse_player(spec: str, default_speed: str) -> tuple[str, str]:
    name, _, speed = spec.partition("@")
    speed = speed or default_speed
    if name not in AGENTS:
        raise SystemExit(f"modèle inconnu : {name} (disponibles : {', '.join(AGENTS)})")
    if speed not in SPEEDS:
        raise SystemExit(f"vitesse inconnue : {speed} (disponibles : {', '.join(SPEEDS)})")
    return name, speed


def _init() -> None:
    global _ctx
    _ctx = SolverContext.load(DATA)


def _run(job: tuple) -> dict:
    (a, sa), (b, sb), word, seed = job
    rng = random.Random(seed)
    rec = play_duel(word, (make_agent(a), make_agent(b)), (SPEEDS[sa], SPEEDS[sb]), _ctx, rng)
    return {"a": f"{a}@{sa}", "b": f"{b}@{sb}", "word": word, "winner": rec.winner, "reason": rec.reason,
            "attempts": rec.attempts, "solved": rec.solved, "solve_times": rec.solve_times,
            "rejections": rec.rejections, "guesses": rec.guesses}


def elo(players: list[str], duels: list[dict], iterations: int = 200) -> dict[str, float]:
    """Force Bradley-Terry (algorithme MM), exprimée en Elo centré sur 1500."""
    wins = {p: 0.5 for p in players}  # a priori faible : pas de force infinie
    games: dict[tuple[str, str], float] = {}
    for d in duels:
        key = tuple(sorted((d["a"], d["b"])))
        games[key] = games.get(key, 0) + 1
        if d["winner"] is None:
            wins[d["a"]] += 0.5
            wins[d["b"]] += 0.5
        else:
            wins[d["a"] if d["winner"] == 0 else d["b"]] += 1
    strength = {p: 1.0 for p in players}
    for _ in range(iterations):
        new = {}
        for p in players:
            denom = sum(n / (strength[p] + strength[q]) for (x, y), n in games.items() if p in (x, y)
                        for q in [y if x == p else x])
            new[p] = wins[p] / denom if denom else strength[p]
        mean = math.exp(statistics.mean(math.log(v) for v in new.values()))
        strength = {p: v / mean for p, v in new.items()}
    return {p: round(1500 + 400 * math.log10(v), 1) for p, v in strength.items()}


def summarize(players: list[str], duels: list[dict]) -> dict:
    per = {}
    for p in players:
        mine = [(d, 0 if d["a"] == p else 1) for d in duels if p in (d["a"], d["b"])]
        solved = [(d, s) for d, s in mine if d["solved"][s]]
        per[p] = {
            "duels": len(mine),
            "wins": sum(1 for d, s in mine if d["winner"] == s),
            "draws": sum(1 for d, _ in mine if d["winner"] is None),
            "losses": sum(1 for d, s in mine if d["winner"] == 1 - s),
            "win_rate_pct": round(100 * (sum(1 for d, s in mine if d["winner"] == s)
                                         + 0.5 * sum(1 for d, _ in mine if d["winner"] is None)) / len(mine), 1),
            "solved_pct": round(100 * len(solved) / len(mine), 1),
            "mean_attempts_solved": round(statistics.mean(d["attempts"][s] for d, s in solved), 3) if solved else None,
            "mean_solve_time_s": round(statistics.mean(d["solve_times"][s] for d, s in solved), 1) if solved else None,
            "rejections_per_duel": round(sum(d["rejections"][s] for d, s in mine) / len(mine), 2),
        }
    pairs = {}
    for d in duels:
        key = f"{d['a']} vs {d['b']}"
        row = pairs.setdefault(key, {"a_wins": 0, "draws": 0, "b_wins": 0})
        row["draws" if d["winner"] is None else ("a_wins" if d["winner"] == 0 else "b_wins")] += 1
    ratings = elo(players, duels)
    ranking = sorted(players, key=lambda p: -ratings[p])
    return {"ranking": [{"player": p, "elo": ratings[p], **per[p]} for p in ranking], "pairs": pairs,
            "reasons": {r: sum(1 for d in duels if d["reason"] == r) for r in sorted({d["reason"] for d in duels})}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agents", default=",".join(AGENTS), help="modèles, séparés par des virgules (modele@vitesse)")
    parser.add_argument("--speed", default="rapide", help="vitesse par défaut")
    parser.add_argument("--duels", type=int, default=200, help="duels par paire (mêmes mots pour chaque paire)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    players = [parse_player(s.strip(), args.speed) for s in args.agents.split(",") if s.strip()]
    if len(players) < 2:
        raise SystemExit("il faut au moins deux joueurs")
    rng = random.Random(args.seed)
    words = rng.sample(known_solutions(), args.duels)
    jobs = [(a, b, w, rng.randrange(2**31)) for a, b in itertools.combinations(players, 2) for w in words]
    t0 = time.time()
    with mp.Pool(args.workers, initializer=_init) as pool:
        duels = pool.map(_run, jobs, chunksize=16)
    names = [f"{n}@{s}" for n, s in players]
    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "elapsed_s": round(time.time() - t0, 1),
              "duels_per_pair": args.duels, "seed": args.seed, **summarize(names, duels)}
    out = Path(args.out) if args.out else OUT_DIR / f"tournoi_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    with out.with_name(out.stem + "_duels.jsonl").open("w", encoding="utf-8") as f:
        for d in duels:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    print(f"{len(duels)} duels en {report['elapsed_s']} s -> {out}")
    print(f"{'modèle':34} {'Elo':>7} {'vict.%':>7} {'V-N-D':>13} {'trouvés%':>9} {'essais':>7} {'temps':>6}")
    for r in report["ranking"]:
        vnd = f"{r['wins']}-{r['draws']}-{r['losses']}"
        print(f"{r['player']:34} {r['elo']:7.1f} {r['win_rate_pct']:7.1f} {vnd:>13} {r['solved_pct']:9.1f} "
              f"{r['mean_attempts_solved'] or 0:7.2f} {r['mean_solve_time_s'] or 0:6.1f}")


if __name__ == "__main__":
    main()
