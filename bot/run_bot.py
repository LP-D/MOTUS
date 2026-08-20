#!/usr/bin/env python3
"""Boucle Solver <-> TuzmoClient : résout le mot du jour Tusmo, ou s'arrête après 6 essais."""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

from bot.tuzmo_client import TuzmoClient  # noqa: E402

URL = "https://www.tusmo.xyz/daily"
CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "corpus_fr.txt"
MAX_ATTEMPTS = 6


def main() -> None:
    corpus = Corpus.from_file(CORPUS_PATH)

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

        solver = Solver(letter=letter, length=length, corpus=corpus)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            guess = solver.suggest(top_n=1)[0][0]
            print(f"Essai {attempt}/{MAX_ATTEMPTS}: {guess}")
            solver.play(guess)
            client.submit_guess(guess)
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
