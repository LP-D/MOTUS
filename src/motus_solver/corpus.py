from __future__ import annotations

import unicodedata
from collections import defaultdict
from pathlib import Path


LIGATURES = {"Œ": "OE", "Æ": "AE"}


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def normalize_word(word: str) -> str:
    text = word.strip().upper()
    for ligature, replacement in LIGATURES.items():
        text = text.replace(ligature, replacement)
    return strip_accents(text)


class Corpus:
    def __init__(self, words: list[str]):
        self.words = words
        self._by_first_length: dict[tuple[str, int], list[str]] = defaultdict(list)
        for word in words:
            self._by_first_length[(word[0], len(word))].append(word)

    @classmethod
    def from_file(cls, path: str | Path, min_length: int = 5, max_length: int = 9) -> "Corpus":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Fichier introuvable : {path.resolve()}")

        seen: set[str] = set()
        words: list[str] = []
        for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            word = normalize_word(stripped.split()[0])
            if not (word.isascii() and word.isalpha()):
                continue
            if not (min_length <= len(word) <= max_length):
                continue
            if word in seen:
                continue
            seen.add(word)
            words.append(word)

        if not words:
            raise ValueError(f"Aucun mot exploitable dans {path}")
        return cls(sorted(words))

    def subset(self, first_letter: str, length: int) -> list[str]:
        return list(self._by_first_length.get((first_letter.upper(), length), []))

    def __len__(self) -> int:
        return len(self.words)

    def __iter__(self):
        return iter(self.words)
