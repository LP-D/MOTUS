from __future__ import annotations

import random
import time

from playwright.sync_api import Locator, Page

from .network_monitor import ApiCall, NetworkMonitor, is_throttle_signal, result_from_body
from .parser import row_pattern
from .timing import CycleTimer

BOARD_SELECTOR = ".board.board--stage"
ROW_SELECTOR = f"{BOARD_SELECTOR} .board-row"
KEYBOARD_SELECTOR = ".keyboard button"
ENTER_KEY = "⏎"
BACKSPACE_KEY = "⌫"

# Débit validé par le stress test initial (1.5-2.5s entre deux requêtes) : le
# client attend au besoin avant d'envoyer un coup, il ne va JAMAIS plus vite.
DEFAULT_MIN_REQUEST_GAP_S = (1.5, 2.5)
REQUEST_SENT_TIMEOUT_S = 3.0  # Entrée pressée sans requête /guess dans ce délai -> non envoyé
RESPONSE_TIMEOUT_S = 10.0  # au-delà : signal de throttling (latence > 10s), arrêt d'urgence
CLICK_TIMEOUT_MS = 5000  # un clavier bloqué remonte vite en erreur au lieu de pendre 30s
REVEAL_TIMEOUT_MS = 5000
# Bouton ↻ de /infinite : 1er clic = armement d'une confirmation valable 3 s (score > 0)
RESET_ARM_WAIT_S = 0.5
API_CODE = {"correct": "2", "present": "1", "absent": "0"}


class WordRejectedError(RuntimeError):
    """Le serveur a répondu `INVALID_WORD` pour CE mot précisément (corps de la
    requête vérifié) — seul cas où un mot peut être mis en liste noire."""

    def __init__(self, word: str):
        super().__init__(f'mot rejeté par Tuzmo (INVALID_WORD confirmé serveur) : {word!r}')
        self.word = word


class GuessNotSentError(RuntimeError):
    """Entrée pressée mais aucune requête /guess partie (ex. ligne incomplète) —
    ce n'est PAS un rejet du mot, ne jamais le mettre en liste noire."""


class GuessInputError(RuntimeError):
    """Le mot réellement envoyé au serveur diffère du mot voulu (ligne restée
    remplie par un coup précédent, clics perdus...) — pas un rejet du mot voulu."""

    def __init__(self, intended: str, sent: str | None):
        super().__init__(f"mot envoyé {sent!r} != mot voulu {intended!r}")
        self.intended = intended
        self.sent = sent


class GameStateError(RuntimeError):
    """Erreur serveur autre que INVALID_WORD (ex. GAME_OVER)."""

    def __init__(self, server_error: str):
        super().__init__(f"erreur serveur : {server_error}")
        self.server_error = server_error


class ThrottlingDetectedError(RuntimeError):
    """429, Retry-After ou latence > 10s : arrêt d'urgence, distinct d'un échec
    de jeu ou de correction."""

    def __init__(self, reason: str, call: ApiCall | None = None):
        super().__init__(f"throttling détecté : {reason}")
        self.reason = reason
        self.call = call


class TuzmoClient:
    """Pilote une partie Tusmo via le clavier virtuel, avec la RÉPONSE SERVEUR
    comme source de vérité (acceptation, rejet, feedback).

    Historique du correctif (diagnostic du 23/09/2026, scripts/diagnose_guess_pipeline.py) :
    l'ancienne version déduisait l'issue d'un coup de l'état DOM de la ligne. Or
    après un vrai rejet, Tuzmo ne vide PAS la ligne : les lettres du mot suivant
    étaient ignorées (ligne pleine) et Entrée renvoyait l'ANCIEN mot, rejeté à
    nouveau — le bot attribuait ce rejet au nouveau mot (mis en liste noire à
    tort), en cascade jusqu'à épuisement des candidats. Désormais : la ligne est
    vidée après tout coup non accepté, le mot réellement envoyé est vérifié dans
    le corps de la requête, et seul un `INVALID_WORD` portant sur ce mot exact
    lève `WordRejectedError`.
    """

    def __init__(
        self,
        page: Page,
        monitor: NetworkMonitor | None = None,
        min_request_gap_s: tuple[float, float] = DEFAULT_MIN_REQUEST_GAP_S,
    ):
        self.page = page
        self.monitor = monitor or NetworkMonitor(page)
        self.min_request_gap_s = min_request_gap_s
        self._attempt_count = 0
        self._keyboard_buttons: dict[str, Locator] | None = None
        self._row_dirty = False
        self.last_pattern: str | None = None
        self.last_call: ApiCall | None = None

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

    def clear_current_row(self, length: int) -> None:
        """Efface les lettres restées dans la ligne courante (après un rejet ou un
        envoi raté). Un retour arrière sur une ligne vide est sans effet."""
        backspace = self._keyboard()[BACKSPACE_KEY]
        for _ in range(length):
            backspace.click(timeout=CLICK_TIMEOUT_MS)
            time.sleep(random.uniform(0.03, 0.07))
        self._row_dirty = False

    def _respect_request_gap(self) -> None:
        last = self.monitor.last_guess_sent_at()
        if last is None:
            return
        wait = last + random.uniform(*self.min_request_gap_s) - time.time()
        if wait > 0:
            time.sleep(wait)

    def _wait_for_new_call(
        self, known_ids: set[int], kind: str = "guess", timeout_s: float | None = None
    ) -> ApiCall | None:
        deadline = time.time() + (REQUEST_SENT_TIMEOUT_S if timeout_s is None else timeout_s)
        while time.time() < deadline:
            for call in self.monitor.calls:
                if call.kind == kind and id(call) not in known_ids:
                    return call
            self.page.wait_for_timeout(50)
        return None

    def _wait_for_response(self, call: ApiCall) -> None:
        deadline = call.t_sent + RESPONSE_TIMEOUT_S
        while call.status is None and call.failure is None and time.time() < deadline:
            self.page.wait_for_timeout(50)
        if call.status is None:
            reason = f"échec réseau ({call.failure})" if call.failure else f"aucune réponse en {RESPONSE_TIMEOUT_S:.0f}s"
            raise ThrottlingDetectedError(reason, call)
        self.monitor.resolve_bodies()

    def _wait_for_row_reveal(self, row_index: int, length: int) -> None:
        """Attend la fin de l'animation de révélation (le clavier n'accepte pas
        de nouvelles lettres avant). Best effort : après une victoire, /infinite
        remplace le plateau par le mot suivant, la ligne peut ne plus exister."""
        try:
            self.page.wait_for_function(
                """([sel, i, n]) => {
                    const row = document.querySelectorAll(sel)[i];
                    if (!row) return true;
                    const cells = row.querySelectorAll('.cell');
                    let done = 0;
                    cells.forEach(c => { if (c.classList.contains('cell--revealed') || c.classList.contains('cell--absent')) done++; });
                    return done >= n;
                }""",
                arg=[ROW_SELECTOR, row_index, length],
                timeout=REVEAL_TIMEOUT_MS,
            )
        except Exception:
            pass

    def submit_guess(
        self,
        word: str,
        timer: CycleTimer | None = None,
        timeout: float | None = None,
        letter_delay: tuple[float, float] = (0.08, 0.22),
        enter_delay: tuple[float, float] = (0.15, 0.35),
    ) -> str:
        """Tape et soumet `word`, retourne le pattern "0/1/2" renvoyé par le serveur.

        Lève `WordRejectedError` (INVALID_WORD confirmé pour ce mot exact),
        `GuessInputError` (le serveur a reçu un autre mot), `GuessNotSentError`
        (aucune requête partie), `GameStateError` (autre erreur serveur) ou
        `ThrottlingDetectedError` (429 / Retry-After / latence > 10s).
        `timeout` est conservé pour compatibilité d'appel (les délais sont
        désormais bornés par les constantes du module)."""
        word = word.upper()
        keyboard = self._keyboard()
        if self._row_dirty:
            self.clear_current_row(len(word))

        for letter in word[1:]:  # la 1ère lettre est déjà offerte, jamais tapée
            keyboard[letter].click(timeout=CLICK_TIMEOUT_MS)
            time.sleep(random.uniform(*letter_delay))
        if timer is not None:
            timer.mark("guess_typed")

        time.sleep(random.uniform(*enter_delay))
        self._respect_request_gap()
        known_ids = {id(c) for c in self.monitor.calls}
        keyboard[ENTER_KEY].click(timeout=CLICK_TIMEOUT_MS)
        if timer is not None:
            timer.mark("guess_submitted")

        call = self._wait_for_new_call(known_ids)
        if call is None:
            self._row_dirty = True
            raise GuessNotSentError(f"aucune requête /guess après Entrée pour {word!r}")
        self._wait_for_response(call)
        self.last_call = call
        throttle = is_throttle_signal(call)
        if throttle:
            raise ThrottlingDetectedError(throttle, call)

        if (call.guess or "").upper() != word:
            self._row_dirty = True
            raise GuessInputError(word, call.guess)
        if call.server_error == "INVALID_WORD":
            self._row_dirty = True
            raise WordRejectedError(word)
        if call.server_error:
            raise GameStateError(call.server_error)

        result = result_from_body(call.body)
        if not result or len(result) != len(word):
            raise GameStateError(f"réponse sans résultat exploitable : {call.body!r}")
        self.last_pattern = "".join(API_CODE[r] for r in result)
        if timer is not None:
            timer.mark("feedback_confirmed")

        self._wait_for_row_reveal(self._attempt_count, len(word))
        self._attempt_count += 1
        return self.last_pattern

    def resume_from(self, n_accepted: int) -> None:
        """Partie reprise côté serveur avec `n_accepted` coups déjà joués : la
        prochaine saisie va dans la ligne `n_accepted` (pas dans la 1re)."""
        self._attempt_count = n_accepted

    def current_session_id(self) -> str | None:
        """Identifiant de la partie réellement utilisée (URL du dernier coup envoyé)."""
        for call in reversed(self.monitor.calls):
            if call.kind == "guess" and "/api/game/" in call.url:
                return call.url.split("/api/game/", 1)[1].split("/", 1)[0]
        return None

    def reveal_answer(self, session_id: str) -> str | None:
        """Abandonne le mot côté serveur et récupère la solution : POST
        /api/game/{id}/giveup, requête identique à celle du site (`api.giveUp` :
        fetch JSON, corps `{}`, cookies de la page). /infinite n'affiche pas ce
        bouton ; d'après le code du site, l'endpoint renvoie `session.answer` (validation
        en direct : docs/diagnostics/2026-09-23_cycle_amelioration_2.md). Sert aux
        solutions hors corpus (cas A6, P8).

        Lève `GameStateError` si la requête n'aboutit pas, `ThrottlingDetectedError`
        sur throttling."""
        self._respect_request_gap()
        known_ids = {id(c) for c in self.monitor.calls}
        self.page.evaluate(
            """async (id) => {
                const r = await fetch(`/api/game/${id}/giveup`, {
                    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({})
                });
                return r.status;
            }""",
            session_id,
        )
        call = self._wait_for_new_call(known_ids, kind="give_up")
        if call is None:
            raise GameStateError("abandon (giveup) non transmis")
        self._wait_for_response(call)
        throttle = is_throttle_signal(call)
        if throttle:
            raise ThrottlingDetectedError(throttle, call)
        if call.server_error or call.status != 200:
            raise GameStateError(call.server_error or f"HTTP {call.status}")
        return ((call.body or {}).get("session") or {}).get("answer")

    def abandon_current_word(self) -> None:
        """Clôt côté serveur un mot que le bot ne peut plus trouver, via le bouton
        ↻ de /infinite (`button.run-reset` : POST /api/game/{id}/reset, puis le
        site relance lui-même une partie). Sans cela, l'invité conservé d'une
        partie à l'autre retrouverait ce mot au chargement suivant.

        Vérifié en direct le 23/09/2026 : /infinite n'affiche PAS de bouton
        "Abandonner" (le libellé existe dans le code du site, pas dans ce mode).
        Le bouton ↻ demande une confirmation quand le score est > 0 : le 1er clic
        l'arme, un 2e clic dans les 3 s déclenche la réinitialisation.

        Lève `GameStateError` si la réinitialisation n'a pas été transmise,
        `ThrottlingDetectedError` sur throttling."""
        self._respect_request_gap()
        known_ids = {id(c) for c in self.monitor.calls}
        button = self.page.locator("button.run-reset")
        if button.count() == 0:
            raise GameStateError("bouton de réinitialisation (run-reset) introuvable")
        button.first.click(timeout=CLICK_TIMEOUT_MS)
        # Attente COURTE : si le 1er clic n'a fait qu'armer la confirmation, le 2e
        # clic doit tomber dans la fenêtre de 3 s du site. Attendre ici le délai
        # standard (3 s) laissait expirer la fenêtre : le 2e clic ré-armait
        # simplement le bouton (constaté en direct le 23/09/2026).
        call = self._wait_for_new_call(known_ids, kind="reset", timeout_s=RESET_ARM_WAIT_S)
        if call is None:
            button.first.click(timeout=CLICK_TIMEOUT_MS)
            call = self._wait_for_new_call(known_ids, kind="reset")
        if call is None:
            raise GameStateError("réinitialisation non transmise (aucune requête /reset)")
        self._wait_for_response(call)
        throttle = is_throttle_signal(call)
        if throttle:
            raise ThrottlingDetectedError(throttle, call)
        if call.server_error:
            raise GameStateError(call.server_error)

    def read_feedback(self, timer: CycleTimer | None = None) -> str:
        """Pattern du dernier coup accepté, tel que renvoyé par le SERVEUR.

        Repli sur la lecture DOM uniquement si aucune réponse serveur n'a été
        capturée (ne devrait pas arriver avec submit_guess)."""
        if self._attempt_count == 0:
            raise RuntimeError("appelle submit_guess(word) avant read_feedback()")
        if timer is not None:
            timer.mark("feedback_detected")
        pattern = self.last_pattern if self.last_pattern is not None else self.read_dom_pattern(self._attempt_count - 1)
        if timer is not None:
            timer.mark("feedback_parsed")
        return pattern

    def read_dom_pattern(self, row_index: int) -> str:
        row = self.page.query_selector_all(ROW_SELECTOR)[row_index]
        raw: list[tuple[str, str | None]] = []
        for cell in row.query_selector_all(".cell"):
            cell_class = cell.get_attribute("class") or ""
            mark = cell.query_selector(".cell__mark")
            mark_class = mark.get_attribute("class") if mark else None
            raw.append((cell_class, mark_class))
        return row_pattern(raw)

    def is_word_rejected(self) -> bool:
        """Détecte le toast "Mot inconnu" — lecture d'état pure (diagnostic)."""
        return self.page.locator("text=Mot inconnu").count() > 0
