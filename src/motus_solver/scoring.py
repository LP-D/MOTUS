from __future__ import annotations

from collections import Counter

import numpy as np

from .corpus import Corpus
from .feedback import LETTER_TO_INDEX

VOWELS = set("AEIOUY")


def letter_frequencies(corpus: Corpus) -> dict[str, float]:
    counts: Counter[str] = Counter()
    total = 0
    for word in corpus:
        for letter in set(word):
            counts[letter] += 1
        total += 1
    if total == 0:
        return {letter: 0.0 for letter in LETTER_TO_INDEX}
    return {letter: counts.get(letter, 0) / total for letter in LETTER_TO_INDEX}


def positional_frequencies(corpus: Corpus, length: int) -> np.ndarray:
    words = [w for w in corpus if len(w) == length]
    freqs = np.zeros((length, 26), dtype=np.float64)
    if not words:
        return freqs
    for word in words:
        for position, letter in enumerate(word):
            freqs[position, LETTER_TO_INDEX[letter]] += 1
    return freqs / len(words)


def word_freq_score(word: str, global_freq: dict[str, float], positional: np.ndarray) -> float:
    global_component = sum(global_freq.get(letter, 0.0) for letter in set(word)) / len(word)
    positional_component = sum(
        positional[i, LETTER_TO_INDEX[letter]] for i, letter in enumerate(word)
    ) / len(word)
    return 0.5 * global_component + 0.5 * positional_component


def vowel_count(word: str) -> int:
    return sum(1 for c in word if c in VOWELS)


def distinct_count(word: str) -> int:
    return len(set(word))


def composite_score(
    entropy: float,
    max_entropy: float,
    vowels: int,
    distinct: int,
    freq_score: float,
    length: int,
) -> float:
    h_norm = entropy / max_entropy if max_entropy > 0 else 0.0
    vowel_norm = vowels / length
    distinct_norm = distinct / length
    return 0.40 * h_norm + 0.25 * vowel_norm + 0.20 * distinct_norm + 0.15 * freq_score
