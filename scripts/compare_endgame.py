#!/usr/bin/env python3
"""Rejeu hors ligne de toutes les solutions réelles connues (révélées par le serveur
ou trouvées par le bot), pour chaque stratégie, avec et sans la fin de partie par mot
sonde (motus_solver.endgame).

Mesures par variante, sur exactement les mêmes mots :
- défaites (solution non trouvée en 6 essais) et essais moyens ;
- temps de calcul du solveur (moyen par partie, maximum sur un coup) ;
- nombre de coups remplacés par la fin de partie.

Le rejeu ne simule pas les rejets du dictionnaire du jeu : les deux stratégies
partagent la même liste noire et les mêmes mots valides connus.

Leave-one-out par défaut : la solution rejouée est retirée des mots déjà acceptés.
Toutes les solutions connues y figurent, alors qu'en jeu réel seules 6,4 % des
solutions tirées le 25/09/2026 étaient déjà connues au moment du tirage. Sans ce
retrait, le départage « mot déjà accepté d'abord » tombe sur la solution en fin de
partie et avantage artificiellement la stratégie de base.

    python scripts/compare_endgame.py
    (résumé : data/improvement_runs/endgame/comparison.json, détail par mot : comparison_rows.jsonl)
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import statistics
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import ROOT_CACHE_FILES, load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.feedback import pattern_string  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

DATA = ROOT_DIR / "data"
MAX_ATTEMPTS = 6
VARIANTS = [("entropy_pure", False), ("entropy_pure", True), ("composite", False), ("composite", True)]

_state: dict = {}


def known_solutions() -> list[str]:
    words = {json.loads(line)["answer"].upper()
             for line in (DATA / "revealed_solutions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    words |= {g["solution"].upper() for g in json.loads((DATA / "dashboard_stats.json").read_text(encoding="utf-8"))
              if g.get("solution")}
    return sorted(words)


def _init(leave_one_out: bool = True) -> None:
    _state["leave_one_out"] = leave_one_out
    _state["corpus"] = Corpus.from_file(DATA / "corpus_fr.txt")
    _state["blocklist"] = load_blocklist(DATA / "known_invalid_words.json")
    _state["known_valid"] = load_blocklist(DATA / "known_valid_words.json")
    _state["caches"] = {s: load_cache(DATA / f) for s, f in ROOT_CACHE_FILES.items()}


def play(solution: str, strategy: str, endgame: bool) -> dict:
    # La solution est retirée des mots déjà acceptés (leave-one-out) : toutes les
    # solutions connues y figurent, et le solveur joue un mot accepté en premier à
    # égalité. Sans ce retrait, il tomberait sur la solution en fin de partie, ce qui
    # n'arrive pas quand un mot sort pour la première fois.
    known_valid = _state["known_valid"] - {solution} if _state.get("leave_one_out", True) else _state["known_valid"]
    solver = Solver(letter=solution[0], length=len(solution), corpus=_state["corpus"],
                    root_cache=_state["caches"][strategy], blocklist=_state["blocklist"],
                    known_valid=known_valid, strategy=strategy, endgame=endgame)
    if solution not in solver.candidates:
        return {"in_corpus": False}
    times, probes, replaced, guesses = [], 0, 0, []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t0 = time.perf_counter()
        guess = solver.suggest(top_n=1)[0][0]
        times.append(time.perf_counter() - t0)
        if solver.last_endgame is not None:
            replaced += 1
            probes += solver.last_endgame.probe
        guesses.append(guess)
        pattern = pattern_string(guess, solution)
        if pattern == "2" * len(solution):
            return {"in_corpus": True, "attempts": attempt, "solver_s": sum(times), "max_move_s": max(times),
                    "replaced": replaced, "probes": probes, "guesses": guesses}
        solver.play(guess)
        solver.update(pattern)
    return {"in_corpus": True, "attempts": None, "solver_s": sum(times), "max_move_s": max(times),
            "replaced": replaced, "probes": probes, "guesses": guesses}


def run_word(word: str) -> dict:
    return {"word": word, **{f"{s}|{'endgame' if e else 'base'}": play(word, s, e) for s, e in VARIANTS}}


def sign_test_p(wins: int, losses: int) -> float | None:
    n = wins + losses
    if not n:
        return None
    k = min(wins, losses)
    return round(min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n), 4)


def summarize(rows: list[dict]) -> dict:
    keys = [f"{s}|{'endgame' if e else 'base'}" for s, e in VARIANTS]
    rows = [r for r in rows if r[keys[0]]["in_corpus"]]
    out: dict = {"words": len(rows), "variants": {}}
    for key in keys:
        res = [r[key] for r in rows]
        won = [x["attempts"] for x in res if x["attempts"]]
        out["variants"][key] = {
            "losses": sum(1 for x in res if not x["attempts"]),
            "mean_attempts_won": round(statistics.mean(won), 4),
            # défaite comptée 7 essais : une seule mesure qui pénalise les défaites
            "mean_attempts_loss_as_7": round(statistics.mean(x["attempts"] or 7 for x in res), 4),
            "distribution": {str(a): sum(1 for w in won if w == a) for a in range(1, 7)},
            "mean_solver_s_per_game": round(statistics.mean(x["solver_s"] for x in res), 4),
            "p95_solver_s_per_game": round(sorted(x["solver_s"] for x in res)[int(0.95 * len(res)) - 1], 4),
            "max_move_s": round(max(x["max_move_s"] for x in res), 3),
            "games_with_endgame_move": sum(1 for x in res if x["replaced"]),
            "games_with_probe": sum(1 for x in res if x["probes"]),
        }
    pairs = {}
    for a, b in [("entropy_pure|base", "composite|base"), ("entropy_pure|endgame", "composite|endgame"),
                 ("entropy_pure|endgame", "entropy_pure|base"), ("composite|endgame", "composite|base")]:
        va = [r[a]["attempts"] or 7 for r in rows]
        vb = [r[b]["attempts"] or 7 for r in rows]
        wins = sum(1 for x, y in zip(va, vb) if x < y)
        losses = sum(1 for x, y in zip(va, vb) if x > y)
        pairs[f"{a} vs {b}"] = {"better": wins, "worse": losses, "equal": len(va) - wins - losses,
                                "mean_diff": round(statistics.mean(x - y for x, y in zip(va, vb)), 4),
                                "sign_test_p": sign_test_p(wins, losses)}
    out["paired"] = pairs
    out["lost_words"] = {k: [r["word"] for r in rows if not r[k]["attempts"]] for k in keys}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(DATA / "improvement_runs" / "endgame" / "comparison.json"))
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-leave-one-out", action="store_true",
                        help="garde chaque solution dans les mots déjà acceptés (biais, cf. docstring)")
    args = parser.parse_args()
    words = known_solutions()[: args.limit]
    t0 = time.time()
    with mp.Pool(args.workers, initializer=_init, initargs=(not args.no_leave_one_out,)) as pool:
        rows = []
        for i, row in enumerate(pool.imap_unordered(run_word, words, chunksize=4), 1):
            rows.append(row)
            if i % 100 == 0:
                print(f"{i}/{len(words)} mots ({time.time() - t0:.0f} s)", flush=True)
    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "elapsed_s": round(time.time() - t0, 1),
              "leave_one_out": not args.no_leave_one_out, **summarize(rows)}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    with out.with_name(out.stem + "_rows.jsonl").open("w", encoding="utf-8") as f:
        for row in sorted(rows, key=lambda r: r["word"]):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
