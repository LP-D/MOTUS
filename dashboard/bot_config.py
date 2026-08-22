"""Configuration du bot réglable en direct depuis le dashboard, sans redémarrage.

Variable partagée thread-safe (pas de fichier relu à chaque coup) : le thread du
bot (`bot_runner.py`) lit `config.get_typing_delay_s()` juste avant chaque coup,
le backend FastAPI écrit via `config.set_typing_delay_ms()` sur requête HTTP. Le
changement s'applique donc au prochain coup joué, jamais en cours de frappe.

Valeurs par défaut = comportement historique de `TuzmoClient.submit_guess` (80-220ms
par lettre, 150-350ms avant validation) : rien ne change si l'utilisateur n'y touche
pas.
"""
from __future__ import annotations

import threading

DEFAULT_LETTER_DELAY_MIN_MS = 80.0
DEFAULT_LETTER_DELAY_MAX_MS = 220.0
DEFAULT_ENTER_DELAY_MIN_MS = 150.0
DEFAULT_ENTER_DELAY_MAX_MS = 350.0


class BotConfig:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._letter_delay_ms = (DEFAULT_LETTER_DELAY_MIN_MS, DEFAULT_LETTER_DELAY_MAX_MS)
        self._enter_delay_ms = (DEFAULT_ENTER_DELAY_MIN_MS, DEFAULT_ENTER_DELAY_MAX_MS)

    def get_typing_delay_s(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Retourne (letter_delay_s, enter_delay_s), prêt à passer à
        `TuzmoClient.submit_guess(letter_delay=..., enter_delay=...)`."""
        with self._lock:
            letter_min, letter_max = self._letter_delay_ms
            enter_min, enter_max = self._enter_delay_ms
        return (letter_min / 1000, letter_max / 1000), (enter_min / 1000, enter_max / 1000)

    def set_letter_delay_ms(self, delay_min_ms: float, delay_max_ms: float) -> None:
        _validate_interval(delay_min_ms, delay_max_ms)
        with self._lock:
            self._letter_delay_ms = (delay_min_ms, delay_max_ms)

    def as_dict(self) -> dict:
        with self._lock:
            letter_min, letter_max = self._letter_delay_ms
            enter_min, enter_max = self._enter_delay_ms
        return {
            "letter_delay_min_ms": letter_min,
            "letter_delay_max_ms": letter_max,
            "enter_delay_min_ms": enter_min,
            "enter_delay_max_ms": enter_max,
        }


def _validate_interval(delay_min_ms: float, delay_max_ms: float) -> None:
    if delay_min_ms < 0:
        raise ValueError("delay_min_ms doit être >= 0")
    if delay_max_ms < delay_min_ms:
        raise ValueError("delay_max_ms doit être >= delay_min_ms")


config = BotConfig()
