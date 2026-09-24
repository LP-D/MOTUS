"""Tâches 1 et 5 : surveillance H8 (partie introuvable) et critère d'arrêt de la
boucle d'amélioration (au moins 5 runs du cycle, 0 erreur, temps total des 10 mots
inférieur au run précédent)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from run_improvement_cycle import cumulative_trio, h8_watch, summarize  # noqa: E402

from bot.network_monitor import ApiCall  # noqa: E402


def call(kind, url, body=None, error=None, t=0.0):
    return ApiCall(kind=kind, method="POST" if kind != "other" else "GET", url=url, guess=None,
                   t_sent=t, t_received=t + 0.1, status=404 if error == "NOT_FOUND" else 200,
                   body=body if error is None else {"error": error})


def test_h8_watch_flags_not_found_and_session_mismatch():
    calls = [
        call("other", "https://www.tusmo.xyz/api/me", t=1.000),
        call("create_session", "https://www.tusmo.xyz/api/game", body={"session": {"id": "A", "status": "playing"}}, t=1.002),
        call("guess", "https://www.tusmo.xyz/api/game/B/guess", error="NOT_FOUND", t=3.0),
    ]
    watch = h8_watch(1, [], calls, {"created": True}, cookie_before_load=True)
    assert watch["not_found"] is True
    assert watch["guess_session_matches_create"] is False  # coup envoyé sur une AUTRE partie
    assert watch["create_minus_me_sent_s"] == 0.002
    assert watch["guest_precreated"] is True and watch["first_game_of_context"] is True


def test_h8_watch_clean_game():
    calls = [
        call("create_session", "https://www.tusmo.xyz/api/game", body={"session": {"id": "A"}}),
        call("guess", "https://www.tusmo.xyz/api/game/A/guess", body={"result": ["correct"]}),
    ]
    watch = h8_watch(2, [], calls, {"created": True}, cookie_before_load=True)
    assert watch["not_found"] is False and watch["guess_session_matches_create"] is True


def game(total, errors=(), solved=True):
    return {"game": 1, "letter": "A", "length": 5, "solution": "ABCDE" if solved else None,
            "outcome": "solved" if solved else "candidates_exhausted", "attempts": 3, "rejections": 0,
            "root_rejected": False, "time_s": {k: 0.0 for k in ("load", "solver", "typing", "enter_and_rate_wait",
                                                               "server_roundtrip", "reveal_wait")} | {"total": total},
            "max_solver_s": 0, "max_latency_s": 0.1, "trio": None, "errors": list(errors), "h8": {"not_found": False}}


def test_stop_criterion_needs_five_cycle_runs_zero_errors_and_lower_total_time():
    previous = {"total_time_s": 100.0, "mean_time_per_word_s": 10.0, "games": 10}
    fast_clean = [game(9.0) for _ in range(10)]
    assert summarize(10, fast_clean, None, previous, cycle_start=6)["stop_criterion_met"] is True
    assert summarize(9, fast_clean, None, previous, cycle_start=6)["stop_criterion_met"] is False  # 4e run du cycle
    with_error = fast_clean[:-1] + [game(9.0, errors=[{"type": "error"}])]
    assert summarize(10, with_error, None, previous, cycle_start=6)["stop_criterion_met"] is False
    slower = [game(10.5) for _ in range(10)]
    assert summarize(10, slower, None, previous, cycle_start=6)["stop_criterion_met"] is False


def test_cumulative_trio_accumulates_across_runs(tmp_path):
    def line(a, b):
        return json.dumps({"trio": {"sim_trio_then_bot": {"attempts": a}, "sim_bot_only": {"attempts": b}}})

    (tmp_path / "run_01_games.jsonl").write_text(line(4, 3) + "\n" + line(3, 3) + "\n", encoding="utf-8")
    (tmp_path / "run_02_games.jsonl").write_text(line(2, 3) + "\n", encoding="utf-8")
    (tmp_path / "run_03_games.jsonl").write_text(line(5, 3) + "\n", encoding="utf-8")
    cumul = cumulative_trio(tmp_path, upto_run=2)
    assert cumul == {"words_compared": 3, "trio_better": 1, "trio_worse": 1, "equal": 1,
                     "mean_attempts_trio_then_bot": 3, "mean_attempts_bot_only": 3}
