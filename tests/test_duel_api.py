"""Page /duel du dashboard : duel simulé contre un bot, entièrement local."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from fastapi.testclient import TestClient  # noqa: E402

import duel_api  # noqa: E402
from backend import app  # noqa: E402
from motus_solver.agents import SolverContext  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402

client = TestClient(app)
WORDS = [f"PA{c}ES" for c in "GLMNTVY"]


@pytest.fixture(autouse=True)
def small_world(monkeypatch, tmp_path):
    ctx = SolverContext(corpus=Corpus(WORDS), caches={"entropy_pure": {}, "composite": {}}, blocklist=set(),
                        known_valid=set(WORDS))
    monkeypatch.setattr(duel_api, "_ctx", ctx)
    monkeypatch.setattr(duel_api, "answer_pool", lambda: ["PAVES"])
    monkeypatch.setattr(duel_api, "HISTORY", tmp_path / "duel_history.jsonl")
    monkeypatch.setattr(duel_api, "PROFILES", tmp_path / "duel_profiles.json")
    monkeypatch.setattr(duel_api, "_profiles", None)
    monkeypatch.setattr(duel_api, "COUNTDOWN_S", 0.0)


def test_play_a_duel_against_a_slow_bot():
    state = client.post("/api/duel/new", json={"bot": "entropy_pure", "speed": "lent"}).json()
    assert state["letter"] == "P" and state["length"] == 5
    bad = client.post(f"/api/duel/{state['id']}/guess", json={"word": "PXXXX"})
    assert bad.status_code == 422 and bad.json()["error"] == "Mot inconnu"
    assert bad.json()["me"]["rows"] == []  # un mot refusé ne coûte pas d'essai

    won = client.post(f"/api/duel/{state['id']}/guess", json={"word": "PAVES"}).json()
    assert won["result"]["winner"] == "toi" and won["result"]["word"] == "PAVES"
    history = client.get("/api/duel-history").json()
    assert history["duels"] == 1 and history["by_bot"]["entropy_pure@lent"]["toi"] == 1


def test_bot_letters_hidden_until_the_end():
    state = client.post("/api/duel/new", json={"bot": "aleatoire", "speed": "instantane"}).json()
    live = duel_api._sessions[state["id"]]
    live.duel.submit(duel_api.BOT, "PAGES", 0.1)  # coup du bot posé à la main
    live.pending = None
    rows = client.get(f"/api/duel/{state['id']}").json()["bot_side"]["rows"]
    assert rows[0]["word"] is None and rows[0]["pattern"] == "22022"


def test_unknown_bot_is_refused():
    assert client.post("/api/duel/new", json={"bot": "nope"}).status_code == 400


def test_learning_bot_remembers_your_openers():
    import json

    state = client.post("/api/duel/new", json={"bot": "entropy_pure_infos", "speed": "lent"}).json()
    client.post(f"/api/duel/{state['id']}/guess", json={"word": "PAVES"})
    saved = json.loads(duel_api.PROFILES.read_text(encoding="utf-8"))
    assert saved["toi"]["openers"]["P_5"] == ["PAVES"]
