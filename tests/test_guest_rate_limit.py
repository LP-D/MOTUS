"""Non-régression du 429 "guest creation rate limited" (23/09/2026).

Preuve : après 15 parties dans cette session, chaque nouvelle page /infinite recevait
`429 {"message":"guest creation rate limited"}` sur GET /api/me et POST /api/game
(docs/diagnostics/2026-09-23_cascade_rejets.md). Cause : un contexte navigateur
neuf, donc sans cookie, était ouvert à chaque partie, et Tuzmo créait un nouvel
invité anonyme à chaque fois. Le correctif réutilise un seul contexte, donc un seul
invité, pour toute la boucle, et remonte un 429 de chargement comme un throttling
explicite, pas comme un timeout Playwright."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import bot_runner  # noqa: E402

from bot.network_monitor import NetworkMonitor  # noqa: E402
from bot.tuzmo_client import ThrottlingDetectedError  # noqa: E402


class FakePage:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.pages: list[FakePage] = []
        self.closed = False
        self.cookie_jar = [{"name": "tusmo_token", "value": "existing"}]  # invité déjà créé

    def cookies(self, url=None):
        return list(self.cookie_jar)

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.contexts: list[FakeContext] = []

    def new_context(self, **kwargs):
        ctx = FakeContext()
        self.contexts.append(ctx)
        return ctx


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_runner, "DEFAULT_LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(bot_runner, "DEFAULT_AUTH_STATE", tmp_path / "absent.json")
    monkeypatch.setattr(bot_runner, "INTER_GAME_DELAY_S", (0.0, 0.0))
    r = bot_runner.BotRunner()
    r.status = "running"
    return r


def test_loop_reuses_a_single_context_so_a_single_guest(runner, monkeypatch):
    browser = FakeBrowser()
    monkeypatch.setattr(runner, "_open_game_page", lambda context: context.new_page())
    monkeypatch.setattr(
        runner, "_play_one_game",
        lambda page, corpus, cache, blocklist: ({"outcome": "solved", "solved": True}, blocklist),
    )

    runner._run_games(browser, 5, corpus=None, root_cache={}, blocklist=set())

    assert len(browser.contexts) == 1  # un seul invité pour 5 parties
    context = browser.contexts[0]
    assert len(context.pages) == 5
    assert all(p.closed for p in context.pages)
    assert context.closed
    assert runner.status == "idle"


def test_rate_limit_on_page_load_stops_loop_as_throttled(runner, monkeypatch):
    browser = FakeBrowser()
    opened = []

    def open_page(context):
        opened.append(1)
        if len(opened) == 2:
            raise ThrottlingDetectedError("HTTP 429 sur GET https://www.tusmo.xyz/api/me")
        return context.new_page()

    monkeypatch.setattr(runner, "_open_game_page", open_page)
    monkeypatch.setattr(
        runner, "_play_one_game",
        lambda page, corpus, cache, blocklist: ({"outcome": "solved", "solved": True}, blocklist),
    )

    runner._run_games(browser, 10, corpus=None, root_cache={}, blocklist=set())

    assert len(opened) == 2  # aucune partie tentée après le 429
    assert runner.status == "throttled"
    events = []
    while not runner.events.empty():
        events.append(runner.events.get_nowait())
    assert any(e["type"] == "throttled" for e in events)
    assert browser.contexts[0].closed


class _Req:
    def __init__(self, url, method="GET"):
        self.url, self.method, self.post_data, self.failure = url, method, None, None


class _Resp:
    def __init__(self, request, status):
        self.request, self.status, self.headers = request, status, {}

    def json(self):
        return {"statusCode": self.status, "message": "guest creation rate limited"}


class _EventPage:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


def test_monitor_sees_429_on_api_me_not_only_api_game():
    """Le premier 429 observé portait sur GET /api/me : le moniteur doit le voir."""
    page = _EventPage()
    monitor = NetworkMonitor(page)
    req = _Req("https://www.tusmo.xyz/api/me")
    page.handlers["request"](req)
    page.handlers["response"](_Resp(req, 429))

    signal = monitor.first_throttle_signal()
    assert signal is not None
    call, reason = signal
    assert "/api/me" in call.url
    assert "429" in reason


# --- invité créé avant le 1er chargement (course GET /api/me <-> POST /api/game) ---

class _ApiResp:
    def __init__(self, status=200, headers=None):
        self.status, self.headers = status, headers or {}


class _Request:
    def __init__(self, ctx, status, headers):
        self.ctx, self.status, self.headers = ctx, status, headers

    def get(self, url, timeout=None):
        self.ctx.api_calls.append(url)
        if self.status == 200:
            self.ctx.cookie_jar.append({"name": "tusmo_token", "value": "x"})
        return _ApiResp(self.status, self.headers)


class _GuestContext(FakeContext):
    def __init__(self, cookies=(), status=200, headers=None):
        super().__init__()
        self.cookie_jar = list(cookies)  # remplace l'invité par défaut du FakeContext
        self.api_calls = []
        self.request = _Request(self, status, headers)

    def cookies(self, url=None):
        return list(self.cookie_jar)


def test_guest_is_created_once_before_first_page_load(runner, monkeypatch):
    """Au chargement, le site envoie GET /api/me et POST /api/game en même temps :
    sur un invité neuf, les deux en créent un et la partie peut appartenir à
    l'autre (NOT_FOUND au 1er coup, run d'amélioration n° 4)."""
    monkeypatch.setattr(bot_runner.time, "sleep", lambda s: None)
    ctx = _GuestContext()
    runner._ensure_guest(ctx)
    assert ctx.api_calls == [bot_runner.API_ME_URL]
    runner._ensure_guest(ctx)  # cookie présent : aucune nouvelle requête
    assert ctx.api_calls == [bot_runner.API_ME_URL]


def test_existing_guest_cookie_means_no_extra_request(runner):
    ctx = _GuestContext(cookies=[{"name": "tusmo_token", "value": "saved"}])
    runner._ensure_guest(ctx)
    assert ctx.api_calls == []


@pytest.mark.parametrize("status, headers", [(429, {}), (200, {"Retry-After": "30"})], ids=["429", "retry_after"])
def test_guest_creation_throttling_stops_loop_before_any_game(runner, monkeypatch, status, headers):
    monkeypatch.setattr(bot_runner.time, "sleep", lambda s: None)
    ctx = _GuestContext(status=status, headers=headers)

    class _B:
        def new_context(self, **kw):
            return ctx

    opened = []
    monkeypatch.setattr(runner, "_open_game_page", lambda c: opened.append(1))
    runner._run_games(_B(), 5, corpus=None, root_cache={}, blocklist=set())
    assert opened == []
    assert runner.status == "throttled"
