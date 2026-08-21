"""Pilote une partie du bot dans un thread séparé et pousse chaque événement dans
une queue thread-safe que le backend FastAPI relaie au frontend via WebSocket.

Aucune logique de jeu ni de scoring n'est modifiée ici : réutilise tel quel
`motus_solver.solver.Solver` et `bot.tuzmo_client.TuzmoClient` (même gestion du
rejet "Mot inconnu" par recalcul dynamique que `scripts/benchmark_bot_latency.py`).
"""
from __future__ import annotations

import queue
import sys
import threading
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

from bot.timing import CycleTimer  # noqa: E402
from bot.tuzmo_client import TuzmoClient, WordRejectedError  # noqa: E402

DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_ROOT_CACHE = ROOT_DIR / "data" / "root_cache.json"
DEFAULT_AUTH_STATE = ROOT_DIR / "data" / "tuzmo_auth_state.json"
DEFAULT_LOG = ROOT_DIR / "data" / "dashboard_bot_log.jsonl"
DEFAULT_BLOCKLIST = ROOT_DIR / "data" / "known_invalid_words.json"
URL = "https://www.tusmo.xyz/infinite"
MAX_ATTEMPTS = 6
REJECT_TIMEOUT_MS = 6000
MAX_SUGGEST_RETRIES = 20


class BotRunner:
    """Un seul bot actif à la fois (dashboard mono-utilisateur, local uniquement)."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.events: queue.Queue[dict] = queue.Queue()
        self.status = "idle"  # idle | running | stopped | error

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._lock:
            if self.is_running():
                return False
            self._stop_event.clear()
            self.status = "running"
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.is_running():
                return False
            self._stop_event.set()
            return True

    def _emit(self, event_type: str, **data) -> None:
        self.events.put({"type": event_type, "timestamp": time.time(), **data})

    def _run(self) -> None:
        try:
            corpus = Corpus.from_file(DEFAULT_CORPUS)
            root_cache = load_cache(DEFAULT_ROOT_CACHE)
            blocklist = load_blocklist(DEFAULT_BLOCKLIST)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context_kwargs = {"viewport": {"width": 1280, "height": 900}}
                if DEFAULT_AUTH_STATE.exists():
                    context_kwargs["storage_state"] = str(DEFAULT_AUTH_STATE)
                    self._emit("auth_loaded", path=str(DEFAULT_AUTH_STATE))
                else:
                    self._emit("auth_missing")
                context = browser.new_context(**context_kwargs)
                page = context.new_page()
                page.goto(URL, wait_until="networkidle", timeout=20000)
                page.locator("button", has_text="C'est parti").first.click()
                page.wait_for_selector(".board.board--stage", timeout=10000)
                page.wait_for_timeout(400)

                client = TuzmoClient(page)
                letter = client.get_first_letter()
                length = client.get_word_length()
                self._emit("game_started", letter=letter, length=length)

                solver = Solver(
                    letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist
                )
                solved = False

                for attempt in range(1, MAX_ATTEMPTS + 1):
                    if self._stop_event.is_set():
                        self._emit("stopped")
                        self.status = "stopped"
                        browser.close()
                        return
                    if not solver.candidates:
                        self._emit("candidates_exhausted", attempt=attempt)
                        break

                    accepted_timer: CycleTimer | None = None
                    accepted_guess: str | None = None
                    retries = 0
                    while solver.candidates and retries < MAX_SUGGEST_RETRIES:
                        if self._stop_event.is_set():
                            break
                        guess = solver.suggest(top_n=1)[0][0]
                        retries += 1
                        timer = CycleTimer(attempt=attempt, log_path=DEFAULT_LOG)
                        timer.set_cycle_start()
                        timer.mark("solver_suggest")
                        self._emit("guess_proposed", attempt=attempt, guess=guess)
                        try:
                            client.submit_guess(guess, timer=timer, timeout=REJECT_TIMEOUT_MS)
                        except WordRejectedError:
                            record = timer.finish({"outcome": "rejected", "guess": guess})
                            blocklist = add_to_blocklist(guess, DEFAULT_BLOCKLIST)
                            self._emit("guess_rejected", attempt=attempt, guess=guess, record=record)
                            if guess in solver.candidates:
                                solver.candidates.remove(guess)
                            continue
                        except PlaywrightTimeoutError as exc:
                            self._emit("error", message=str(exc))
                            self.status = "error"
                            browser.close()
                            return
                        accepted_timer = timer
                        accepted_guess = guess
                        break

                    if self._stop_event.is_set():
                        self._emit("stopped")
                        self.status = "stopped"
                        browser.close()
                        return

                    if accepted_guess is None:
                        self._emit("candidates_exhausted", attempt=attempt)
                        break

                    solver.play(accepted_guess)
                    pattern = client.read_feedback(timer=accepted_timer)
                    record = accepted_timer.finish(
                        {"outcome": "completed", "guess": accepted_guess, "pattern": pattern}
                    )
                    solver.update(pattern)
                    self._emit(
                        "feedback_received", attempt=attempt, guess=accepted_guess, pattern=pattern, record=record
                    )

                    if solver.is_solved():
                        solved = True
                        self._emit("solved", solution=solver.solution, attempts=attempt)
                        break

                if not solved and not self._stop_event.is_set():
                    self._emit("not_solved")

                self.status = "idle"
                browser.close()
        except Exception as exc:  # le thread ne doit jamais planter en silence
            self._emit("error", message=f"{exc.__class__.__name__}: {exc}")
            self.status = "error"


runner = BotRunner()
