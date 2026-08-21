from __future__ import annotations

import random
import time

from playwright.sync_api import Locator, Page

from .parser import row_pattern
from .timing import CycleTimer

BOARD_SELECTOR = ".board.board--stage"
ROW_SELECTOR = f"{BOARD_SELECTOR} .board-row"
KEYBOARD_SELECTOR = ".keyboard button"
ENTER_KEY = "⏎"
BACKSPACE_KEY = "⌫"


class WordRejectedError(RuntimeError):
    """Le mot soumis a été rejeté par le dictionnaire de validation du jeu ("Mot
    inconnu"), détecté explicitement plutôt que via un TimeoutError générique."""

    def __init__(self, word: str):
        super().__init__(f'mot rejeté par Tuzmo ("Mot inconnu") : {word!r}')
        self.word = word


class TuzmoClient:
    def __init__(self, page: Page):
        self.page = page
        self._attempt_count = 0
        self._keyboard_buttons: dict[str, Locator] | None = None

    def _keyboard(self) -> dict[str, Locator]:
        if self._keyboard_buttons is None:
            buttons = self.page.query_selector_all(KEYBOARD_SELECTOR)
            self._keyboard_buttons = {(b.text_content() or "").strip(): b for b in buttons}
        return self._keyboard_buttons

    def get_first_letter(self) -> str:
        cell = self.page.query_selector(f"{ROW_SELECTOR}:first-child .cell:first-child .cell__letter")
        return (cell.text_content() or "").strip().upper()

    def get_word_length(self) -> int:
        return self.page.eval_on_selector_all(f"{ROW_SELECTOR}:first-child .cell", "cells => cells.length")

    def submit_guess(self, word: str, timer: CycleTimer | None = None, timeout: float | None = None) -> None:
        """`timer` : instrumentation optionnelle (aucun effet sur le déroulement du
        jeu si omis). `timeout` : délai d'attente (ms) de la confirmation du coup,
        transmis tel quel à Playwright (défaut Playwright si omis, comportement
        historique inchangé) — un appelant peut réduire ce délai pour détecter plus
        vite un rejet ("Mot inconnu", cf. bot/timing.py) sans changer la logique."""
        keyboard = self._keyboard()
        for letter in word.upper()[1:]:  # la 1ère lettre est déjà offerte, jamais tapée
            keyboard[letter].click()
            time.sleep(random.uniform(0.08, 0.22))
        if timer is not None:
            timer.mark("guess_typed")

        time.sleep(random.uniform(0.15, 0.35))
        keyboard[ENTER_KEY].click()
        if timer is not None:
            timer.mark("guess_submitted")

        # Attend l'une ou l'autre issue (accepté -> cases révélées ; rejeté -> ligne
        # secouée ("anim-shake"), ou toast "Mot inconnu" transitoirement visible).
        self.page.wait_for_selector(".cell--revealed, .cell--absent, .toast, .anim-shake", timeout=timeout)
        self.page.wait_for_timeout(500)

        # Le toast se referme en quelques centaines de ms (trop tôt pour être détecté
        # de façon fiable ici) : le signal robuste est l'état final de la ligne
        # elle-même — un mot rejeté ne fait jamais passer ses cases en
        # cell--revealed/cell--absent, elles restent cell--dot.
        row = self.page.query_selector_all(ROW_SELECTOR)[self._attempt_count]
        if row.query_selector(".cell--revealed, .cell--absent") is None:
            raise WordRejectedError(word)

        if timer is not None:
            timer.mark("feedback_confirmed")
        self._attempt_count += 1

    def read_feedback(self, timer: CycleTimer | None = None) -> str:
        if self._attempt_count == 0:
            raise RuntimeError("appelle submit_guess(word) avant read_feedback()")

        row = self.page.query_selector_all(ROW_SELECTOR)[self._attempt_count - 1]
        raw: list[tuple[str, str | None]] = []
        for cell in row.query_selector_all(".cell"):
            cell_class = cell.get_attribute("class") or ""
            mark = cell.query_selector(".cell__mark")
            mark_class = mark.get_attribute("class") if mark else None
            raw.append((cell_class, mark_class))
        if timer is not None:
            timer.mark("feedback_detected")

        pattern = row_pattern(raw)
        if timer is not None:
            timer.mark("feedback_parsed")
        return pattern

    def is_word_rejected(self) -> bool:
        """Détecte le toast "Mot inconnu" (mot hors dictionnaire de validation du
        jeu) — lecture d'état pure, n'affecte pas le déroulement du jeu."""
        return self.page.locator("text=Mot inconnu").count() > 0
