from __future__ import annotations

import json
from pathlib import Path


def load_blocklist(path: str | Path) -> set[str]:
    path = Path(path)
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def save_blocklist(words: set[str], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(words), ensure_ascii=False, indent=2), encoding="utf-8")


def add_to_blocklist(word: str, path: str | Path) -> set[str]:
    """Ajoute `word` à la liste noire persistante et la sauvegarde immédiatement
    (un mot rejeté par le jeu réel une fois ne doit plus jamais être reproposé,
    même après redémarrage du bot/dashboard). Retourne la liste noire à jour."""
    words = load_blocklist(path)
    normalized = word.upper()
    if normalized not in words:
        words.add(normalized)
        save_blocklist(words, path)
    return words
