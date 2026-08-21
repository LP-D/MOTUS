#!/usr/bin/env python3
"""Boucle Solver <-> TuzmoClient : résout le mot du jour Tusmo (/daily, une partie
par jour), ou s'arrête après 6 essais. En cas de rejet ("Mot inconnu"), retire le
mot du sous-corpus et redemande une proposition au solveur (cf. bot/tuzmo_client.py,
scripts/benchmark_bot_latency.py pour la même logique)."""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))

from motus_solver.blocklist import add_to_blocklist, load_blocklist  # noqa: E402
from motus_solver.cache import load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

from bot.tuzmo_client import TuzmoClient, WordRejectedError  # noqa: E402

URL = "https://www.tusmo.xyz/daily"
CORPUS_PATH = ROOT_DIR / "data" / "corpus_fr.txt"
ROOT_CACHE_PATH = ROOT_DIR / "data" / "root_cache.json"
BLOCKLIST_PATH = ROOT_DIR / "data" / "known_invalid_words.json"
MAX_ATTEMPTS = 6
REJECT_TIMEOUT_MS = 6000
MAX_SUGGEST_RETRIES = 20


def play_move(client: TuzmoClient, solver: Solver, attempt: int) -> str | None:
    """Propose un mot (recalcul dynamique à chaque rejet) et le soumet, jusqu'à
    acceptation ou épuisement des candidats. Retourne le mot joué, ou None."""
    retries = 0
    while solver.candidates and retries < MAX_SUGGEST_RETRIES:
        guess = solver.suggest(top_n=1)[0][0]
        retries += 1
        try:
            client.submit_guess(guess, timeout=REJECT_TIMEOUT_MS)
        except WordRejectedError:
            print(f"  essai {attempt} : {guess!r} rejeté (Mot inconnu), nouvelle proposition...")
            add_to_blocklist(guess, BLOCKLIST_PATH)
            solver.candidates.remove(guess)
            continue
        return guess
    return None


def main() -> None:
    corpus = Corpus.from_file(CORPUS_PATH)
    root_cache = load_cache(ROOT_CACHE_PATH)
    blocklist = load_blocklist(BLOCKLIST_PATH)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL, wait_until="networkidle")

        start_button = page.locator("button", has_text="C'est parti")
        if start_button.count() and start_button.first.is_visible():
            start_button.first.click()
        page.wait_for_selector(".board.board--stage")

        client = TuzmoClient(page)
        letter = client.get_first_letter()
        length = client.get_word_length()
        print(f"Lettre de départ: {letter}, longueur: {length}")

        solver = Solver(letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                guess = play_move(client, solver, attempt)
            except PlaywrightTimeoutError:
                print("  ni feedback ni rejet détecté à temps : anomalie, arrêt.")
                break

            if guess is None:
                print(f"  essai {attempt} : plus aucun candidat jouable.")
                break

            print(f"Essai {attempt}/{MAX_ATTEMPTS}: {guess}")
            solver.play(guess)
            pattern = client.read_feedback()
            print(f"  retour: {pattern}")
            solver.update(pattern)

            if solver.is_solved():
                print(f"Résolu : {solver.solution}")
                break

            time.sleep(random.uniform(0.6, 1.4))
        else:
            print(f"Non résolu après {MAX_ATTEMPTS} essais. Candidats restants: {solver.candidates[:10]}")

        browser.close()


if __name__ == "__main__":
    main()
