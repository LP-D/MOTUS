#!/usr/bin/env python3
"""Comparatif composite vs entropy_pure sur des runs /infinite alternés
(scripts/run_improvement_cycle.py --strategy ...), en deux volets :

1. mesuré en jeu réel, par stratégie : taux de résolution, temps moyen par mot,
   essais moyens, rejets (taux par coup soumis, causes) ;
2. hors ligne et apparié : chaque mot tiré dans l'un des runs, solution connue
   (trouvée ou révélée), est rejoué par les DEUX stratégies avec leur cache racine,
   la liste noire et les mots valides actuels. Les runs tirent des mots différents :
   ce volet compare les stratégies sur exactement les mêmes mots. Il mesure
   l'efficacité de résolution seule (rejets exclus, la liste noire les évite).
3. (`--all-known-solutions`) même rejeu apparié sur toutes les solutions réelles
   connues du serveur (révélées par giveup ou trouvées par le bot) : un échantillon
   de la vraie distribution des tirages, bien plus large que 5 runs.

    python scripts/compare_strategies.py --out-dir data/improvement_runs/alternated --all-known-solutions
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.feedback import pattern_string  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

DATA = ROOT_DIR / "data"
CACHES = {"composite": DATA / "root_cache.json", "entropy_pure": DATA / "root_cache_entropy_pure.json"}
MAX_SIM_ATTEMPTS = 12
STRATEGIES = ("composite", "entropy_pure")


def load_games(out_dir: Path) -> list[dict]:
    games = []
    for path in sorted(out_dir.glob("run_*_games.jsonl")):
        run = int(path.stem.split("_")[1])
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                games.append({**json.loads(line), "run": run})
    return games


def live_metrics(games: list[dict]) -> dict:
    solved = [g for g in games if g["outcome"] == "solved"]
    submitted = [m for g in games for m in g["moves"] if m["result"] in ("accepted", "rejected")]
    rejected = [m for m in submitted if m["result"] == "rejected"]
    times = [g["time_s"]["total"] for g in games]
    return {
        "runs": sorted({g["run"] for g in games}), "words": len(games), "solved": len(solved),
        "resolution_rate_pct": round(100 * len(solved) / len(games), 1) if games else None,
        "mean_time_per_word_s": round(statistics.mean(times), 2) if times else None,
        "mean_attempts_solved": round(statistics.mean(g["attempts"] for g in solved), 2) if solved else None,
        "rejections": len(rejected),
        "rejection_rate_per_submitted_pct": round(100 * len(rejected) / len(submitted), 1) if submitted else None,
        "rejections_per_word": round(len(rejected) / len(games), 2) if games else None,
        "root_rejections": sum(1 for m in rejected if (m.get("rejection_cause") or {}).get("source") == "root_cache"),
        "rejections_pre_validated": sum(1 for m in rejected if (m.get("rejection_cause") or {}).get("pre_validated")),
        "mean_solver_s_per_word": round(statistics.mean(g["time_s"]["solver"] for g in games), 3) if games else None,
        "max_latency_s": max((g["max_latency_s"] or 0 for g in games), default=0),
        "errors": sum(len(g["errors"]) for g in games),
    }


def simulate(strategy: str, solution: str, corpus: Corpus, cache: dict, blocklist: set[str],
             known_valid: set[str]) -> int | None:
    solver = Solver(letter=solution[0], length=len(solution), corpus=corpus, root_cache=cache,
                    blocklist=blocklist, known_valid=known_valid, strategy=strategy)
    if solution not in solver.candidates:
        return None
    for attempt in range(1, MAX_SIM_ATTEMPTS + 1):
        guess = solver.suggest(top_n=1)[0][0]
        pattern = pattern_string(guess, solution)
        if pattern == "2" * len(solution):
            return attempt
        solver.play(guess)
        solver.update(pattern)
    return None


def sign_test_p(wins: int, losses: int) -> float | None:
    """p-valeur bilatérale du test du signe (égalités exclues)."""
    n = wins + losses
    if not n:
        return None
    k = min(wins, losses)
    return round(min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n), 4)


def known_solutions() -> list[str]:
    """Solutions réelles connues : révélées (giveup) et trouvées par le bot."""
    words = {json.loads(line)["answer"].upper()
             for line in (DATA / "revealed_solutions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    words |= {g["solution"].upper() for g in json.loads((DATA / "dashboard_stats.json").read_text(encoding="utf-8"))
              if g.get("solution")}
    return sorted(words)


def paired(games: list[dict], words: list[str] | None = None) -> dict:
    corpus = Corpus.from_file(DATA / "corpus_fr.txt")
    blocklist = load_blocklist(DATA / "known_invalid_words.json")
    known_valid = load_blocklist(DATA / "known_valid_words.json")
    caches = {s: load_cache(p) for s, p in CACHES.items()}
    if words is None:
        words = sorted({(g["solution"] or g.get("revealed_answer")) for g in games
                        if g["solution"] or g.get("revealed_answer")})
    rows = []
    for word in words:
        row = {"word": word, **{s: simulate(s, word, corpus, caches[s], blocklist, known_valid) for s in STRATEGIES}}
        rows.append(row)
    both = [r for r in rows if all(r[s] for s in STRATEGIES)]
    out = {"words": len(rows), "compared": len(both),
           "failures_over_6": {s: sum(1 for r in rows if not r[s] or r[s] > 6) for s in STRATEGIES}}
    if both:
        out.update({f"mean_attempts_{s}": round(statistics.mean(r[s] for r in both), 2) for s in STRATEGIES})
        out["entropy_pure_better"] = sum(1 for r in both if r["entropy_pure"] < r["composite"])
        out["composite_better"] = sum(1 for r in both if r["composite"] < r["entropy_pure"])
        out["equal"] = sum(1 for r in both if r["composite"] == r["entropy_pure"])
        out["sign_test_p"] = sign_test_p(out["entropy_pure_better"], out["composite_better"])
        out[f"le2_pct"] = {s: round(100 * sum(1 for r in both if r[s] <= 2) / len(both), 1) for s in STRATEGIES}
        by_len = defaultdict(list)
        for r in both:
            by_len[len(r["word"])].append(r)
        out["by_length"] = {n: {"words": len(rs), **{s: round(statistics.mean(r[s] for r in rs), 2) for s in STRATEGIES}}
                            for n, rs in sorted(by_len.items())}
    out["rows"] = rows
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-paired", action="store_true")
    parser.add_argument("--all-known-solutions", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    games = load_games(out_dir)
    report = {"live": {s: live_metrics([g for g in games if g.get("strategy") == s]) for s in STRATEGIES}}
    if not args.no_paired:
        report["paired_offline"] = paired(games)
    if args.all_known_solutions:
        report["paired_offline_all_known_solutions"] = paired(games, known_solutions())
    (out_dir / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"live": report["live"], **{
        key: {k: v for k, v in report[key].items() if k != "rows"}
        for key in ("paired_offline", "paired_offline_all_known_solutions") if key in report}},
        ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
