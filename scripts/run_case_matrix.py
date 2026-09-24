#!/usr/bin/env python3
"""Boucle autonome bornée de validation des cas particuliers (/infinite uniquement).

Joue des parties via le VRAI chemin de production (`dashboard.bot_runner.BotRunner
._play_one_game`, client corrigé, liste noire réelle, stats réelles) avec un
moniteur réseau branché AVANT le chargement de la page, et vérifie sur preuve
serveur la matrice de cas :

  A. lettre imposée + longueur détectées (DOM) == valeurs de la session serveur
     (POST /api/game) — 100% exigé ; un échec arrête la boucle (prioritaire).
  B. lettres répétées : sur chaque partie gagnée (solution connue), le pattern
     local `pattern_string(coup, solution)` == pattern serveur pour chaque coup.
  C. mots courts (5) vs longs (9+) : partie menée à terme, temps du coup 1 mesuré.
  D. mot du cache racine rejeté dès le coup 1 : repli dynamique réellement envoyé.
  E. rejets en cascade (>= 2 rejets confirmés d'affilée) : chaque requête porte le
     mot voulu, aucun mot envoyé deux fois.
  F. défaite réelle (6 coups) forcée sur une partie : arrêt propre, aucune requête
     après le 6e coup, partie suivante saine.

Bornes (la première atteinte arrête) : couverture complète, plafond de 40 parties
pour la session (registre partagé avec les autres scripts), signal de throttling
(429 / Retry-After / latence > 10s — arrêt d'urgence distinct d'un échec de
correction), ou premier cas en échec (pour corriger avant de reprendre). Débit :
celui du client (>= 1.5-2.5s entre requêtes) + 1.5-2.5s entre deux parties.

    python scripts/run_case_matrix.py --ledger <registre> --out-dir <dossier>
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "dashboard"))

import bot_runner  # noqa: E402
from bot_runner import BotRunner  # noqa: E402

from bot.network_monitor import NetworkMonitor, is_throttle_signal  # noqa: E402
from bot.tuzmo_client import ThrottlingDetectedError  # noqa: E402
from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import cache_key, load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.feedback import pattern_string  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

SESSION_GAME_CAP = 40
CASES = ("A_detection", "B_repeated_letters", "C_short_5", "C_long_9plus", "D_root_rejected",
         "E_cascade", "F_real_loss")


class LosingSolver(Solver):
    """Force une vraie défaite (cas F) : après le coup 1, ne propose que des mots
    déjà éliminés (donc jamais la solution), sans jamais reproposer un mot."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._group = list(self.candidates)  # déjà filtré par la liste noire
        self._proposed: set[str] = set()

    def suggest(self, top_n: int = 5):
        if not self.history:
            return super().suggest(top_n)
        alive = set(self.candidates)
        for word in self._group:
            if word not in alive and word not in self._proposed:
                self._proposed.add(word)
                return [(word, 0.0, 0)]
        return super().suggest(top_n)


class AbandonSolver(Solver):
    """Force le chemin "partie impossible à gagner" après le 1er coup accepté
    (vide les candidats, comme une solution hors corpus) : exerce l'abandon réel
    (bouton ↻ run-reset de /infinite) et la reprise éventuelle au chargement suivant."""

    def update(self, pattern: str) -> None:
        super().update(pattern)
        self.candidates = []


class MatrixRunner(BotRunner):
    """Chemin de production inchangé ; le moniteur réseau de la page (branché avant
    le chargement par `BotRunner._open_game_page`) sert à évaluer la matrice."""

    @property
    def monitor(self) -> NetworkMonitor:
        return self.page_monitor


def drain(queue_) -> list[dict]:
    events = []
    while not queue_.empty():
        events.append(queue_.get_nowait())
    return events


def has_repeats(word: str | None) -> bool:
    return bool(word) and len(set(word)) < len(word)


def evaluate_game(index, events, calls, root_cache, forced_loss, result) -> dict:
    started = next((e for e in events if e["type"] == "game_started"), {})
    letter, length = started.get("letter"), started.get("length")
    guess_calls = [c for c in calls if c.kind == "guess"]
    sessions = [c for c in calls if c.kind == "create_session" and c.body]
    checks: dict[str, dict] = {}

    # A — détection lettre/longueur vs session serveur
    server = sessions[-1].body if sessions else {}
    server_session = server.get("session", server) if isinstance(server, dict) else {}
    s_letter, s_len = server_session.get("firstLetter"), server_session.get("wordLength")
    if s_letter is None:
        checks["A_detection"] = {"status": "unverifiable", "dom": [letter, length], "server_body_keys": list(server)}
    else:
        ok = (letter == s_letter.upper() and length == s_len)
        checks["A_detection"] = {"status": "pass" if ok else "FAIL", "dom": [letter, length], "server": [s_letter, s_len]}

    # invariants réseau (tous cas) : mot envoyé == mot voulu, aucun doublon
    sent = [c.guess for c in guess_calls]
    not_submitted = [e for e in events if e["type"] == "guess_not_submitted"]
    duplicates = sorted({w for w in sent if sent.count(w) > 1})
    throttles = [is_throttle_signal(c) for c in calls if is_throttle_signal(c)]
    latencies = [c.latency_s for c in calls if c.latency_s is not None]

    feedback = [e for e in events if e["type"] == "feedback_received"]
    solved = next((e for e in events if e["type"] == "solved"), None)
    solution = solved["solution"] if solved else None

    # B — lettres répétées (vérifiable seulement si la solution est connue)
    if solution:
        mismatches = [
            {"guess": f["guess"], "server": f["pattern"], "local": pattern_string(f["guess"], solution)}
            for f in feedback if pattern_string(f["guess"], solution) != f["pattern"]
        ]
        repeated = has_repeats(solution) or any(has_repeats(f["guess"]) for f in feedback)
        checks["B_repeated_letters"] = {
            "status": ("pass" if not mismatches else "FAIL") if repeated else "not_applicable",
            "solution": solution, "guesses": [f["guess"] for f in feedback], "mismatches": mismatches,
        }

    # C — longueur + temps du coup 1
    # temps de calcul du solveur = écart entre l'événement précédent et la proposition
    # (le CycleTimer du bot démarre après suggest(), il ne le mesure pas)
    proposals = [e for e in events if e["type"] == "guess_proposed"]

    def suggest_time(proposal) -> float | None:
        before = [e for e in events if e["timestamp"] <= proposal["timestamp"] and e is not proposal
                  and e["type"] in {"game_started", "guess_rejected", "feedback_received", "guess_not_submitted"}]
        return round(proposal["timestamp"] - before[-1]["timestamp"], 3) if before else None

    move1_s = suggest_time(proposals[0]) if proposals else None
    completed = result["outcome"] in {"solved", "not_solved", "candidates_exhausted"}
    c_entry = {"status": "pass" if completed else "FAIL", "length": length, "outcome": result["outcome"],
               "attempts": len(feedback)}
    if length == 5:
        checks["C_short_5"] = c_entry
    elif length and length >= 9:
        checks["C_long_9plus"] = c_entry

    # D — mot du cache racine rejeté au coup 1
    root = (root_cache.get(cache_key(letter, length)) or {}).get("word") if letter else None
    rejected_1 = [e["guess"] for e in events if e["type"] == "guess_rejected" and e["attempt"] == 1]
    if root and root in rejected_1:
        later = [p["guess"] for p in proposals if p["attempt"] == 1]
        after = later[later.index(root) + 1:] if root in later else []
        next_word = after[0] if after else None
        fallback = next((p for p in proposals if p["attempt"] == 1 and p["guess"] == next_word), None)
        move1_s = suggest_time(fallback) if fallback else None
        checks["D_root_rejected"] = {
            "status": "pass" if next_word and next_word != root and next_word in sent else "FAIL",
            "root_word": root, "next_proposed": next_word, "next_sent": next_word in sent,
            "move1_dynamic_fallback_s": move1_s,
        }

    # E — cascade de rejets confirmés
    runs, current = [], []
    for e in events:
        if e["type"] == "guess_rejected":
            current.append(e["guess"])
        elif e["type"] in {"feedback_received", "game_started"}:
            if len(current) >= 2:
                runs.append(current)
            current = []
    if len(current) >= 2:
        runs.append(current)
    if runs:
        longest = max(runs, key=len)
        ok = all(w in sent for w in longest) and not duplicates and not not_submitted
        checks["E_cascade"] = {"status": "pass" if ok else "FAIL", "longest_run": longest,
                               "each_sent_as_intended": all(w in sent for w in longest)}

    # F — défaite réelle forcée
    if forced_loss:
        last_accepted_t = max((c.t_sent for c in guess_calls if c.server_error is None), default=None)
        after_last = [c.guess for c in guess_calls if last_accepted_t and c.t_sent > last_accepted_t]
        last_body = next((c.body for c in reversed(guess_calls) if c.server_error is None), {}) or {}
        ok = result["outcome"] == "not_solved" and len(feedback) == 6 and not after_last
        checks["F_real_loss"] = {"status": "pass" if ok else "FAIL", "outcome": result["outcome"],
                                 "accepted_guesses": len(feedback), "requests_after_6th": after_last,
                                 "server_finished_block": last_body.get("finished")}

    return {
        "game": index, "letter": letter, "length": length, "forced_loss": forced_loss,
        "outcome": result["outcome"], "solution": solution, "checks": checks,
        "network": {"guess_requests": len(guess_calls), "sent_words": sent, "duplicates": duplicates,
                    "not_submitted": len(not_submitted), "max_latency_s": max(latencies, default=None),
                    "statuses": sorted({c.status for c in calls if c.status}), "throttle_signals": throttles},
        "move1_suggest_s": move1_s,
        "api_calls": [
            {"kind": c.kind, "method": c.method, "path": c.url.split("tusmo.xyz", 1)[-1], "status": c.status,
             "latency_s": c.latency_s, "guess": c.guess, "server_error": c.server_error,
             "rate_limit_headers": c.rate_limit_headers, "failure": c.failure,
             "t_sent": round(c.t_sent, 3)}
            for c in calls
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--force-loss-game", type=int, default=2, help="N° de partie (dans ce run) à perdre exprès (0 = aucune).")
    parser.add_argument("--max-games", type=int, default=0, help="Plafond de parties pour CE run (0 = seul le plafond de session).")
    parser.add_argument("--plan", default="",
                        help="Scénarios successifs dans UN seul contexte, ex. normal,abandon,normal,loss,normal "
                             "(remplace --force-loss-game et la couverture ; s'arrête à la 1re anomalie).")
    parser.add_argument("--until-game-error", action="store_true",
                        help="Mode investigation : ignore la couverture, s'arrête à la 1re partie en game_error.")
    args = parser.parse_args()
    plan = [m.strip() for m in args.plan.split(",") if m.strip()]
    assert all(m in {"normal", "abandon", "loss"} for m in plan), plan
    if plan:
        args.max_games, args.force_loss_game = len(plan), 0
    ledger, out_dir = Path(args.ledger), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    games_path, summary_path = out_dir / "matrix_games.jsonl", out_dir / "matrix_summary.json"

    corpus = Corpus.from_file(bot_runner.DEFAULT_CORPUS)
    root_cache = load_cache(bot_runner.DEFAULT_ROOT_CACHE)
    blocklist = load_blocklist(bot_runner.DEFAULT_BLOCKLIST)
    runner = MatrixRunner()
    coverage = {case: [] for case in CASES}
    stop_reason = None
    loss_needs_followup = False
    previous_resumed = False
    games_run = 0
    real_solver, real_record = bot_runner.Solver, bot_runner.record_game

    def ledger_count() -> int:
        return len([l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]) if ledger.exists() else 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # UN seul contexte (donc un seul invité Tuzmo) pour tout le run : un contexte
        # par partie déclenchait le 429 "guest creation rate limited" (23/09/2026)
        context = runner._new_context(browser)
        runner._ensure_guest(context)  # invité créé avant le 1er chargement (course /api/me <-> /api/game)
        try:
            while True:
                if args.max_games and games_run >= args.max_games:
                    stop_reason = f"plafond de {args.max_games} parties pour ce run"
                    break
                if ledger_count() >= SESSION_GAME_CAP:
                    stop_reason = f"plafond de {SESSION_GAME_CAP} parties de session atteint"
                    break
                games_run += 1
                mode = plan[games_run - 1] if plan else ("loss" if games_run == args.force_loss_game else "normal")
                forced = mode == "loss"
                with ledger.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": time.time(), "script": "run_case_matrix", "forced_loss": forced}) + "\n")

                try:
                    page = runner._open_game_page(context)
                except ThrottlingDetectedError as exc:
                    stop_reason = f"THROTTLING au chargement : {exc.reason}"
                    break
                drain(runner.events)
                if mode in {"loss", "abandon"}:
                    bot_runner.Solver = LosingSolver if mode == "loss" else AbandonSolver
                    # partie perdue/abandonnée volontairement : hors des stats réelles du bot
                    bot_runner.record_game = lambda **kw: None
                try:
                    result, blocklist = runner._play_one_game(page, corpus, root_cache, blocklist)
                finally:
                    bot_runner.Solver, bot_runner.record_game = real_solver, real_record
                    events = drain(runner.events)
                    runner.monitor.resolve_bodies()
                    calls = list(runner.monitor.calls)
                    page.close()

                report = evaluate_game(games_run, events, calls, root_cache, forced, result)
                if loss_needs_followup:
                    # F (suite) : la partie qui suit une défaite doit être saine
                    a_ok = report["checks"]["A_detection"]["status"] in {"pass", "unverifiable"}
                    first_ok = bool(report["network"]["sent_words"]) and report["network"]["not_submitted"] == 0
                    report["checks"]["F_next_game_clean"] = {"status": "pass" if a_ok and first_ok else "FAIL"}
                    loss_needs_followup = False
                if forced:
                    loss_needs_followup = True
                report["mode"] = mode
                report["session"] = {
                    "info": [e for e in events if e["type"] == "session_info"],
                    "resumed": bool(result.get("resumed")),
                    "abandoned": bool(result.get("abandoned")),
                    "abandon_failed": bool(result.get("abandon_failed")),
                    "create_calls": len([c for c in calls if c.kind == "create_session"]),
                    "api_me_statuses": [c.status for c in calls if c.url.split("?")[0].endswith("/api/me")],
                }
                report["events"] = [{k: v for k, v in e.items() if k != "record"} for e in events]
                with games_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(report, ensure_ascii=False) + "\n")

                for case, check in report["checks"].items():
                    key = "F_real_loss" if case == "F_next_game_clean" else case
                    coverage.setdefault(key, []).append({"game": games_run, "status": check["status"], "case": case})
                print(json.dumps({"game": games_run, "mode": mode, "group": f"{report['letter']}{report['length']}",
                                  "outcome": report["outcome"], "session": {k: report["session"][k] for k in
                                  ("resumed", "abandoned", "create_calls", "api_me_statuses")},
                                  "checks": {k: v["status"] for k, v in report["checks"].items()},
                                  "net": {k: report["network"][k] for k in ("guess_requests", "max_latency_s", "not_submitted")}},
                                 ensure_ascii=False), flush=True)

                if result["outcome"] == "throttled" or report["network"]["throttle_signals"]:
                    stop_reason = f"THROTTLING : {report['network']['throttle_signals'] or result.get('exception')}"
                    break
                failures = [c for c, v in report["checks"].items() if v["status"] == "FAIL"]
                if report["checks"]["A_detection"]["status"] == "FAIL":
                    stop_reason = "échec de détection lettre/longueur (prioritaire)"
                    break
                if failures:
                    stop_reason = f"cas en échec : {failures} (partie {games_run})"
                    break
                if result["outcome"] == "session_not_playable" or result.get("abandon_failed"):
                    stop_reason = f"contexte persistant : {result['outcome']} / abandon_failed={result.get('abandon_failed')} (partie {games_run})"
                    break
                if result.get("resumed") and previous_resumed:
                    stop_reason = f"deux parties reprises d'affilée (partie {games_run})"
                    break
                previous_resumed = bool(result.get("resumed"))
                if plan and result.get("resumed") and games_run > 1 and plan[games_run - 2] != "normal":
                    stop_reason = f"partie reprise après un '{plan[games_run - 2]}' : clôture serveur inefficace (partie {games_run})"
                    break
                if result["outcome"] == "game_error":
                    stop_reason = f"partie en game_error : {result.get('exception')} (partie {games_run})"
                    break
                if args.until_game_error:
                    time.sleep(random.uniform(*bot_runner.INTER_GAME_DELAY_S))
                    continue
                if result["outcome"] == "error":
                    stop_reason = f"erreur de partie : {result.get('exception')}"
                    break
                if plan:
                    if games_run >= len(plan):
                        stop_reason = "plan de validation terminé"
                        break
                    time.sleep(random.uniform(*bot_runner.INTER_GAME_DELAY_S))
                    continue
                covered = {
                    c: any(r["status"] == "pass" for r in coverage.get(c, []))
                    for c in CASES
                }
                covered["F_real_loss"] = (
                    any(r["case"] == "F_real_loss" and r["status"] == "pass" for r in coverage["F_real_loss"])
                    and any(r["case"] == "F_next_game_clean" and r["status"] == "pass" for r in coverage["F_real_loss"])
                )
                if all(covered.values()):
                    stop_reason = "couverture complète de la matrice"
                    break
                time.sleep(random.uniform(*bot_runner.INTER_GAME_DELAY_S))
        finally:
            context.close()
            browser.close()
            summary = {"stop_reason": stop_reason, "games_this_run": games_run,
                       "session_games_total": ledger_count(), "coverage": coverage}
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"stop_reason": stop_reason, "games_this_run": games_run,
                              "session_total": ledger_count()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
