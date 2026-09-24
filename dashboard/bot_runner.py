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
import random
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))

from motus_solver.blocklist import add_to_blocklist, load_blocklist  # noqa: E402
from motus_solver.cache import load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

from bot.network_monitor import NetworkMonitor, session_from_body  # noqa: E402
from bot.parser import CORRECT  # noqa: E402
from bot.timing import CycleTimer  # noqa: E402
from bot.tuzmo_client import (  # noqa: E402
    GameStateError,
    GuessInputError,
    GuessNotSentError,
    ThrottlingDetectedError,
    TuzmoClient,
    WordRejectedError,
)

from bot_config import config as bot_config  # noqa: E402
from stats_store import record_game  # noqa: E402

DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_ROOT_CACHE = ROOT_DIR / "data" / "root_cache.json"
DEFAULT_ROOT_CACHE_ENTROPY_PURE = ROOT_DIR / "data" / "root_cache_entropy_pure.json"
DEFAULT_AUTH_STATE = ROOT_DIR / "data" / "tuzmo_auth_state.json"
DEFAULT_LOG = ROOT_DIR / "data" / "dashboard_bot_log.jsonl"
DEFAULT_BLOCKLIST = ROOT_DIR / "data" / "known_invalid_words.json"
# mots acceptés par le serveur (même format que la liste noire, cf. motus_solver.blocklist)
DEFAULT_KNOWN_VALID = ROOT_DIR / "data" / "known_valid_words.json"
# solutions révélées par abandon (mots hors corpus) : journal + ajout au corpus
DEFAULT_REVEALED = ROOT_DIR / "data" / "revealed_solutions.jsonl"
URL = "https://www.tusmo.xyz/infinite"
API_ME_URL = "https://www.tusmo.xyz/api/me"
GUEST_COOKIE = "tusmo_token"
START_BUTTON_SELECTOR = "button:has-text(\"C'est parti\")"
PAGE_READY_SELECTOR = f"{START_BUTTON_SELECTOR}, .board.board--stage"
MAX_ATTEMPTS = 6
REJECT_TIMEOUT_MS = 6000
MAX_SUGGEST_RETRIES = 20
# Coups non transmis correctement (mot envoyé != mot voulu, ou aucune requête) :
# retentés après nettoyage de la ligne, mais plafonnés pour ne jamais boucler.
MAX_INPUT_FAILURES_PER_ATTEMPT = 3
API_PATTERN_CODE = {"correct": "2", "present": "1", "absent": "0"}
# Pause entre deux parties : la création de session (POST /api/game) compte aussi
# dans le débit validé (1.5-2.5s entre requêtes) — jamais d'enchaînement plus rapide.
INTER_GAME_DELAY_S = (1.5, 2.5)

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
    "throttled",
    "guess_not_submitted",
    "session_info",
    "game_resumed",
    "gave_up",
    "reveal_failed",
    "solution_revealed",
    "guest_ready",
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
        self.status = "idle"  # idle | running | stopped | error | throttled
        self.current_iteration = 0
        self.total_iterations = 0
        self.page_monitor: NetworkMonitor | None = None
        # "composite" (défaut) ou "entropy_pure" (alternative, jamais activée par
        # défaut ; son cache racine a les mêmes coups de repli que le composite)
        self.strategy = "composite"

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

    def root_cache_path(self) -> Path:
        return DEFAULT_ROOT_CACHE_ENTROPY_PURE if self.strategy == "entropy_pure" else DEFAULT_ROOT_CACHE

    def _new_context(self, browser: Browser) -> BrowserContext:
        context_kwargs = {"viewport": {"width": 1280, "height": 900}}
        if DEFAULT_AUTH_STATE.exists():
            context_kwargs["storage_state"] = str(DEFAULT_AUTH_STATE)
        return browser.new_context(**context_kwargs)

    def _ensure_guest(self, context: BrowserContext) -> dict:
        """Crée l'invité AVANT le 1er chargement de page du contexte.

        Au chargement, le site envoie GET /api/me et POST /api/game au même instant
        (0 à 8 ms d'écart, mesuré sur 22 chargements). Sur un invité neuf, les deux
        partent sans cookie et chacun peut créer un invité : si le navigateur garde
        le cookie de l'autre, la partie n'appartient pas à l'invité courant et le 1er
        coup renvoie NOT_FOUND (H8, puis 1er mot du run d'amélioration n° 4). Un seul
        GET /api/me préalable, qui partage les cookies du contexte, supprime la
        course. Sans effet si le cookie existe déjà (session sauvegardée)."""
        if any(c.get("name") == GUEST_COOKIE for c in context.cookies(API_ME_URL)):
            return {"created": False, "reason": "cookie invité déjà présent"}
        started = time.time()
        response = context.request.get(API_ME_URL, timeout=10000)
        latency = time.time() - started
        retry_after = {k.lower(): v for k, v in response.headers.items()}.get("retry-after")
        if response.status == 429 or retry_after is not None or latency > 10.0:
            raise ThrottlingDetectedError(
                f"création d'invité : HTTP {response.status}, Retry-After={retry_after}, {latency:.1f}s"
            )
        cookie_set = any(c.get("name") == GUEST_COOKIE for c in context.cookies(API_ME_URL))
        info = {"created": True, "status": response.status, "latency_s": round(latency, 3),
                "t_ready": time.time(), "cookie_set": cookie_set}
        self._emit("guest_ready", **info)
        time.sleep(random.uniform(*INTER_GAME_DELAY_S))  # débit validé avant le 1er chargement
        return info

    def _open_game_page(self, context: BrowserContext) -> Page:
        """Ouvre /infinite dans `context` et démarre une partie.

        Un 429 (ou tout signal de throttling) pendant le chargement lève
        `ThrottlingDetectedError` au lieu d'un simple timeout Playwright — c'est
        ainsi que se manifestait le "guest creation rate limited" du 23/09/2026
        (plateau jamais affiché, cause invisible sans lecture réseau)."""
        page = context.new_page()
        monitor = NetworkMonitor(page)  # avant goto : voit /api/me et POST /api/game
        self.page_monitor = monitor  # exposé pour l'instrumentation (scripts/run_case_matrix.py)

        def check_throttle() -> None:
            signal = monitor.first_throttle_signal()
            if signal:
                call, reason = signal
                page.close()
                raise ThrottlingDetectedError(f"{reason} sur {call.method} {call.url}", call)

        # "domcontentloaded" + attente explicite, au lieu de "networkidle" (500 ms de
        # silence réseau imposées à chaque partie). On attend le bouton de départ OU
        # le plateau : avec l'invité conservé, l'écran "C'est parti" ne s'affiche
        # qu'au 1er chargement du contexte — attendre le bouton seul coûtait 10 s
        # (timeout) à chaque partie suivante (régression mesurée au run n° 3).
        page.goto(URL, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_selector(PAGE_READY_SELECTOR, timeout=10000)
        except PlaywrightTimeoutError:
            check_throttle()  # ni bouton ni plateau : 429 au chargement ?
        check_throttle()
        start_button = page.locator("button", has_text="C'est parti")
        if start_button.count():
            start_button.first.click()
        try:
            page.wait_for_selector(".board.board--stage", timeout=10000)
        except PlaywrightTimeoutError:
            check_throttle()
            raise
        check_throttle()
        self._restart_if_finished(page, monitor)
        check_throttle()
        page.wait_for_timeout(400)
        return page

    def _restart_if_finished(self, page: Page, monitor: NetworkMonitor) -> None:
        """Si le chargement renvoie une partie déjà TERMINÉE (invité conservé après
        une défaite ou un abandon), clique une fois "Rejouer" : le site relance alors
        POST /api/game (cf. `onPlayAgain` de la vue /infinite). Sinon, rien. Si ça ne
        suffit pas, `_play_one_game` refusera la session et la boucle s'arrêtera."""
        monitor.resolve_bodies()
        creates = [session_from_body(c.body) for c in monitor.calls if c.kind == "create_session" and c.body]
        creates = [c for c in creates if c]
        if not creates or creates[-1].get("status") in (None, "playing"):
            return
        replay = page.locator("button", has_text="Rejouer")
        if not replay.count():
            return
        self._emit("session_info", session_id=creates[-1].get("id"), status=creates[-1].get("status"),
                   action="rejouer")
        time.sleep(random.uniform(*INTER_GAME_DELAY_S))  # débit validé : nouvelle requête de création
        n_before = len(creates)
        replay.first.click(timeout=5000)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            monitor.resolve_bodies()
            done = [c for c in monitor.calls if c.kind == "create_session" and c.status is not None]
            if len(done) > n_before:
                break
            page.wait_for_timeout(100)

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

        known_valid = load_blocklist(DEFAULT_KNOWN_VALID)
        solver = Solver(letter=letter, length=length, corpus=corpus, root_cache=root_cache, blocklist=blocklist,
                        known_valid=known_valid, strategy=self.strategy)
        solved = False
        confirmed_solution: str | None = None
        guesses_played: list[str] = []
        stats_saved = False
        result = {"outcome": "unknown", "solved": False, "letter": letter, "length": length}

        # Avec un contexte (donc un invité) conservé d'une partie à l'autre, le
        # serveur peut renvoyer la partie /infinite en cours de cet invité au lieu
        # d'une nouvelle (cf. startGame du site : il rejoue `session.guesses`).
        session, n_create_calls = self._current_session()
        if session is not None:
            prior = session.get("guesses") or []
            self._emit("session_info", session_id=session.get("id"), status=session.get("status"),
                       prior_guesses=len(prior), create_calls=n_create_calls)
            if session.get("status") not in (None, "playing"):
                self._emit("error", message=f"session renvoyée non jouable (status={session.get('status')!r})")
                result["outcome"] = "session_not_playable"
                return result, blocklist
            if prior:
                for g in prior:
                    word = g["word"].upper()
                    solver.play(word)
                    solver.update("".join(API_PATTERN_CODE[r] for r in g["result"]))
                    guesses_played.append(word)
                client.resume_from(len(prior))
                result["resumed"] = True
                self._emit("game_resumed", prior_guesses=[g["word"] for g in prior])
        first_attempt = len(guesses_played) + 1
        exhausted = False

        def save_stats(outcome: str, revealed: str | None = None) -> None:
            nonlocal stats_saved
            record_game(
                letter=letter,
                length=length,
                attempts=len(guesses_played),
                solved=solved,
                outcome=outcome,
                guesses=guesses_played,
                solution=confirmed_solution or revealed,
            )
            stats_saved = True
            result["outcome"] = outcome
            result["solved"] = solved

        for attempt in range(first_attempt, MAX_ATTEMPTS + 1):
            if self._stop_event.is_set():
                result["outcome"] = "stopped"
                return result, blocklist
            if not solver.candidates:
                self._emit("candidates_exhausted", attempt=attempt)
                exhausted = True
                break

            accepted_timer: CycleTimer | None = None
            accepted_guess: str | None = None
            retries = 0
            input_failures = 0
            while solver.candidates and retries < MAX_SUGGEST_RETRIES:
                if self._stop_event.is_set():
                    break
                t_suggest = time.perf_counter()
                guess = solver.suggest(top_n=1)[0][0]
                solver_s = round(time.perf_counter() - t_suggest, 4)
                retries += 1
                timer = CycleTimer(attempt=attempt, log_path=DEFAULT_LOG)
                timer.set_cycle_start()
                timer.mark("solver_suggest")
                self._emit("guess_proposed", attempt=attempt, guess=guess, solver_s=solver_s,
                           candidates=len(solver.candidates))
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
                    # INVALID_WORD confirmé par le serveur pour CE mot exact (corps de
                    # la requête vérifié par le client) : seul cas de mise en liste noire.
                    record = timer.finish({"outcome": "rejected", "guess": guess})
                    blocklist = add_to_blocklist(guess, DEFAULT_BLOCKLIST)
                    self._emit("guess_rejected", attempt=attempt, guess=guess, record=record)
                    if guess in solver.candidates:
                        solver.candidates.remove(guess)
                    continue
                except (GuessInputError, GuessNotSentError) as exc:
                    # Le serveur n'a pas jugé CE mot (autre mot reçu, ou rien d'envoyé) :
                    # jamais de liste noire ici — c'est la cause de la cascade de faux
                    # rejets diagnostiquée le 23/09/2026. Même mot retenté (ligne vidée).
                    record = timer.finish({"outcome": "not_submitted", "guess": guess, "error": str(exc)})
                    self._emit("guess_not_submitted", attempt=attempt, guess=guess, reason=str(exc), record=record)
                    input_failures += 1
                    if input_failures >= MAX_INPUT_FAILURES_PER_ATTEMPT:
                        self._emit("error", message=f"saisie impossible après {input_failures} essais : {exc}")
                        result["outcome"] = "error"
                        result["exception"] = exc
                        return result, blocklist
                    continue
                except ThrottlingDetectedError as exc:
                    self._emit("throttled", reason=exc.reason)
                    result["outcome"] = "throttled"
                    result["exception"] = exc
                    return result, blocklist
                except GameStateError as exc:
                    self._emit("error", message=str(exc))
                    save_stats("game_error")
                    result["exception"] = exc
                    return result, blocklist
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
                exhausted = True
                break

            guesses_played.append(accepted_guess)
            if accepted_guess not in known_valid:
                known_valid = add_to_blocklist(accepted_guess, DEFAULT_KNOWN_VALID)  # même format de fichier
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

        if exhausted:
            # solution hors corpus : abandon côté serveur AVANT d'enregistrer les
            # stats, pour y inscrire la solution révélée
            result["outcome"] = "candidates_exhausted"
            if page is not None and not self._stop_event.is_set():
                self._abandon(client, result, corpus, letter, length)
            if result["outcome"] != "throttled":
                save_stats("candidates_exhausted", revealed=result.get("answer"))
        elif not solved and not self._stop_event.is_set():
            self._emit("not_solved")
            if not stats_saved:
                save_stats("not_solved")

        return result, blocklist

    def _current_session(self) -> tuple[dict | None, int]:
        """Session renvoyée par le dernier POST /api/game de la page (moniteur
        branché par `_open_game_page`), et nombre d'appels de création observés
        (plusieurs appels concurrents = piste du NOT_FOUND de H8)."""
        if self.page_monitor is None:
            return None, 0
        self.page_monitor.resolve_bodies()
        creates = [c for c in self.page_monitor.calls if c.kind == "create_session"]
        bodies = [session_from_body(c.body) for c in creates if c.body]
        bodies = [b for b in bodies if b]
        return (bodies[-1] if bodies else None), len(creates)

    def _abandon(self, client: TuzmoClient, result: dict, corpus: Corpus | None = None,
                 letter: str | None = None, length: int | None = None) -> None:
        """Mot que le bot ne peut plus trouver (solution hors corpus) : le clore côté
        serveur, sinon le même invité le retrouverait au chargement suivant.

        1. `giveup` (endpoint du site) : clôt la partie ET révèle la solution, qui est
           journalisée et ajoutée au corpus pour les parties suivantes (A6, P8).
        2. En cas d'échec, bouton ↻ (reset) : clôt sans révéler."""
        answer = None
        session_id = client.current_session_id() if hasattr(client, "current_session_id") else None
        if session_id and hasattr(client, "reveal_answer"):
            try:
                answer = client.reveal_answer(session_id)
            except ThrottlingDetectedError as exc:
                self._emit("throttled", reason=exc.reason)
                result["outcome"] = "throttled"
                return
            except Exception as exc:  # giveup indisponible : repli sur ↻
                self._emit("reveal_failed", message=str(exc))
        if answer:
            result["answer"] = answer.upper()
            result["abandoned"] = True
            self._emit("gave_up", method="giveup", answer=result["answer"])
            self._record_revealed(result["answer"], corpus, letter, length)
            return
        try:
            client.abandon_current_word()
        except ThrottlingDetectedError as exc:
            self._emit("throttled", reason=exc.reason)
            result["outcome"] = "throttled"
            return
        except (GameStateError, PlaywrightTimeoutError) as exc:
            self._emit("error", message=f"abandon impossible : {exc}")
            result["abandon_failed"] = True
            return
        result["abandoned"] = True
        self._emit("gave_up", method="reset")

    def _record_revealed(self, answer: str, corpus: Corpus | None, letter: str | None, length: int | None) -> None:
        """Journalise une solution révélée ; si elle est hors corpus, l'ajoute au
        corpus (fichier + mémoire) et aux mots connus valides."""
        in_corpus = bool(corpus) and answer in set(corpus.subset(answer[0], len(answer)))
        entry = {"t": time.time(), "letter": letter, "length": length, "answer": answer, "was_in_corpus": in_corpus}
        try:
            with open(DEFAULT_REVEALED, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if corpus is not None and not in_corpus and corpus.add_word(answer):
                with open(DEFAULT_CORPUS, "a", encoding="utf-8") as f:
                    f.write(answer + "\n")
            add_to_blocklist(answer, DEFAULT_KNOWN_VALID)  # même format de fichier
        except OSError as exc:
            self._emit("error", message=f"solution révélée non enregistrée : {exc}")
        self._emit("solution_revealed", **entry)

    def _run_games(
        self, browser: Browser, iterations: int, corpus: Corpus, root_cache: dict, blocklist: set[str]
    ) -> None:
        """Boucle de N parties dans UN SEUL contexte navigateur.

        Correctif du 23/09/2026 : l'ancienne version ouvrait un contexte neuf (sans
        cookie) par partie, donc Tuzmo créait un nouvel invité anonyme à chaque
        partie, jusqu'au 429 "guest creation rate limited" (GET /api/me, POST
        /api/game) après une quinzaine de parties. Le contexte, et donc l'invité,
        est désormais conservé pendant toute la boucle ; seule la page change à
        chaque partie. Ce chemin n'a pas pu être revalidé en direct (le serveur
        limitait déjà la création d'invités au moment du correctif)."""
        if DEFAULT_AUTH_STATE.exists():
            self._emit("auth_loaded", path=str(DEFAULT_AUTH_STATE))
        else:
            self._emit("auth_missing")
        context = self._new_context(browser)
        previous_resumed = False
        try:
            try:
                self._ensure_guest(context)
            except ThrottlingDetectedError as exc:
                self._emit("throttled", reason=exc.reason)
                self.status = "throttled"
                return
            for i in range(1, iterations + 1):
                if self._stop_event.is_set():
                    break

                self.current_iteration = i
                self._emit("loop_progress", current=i, total=iterations)

                try:
                    page = self._open_game_page(context)
                except ThrottlingDetectedError as exc:
                    self._emit("throttled", reason=exc.reason)
                    self.status = "throttled"
                    return
                try:
                    result, blocklist = self._play_one_game(page, corpus, root_cache, blocklist)
                finally:
                    page.close()

                if result["outcome"] == "error":
                    self.status = "error"
                    return
                if result["outcome"] == "throttled":
                    # Arrêt d'urgence de toute la boucle (429 / Retry-After / latence
                    # > 10s) : ne jamais enchaîner de partie suivante dans ce cas.
                    self.status = "throttled"
                    return
                if result["outcome"] == "stopped":
                    break
                # Garde-fous du contexte persistant : jamais de boucle sur une partie
                # que le bot ne sait pas mener à terme.
                if result["outcome"] == "session_not_playable" or result.get("abandon_failed"):
                    self.status = "error"
                    return
                if result.get("resumed") and previous_resumed:
                    self._emit("error", message="deux parties reprises d'affilée : arrêt pour éviter une boucle")
                    self.status = "error"
                    return
                previous_resumed = bool(result.get("resumed"))
                if i < iterations and self._stop_event.wait(random.uniform(*INTER_GAME_DELAY_S)):
                    break

            if self._stop_event.is_set():
                self._emit("stopped")
                self.status = "stopped"
            else:
                self._emit("loop_finished", completed=self.current_iteration, total=iterations)
                self.status = "idle"
        finally:
            context.close()

    def _run(self, iterations: int) -> None:
        try:
            corpus = Corpus.from_file(DEFAULT_CORPUS)
            root_cache = load_cache(self.root_cache_path())
            blocklist = load_blocklist(DEFAULT_BLOCKLIST)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    self._run_games(browser, iterations, corpus, root_cache, blocklist)
                finally:
                    browser.close()
        except Exception as exc:  # le thread ne doit jamais planter en silence
            self._emit("error", message=f"{exc.__class__.__name__}: {exc}")
            self.status = "error"


runner = BotRunner()
