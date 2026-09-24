"""Tâche 2 (validation ciblée des premiers coups) : comportement du script face à un
faux serveur Tuzmo — classement des mots, clôture des parties, arrêts de sécurité."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_root_candidates as vrc  # noqa: E402


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeServer:
    """Parties successives imposées par `draws` ; `invalid` = mots refusés."""

    def __init__(self, draws, invalid, throttle_at=None, create_status="playing"):
        self.draws, self.invalid = list(draws), set(invalid)
        self.throttle_at, self.create_status = throttle_at, create_status
        self.calls, self.session = [], None

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url.replace(vrc.BASE_URL, ""), json))
        if self.throttle_at and len(self.calls) >= self.throttle_at:
            return FakeResponse(429, {"error": "rate"})
        path = url.replace(vrc.BASE_URL, "")
        if path == "/api/me":
            return FakeResponse(200, {"id": "guest"})
        if path == "/api/game":
            letter, length = self.draws.pop(0) if self.draws else ("Z", 5)
            self.session = {"id": f"s{len(self.calls)}", "status": self.create_status, "firstLetter": letter,
                            "wordLength": length, "guesses": []}
            return FakeResponse(200, {"session": dict(self.session)})
        if path.endswith("/giveup"):
            self.session["status"] = "lost"
            return FakeResponse(200, {"session": {**self.session, "answer": "REVEL"}})
        word = json["guess"]
        if word in self.invalid:
            return FakeResponse(200, {"session": dict(self.session), "error": "INVALID_WORD"})
        self.session["guesses"].append({"word": word})
        return FakeResponse(200, {"session": dict(self.session), "result": ["absent"] * len(word)})


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    cache = {"R_5": {"word": "RIVER", "entropy": 1, "alternatives": [{"word": "RATER", "entropy": 1},
                                                                      {"word": "RADIO", "entropy": 1}]}}
    (data / "root_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    (data / "root_cache_entropy_pure.json").write_text(json.dumps(
        {"R_5": {"word": "ROBOT", "entropy": 1, "alternatives": [{"word": "RIVER", "entropy": 1}]}}), encoding="utf-8")
    (data / "known_valid_words.json").write_text(json.dumps(["RADIO"]), encoding="utf-8")
    (data / "known_invalid_words.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(vrc, "DATA", data)
    monkeypatch.setattr(vrc.time, "sleep", lambda s: None)
    args = type("A", (), {"max_requests": 50, "log": str(data / "log.jsonl")})()
    return data, args


def install(monkeypatch, server):
    monkeypatch.setattr(vrc.requests, "Session", lambda: type("S", (), {
        "headers": {}, "request": staticmethod(server.request)})())


def test_candidates_are_classified_and_session_closed(env, monkeypatch):
    data, args = env
    server = FakeServer(draws=[("R", 5)], invalid={"RATER"})
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert set(summary["valid_words"]) == {"RIVER", "ROBOT"}  # RADIO déjà connu : non retesté
    assert summary["invalid_words"] == ["RATER"]
    assert "RATER" in json.loads((data / "known_invalid_words.json").read_text(encoding="utf-8"))
    assert {"RIVER", "ROBOT"} <= set(json.loads((data / "known_valid_words.json").read_text(encoding="utf-8")))
    assert any(url.endswith("/giveup") for _, url, _ in server.calls)  # partie close
    assert summary["groups_fully_validated"] == 1 and summary["rejection_rate_pct"] == pytest.approx(33.3)


def test_throttling_stops_immediately(env, monkeypatch):
    _, args = env
    server = FakeServer(draws=[("R", 5)], invalid=set(), throttle_at=3)
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert summary["stop_reason"].startswith("ARRÊT D'URGENCE")
    assert len(server.calls) == 3  # aucune requête après le 429


def test_unplayable_sessions_never_loop(env, monkeypatch):
    _, args = env
    server = FakeServer(draws=[("R", 5)] * 10, invalid=set(), create_status="lost")
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert "non jouables" in summary["stop_reason"]
    assert summary["requests"] <= 3
