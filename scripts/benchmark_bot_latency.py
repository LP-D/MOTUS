#!/usr/bin/env python3
"""Mesure la latence du bot Playwright en conditions réelles sur Tuzmo, coup par
coup, en s'appuyant sur l'instrumentation de `bot/timing.py` et `bot/tuzmo_client.py`.

Mode utilisé : /infinite (rejouable à volonté). /daily est limité à une partie par
jour (inutilisable pour un run répété) ; /entrainement est un écran teaser premium
(aucun plateau jouable sans compte payant) — vérifié en amont, cf. rapport de
session. /infinite a la même structure DOM que /daily (mêmes sélecteurs board/cell/
keyboard, vérifiés) et ne dépend d'aucun adversaire réel, conformément à la tâche.

Constat pris en compte (découvert en préparant ce script, cf. session) : le
dictionnaire de validation de Tuzmo est plus restrictif que le corpus du solveur —
~43-59% des mots proposés par le cache racine actuel sont rejetés ("Mot inconnu").
Un rejet est donc un événement attendu, pas une anomalie : `suggest_and_submit_with_retry`
retire le mot rejeté du sous-corpus du solveur et lui redemande une proposition
(recalcul dynamique, pas une liste figée) jusqu'à acceptation ou épuisement réel des
candidats — plus de plafond artificiel à 5 essais. Cette gestion vit uniquement dans
ce script (orchestration du benchmark), `bot/tuzmo_client.py` n'est pas modifié dans
son comportement par défaut (timer/timeout optionnels, no-op si omis).

Ne modifie aucune logique de jeu ni de scoring — mesure uniquement.

    python scripts/benchmark_bot_latency.py --games 10
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Callable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT_DIR_FOR_IMPORTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR_FOR_IMPORTS / "src"))
sys.path.insert(0, str(ROOT_DIR_FOR_IMPORTS))

from motus_solver.blocklist import add_to_blocklist, load_blocklist  # noqa: E402
from motus_solver.cache import load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

from bot.timing import CycleTimer  # noqa: E402
from bot.tuzmo_client import GuessInputError, GuessNotSentError, TuzmoClient, WordRejectedError  # noqa: E402

URL = "https://www.tusmo.xyz/infinite"
MAX_ATTEMPTS = 6
REJECT_TIMEOUT_MS = 6000  # court : suffisant pour détecter "Mot inconnu" sans traîner
# Filet de sécurité, pas un "top-5" figé : borne le pire cas (groupe où la quasi-
# totalité des mots seraient rejetés) à un temps encore compatible avec un duel de
# 45s (20 x ~2s/coup ≈ 40s) plutôt que de vider tout le sous-corpus un par un.
MAX_SUGGEST_RETRIES = 20

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_ROOT_CACHE = ROOT_DIR / "data" / "root_cache.json"
DEFAULT_LOG = ROOT_DIR / "data" / "bot_latency_log.jsonl"
DEFAULT_REPORT = ROOT_DIR / "data" / "bot_latency_report.json"
DEFAULT_BLOCKLIST = ROOT_DIR / "data" / "known_invalid_words.json"


def suggest_and_submit_with_retry(
    solver: Solver,
    submit: Callable[[str], bool],
    max_retries: int | None = MAX_SUGGEST_RETRIES,
) -> str | None:
    """Propose un mot via le solveur — recalcul dynamique à CHAQUE tentative (pas une
    liste de suggestions figée à l'avance) — et le soumet via `submit(word) -> bool`
    (True = accepté). Si rejeté, le mot est retiré du sous-corpus du solveur et une
    nouvelle proposition est recalculée sur ce sous-corpus amputé, jusqu'à
    acceptation, épuisement réel des candidats, ou `max_retries` atteint (filet de
    sécurité — cf. commentaire sur MAX_SUGGEST_RETRIES).

    Retourne le mot accepté, ou None si aucun mot n'a pu être soumis.
    """
    retries = 0
    while solver.candidates:
        if max_retries is not None and retries >= max_retries:
            return None
        guess = solver.suggest(top_n=1)[0][0]
        retries += 1
        if submit(guess):
            return guess
        if guess in solver.candidates:
            solver.candidates.remove(guess)
    return None


def play_one_game(
    client: TuzmoClient,
    corpus: Corpus,
    root_cache: dict,
    game_index: int,
    log_path: Path,
    blocklist_path: Path = DEFAULT_BLOCKLIST,
) -> dict:
    letter = client.get_first_letter()
    length = client.get_word_length()
    blocklist = load_blocklist(blocklist_path)
    solver = Solver(letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not solver.candidates:
            # Pool épuisé : soit tous les candidats restants ont été rejetés par le
            # jeu, soit la cible réelle n'est pas dans notre corpus (le feedback
            # observé ne correspond alors à aucun mot restant) — les deux sont des
            # symptômes du même écart corpus/dictionnaire de jeu, mesuré séparément
            # (data/root_cache_word_validation.json), pas une anomalie du benchmark.
            return {"solved": False, "attempts": attempt - 1, "outcome": "candidates_exhausted"}

        accepted_timer: CycleTimer | None = None

        def submit(guess: str) -> bool:
            nonlocal accepted_timer
            timer = CycleTimer(attempt=attempt, log_path=log_path)
            timer.set_cycle_start()
            timer.mark("solver_suggest")
            try:
                try:
                    client.submit_guess(guess, timer=timer, timeout=REJECT_TIMEOUT_MS)
                except (GuessInputError, GuessNotSentError):
                    # le serveur n'a pas jugé ce mot (autre mot reçu / rien envoyé) :
                    # une seule nouvelle tentative, ligne vidée par le client, jamais
                    # de liste noire sur ce motif
                    client.submit_guess(guess, timer=timer, timeout=REJECT_TIMEOUT_MS)
            except WordRejectedError:
                timer.finish({"game": game_index, "outcome": "rejected", "guess": guess})
                add_to_blocklist(guess, blocklist_path)
                return False
            except PlaywrightTimeoutError:
                # ni feedback ni toast détecté dans le délai : anomalie réelle
                # (site lent/bloqué), distincte d'un rejet explicite — on ne masque pas
                timer.finish({"game": game_index, "outcome": "timeout", "guess": guess})
                raise
            accepted_timer = timer
            return True

        played_guess = suggest_and_submit_with_retry(solver, submit)

        if played_guess is None:
            return {"solved": False, "attempts": attempt, "outcome": "candidates_exhausted_after_rejections"}

        solver.play(played_guess)
        pattern = client.read_feedback(timer=accepted_timer)
        accepted_timer.finish(
            {"game": game_index, "outcome": "completed", "guess": played_guess, "pattern": pattern}
        )
        solver.update(pattern)

        # victoire confirmée par le pattern serveur du coup joué, pas par
        # l'élimination locale des candidats (cf. tests/test_solved_confirmation.py)
        if pattern == "2" * length:
            return {"solved": True, "attempts": attempt, "outcome": "solved"}

    return {"solved": False, "attempts": MAX_ATTEMPTS, "outcome": "not_solved"}


def load_records(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    k = (len(values) - 1) * p
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def build_report(records: list[dict], games: list[dict]) -> dict:
    completed = [r for r in records if r.get("outcome") == "completed"]
    rejected = [r for r in records if r.get("outcome") == "rejected"]

    step_keys = sorted({k for r in completed for k in r if k.startswith("duration_") and k != "duration_total_s"})
    per_step = {}
    for key in step_keys:
        values = [r[key] for r in completed if key in r]
        if values:
            per_step[key] = {
                "mean_s": round(statistics.mean(values), 4),
                "median_s": round(statistics.median(values), 4),
                "p95_s": round(percentile(values, 0.95), 4),
                "n": len(values),
            }

    totals = [r["duration_total_s"] for r in completed if "duration_total_s" in r]
    solved_games = [g for g in games if g["solved"]]
    outcome_counts: dict[str, int] = {}
    for g in games:
        outcome_counts[g["outcome"]] = outcome_counts.get(g["outcome"], 0) + 1

    return {
        "n_games": len(games),
        "n_games_solved": len(solved_games),
        "game_outcomes": outcome_counts,
        "n_moves_completed": len(completed),
        "n_moves_rejected": len(rejected),
        "rejection_rate": round(len(rejected) / (len(completed) + len(rejected)), 3) if (completed or rejected) else None,
        "per_step_latency": per_step,
        "move_total_latency": {
            "mean_s": round(statistics.mean(totals), 4) if totals else None,
            "median_s": round(statistics.median(totals), 4) if totals else None,
            "p95_s": round(percentile(totals, 0.95), 4) if totals else None,
        },
        "attempts_per_game": {
            "mean": round(statistics.mean([g["attempts"] for g in games]), 2) if games else None,
            "values": [g["attempts"] for g in games],
        },
        "game_total_latency_by_attempts": {
            str(n): round(n * statistics.mean(totals), 2) if totals else None for n in range(1, MAX_ATTEMPTS + 1)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS))
    parser.add_argument("--root-cache", default=str(DEFAULT_ROOT_CACHE))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--blocklist", default=str(DEFAULT_BLOCKLIST))
    parser.add_argument("--fresh-log", action="store_true", help="Repart d'un log vide plutôt que d'accumuler.")
    args = parser.parse_args()

    corpus = Corpus.from_file(args.corpus_path)
    root_cache = load_cache(args.root_cache)
    log_path = Path(args.log)
    if args.fresh_log and log_path.exists():
        log_path.unlink()

    games: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for i in range(args.games):
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(URL, wait_until="networkidle", timeout=20000)
            page.locator("button", has_text="C'est parti").first.click()
            page.wait_for_selector(".board.board--stage", timeout=10000)
            page.wait_for_timeout(400)

            client = TuzmoClient(page)
            result = play_one_game(
                client, corpus, root_cache, game_index=i, log_path=log_path, blocklist_path=Path(args.blocklist)
            )
            games.append(result)
            print(f"[{i + 1}/{args.games}] {result}")
            page.close()
        browser.close()

    records = load_records(log_path)
    report = build_report(records, games)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(games)} partie(s) jouée(s), {report['n_games_solved']} résolue(s).")
    print(f"Taux de rejet ('Mot inconnu') : {report['rejection_rate']}")
    print(f"Log détaillé : {log_path}")
    print(f"Rapport agrégé : {args.report}")


if __name__ == "__main__":
    main()
