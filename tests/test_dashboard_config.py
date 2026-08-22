"""Tâche 1 : le délai de saisie doit être réglable en direct via l'API, sans
redémarrage, et relu correctement. Teste l'endpoint FastAPI réel (TestClient),
pas seulement l'objet de config sous-jacent."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from fastapi.testclient import TestClient

from backend import app
from bot_config import DEFAULT_LETTER_DELAY_MAX_MS, DEFAULT_LETTER_DELAY_MIN_MS, config

client = TestClient(app)


def teardown_function() -> None:
    # évite qu'un test laisse la config globale modifiée pour les suivants
    config.set_letter_delay_ms(DEFAULT_LETTER_DELAY_MIN_MS, DEFAULT_LETTER_DELAY_MAX_MS)


def test_get_typing_delay_returns_default_values():
    response = client.get("/bot/config/typing_delay")
    assert response.status_code == 200
    body = response.json()
    assert body["letter_delay_min_ms"] == DEFAULT_LETTER_DELAY_MIN_MS
    assert body["letter_delay_max_ms"] == DEFAULT_LETTER_DELAY_MAX_MS


def test_post_typing_delay_applies_and_is_reread_correctly():
    response = client.post(
        "/bot/config/typing_delay", json={"letter_delay_min_ms": 300, "letter_delay_max_ms": 600}
    )
    assert response.status_code == 200
    assert response.json() == {
        "letter_delay_min_ms": 300,
        "letter_delay_max_ms": 600,
        "enter_delay_min_ms": config.as_dict()["enter_delay_min_ms"],
        "enter_delay_max_ms": config.as_dict()["enter_delay_max_ms"],
    }

    # relu correctement par un GET ultérieur (persistance in-memory, pas juste la
    # réponse de la requête POST elle-même)
    reread = client.get("/bot/config/typing_delay")
    assert reread.json()["letter_delay_min_ms"] == 300
    assert reread.json()["letter_delay_max_ms"] == 600

    # et par le bot lui-même via get_typing_delay_s() (ce que bot_runner.py lit
    # réellement avant chaque coup)
    letter_delay_s, _enter_delay_s = config.get_typing_delay_s()
    assert letter_delay_s == (0.3, 0.6)


def test_post_typing_delay_rejects_invalid_interval():
    response = client.post(
        "/bot/config/typing_delay", json={"letter_delay_min_ms": 500, "letter_delay_max_ms": 100}
    )
    assert response.status_code == 400
    # la config globale ne doit pas avoir été modifiée par une requête invalide
    assert config.as_dict()["letter_delay_min_ms"] == DEFAULT_LETTER_DELAY_MIN_MS
