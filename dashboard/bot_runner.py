"""Pilote une ou plusieurs parties du bot dans un thread séparé et pousse chaque
événement dans une queue thread-safe que le backend FastAPI relaie au frontend via
WebSocket.

Aucune logique de jeu ni de scoring n'est modifiée ici : réutilise tel quel
`motus_solver.solver.Solver` et `bot.tuzmo_client.TuzmoClient` (même gestion du
rejet "Mot inconnu" par recalcul dynamique que `scripts/benchmark_bot_latency.py`).
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import Browser, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))

from motus_solver.blocklist import add_to_blocklist, load_blocklist  # noqa: E402
from motus_solver.cache import load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

from bot.parser import CORRECT  # noqa: E402
from bot.timing import CycleTimer  # noqa: E402
from bot.tuzmo_client import TuzmoClient, WordRejectedError  # noqa: E402

from bot_config import config as bot_config  # noqa: E402
from stats_store import record_game  # noqa: E402

DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_ROOT_CACHE = ROOT_DIR / "data" / "root_cache.json"
DEFAULT_AUTH_STATE = ROOT_DIR / "data" / "tuzmo_auth_state.json"
DEFAULT_LOG = ROOT_DIR / "data" / "dashboard_bot_log.jsonl"
DEFAULT_BLOCKLIST = ROOT_DIR / "data" / "known_invalid_words.json"
URL = "https://www.tusmo.xyz/infinite"
MAX_ATTEMPTS = 6
REJECT_TIMEOUT_MS = 6000
MAX_SUGGEST_RETRIES = 20

# Sous-ensemble d'événements de cycle de vie (démarrage/arrêt d'une partie ou d'une
# boucle) persistés dans DEFAULT_LOG en plus d'être poussés sur le WebSocket — pour
# pouvoir diagnostiquer après coup un démarrage de partie inattendu (cf. tâche
# "diagnostic partie auto-lancée") même si personne n'observait le dashboard en
# direct au moment des faits. `GET /api/logs` les expose déjà sans code additionnel.
_LIFECYCLE_EVENT_TYPES = {
    "start_requested",
    "game_started",
    "loop_progress",
    "loop_finished",
    "stopped",
    "error",
    "auth_loaded",
    "auth_missing",
}


class BotRunner:
    """Un seul bot actif à la fois (dashboard mono-utilisateur, local uniquement).

    `start(iterations=N)` enchaîne N parties consécutives sans intervention
    manuelle (relance automatique après victoire/défaite/candidats épuisés) —
    `iterations=1` (défaut) reproduit exactement le comportement "une partie"
    d'origine. Chaque partie est indépendante (nouvelle page/session à chaque
    fois, cf. commentaire dans `_run` sur ce choix) ; les stats de chaque partie
    sont persistées immédiatement (`stats_store.record_game`), donc une
    interruption (bouton Arrêter / Ctrl+C côté CLI) entre deux parties ne perd
    jamais la progression déjà loggée — reprendre consiste simplement à relancer
    la boucle, les stats s'accumulent au fil des lancements.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.events: queue.Queue[dict] = queue.Queue()
        self.status = "idle"  # idle | running | stopped | error
        self.current_iteration = 0
        self.total_iterations = 0

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, iterations: int = 1) -> bool:
        with self._lock:
            # `BotRunner` est un singleton de durée de vie du processus (cf.
            # `runner = BotRunner()` en bas de fichier) : c'est le SEUL endroit du
            # code qui peut faire démarrer une partie (aucun polling ni callback
            # résiduel ailleurs ne le fait — vérifié). Un démarrage "inattendu"
            # observé plus tard est donc forcément un appel explicite à `start()`
            # antérieur (ex. un test précédent dans le même processus long-lived
            # jamais arrêté), jamais un déclenchement spontané côté bot — ce log
            # permet de le confirmer après coup plutôt que de le supposer.
            already_running = self.is_running()
            self._emit(
                "start_requested",
                accepted=not already_running,
                status_before=self.status,
                current_iteration=self.current_iteration,
                total_iterations=self.total_iterations,
                requested_iterations=max(1, iterations),
            )
            if already_running:
                return False
            self._stop_event.clear()
            self.status = "running"
            self.current_iteration = 0
            self.total_iterations = max(1, iterations)
            self._thread = threading.Thread(target=self._run, args=(self.total_iterations,), daemon=True)
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.is_running():
                return False
            self._stop_event.set()
            return True

    def _emit(self, event_type: str, **data) -> None:
        event = {"type": event_type, "timestamp": time.time(), **data}
        self.events.put(event)
        if event_type in _LIFECYCLE_EVENT_TYPES:
            self._append_lifecycle_log(event)

    def _append_lifecycle_log(self, event: dict) -> None:
        try:
            with open(DEFAULT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _open_game_page(self, browser: Browser) -> Page:
        context_kwargs = {"viewport": {"width": 1280, "height": 900}}
        if DEFAULT_AUTH_STATE.exists():
            context_kwargs["storage_state"] = str(DEFAULT_AUTH_STATE)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto(URL, wait_until="networkidle", timeout=20000)
        page.locator("button", has_text="C'est parti").first.click()
        page.wait_for_selector(".board.board--stage", timeout=10000)
        page.wait_for_timeout(400)
        return page

    def _play_one_game(
        self, page: Page, corpus: Corpus, root_cache: dict, blocklist: set[str]
    ) -> tuple[dict, set[str]]:
        """Joue une partie complète sur `page` (déjà chargée sur /infinite, prête).
        Retourne (résultat, blocklist à jour — peut avoir grandi si un mot a été
        rejeté pendant cette partie)."""
        client = TuzmoClient(page)
        letter = client.get_first_letter()
        length = client.get_word_length()
        self._emit("game_started", letter=letter, length=length)

        solver = Solver(letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist)
        solved = False
        confirmed_solution: str | None = None
        guesses_played: list[str] = []
        stats_saved = False
        result = {"outcome": "unknown", "solved": False, "letter": letter, "length": length}

        def save_stats(outcome: str) -> None:
            nonlocal stats_saved
            record_game(
                letter=letter,
                length=length,
                attempts=len(guesses_played),
                solved=solved,
                outcome=outcome,
                guesses=guesses_played,
                solution=confirmed_solution,
            )
            stats_saved = True
            result["outcome"] = outcome
            result["solved"] = solved

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self._stop_event.is_set():
                result["outcome"] = "stopped"
                return result, blocklist
            if not solver.candidates:
                self._emit("candidates_exhausted", attempt=attempt)
                save_stats("candidates_exhausted")
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
                letter_delay_s, enter_delay_s = bot_config.get_typing_delay_s()
                try:
                    client.submit_guess(
                        guess,
                        timer=timer,
                        timeout=REJECT_TIMEOUT_MS,
                        letter_delay=letter_delay_s,
                        enter_delay=enter_delay_s,
                    )
                except WordRejectedError:
                    record = timer.finish({"outcome": "rejected", "guess": guess})
                    blocklist = add_to_blocklist(guess, DEFAULT_BLOCKLIST)
                    self._emit("guess_rejected", attempt=attempt, guess=guess, record=record)
                    if guess in solver.candidates:
                        solver.candidates.remove(guess)
                    continue
                except PlaywrightTimeoutError as exc:
                    self._emit("error", message=str(exc))
                    result["outcome"] = "error"
                    result["exception"] = exc
                    return result, blocklist
                accepted_timer = timer
                accepted_guess = guess
                break

            if self._stop_event.is_set():
                result["outcome"] = "stopped"
                return result, blocklist

            if accepted_guess is None:
                self._emit("candidates_exhausted", attempt=attempt)
                save_stats("candidates_exhausted")
                break

            guesses_played.append(accepted_guess)
            solver.play(accepted_guess)
            pattern = client.read_feedback(timer=accepted_timer)
            record = accepted_timer.finish({"outcome": "completed", "guess": accepted_guess, "pattern": pattern})
            solver.update(pattern)
            self._emit(
                "feedback_received", attempt=attempt, guess=accepted_guess, pattern=pattern, record=record
            )

            # Victoire confirmée UNIQUEMENT par le pattern du coup qui vient d'être
            # soumis (toutes les cases "correct", cf. bot/parser.py) — ce pattern
            # provient de la réponse du serveur Tuzmo au coup réellement joué.
            # solver.is_solved() (candidats réduits à un seul par élimination) n'est
            # PAS utilisé ici : c'est une déduction locale qui peut être vraie sans
            # que le mot effectivement joué soit le bon (ex. écart entre le corpus
            # local et le dictionnaire de validation réel de Tuzmo) — voir
            # tests/test_solved_confirmation.py.
            if pattern == CORRECT * length:
                solved = True
                confirmed_solution = accepted_guess
                self._emit("solved", solution=confirmed_solution, attempts=attempt)
                save_stats("solved")
                break

        if not solved and not self._stop_event.is_set():
            self._emit("not_solved")
            if not stats_saved:
                save_stats("not_solved")

        return result, blocklist

    def _run(self, iterations: int) -> None:
        try:
            corpus = Corpus.from_file(DEFAULT_CORPUS)
            root_cache = load_cache(DEFAULT_ROOT_CACHE)
            blocklist = load_blocklist(DEFAULT_BLOCKLIST)

            with sync_playwright() as playwright:
                # Un seul navigateur pour toute la boucle (évite de relancer Chromium
                # à chaque partie) ; une page/contexte frais PAR partie — plus simple
                # et plus robuste que de tenter de réutiliser l'enchaînement de mot
                # côté serveur en cas de victoire (comportement non vérifié en cas
                # d'échec, cf. docs/tuzmo_site_notes.md) : chaque partie redémarre
                # proprement, indépendamment du résultat de la précédente.
                browser = playwright.chromium.launch(headless=True)
                if DEFAULT_AUTH_STATE.exists():
                    self._emit("auth_loaded", path=str(DEFAULT_AUTH_STATE))
                else:
                    self._emit("auth_missing")

                for i in range(1, iterations + 1):
                    if self._stop_event.is_set():
                        break

                    self.current_iteration = i
                    self._emit("loop_progress", current=i, total=iterations)

                    page = self._open_game_page(browser)
                    try:
                        result, blocklist = self._play_one_game(page, corpus, root_cache, blocklist)
                    finally:
                        page.context.close()

                    if result["outcome"] == "error":
                        self.status = "error"
                        browser.close()
                        return
                    if result["outcome"] == "stopped":
                        break

                if self._stop_event.is_set():
                    self._emit("stopped")
                    self.status = "stopped"
                else:
                    self._emit("loop_finished", completed=self.current_iteration, total=iterations)
                    self.status = "idle"
                browser.close()
        except Exception as exc:  # le thread ne doit jamais planter en silence
            self._emit("error", message=f"{exc.__class__.__name__}: {exc}")
            self.status = "error"


runner = BotRunner()
