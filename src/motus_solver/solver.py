from __future__ import annotations

from dataclasses import dataclass

from .corpus import Corpus
from .feedback import pattern_codes, pattern_to_code, words_to_matrix
from .scoring import letter_frequencies, positional_frequencies
from .tree import score_guess


@dataclass
class Move:
    word: str
    pattern: str


class Solver:
    def __init__(self, letter: str, length: int, corpus: Corpus):
        self.letter = letter.upper()
        self.length = length
        self.corpus = corpus
        self.candidates: list[str] = corpus.subset(self.letter, length)
        if not self.candidates:
            raise ValueError(f"aucun candidat pour letter={letter!r} length={length}")
        self.history: list[Move] = []
        self._global_freq = letter_frequencies(corpus)
        self._positional_freq = positional_frequencies(corpus, length)

    def suggest(self, top_n: int = 5) -> list[tuple[str, float, int]]:
        candidates_arr = words_to_matrix(self.candidates)
        scored = []
        for guess in self.candidates:
            score, entropy, vowels = score_guess(
                guess, self.candidates, candidates_arr, self._global_freq, self._positional_freq
            )
            scored.append((score, guess, entropy, vowels))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [(guess, entropy, vowels) for _, guess, entropy, vowels in scored[:top_n]]

    def play(self, word: str) -> None:
        self.history.append(Move(word=word.upper(), pattern=""))

    def update(self, pattern: str) -> None:
        if not self.history or self.history[-1].pattern:
            raise ValueError("appelle play(word) avant update(pattern)")
        guess = self.history[-1].word
        self.history[-1] = Move(word=guess, pattern=pattern)

        target_code = pattern_to_code(pattern)
        candidates_arr = words_to_matrix(self.candidates)
        codes = pattern_codes(guess, candidates_arr)
        self.candidates = [w for w, c in zip(self.candidates, codes) if c == target_code]

    def is_solved(self) -> bool:
        return len(self.candidates) == 1

    @property
    def solution(self) -> str | None:
        return self.candidates[0] if self.is_solved() else None
