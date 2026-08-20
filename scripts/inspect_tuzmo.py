#!/usr/bin/env python3
"""Exploration ponctuelle du DOM de tusmo.xyz (étape 3, préalable à bot/parser.py).

Ouvre la page "Mot du jour", joue un essai (pour révéler les cases colorées),
prend un screenshot, et dumpe le HTML de la grille de jeu pour inspection
manuelle de l'encodage du retour (0=absent / 1=présent / 2=correct).
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://www.tusmo.xyz/daily"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SCREENSHOT_PATH = DATA_DIR / "tuzmo_screenshot.png"
DOM_SAMPLE_PATH = DATA_DIR / "tuzmo_dom_sample.html"

# Mot valide de 7 lettres (le jeu rejette les essais hors dictionnaire) utilisé
# uniquement pour déclencher un retour coloré et observer son encodage.
PROBE_WORD = "EPARGNE"
ENTER_KEY = "⏎"


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL, wait_until="networkidle")

        start_button = page.locator("button", has_text="C'est parti")
        if start_button.count() and start_button.first.is_visible():
            start_button.first.click()

        page.wait_for_selector(".board.board--stage")

        keyboard_buttons = {
            (b.text_content() or "").strip(): b for b in page.query_selector_all(".keyboard button")
        }
        for letter in PROBE_WORD[1:]:  # la 1ère lettre est déjà offerte par le jeu
            keyboard_buttons[letter].click()
        keyboard_buttons[ENTER_KEY].click()
        page.wait_for_selector(".cell--revealed, .cell--absent")
        page.wait_for_timeout(500)  # laisse finir les animations de révélation

        page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
        print(f"Screenshot: {SCREENSHOT_PATH}")

        board_html = page.query_selector(".board.board--stage").inner_html()
        DOM_SAMPLE_PATH.write_text(board_html, encoding="utf-8")
        print(f"HTML zone de jeu: {DOM_SAMPLE_PATH}")

        browser.close()


if __name__ == "__main__":
    main()
