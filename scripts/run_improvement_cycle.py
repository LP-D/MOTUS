#!/usr/bin/env python3
"""Cycle d'amélioration : joue N mots /infinite (chemin de production, un seul
invité) et journalise pour chaque mot la solution, la taille, les essais, les
temps (chargement, recherche solveur, saisie, attente de débit, réponse serveur,
animation) et la pertinence du trio d'ouverture de motus_trios_fusionne.xlsm
comparé à ce que joue le bot. Produit une synthèse de run et évalue le critère
d'arrêt (aucune erreur + temps moyen par mot inférieur au run précédent).

Garde-fous : débit imposé par le client (>= 1.5-2.5s entre requêtes, 1.5-2.5s
entre parties), arrêt d'urgence sur throttling (429 / Retry-After / latence > 5 s).

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

from bot.network_monitor import session_from_body  # noqa: E402
from bot.tuzmo_client import ThrottlingDetectedError  # noqa: E402
from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import DEFAULT_STRATEGY, STRATEGIES, cache_key, load_cache  # noqa: E402
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
             blocklist: set[str], root_cache: dict | None = None, strategy: str = DEFAULT_STRATEGY) -> dict:
    """Joue `opening` puis le solveur (`strategy`, celle du run) contre `solution`,
    hors ligne. Sans ouverture imposée, le coup 1 vient du cache racine (comme le bot)."""
    solver = Solver(letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist,
                    strategy=strategy)
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


def h8_watch(index, events, calls, guest_info, cookie_before_load) -> dict:
    """Surveillance dédiée de H8 ("partie introuvable", NOT_FOUND) : où en était la
    séquence création d'invité / chargement de page quand il survient."""
    creates = [c for c in calls if c.kind == "create_session"]
    create_ids = [((session_from_body(c.body) or {}).get("id")) for c in creates if c.body]
    guess_ids = sorted({c.url.split("/api/game/", 1)[1].split("/", 1)[0] for c in calls
                        if c.kind == "guess" and "/api/game/" in c.url})
    me = [c for c in calls if c.url.split("?")[0].endswith("/api/me")]
    not_found = [c.url for c in calls if c.server_error == "NOT_FOUND"] + [
        e.get("message") for e in events if e["type"] == "error" and "NOT_FOUND" in str(e.get("message"))]
    overlap = None
    if me and creates:
        overlap = round(creates[0].t_sent - me[0].t_sent, 3)
    return {
        "not_found": bool(not_found), "not_found_detail": not_found,
        "first_game_of_context": index == 1, "guest_precreated": bool(guest_info.get("created")),
        "guest_info": guest_info if index == 1 else None,
        "cookie_before_load": cookie_before_load,
        "create_calls": len(creates), "create_session_ids": create_ids, "guess_session_ids": guess_ids,
        "guess_session_matches_create": bool(guess_ids) and set(guess_ids) <= set(i for i in create_ids if i),
        "create_minus_me_sent_s": overlap,
    }


def move_origin(attempt: int, guess: str, root_entry: dict | None, known_valid: set[str], resumed: bool) -> dict:
    """D'où vient un coup, pour expliquer un rejet : cache racine (rang 0 = coup 1,
    1..9 = replis) ou calcul dynamique, et s'il était déjà prouvé valide."""
    ranked = [e["word"] for e in (root_entry, *root_entry.get("alternatives", ()))] if root_entry else []
    from_root = attempt == 1 and not resumed and guess in ranked
    return {"source": "root_cache" if from_root else "dynamic",
            "root_rank": ranked.index(guess) if from_root else None,
            "pre_validated": guess in known_valid}


def game_log(index, report, events, calls, result, t0, t_ready, t_end, trio, corpus, blocklist, root_cache,
             known_valid: set[str] | None = None, strategy: str = DEFAULT_STRATEGY):
    letter, length = report["letter"], report["length"]
    solution = report["solution"]
    root_entry = root_cache.get(cache_key(letter, length)) if letter else None
    proposals = [e for e in events if e["type"] == "guess_proposed"]
    outcomes = [e for e in events if e["type"] in {"feedback_received", "guess_rejected", "guess_not_submitted"}]
    guess_calls = [c for c in calls if c.kind == "guess"]
    moves = []
    for i, p in enumerate(proposals):
        out = outcomes[i] if i < len(outcomes) else {}
        rec = out.get("record") or {}
        call = next((c for c in guess_calls if c.guess == p["guess"]), None)
        result_kind = {"feedback_received": "accepted", "guess_rejected": "rejected"}.get(out.get("type"), out.get("type"))
        cause = None
        if result_kind == "rejected":
            cause = {"api_error": call.server_error if call else None,
                     **move_origin(p["attempt"], p["guess"], root_entry, known_valid or set(), bool(result.get("resumed")))}
        elif result_kind == "guess_not_submitted":
            cause = {"not_submitted": out.get("reason")}
        moves.append({
            "attempt": p["attempt"], "guess": p["guess"], "candidates_before": p.get("candidates"),
            "result": result_kind,
            "rejection_cause": cause,
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
            with_trio = simulate(trio["words"], solution, letter, length, corpus, blocklist, strategy=strategy)
            bot_only = simulate([], solution, letter, length, corpus, blocklist, root_cache, strategy=strategy)
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
        # tirage du groupe : 0 = groupe jamais observé jusque-là (statut incertain)
        "group_draws_before": result.get("group_draws_before"),
        "moves": moves, "trio": trio_cmp, "errors": errors, "checks": {k: v["status"] for k, v in report["checks"].items()},
        "session_events": [{k: v for k, v in e.items() if k != "record"} for e in events
                           if e["type"] in {"session_info", "game_resumed", "gave_up", "guest_ready"}],
        "api_calls": report.get("api_calls", []),
    }


def cumulative_trio(out_dir: Path, upto_run: int) -> dict:
    """Comparatif trio Excel vs bot cumulé sur tous les runs journalisés (1..N)."""
    better = worse = equal = 0
    t_att, b_att = [], []
    for path in sorted(out_dir.glob("run_*_games.jsonl")):
        if int(path.stem.split("_")[1]) > upto_run:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            trio = json.loads(line).get("trio") or {}
            a = (trio.get("sim_trio_then_bot") or {}).get("attempts")
            b = (trio.get("sim_bot_only") or {}).get("attempts")
            if a and b:
                t_att.append(a)
                b_att.append(b)
                better += a < b
                worse += a > b
                equal += a == b
    return {"words_compared": len(t_att), "trio_better": better, "trio_worse": worse, "equal": equal,
            "mean_attempts_trio_then_bot": round(statistics.mean(t_att), 2) if t_att else None,
            "mean_attempts_bot_only": round(statistics.mean(b_att), 2) if b_att else None}


def summarize(run: int, games: list[dict], stop_reason: str | None, previous: dict | None,
              cycle_start: int = 1, out_dir: Path | None = None) -> dict:
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
        "rejection_causes": [dict(m["rejection_cause"] or {}, word=m["guess"], game=g["game"], attempt=m["attempt"])
                             for g in games for m in g.get("moves", ()) if m["result"] == "rejected"],
        "unobserved_groups_drawn": [f"{g['letter']}_{g['length']}" for g in games
                                    if g.get("group_draws_before") == 0 and not g.get("resumed")],
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
    summary["h8"] = {"not_found_games": [g["game"] for g in games if (g.get("h8") or {}).get("not_found")],
                     "guest_precreated": any((g.get("h8") or {}).get("guest_precreated") for g in games),
                     "session_id_mismatch_games": [g["game"] for g in games
                                                   if (g.get("h8") or {}).get("guess_session_ids")
                                                   and not g["h8"]["guess_session_matches_create"]]}
    summary["cycle"] = {"cycle_start_run": cycle_start, "runs_in_cycle": run - cycle_start + 1}
    if out_dir is not None:
        summary["trio_vs_bot_cumulative"] = cumulative_trio(out_dir, run)
    if previous:
        faster = summary["total_time_s"] < previous["total_time_s"] and len(games) >= previous.get("games", 0)
        summary["vs_previous"] = {"previous_total_time_s": previous["total_time_s"],
                                  "previous_mean_time_per_word_s": previous["mean_time_per_word_s"], "faster": faster}
        summary["stop_criterion_met"] = (summary["cycle"]["runs_in_cycle"] >= 5 and summary["errors"] == 0
                                         and faster)
    else:
        summary["stop_criterion_met"] = False
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--cycle-start", type=int, default=None,
                        help="1er run du cycle en cours (minimum 5 runs comptés à partir de lui).")
    parser.add_argument("--strategy", choices=STRATEGIES, default=DEFAULT_STRATEGY)
    parser.add_argument("--force-abandon-game", type=int, default=0,
                        help="Validation : force le chemin 'solution hors corpus' (abandon + révélation) sur cette partie.")
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
    runner = MatrixRunner()
    runner.strategy = args.strategy
    root_cache = load_cache(runner.root_cache_path())
    blocklist = load_blocklist(bot_runner.DEFAULT_BLOCKLIST)
    cycle_start = args.cycle_start or args.run
    games: list[dict] = []
    stop_reason = None
    real_record_game = bot_runner.record_game

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = runner._new_context(browser)
        try:
            # invité créé avant le 1er chargement (course /api/me <-> /api/game, H8)
            guest_info = runner._ensure_guest(context)
        except ThrottlingDetectedError as exc:
            guest_info, stop_reason = {"error": exc.reason}, f"THROTTLING à la création d'invité : {exc.reason}"
        guest_events = drain(runner.events)
        try:
            for index in range(1, args.games + 1 if stop_reason is None else 1):
                cookie_before_load = any(c.get("name") == bot_runner.GUEST_COOKIE
                                         for c in context.cookies(bot_runner.API_ME_URL))
                t0 = time.time()
                try:
                    page = runner._open_game_page(context)
                except ThrottlingDetectedError as exc:
                    stop_reason = f"THROTTLING au chargement : {exc.reason}"
                    break
                t_ready = time.time()
                drain(runner.events)
                forced_abandon = index == args.force_abandon_game
                if forced_abandon:
                    from run_case_matrix import AbandonSolver
                    bot_runner.Solver, bot_runner.record_game = AbandonSolver, (lambda **kw: None)
                try:
                    blocklist_at_start = set(blocklist)
                    known_valid_at_start = load_blocklist(bot_runner.DEFAULT_KNOWN_VALID)
                    result, blocklist = runner._play_one_game(page, corpus, root_cache, blocklist)
                finally:
                    if forced_abandon:
                        bot_runner.Solver, bot_runner.record_game = Solver, real_record_game
                    t_end = time.time()
                    events = drain(runner.events)
                    runner.monitor.resolve_bodies()
                    calls = list(runner.monitor.calls)
                    page.close()
                report = evaluate_game(index, events, calls, root_cache, False, result)
                trio = trios.get((report["letter"], report["length"]))
                log = game_log(index, report, events, calls, result, t0, t_ready, t_end, trio, corpus,
                               blocklist_at_start, root_cache, known_valid_at_start, args.strategy)
                log["strategy"] = args.strategy
                log["h8"] = h8_watch(index, events, calls, guest_info, cookie_before_load)
                log["forced_abandon"] = forced_abandon
                log["revealed_answer"] = result.get("answer")
                if forced_abandon:
                    log["errors"] = [e for e in log["errors"] if e.get("check") is None]
                if log["h8"]["not_found"]:
                    log["errors"].append({"type": "H8_NOT_FOUND", **{k: log["h8"][k] for k in (
                        "first_game_of_context", "guest_precreated", "cookie_before_load",
                        "create_session_ids", "guess_session_ids", "create_minus_me_sent_s")}})
                games.append(log)
                with games_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(log, ensure_ascii=False) + "\n")
                t = log["trio"] or {}
                print(json.dumps({
                    "game": index, "word": log["solution"], "len": log["length"], "outcome": log["outcome"],
                    "attempts": log["attempts"], "rej": log["rejections"], "time": log["time_s"]["total"],
                    "solver_max": log["max_solver_s"], "errors": len(log["errors"]),
                    "h8": log["h8"]["not_found"], "revealed": log["revealed_answer"],
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
            summary = summarize(args.run, games, stop_reason, previous, cycle_start, out)
            summary["guest"] = {"info": guest_info, "events": [{k: v for k, v in e.items()} for e in guest_events]}
            summary["strategy"] = args.strategy
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: summary.get(k) for k in ("run", "solved", "games", "errors", "total_time_s",
                                                          "mean_time_per_word_s", "mean_attempts_solved", "h8",
                                                          "stop_criterion_met", "stop_reason")},
                             ensure_ascii=False))


if __name__ == "__main__":
    main()
