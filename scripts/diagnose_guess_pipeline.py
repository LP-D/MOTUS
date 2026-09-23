#!/usr/bin/env python3
"""Diagnostic des coups "ambigus" (clavier bloqué / rejets en cascade) : rejoue une
partie /infinite avec le client ACTUEL (le 23/09/2026, avant correctif : c'est ce
script qui a produit la preuve de la cascade, cf.
docs/diagnostics/2026-09-23_cascade_rejets.md ; depuis le correctif il sert de
contrôle — `misclassified` doit rester false et le mot envoyé == mot voulu) et
un moniteur réseau passif (bot/network_monitor.py), pour confronter pour chaque
coup la décision du bot (accepté / rejeté / exception) à la vérité serveur :

- la requête POST /guess est-elle bien partie ? avec quel mot exactement ?
- code HTTP, latence réelle, en-têtes Retry-After / X-RateLimit-* ;
- réponse serveur : `result` (accepté) ou `error: INVALID_WORD` (rejeté) ;
- état DOM de la ligne au moment de la décision puis 3s plus tard (révélation
  tardive ?).

Garde-fous : débit identique au débit validé (>= 1.5-2.5s entre deux requêtes
/guess, jamais plus rapide), arrêt immédiat sur signal de throttling (429,
Retry-After, latence > 10s), n'écrit JAMAIS dans la vraie liste noire
(data/known_invalid_words.json), plafond de parties via un registre de session.

    python scripts/diagnose_guess_pipeline.py --games 1 --ledger <fichier>
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))

from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

from bot.network_monitor import NetworkMonitor  # noqa: E402
from bot.tuzmo_client import (  # noqa: E402
    ROW_SELECTOR,
    GuessInputError,
    GuessNotSentError,
    ThrottlingDetectedError,
    TuzmoClient,
    WordRejectedError,
)

URL = "https://www.tusmo.xyz/infinite"
MAX_ATTEMPTS = 6
MAX_SUGGEST_RETRIES = 20
REJECT_TIMEOUT_MS = 6000
MIN_GAP_S = (1.5, 2.5)  # débit validé par le stress test (jamais plus rapide)
MAX_GUESS_REQUESTS_PER_GAME = 40
SESSION_GAME_CAP = 40


class ThrottleAbort(RuntimeError):
    pass


def ledger_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def ledger_append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def row_snapshot(page, index: int) -> dict:
    rows = page.query_selector_all(ROW_SELECTOR)
    if index >= len(rows):
        return {"index": index, "exists": False}
    cells = rows[index].query_selector_all(".cell")
    letters = "".join((c.query_selector(".cell__letter").text_content() or "").strip()
                      if c.query_selector(".cell__letter") else "." for c in cells)
    classes = [c.get_attribute("class") or "" for c in cells]
    revealed = sum(1 for k in classes if "cell--revealed" in k or "cell--absent" in k)
    return {"index": index, "exists": True, "letters": letters, "revealed_cells": revealed, "n_cells": len(cells)}


def respect_rate(monitor: NetworkMonitor) -> None:
    last = monitor.last_guess_sent_at()
    if last is None:
        return
    wait = last + random.uniform(*MIN_GAP_S) - time.time()
    if wait > 0:
        time.sleep(wait)


def check_throttle(monitor: NetworkMonitor) -> None:
    signal = monitor.first_throttle_signal()
    if signal:
        call, reason = signal
        raise ThrottleAbort(f"{reason} sur {call.method} {call.url}")


def play_game(page, corpus, root_cache, blocklist, log, observe_s: float) -> dict:
    monitor = NetworkMonitor(page)
    page.goto(URL, wait_until="networkidle", timeout=20000)
    page.locator("button", has_text="C'est parti").first.click()
    page.wait_for_selector(".board.board--stage", timeout=10000)
    page.wait_for_timeout(400)
    check_throttle(monitor)

    client = TuzmoClient(page)
    letter, length = client.get_first_letter(), client.get_word_length()
    log({"event": "game_started", "letter": letter, "length": length})
    solver = Solver(letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist)

    submits = []
    n_requests = 0
    outcome = "unknown"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        accepted = None
        retries = 0
        while solver.candidates and retries < MAX_SUGGEST_RETRIES:
            guess = solver.suggest(top_n=1)[0][0]
            retries += 1
            respect_rate(monitor)
            row_index = client._attempt_count
            t0 = time.time()
            decision, error = "accepted", None
            try:
                client.submit_guess(guess, timeout=REJECT_TIMEOUT_MS)
            except WordRejectedError:
                decision = "rejected"
            except (GuessInputError, GuessNotSentError) as exc:
                decision, error = "input_error", str(exc)
            except ThrottlingDetectedError as exc:
                raise ThrottleAbort(exc.reason) from exc
            except PlaywrightTimeoutError as exc:
                decision, error = "timeout", str(exc).splitlines()[0]
            t_decision = time.time()
            at_decision = row_snapshot(page, row_index)
            # fenêtre d'observation (révélation / réponse tardive) ; 0 = rythme naturel
            # du bot, pour reproduire aussi l'effet cascade tel quel
            if observe_s > 0:
                page.wait_for_timeout(int(observe_s * 1000))
            later = row_snapshot(page, row_index)
            calls = monitor.guess_calls_since(t0)
            n_requests = len([c for c in monitor.calls if c.kind == "guess"])
            record = {
                "event": "submit",
                "attempt": attempt,
                "retry": retries,
                "bot_guess": guess,
                "bot_decision": decision,
                "bot_decision_s": round(t_decision - t0, 3),
                "error": error,
                "row_at_decision": at_decision,
                "row_after_observation": later,
                "observe_s": observe_s,
                "network": [c.as_dict() for c in calls],
            }
            server = calls[-1] if calls else None
            if server is None:
                record["verdict"] = "NO_REQUEST_SENT"
            elif server.server_error == "INVALID_WORD":
                record["verdict"] = "SERVER_REJECTED"
            elif server.server_error:
                record["verdict"] = f"SERVER_ERROR_{server.server_error}"
            elif server.status is None:
                record["verdict"] = "NO_RESPONSE"
            else:
                record["verdict"] = "SERVER_ACCEPTED"
            record["misclassified"] = (
                (decision == "rejected" and record["verdict"] == "SERVER_ACCEPTED")
                or (decision == "accepted" and record["verdict"] != "SERVER_ACCEPTED")
            )
            if server is not None and server.guess and server.guess != guess:
                record["sent_word_differs"] = server.guess
            submits.append(record)
            log(record)
            check_throttle(monitor)

            if n_requests >= MAX_GUESS_REQUESTS_PER_GAME:
                outcome = "request_budget_exhausted"
                return {"outcome": outcome, "submits": submits}
            if decision == "input_error":
                continue
            if decision == "rejected":
                if guess in solver.candidates:
                    solver.candidates.remove(guess)
                continue
            if decision == "timeout":
                outcome = "timeout"
                return {"outcome": outcome, "submits": submits}
            accepted = guess
            accepted_server_result = server.as_dict()["server_result"] if server is not None else None
            break

        if accepted is None:
            outcome = "candidates_exhausted"
            break
        solver.play(accepted)
        try:
            dom_pattern = client.read_feedback()
        except ValueError as exc:  # ex. plateau déjà remplacé par le mot suivant (/infinite)
            dom_pattern = f"ERREUR_DOM: {exc}"
        code_of = {"correct": "2", "present": "1", "absent": "0"}
        api_pattern = "".join(code_of[r] for r in accepted_server_result) if accepted_server_result else None
        pattern = api_pattern or dom_pattern
        log({"event": "feedback", "attempt": attempt, "guess": accepted, "dom_pattern": dom_pattern,
             "api_pattern": api_pattern, "dom_matches_api": dom_pattern == api_pattern})
        if not api_pattern:
            outcome = "no_server_pattern"
            break
        solver.update(pattern)
        if pattern == "2" * length:
            outcome = "solved"
            break
        if not solver.candidates:
            outcome = "candidates_exhausted"
            break
    else:
        outcome = "not_solved"
    return {"outcome": outcome, "submits": submits}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--ledger", required=True, help="Registre des parties de la session (plafond 40).")
    parser.add_argument("--out", required=True, help="Log JSONL détaillé de ce diagnostic.")
    parser.add_argument("--observe-s", type=float, default=3.0,
                        help="Attente d'observation après chaque décision (0 = rythme naturel du bot).")
    args = parser.parse_args()

    ledger, out = Path(args.ledger), Path(args.out)
    corpus = Corpus.from_file(ROOT_DIR / "data" / "corpus_fr.txt")
    root_cache = load_cache(ROOT_DIR / "data" / "root_cache.json")
    # lecture seule : la vraie liste noire est chargée comme le fait le bot, jamais écrite ici
    blocklist = load_blocklist(ROOT_DIR / "data" / "known_invalid_words.json")

    def log(entry: dict) -> None:
        entry = {"t": time.time(), **entry}
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        brief = {k: entry[k] for k in ("event", "attempt", "bot_guess", "bot_decision", "verdict", "misclassified")
                 if k in entry}
        print(json.dumps(brief, ensure_ascii=False), flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # un seul contexte = un seul invité Tuzmo (cf. 429 "guest creation rate limited")
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            for _ in range(args.games):
                if ledger_count(ledger) >= SESSION_GAME_CAP:
                    log({"event": "stop", "reason": f"plafond de {SESSION_GAME_CAP} parties atteint"})
                    break
                ledger_append(ledger, {"t": time.time(), "script": "diagnose_guess_pipeline"})
                page = context.new_page()
                try:
                    result = play_game(page, corpus, root_cache, blocklist, log, args.observe_s)
                    log({"event": "game_finished", "outcome": result["outcome"]})
                except ThrottleAbort as exc:
                    log({"event": "stop", "reason": f"THROTTLING: {exc}"})
                    sys.exit(3)
                finally:
                    page.close()
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
