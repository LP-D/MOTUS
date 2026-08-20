from __future__ import annotations

import random
import time

from playwright.sync_api import Locator, Page

from .parser import row_pattern

BOARD_SELECTOR = ".board.board--stage"
ROW_SELECTOR = f"{BOARD_SELECTOR} .board-row"
KEYBOARD_SELECTOR = ".keyboard button"
ENTER_KEY = "⏎"
BACKSPACE_KEY = "⌫"


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

    def submit_guess(self, word: str) -> None:
        keyboard = self._keyboard()
        for letter in word.upper()[1:]:  # la 1ère lettre est déjà offerte, jamais tapée
            keyboard[letter].click()
            time.sleep(random.uniform(0.08, 0.22))
        time.sleep(random.uniform(0.15, 0.35))
        keyboard[ENTER_KEY].click()
        self.page.wait_for_selector(".cell--revealed, .cell--absent")
        self.page.wait_for_timeout(500)
        self._attempt_count += 1

    def read_feedback(self) -> str:
        if self._attempt_count == 0:
            raise RuntimeError("appelle submit_guess(word) avant read_feedback()")

        row = self.page.query_selector_all(ROW_SELECTOR)[self._attempt_count - 1]
        raw: list[tuple[str, str | None]] = []
        for cell in row.query_selector_all(".cell"):
            cell_class = cell.get_attribute("class") or ""
            mark = cell.query_selector(".cell__mark")
            mark_class = mark.get_attribute("class") if mark else None
            raw.append((cell_class, mark_class))
        return row_pattern(raw)
