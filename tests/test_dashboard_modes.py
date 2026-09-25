"""Phase 4 du 25/09/2026 : le dashboard pilote et affiche le bot dans chaque mode
(sélecteur de mode, état courant, stats filtrées par mode). Aucune partie réelle :
le démarrage du thread de jeu est remplacé par un enregistreur."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from fastapi.testclient import TestClient  # noqa: E402

import backend  # noqa: E402
from backend import app, runner  # noqa: E402
from bot.modes import GameMode  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_real_game(monkeypatch, tmp_path):
    """Le thread de jeu n'est jamais lancé ; le runner singleton est remis à zéro."""
    started = []

    def fake_thread_start(self):
        started.append(True)

    monkeypatch.setattr(runner, "_run", lambda iterations: None)
    monkeypatch.setattr(backend, "daily_game_on", lambda: None)
    import bot_runner
    monkeypatch.setattr(bot_runner, "daily_game_on", lambda: None)
    monkeypatch.setattr(bot_runner, "DEFAULT_LOG", tmp_path / "log.jsonl")
    runner.mode, runner.strategy, runner.status, runner.current_game = GameMode.INFINITE, "entropy_pure", "idle", None
    yield started
    if runner._thread is not None:
        runner._thread.join(timeout=2)
    runner.mode, runner.strategy, runner.status = GameMode.INFINITE, "entropy_pure", "idle"


def test_status_exposes_mode_strategy_and_current_game():
    body = client.get("/api/status").json()
    assert body["mode"] == "infinite" and body["strategy"] == "entropy_pure"
    assert body["supported_modes"] == ["infinite", "daily"] and "ranked" not in body["supported_modes"]
    assert body["strategies"] == ["composite", "entropy_pure"] and body["current_game"] is None
    assert body["daily_played_today"] is False


def test_start_in_daily_mode_caps_to_one_game():
    body = client.post("/api/start", json={"mode": "daily", "iterations": 10, "strategy": "composite"}).json()
    assert body["started"] is True and body["mode"] == "daily" and body["strategy"] == "composite"
    assert body["total_iterations"] == 1


def test_start_ranked_is_refused_with_explanation():
    body = client.post("/api/start", json={"mode": "ranked", "iterations": 3}).json()
    assert body["started"] is False and "vrais joueurs" in body["error"]
    assert runner.mode is GameMode.INFINITE and not runner.is_running()


def test_start_daily_already_played_today_is_refused(monkeypatch):
    import bot_runner
    monkeypatch.setattr(bot_runner, "daily_game_on", lambda: {"solution": "RIVIERE"})
    body = client.post("/api/start", json={"mode": "daily"}).json()
    assert body["started"] is False and body["status"] == "daily_limit" and "demain" in body["error"]


def test_start_rejects_unknown_strategy():
    response = client.post("/api/start", json={"strategy": "au_hasard"})
    assert response.status_code == 400 and response.json()["started"] is False


def test_current_game_follows_events():
    runner._emit("game_started", letter="R", length=7, mode="daily")
    runner._emit("feedback_received", attempt=2, guess="RATERAS", pattern="2000000")
    game = client.get("/api/status").json()["current_game"]
    assert game == {"mode": "daily", "letter": "R", "length": 7, "state": "en cours", "attempts": 2}
    runner._emit("solved", solution="RIVIERE", attempts=3)
    game = client.get("/api/status").json()["current_game"]
    assert game["state"] == "trouvée" and game["solution"] == "RIVIERE"


def test_stats_are_filtered_by_mode(monkeypatch):
    seen = []
    monkeypatch.setattr(backend, "aggregate_stats", lambda mode: seen.append(mode) or {"mode": mode})
    monkeypatch.setattr(backend, "aggregate_solutions_report", lambda mode: seen.append(mode) or {"mode": mode})
    assert client.get("/bot/stats").json() == {"mode": "infinite"}  # défaut : jamais mélangés
    assert client.get("/bot/stats?mode=daily").json() == {"mode": "daily"}
    assert client.get("/bot/stats/solutions?mode=all").json() == {"mode": None}
    assert client.get("/bot/stats?mode=ranked").status_code == 400
    assert seen == ["infinite", "daily", None]
