"""Garde-fous du contexte persistant (un seul invité Tuzmo pour toute la boucle).

Le code du site (startGame : `for (e of session.guesses) applyKeyStates(...)`,
actions `giveUp` / `resetRun` propres au mode infinite) montre que POST /api/game
peut renvoyer à un même invité sa partie /infinite EN COURS. Avec un contexte
conservé d'une partie à l'autre, le bot doit donc :
- reprendre une partie déjà entamée en rejouant ses coups dans le solveur ;
- refuser une session renvoyée non jouable (terminée) au lieu de boucler ;
- clore côté serveur une partie qu'il ne peut plus gagner (candidats épuisés),
  sinon elle serait reprise indéfiniment ;
- s'arrêter si deux parties reprises se suivent.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import bot_runner  # noqa: E402

import bot.tuzmo_client as tc  # noqa: E402
from bot.network_monitor import ApiCall, NetworkMonitor  # noqa: E402
from bot.tuzmo_client import GameStateError, TuzmoClient  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.feedback import pattern_string  # noqa: E402

CODE_TO_API = {"2": "correct", "1": "present", "0": "absent"}


def api_result(guess, target):
    return [CODE_TO_API[c] for c in pattern_string(guess, target)]


class FakeMonitor:
    def __init__(self, session_body):
        self.calls = [ApiCall(kind="create_session", method="POST", url="https://www.tusmo.xyz/api/game",
                              guess=None, t_sent=time.time(), t_received=time.time(), status=200,
                              body=session_body)]

    def resolve_bodies(self):
        pass


def make_client_cls(target, give_up_answer="?", give_up_raises=None):
    class ScriptedClient:
        instances = []

        def __init__(self, page):
            self.last_guess = None
            self.resumed_from = None
            self.submitted = []
            self.gave_up = False
            ScriptedClient.instances.append(self)

        def get_first_letter(self):
            return target[0]

        def get_word_length(self):
            return len(target)

        def resume_from(self, n):
            self.resumed_from = n

        def submit_guess(self, word, **kwargs):
            self.last_guess = word.upper()
            self.submitted.append(self.last_guess)

        def read_feedback(self, timer=None):
            return pattern_string(self.last_guess, target)

        def abandon_current_word(self):
            if give_up_raises:
                raise give_up_raises
            self.gave_up = True

    return ScriptedClient


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_runner, "DEFAULT_LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(bot_runner, "DEFAULT_BLOCKLIST", tmp_path / "blocklist.json")
    recorded = []
    monkeypatch.setattr(bot_runner, "record_game", lambda **kw: recorded.append(kw))
    r = bot_runner.BotRunner()
    r.recorded = recorded
    return r


def events(runner):
    out = []
    while not runner.events.empty():
        out.append(runner.events.get_nowait())
    return out


def test_resumed_session_replays_prior_guesses_into_solver(runner, monkeypatch):
    target = "RIVER"
    corpus = Corpus(["RATER", "RIVER", "ROBOT", "RADIO"])
    client_cls = make_client_cls(target)
    monkeypatch.setattr(bot_runner, "TuzmoClient", client_cls)
    runner.page_monitor = FakeMonitor({"session": {
        "id": "s1", "status": "playing", "firstLetter": "R", "wordLength": 5,
        "guesses": [{"word": "ROBOT", "result": api_result("ROBOT", target)}],
    }})

    result, _ = runner._play_one_game(page=None, corpus=corpus, root_cache=None, blocklist=set())

    client = client_cls.instances[-1]
    assert client.resumed_from == 1  # la saisie reprend à la 2e ligne
    assert "ROBOT" not in client.submitted  # coup déjà joué, pas rejoué au serveur
    assert result["solved"] is True and result["resumed"] is True
    assert runner.recorded[0]["guesses"][0] == "ROBOT"
    assert any(e["type"] == "game_resumed" for e in events(runner))


def test_finished_session_is_refused_not_played(runner, monkeypatch):
    monkeypatch.setattr(bot_runner, "TuzmoClient", make_client_cls("RIVER"))
    runner.page_monitor = FakeMonitor({"session": {"id": "s1", "status": "lost", "firstLetter": "R",
                                                   "wordLength": 5, "guesses": []}})
    result, _ = runner._play_one_game(page=None, corpus=Corpus(["RIVER", "RATER"]), root_cache=None, blocklist=set())
    assert result["outcome"] == "session_not_playable"
    assert runner.recorded == []


def test_unwinnable_game_is_closed_server_side(runner, monkeypatch):
    """Candidats épuisés (solution hors corpus, cas A6) : abandon explicite, pour
    que le même invité ne retrouve pas cette partie au chargement suivant."""
    client_cls = make_client_cls("RUBAN")  # hors corpus
    monkeypatch.setattr(bot_runner, "TuzmoClient", client_cls)
    result, _ = runner._play_one_game(page=object(), corpus=Corpus(["RATER", "RIVER"]), root_cache=None,
                                      blocklist=set())
    assert result["outcome"] == "candidates_exhausted"
    assert client_cls.instances[-1].gave_up is True
    assert result["abandoned"] is True
    assert any(e["type"] == "gave_up" for e in events(runner))


def test_failed_abandon_is_flagged(runner, monkeypatch):
    client_cls = make_client_cls("RUBAN", give_up_raises=GameStateError("bouton de réinitialisation (run-reset) introuvable"))
    monkeypatch.setattr(bot_runner, "TuzmoClient", client_cls)
    result, _ = runner._play_one_game(page=object(), corpus=Corpus(["RATER", "RIVER"]), root_cache=None,
                                      blocklist=set())
    assert result["abandon_failed"] is True


class _Ctx:
    def __init__(self):
        self.closed = False

    def new_page(self):
        return _Page()

    def close(self):
        self.closed = True


class _Page:
    def close(self):
        pass


class _Browser:
    def new_context(self, **kw):
        return _Ctx()


@pytest.mark.parametrize(
    "results, expected_games",
    [
        ([{"outcome": "solved", "resumed": True}, {"outcome": "solved", "resumed": True}], 2),
        ([{"outcome": "candidates_exhausted", "abandon_failed": True}], 1),
        ([{"outcome": "session_not_playable"}], 1),
    ],
    ids=["two_resumed_in_a_row", "abandon_failed", "session_not_playable"],
)
def test_loop_stops_instead_of_looping_on_problem_sessions(runner, monkeypatch, results, expected_games):
    monkeypatch.setattr(bot_runner, "INTER_GAME_DELAY_S", (0.0, 0.0))
    monkeypatch.setattr(bot_runner, "DEFAULT_AUTH_STATE", Path("absent.json"))
    played = []
    monkeypatch.setattr(runner, "_open_game_page", lambda ctx: ctx.new_page())

    def play(page, corpus, cache, blocklist):
        r = results[min(len(played), len(results) - 1)]
        played.append(r)
        return dict(r), blocklist

    monkeypatch.setattr(runner, "_play_one_game", play)
    runner._run_games(_Browser(), 10, corpus=None, root_cache={}, blocklist=set())
    assert len(played) == expected_games
    assert runner.status == "error"


# --- client : bouton ↻ (run-reset) de /infinite ---

class _ResetLocator:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1 if self.page.has_button else 0

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        now = time.time()
        # comportement du site : la confirmation armée expire après 3 s ; un clic
        # après expiration ne fait que ré-armer le bouton
        if self.page.armed_at is not None and now - self.page.armed_at > 3.0:
            self.page.armed_at = None
        self.page.clicks += 1
        needs_confirm = self.page.clicks_needed == 2
        if needs_confirm and self.page.armed_at is None:
            self.page.armed_at = now
            return
        if True:
            now = time.time()
            self.page.monitor.calls.append(ApiCall(
                kind="reset", method="POST", url="https://www.tusmo.xyz/api/game/s1/reset", guess=None,
                t_sent=now, t_received=now, status=200, body={"ok": True},
            ))


class FakeResetPage:
    def __init__(self, has_button=True, clicks_needed=1):
        self.has_button, self.clicks_needed, self.clicks = has_button, clicks_needed, 0
        self.armed_at = None
        self.monitor = NetworkMonitor(self)

    def on(self, event, handler):
        pass

    def locator(self, selector, has_text=None):
        assert selector == "button.run-reset"
        return _ResetLocator(self)

    def wait_for_timeout(self, ms):
        pass


@pytest.mark.parametrize("clicks_needed", [1, 2], ids=["score_0_single_click", "score_positive_confirm"])
def test_client_abandon_uses_run_reset_with_confirmation(monkeypatch, clicks_needed):
    """Délais RÉELS du client (pas de raccourci) : avec l'ancienne attente de 3 s
    entre les deux clics, la confirmation du site expirait et ce test échouait."""
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    page = FakeResetPage(clicks_needed=clicks_needed)
    client = TuzmoClient(page, monitor=page.monitor, min_request_gap_s=(0.0, 0.0))
    client.abandon_current_word()
    assert page.clicks == clicks_needed


def test_client_abandon_without_button_raises(monkeypatch):
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    page = FakeResetPage(has_button=False)
    client = TuzmoClient(page, monitor=page.monitor, min_request_gap_s=(0.0, 0.0))
    with pytest.raises(GameStateError):
        client.abandon_current_word()


# --- chargement : partie terminée renvoyée -> "Rejouer" une seule fois ---

class _ReplayLocator:
    def __init__(self, page, text):
        self.page, self.text = page, text

    def count(self):
        return 1 if self.text in self.page.buttons else 0

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.clicks.append(self.text)
        if self.text == "Rejouer":
            self.page.emit_create(self.page.after_replay_status)


class FakeLoadPage:
    """Page factice : chaque chargement/clic "Rejouer" publie un POST /api/game dans
    le moniteur branché par `_open_game_page` (via `on`)."""

    def __init__(self, load_status, after_replay_status="playing"):
        self.load_status, self.after_replay_status = load_status, after_replay_status
        self.buttons = {"Rejouer"}
        self.clicks = []
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def emit_create(self, status):
        class Req:
            url, method, failure = "https://www.tusmo.xyz/api/game", "POST", None
            post_data = '{"lang":"fr","mode":"infinite"}'

        class Resp:
            def __init__(self, req):
                self.request, self.status, self.headers = req, 200, {}

            def json(self_inner):
                return {"session": {"id": f"s{len(self.clicks)}", "status": status, "guesses": []}}

        req = Req()
        self.handlers["request"](req)
        self.handlers["response"](Resp(req))

    def goto(self, url, **kw):
        self.emit_create(self.load_status)

    def locator(self, selector, has_text=None):
        return _ReplayLocator(self, has_text)

    def wait_for_selector(self, *a, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        pass


class _LoadCtx:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page


def test_finished_session_on_load_triggers_single_replay(runner, monkeypatch):
    monkeypatch.setattr(bot_runner, "INTER_GAME_DELAY_S", (0.0, 0.0))
    page = FakeLoadPage(load_status="lost")
    runner._open_game_page(_LoadCtx(page))
    assert page.clicks == ["Rejouer"]
    session, n_create = runner._current_session()
    assert session["status"] == "playing" and n_create == 2


def test_playing_session_on_load_is_left_untouched(runner, monkeypatch):
    page = FakeLoadPage(load_status="playing")
    runner._open_game_page(_LoadCtx(page))
    assert page.clicks == []
