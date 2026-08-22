"""Tâche 1 (diagnostic partie auto-lancée) : régression garantissant qu'aucune
partie ne peut démarrer autrement que via un appel explicite à `BotRunner.start()`,
et que chaque tentative (acceptée ou refusée) est journalisée avec l'état du
runner au moment de la décision — cf. audit dans docs/tuzmo_site_notes.md."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from bot_runner import BotRunner  # noqa: E402


def test_fresh_runner_has_no_implicit_start():
    runner = BotRunner()
    assert runner.status == "idle"
    assert runner.is_running() is False
    assert runner.current_iteration == 0
    assert runner.events.empty()


def test_start_accepted_logs_start_requested_with_prior_state():
    runner = BotRunner()
    release = threading.Event()
    runner._run = lambda iterations: release.wait(timeout=5)

    accepted = runner.start(iterations=2)
    assert accepted is True

    event = runner.events.get(timeout=1)
    assert event["type"] == "start_requested"
    assert event["accepted"] is True
    assert event["status_before"] == "idle"
    assert event["current_iteration"] == 0
    assert event["requested_iterations"] == 2

    release.set()
    runner._thread.join(timeout=2)


def test_second_start_while_running_is_rejected_and_logged_without_disrupting_loop():
    runner = BotRunner()
    release = threading.Event()
    runner._run = lambda iterations: release.wait(timeout=5)

    runner.start(iterations=1)
    runner.events.get(timeout=1)  # consomme le 1er start_requested (accepté)

    rejected = runner.start(iterations=5)
    assert rejected is False

    event = runner.events.get(timeout=1)
    assert event["type"] == "start_requested"
    assert event["accepted"] is False
    assert event["status_before"] == "running"
    # la tentative refusée ne doit pas écraser l'état de la boucle déjà en cours
    assert runner.total_iterations == 1

    release.set()
    runner._thread.join(timeout=2)
