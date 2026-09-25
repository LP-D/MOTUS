"""Phase 1 du 25/09/2026 : abstraction des modes de jeu (bot/modes.py).

Le quotidien joue une partie par lancement et signale proprement un mot du jour
déjà joué ; l'infini garde son comportement ; le classé est refusé explicitement.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "tests"))

import bot_runner  # noqa: E402
import stats_store  # noqa: E402
from test_persistent_session import FakeMonitor, events, make_client_cls  # noqa: E402,F401
from test_persistent_session import runner  # noqa: E402,F401  (fixture)

from bot.modes import (  # noqa: E402
    DAILY_LIMIT,
    NOT_PLAYABLE,
    PLAYABLE,
    RESTARTABLE,
    GameMode,
    ModeNotSupportedError,
    handler_for,
)
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.draws import load_draw_counts, record_draw  # noqa: E402


def test_handlers_per_mode():
    infinite, daily = handler_for("infinite"), handler_for(GameMode.DAILY)
    assert infinite.url == "https://www.tusmo.xyz/infinite" and daily.url == "https://www.tusmo.xyz/daily"
    assert infinite.games_allowed(10) == 10 and daily.games_allowed(10) == 1
    assert infinite.restart_finished and not daily.restart_finished
    assert infinite.reset_fallback and not daily.reset_fallback


def test_loaded_session_classification():
    infinite, daily = handler_for("infinite"), handler_for("daily")
    for handler in (infinite, daily):
        assert handler.classify_loaded_session(None) == PLAYABLE
        assert handler.classify_loaded_session({"status": "playing"}) == PLAYABLE
    assert infinite.classify_loaded_session({"status": "lost"}) == RESTARTABLE
    assert daily.classify_loaded_session({"status": "won"}) == DAILY_LIMIT
    assert daily.classify_loaded_session({"status": "lost"}) == DAILY_LIMIT
    assert NOT_PLAYABLE not in {infinite.classify_loaded_session({"status": "lost"})}


def test_ranked_is_refused_explicitly():
    with pytest.raises(ModeNotSupportedError, match="vrais joueurs"):
        handler_for("ranked")
    with pytest.raises(ValueError):
        handler_for("duel")


def test_runner_refuses_ranked_and_keeps_current_mode(runner):
    assert runner.mode is GameMode.INFINITE  # comportement historique par défaut
    assert runner.start(iterations=3, mode="ranked") is False
    assert runner.mode is GameMode.INFINITE and not runner.is_running()
    refused = [e for e in events(runner) if e["type"] == "mode_refused"]
    assert refused and refused[0]["mode"] == "ranked"


def test_daily_already_played_is_a_clean_stop_not_an_error(runner, monkeypatch):
    runner.mode = GameMode.DAILY
    monkeypatch.setattr(bot_runner, "TuzmoClient", make_client_cls("RIVER"))
    runner.page_monitor = FakeMonitor({"session": {"id": "d1", "status": "won", "firstLetter": "R",
                                                   "wordLength": 5, "guesses": []}})
    result, _ = runner._play_one_game(page=None, corpus=Corpus(["RIVER", "RATER"]), root_cache=None,
                                      blocklist=set())
    assert result["outcome"] == "daily_limit_reached"
    assert runner.recorded == []  # aucune partie comptée
    evs = events(runner)
    assert any(e["type"] == "daily_limit_reached" and "demain" in e["message"] for e in evs)
    assert not any(e["type"] == "error" for e in evs)


def test_same_finished_session_on_infinite_is_still_refused(runner, monkeypatch):
    monkeypatch.setattr(bot_runner, "TuzmoClient", make_client_cls("RIVER"))
    runner.page_monitor = FakeMonitor({"session": {"id": "i1", "status": "won", "firstLetter": "R",
                                                   "wordLength": 5, "guesses": []}})
    result, _ = runner._play_one_game(page=None, corpus=Corpus(["RIVER", "RATER"]), root_cache=None,
                                      blocklist=set())
    assert result["outcome"] == "session_not_playable"


def test_daily_game_records_mode_in_stats_and_draws(runner, monkeypatch, tmp_path):
    runner.mode = GameMode.DAILY
    draws = tmp_path / "draws.jsonl"
    monkeypatch.setattr(bot_runner, "DEFAULT_DRAWS", draws)
    monkeypatch.setattr(bot_runner, "TuzmoClient", make_client_cls("RIVER"))
    result, _ = runner._play_one_game(page=None, corpus=Corpus(["RIVER", "RATER"]), root_cache=None,
                                      blocklist=set())
    assert result["solved"] and runner.recorded[0]["mode"] == "daily"
    all_events = events(runner)
    started = [e for e in all_events if e["type"] == "game_started"]
    assert started[0]["mode"] == "daily"
    assert json.loads(draws.read_text(encoding="utf-8").splitlines()[0])["mode"] == "daily"
    # le mot du jour ne compte pas dans les tirages /infinite (statut des groupes)
    assert load_draw_counts(draws) == {} and load_draw_counts(draws, mode="daily") == {"R_5": 1}
    # 1er mot du jour d'un groupe : jamais signalé comme groupe /infinite non observé
    assert result["group_draws_before"] == 0
    assert not any(e["type"] == "unobserved_group_drawn" for e in all_events)


def test_daily_unwinnable_game_has_no_reset_fallback(runner, monkeypatch):
    runner.mode = GameMode.DAILY
    client_cls = make_client_cls("RUBAN")  # hors corpus, giveup indisponible (client factice)
    monkeypatch.setattr(bot_runner, "TuzmoClient", client_cls)
    result, _ = runner._play_one_game(page=object(), corpus=Corpus(["RATER", "RIVER"]), root_cache=None,
                                      blocklist=set())
    assert result["outcome"] == "candidates_exhausted" and not result.get("abandon_failed")
    assert client_cls.instances[-1].gave_up is False  # jamais de ↻ (propre à /infinite)


def test_stats_are_split_by_mode(tmp_path):
    path = tmp_path / "stats.json"
    stats_store.record_game("R", 5, 3, True, "solved", ["RATER", "RADIO", "RIVER"], "RIVER", path=path)
    stats_store.record_game("C", 7, 4, True, "solved", ["CADEAUX"], "CADEAUX", path=path, mode="daily")
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) + [
        {"letter": "A", "length": 6, "attempts": 6, "solved": False, "outcome": "not_solved", "guesses": [],
         "solution": None, "timestamp": 1}]), encoding="utf-8")  # partie d'avant les modes : /infinite
    infinite = stats_store.aggregate(path)
    assert infinite["overall"]["total"] == 2 and infinite["games_by_mode"] == {"infinite": 2, "daily": 1}
    assert stats_store.aggregate(path, mode="daily")["overall"]["total"] == 1
    assert stats_store.aggregate(path, mode=None)["overall"]["total"] == 3
    assert stats_store.aggregate_solutions_report(path, mode="daily")["n_solutions_recorded"] == 1


def test_draw_lines_without_mode_count_as_infinite(tmp_path):
    path = tmp_path / "draws.jsonl"
    record_draw(path, "A", 5, "historique")
    record_draw(path, "A", 5, "bot_runner", mode="infinite")
    record_draw(path, "B", 6, "bot_runner", mode="daily")
    assert load_draw_counts(path) == {"A_5": 2}


def test_daily_launch_refused_locally_when_already_played_today(runner, monkeypatch):
    monkeypatch.setattr(bot_runner, "daily_game_on", lambda: {"solution": "RIVIERE", "mode": "daily"})
    assert runner.start(mode="daily") is False
    assert runner.status == "daily_limit" and not runner.is_running()
    evs = [e for e in events(runner) if e["type"] == "daily_limit_reached"]
    assert evs and evs[0]["source"] == "local"


def test_daily_game_on_reads_local_date(tmp_path):
    import datetime as dt

    path = tmp_path / "stats.json"
    stats_store.record_game("R", 7, 3, True, "solved", ["RIVIERE"], "RIVIERE", path=path, mode="daily")
    stats_store.record_game("A", 5, 2, True, "solved", ["ARBRE"], "ARBRE", path=path)  # /infinite : ignoré
    assert stats_store.daily_game_on(path=path)["solution"] == "RIVIERE"
    assert stats_store.daily_game_on(dt.date.today() - dt.timedelta(days=1), path=path) is None


def test_finished_daily_is_detected_before_reading_the_board(runner, monkeypatch):
    """Une partie du jour terminée peut s'afficher sans plateau : la session est
    examinée avant toute lecture du DOM."""
    class NoBoardClient:
        def __init__(self, page):
            pass

        def get_first_letter(self):
            raise AssertionError("plateau lu alors que la partie du jour est terminée")

        get_word_length = get_first_letter

    runner.mode = GameMode.DAILY
    monkeypatch.setattr(bot_runner, "TuzmoClient", NoBoardClient)
    runner.page_monitor = FakeMonitor({"session": {"id": "d1", "mode": "daily", "status": "lost", "guesses": []}})
    result, _ = runner._play_one_game(page=None, corpus=Corpus(["RIVER"]), root_cache=None, blocklist=set())
    assert result["outcome"] == "daily_limit_reached"
