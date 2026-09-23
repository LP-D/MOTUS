#!/usr/bin/env python3
"""Cycle d'amélioration : joue N mots /infinite (chemin de production, un seul
invité) et journalise pour chaque mot la solution, la taille, les essais, les
temps (chargement, recherche solveur, saisie, attente de débit, réponse serveur,
animation) et la pertinence du trio d'ouverture de motus_trios_fusionne.xlsm
comparé à ce que joue le bot. Produit une synthèse de run et évalue le critère
d'arrêt (aucune erreur + temps moyen par mot inférieur au run précédent).

Garde-fous : débit imposé par le client (>= 1.5-2.5s entre requêtes, 1.5-2.5s
entre parties), arrêt d'urgence sur throttling (429 / Retry-After / > 10s).

    python scripts/run_improvement_cycle.py --run 1 --games 10
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "dashboard"))

import bot_runner  # noqa: E402
from run_case_matrix import MatrixRunner, drain, evaluate_game  # noqa: E402

from bot.tuzmo_client import ThrottlingDetectedError  # noqa: E402
from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import cache_key, load_cache  # noqa: E402
from motus_solver.corpus import Corpus, normalize_word  # noqa: E402
from motus_solver.feedback import entropy_from_codes, pattern_codes, pattern_string, words_to_matrix  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

DEFAULT_EXCEL = Path(r"C:\Users\Dufour\OneDrive - DVHE\Documents\MES FICHIERS\PERSO\motus_trios_fusionne.xlsm")
DEFAULT_OUT = ROOT_DIR / "data" / "improvement_runs"
ERROR_EVENTS = {"error", "throttled", "guess_not_submitted"}
MAX_SIM_ATTEMPTS = 12


def load_excel_trios(path: Path) -> dict[tuple[str, int], dict]:
    import openpyxl

    if not path.exists():
        return {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    trios = {}
    for row in list(wb["Meilleurs trios"].iter_rows(values_only=True))[4:]:
        if not row or not row[0] or not isinstance(row[1], int):
            continue
        words = [normalize_word(w) for w in row[2:5] if isinstance(w, str) and w.strip().isalpha()]
        if len(words) == 3:
            trios[(row[0].upper(), row[1])] = {"words": words, "prob_unique": row[5]}
    return trios


def entropy_of(word: str, candidates: list[str]) -> float | None:
    if not candidates or len(word) != len(candidates[0]):
        return None
    return round(entropy_from_codes(pattern_codes(word, words_to_matrix(candidates))), 3)


def simulate(opening: list[str], solution: str, letter: str, length: int, corpus: Corpus,
             blocklist: set[str], root_cache: dict | None = None) -> dict:
    """Joue `opening` puis le solveur (composite) contre `solution`, hors ligne.
    Sans ouverture imposée, le coup 1 vient du cache racine (comme le bot)."""
    solver = Solver(letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist)
    if solution not in solver.candidates:
        return {"valid": False, "reason": "solution absente du sous-corpus (hors corpus ou en liste noire)"}
    remaining, attempts = [], 0
    queue = list(opening)
    while attempts < MAX_SIM_ATTEMPTS:
        guess = queue.pop(0) if queue else solver.suggest(top_n=1)[0][0]
        attempts += 1
        pattern = pattern_string(guess, solution)
        solver.play(guess)
        solver.update(pattern)
        remaining.append(len(solver.candidates))
        if pattern == "2" * length:
            return {"valid": True, "attempts": attempts, "remaining_after": remaining}
    return {"valid": True, "attempts": None, "remaining_after": remaining}


def game_log(index, report, events, calls, result, t0, t_ready, t_end, trio, corpus, blocklist, root_cache):
    letter, length = report["letter"], report["length"]
    solution = report["solution"]
    proposals = [e for e in events if e["type"] == "guess_proposed"]
    outcomes = [e for e in events if e["type"] in {"feedback_received", "guess_rejected", "guess_not_submitted"}]
    guess_calls = [c for c in calls if c.kind == "guess"]
    moves = []
    for i, p in enumerate(proposals):
        out = outcomes[i] if i < len(outcomes) else {}
        rec = out.get("record") or {}
        call = next((c for c in guess_calls if c.guess == p["guess"]), None)
        moves.append({
            "attempt": p["attempt"], "guess": p["guess"], "candidates_before": p.get("candidates"),
            "result": {"feedback_received": "accepted", "guess_rejected": "rejected"}.get(out.get("type"), out.get("type")),
            "pattern": out.get("pattern"),
            "solver_s": p.get("solver_s"),
            "typing_s": rec.get("duration_solver_suggest_to_guess_typed_s"),
            "enter_and_rate_wait_s": rec.get("duration_guess_typed_to_guess_submitted_s"),
            "server_roundtrip_s": rec.get("duration_guess_submitted_to_feedback_confirmed_s"),
            "reveal_wait_s": rec.get("duration_feedback_confirmed_to_feedback_detected_s"),
            "server_latency_s": call.latency_s if call else None,
        })
    accepted = [m for m in moves if m["result"] == "accepted"]
    errors = [{k: v for k, v in e.items() if k != "record"} for e in events if e["type"] in ERROR_EVENTS]
    errors += [{"check": k, **v} for k, v in report["checks"].items() if v.get("status") == "FAIL"]
    if result.get("abandon_failed"):
        errors.append({"type": "abandon_failed"})

    def total(key):
        return round(sum(m[key] or 0 for m in moves), 3)

    trio_cmp = None
    if trio:
        cands = [w for w in corpus.subset(letter, length) if w not in blocklist]
        root = (root_cache.get(cache_key(letter, length)) or {}).get("word")
        first_bot = accepted[0]["guess"] if accepted else None
        trio_cmp = {
            "trio": trio["words"], "excel_prob_unique": trio["prob_unique"],
            "trio_words_blocklisted": [w for w in trio["words"] if w in blocklist],
            "trio_words_outside_corpus": [w for w in trio["words"] if w not in set(corpus.subset(letter, length))],
            "entropy_bits": {"excel_mot1": entropy_of(trio["words"][0], cands),
                             "bot_root": entropy_of(root, cands) if root else None,
                             "bot_first_accepted": entropy_of(first_bot, cands) if first_bot else None},
        }
        if solution:
            with_trio = simulate(trio["words"], solution, letter, length, corpus, blocklist)
            bot_only = simulate([], solution, letter, length, corpus, blocklist, root_cache)
            trio_cmp.update({
                "sim_trio_then_bot": with_trio, "sim_bot_only": bot_only,
                "bot_real_attempts": len(accepted),
                "remaining_after_3": {"trio": (with_trio.get("remaining_after") or [None] * 3)[2:3],
                                      "bot": (bot_only.get("remaining_after") or [None] * 3)[2:3]},
            })
    return {
        "game": index, "letter": letter, "length": length, "solution": solution,
        "outcome": result["outcome"], "attempts": len(accepted),
        "rejections": sum(1 for m in moves if m["result"] == "rejected"),
        "requests": len(guess_calls),
        "time_s": {"total": round(t_end - t0, 3), "load": round(t_ready - t0, 3), "play": round(t_end - t_ready, 3),
                   "solver": total("solver_s"), "typing": total("typing_s"),
                   "enter_and_rate_wait": total("enter_and_rate_wait_s"),
                   "server_roundtrip": total("server_roundtrip_s"), "reveal_wait": total("reveal_wait_s")},
        "max_solver_s": max((m["solver_s"] or 0 for m in moves), default=0),
        "max_latency_s": report["network"]["max_latency_s"],
        "root_rejected": "D_root_rejected" in report["checks"],
        "resumed": bool(result.get("resumed")), "abandoned": bool(result.get("abandoned")),
        "moves": moves, "trio": trio_cmp, "errors": errors, "checks": {k: v["status"] for k, v in report["checks"].items()},
        "session_events": [{k: v for k, v in e.items() if k != "record"} for e in events
                           if e["type"] in {"session_info", "game_resumed", "gave_up", "guest_ready"}],
        "api_calls": report.get("api_calls", []),
    }


def summarize(run: int, games: list[dict], stop_reason: str | None, previous: dict | None) -> dict:
    solved = [g for g in games if g["outcome"] == "solved"]
    times = [g["time_s"]["total"] for g in games]
    trio_rows = [g["trio"] for g in games if g["trio"] and g["trio"].get("sim_trio_then_bot", {}).get("valid")]
    errors = [e for g in games for e in g["errors"]]
    summary = {
        "run": run, "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                             text=True, cwd=ROOT_DIR).stdout.strip(),
        "games": len(games), "solved": len(solved),
        "unsolved": [{"group": f"{g['letter']}{g['length']}", "outcome": g["outcome"]} for g in games if g["outcome"] != "solved"],
        "errors": len(errors), "error_details": errors,
        "mean_time_per_word_s": round(statistics.mean(times), 2) if times else None,
        "total_time_s": round(sum(times), 1),
        "mean_attempts_solved": round(statistics.mean(g["attempts"] for g in solved), 2) if solved else None,
        "attempts_distribution": {str(k): sum(1 for g in solved if g["attempts"] == k) for k in range(1, 7)},
        "rejections": sum(g["rejections"] for g in games), "root_rejections": sum(g["root_rejected"] for g in games),
        "time_breakdown_mean_s": {k: round(statistics.mean(g["time_s"][k] for g in games), 2)
                                  for k in ("load", "solver", "typing", "enter_and_rate_wait", "server_roundtrip",
                                            "reveal_wait")} if games else {},
        "max_solver_s": max((g["max_solver_s"] for g in games), default=0),
        "max_latency_s": max((g["max_latency_s"] or 0 for g in games), default=0),
        "words": [f"{g['solution'] or '?'} ({g['length']}, {g['attempts']} essais, {g['time_s']['total']}s)" for g in games],
        "stop_reason": stop_reason,
    }
    if trio_rows:
        t_att = [t["sim_trio_then_bot"]["attempts"] for t in trio_rows if t["sim_trio_then_bot"]["attempts"]]
        b_att = [t["sim_bot_only"]["attempts"] for t in trio_rows if t["sim_bot_only"].get("attempts")]
        rem_t = [t["sim_trio_then_bot"]["remaining_after"][2] for t in trio_rows
                 if len(t["sim_trio_then_bot"]["remaining_after"]) >= 3]
        rem_b = [t["sim_bot_only"]["remaining_after"][2] for t in trio_rows
                 if len(t["sim_bot_only"].get("remaining_after", [])) >= 3]
        summary["trio_vs_bot"] = {
            "games_compared": len(trio_rows),
            "mean_attempts_trio_then_bot": round(statistics.mean(t_att), 2) if t_att else None,
            "mean_attempts_bot_only_sim": round(statistics.mean(b_att), 2) if b_att else None,
            "trio_better": sum(1 for t in trio_rows if (t["sim_trio_then_bot"]["attempts"] or 99) < (t["sim_bot_only"].get("attempts") or 99)),
            "trio_worse": sum(1 for t in trio_rows if (t["sim_trio_then_bot"]["attempts"] or 99) > (t["sim_bot_only"].get("attempts") or 99)),
            "mean_remaining_after_3_trio": round(statistics.mean(rem_t), 2) if rem_t else None,
            "mean_remaining_after_3_bot": round(statistics.mean(rem_b), 2) if rem_b else None,
            "trio_words_blocklisted": sorted({w for g in games if g["trio"] for w in g["trio"]["trio_words_blocklisted"]}),
        }
    if previous:
        faster = summary["mean_time_per_word_s"] < previous["mean_time_per_word_s"]
        summary["vs_previous"] = {"previous_mean_time_per_word_s": previous["mean_time_per_word_s"], "faster": faster}
        summary["stop_criterion_met"] = run >= 5 and summary["errors"] == 0 and faster
    else:
        summary["stop_criterion_met"] = False
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    games_path = out / f"run_{args.run:02d}_games.jsonl"
    summary_path = out / f"run_{args.run:02d}_summary.json"
    prev_path = out / f"run_{args.run - 1:02d}_summary.json"
    previous = json.loads(prev_path.read_text(encoding="utf-8")) if prev_path.exists() else None
    if games_path.exists():
        games_path.unlink()

    trios = load_excel_trios(Path(args.excel))
    corpus = Corpus.from_file(bot_runner.DEFAULT_CORPUS)
    root_cache = load_cache(bot_runner.DEFAULT_ROOT_CACHE)
    blocklist = load_blocklist(bot_runner.DEFAULT_BLOCKLIST)
    runner = MatrixRunner()
    games: list[dict] = []
    stop_reason = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = runner._new_context(browser)
        runner._ensure_guest(context)  # invité créé avant le 1er chargement (course /api/me <-> /api/game)
        try:
            for index in range(1, args.games + 1):
                t0 = time.time()
                try:
                    page = runner._open_game_page(context)
                except ThrottlingDetectedError as exc:
                    stop_reason = f"THROTTLING au chargement : {exc.reason}"
                    break
                t_ready = time.time()
                drain(runner.events)
                try:
                    blocklist_at_start = set(blocklist)
                    result, blocklist = runner._play_one_game(page, corpus, root_cache, blocklist)
                finally:
                    t_end = time.time()
                    events = drain(runner.events)
                    runner.monitor.resolve_bodies()
                    calls = list(runner.monitor.calls)
                    page.close()
                report = evaluate_game(index, events, calls, root_cache, False, result)
                trio = trios.get((report["letter"], report["length"]))
                log = game_log(index, report, events, calls, result, t0, t_ready, t_end, trio, corpus,
                               blocklist_at_start, root_cache)
                games.append(log)
                with games_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(log, ensure_ascii=False) + "\n")
                t = log["trio"] or {}
                print(json.dumps({
                    "game": index, "word": log["solution"], "len": log["length"], "outcome": log["outcome"],
                    "attempts": log["attempts"], "rej": log["rejections"], "time": log["time_s"]["total"],
                    "solver_max": log["max_solver_s"], "errors": len(log["errors"]),
                    "trio_sim": (t.get("sim_trio_then_bot") or {}).get("attempts"),
                    "bot_sim": (t.get("sim_bot_only") or {}).get("attempts"),
                }, ensure_ascii=False), flush=True)

                if result["outcome"] == "throttled" or report["network"]["throttle_signals"]:
                    stop_reason = f"THROTTLING : {report['network']['throttle_signals'] or result.get('exception')}"
                    break
                if result["outcome"] in {"error", "session_not_playable"} or result.get("abandon_failed"):
                    stop_reason = f"arrêt de sécurité : {result['outcome']} (partie {index})"
                    break
                if index < args.games:
                    time.sleep(random.uniform(*bot_runner.INTER_GAME_DELAY_S))
        finally:
            context.close()
            browser.close()
            summary = summarize(args.run, games, stop_reason, previous)
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: summary[k] for k in ("run", "solved", "games", "errors", "mean_time_per_word_s",
                                                      "mean_attempts_solved", "stop_criterion_met", "stop_reason")},
                             ensure_ascii=False))


if __name__ == "__main__":
    main()
