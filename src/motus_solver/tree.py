from __future__ import annotations

import math

import numpy as np

from .feedback import entropy_from_codes, pattern_codes, words_to_matrix
from .scoring import composite_score, distinct_count, vowel_count, word_freq_score


def score_guess(
    guess: str,
    candidates: list[str],
    candidates_arr: np.ndarray,
    global_freq: dict[str, float],
    positional_freq: np.ndarray,
) -> tuple[float, float, int]:
    codes = pattern_codes(guess, candidates_arr)
    entropy = entropy_from_codes(codes)
    max_entropy = math.log2(len(candidates)) if len(candidates) > 1 else 1.0
    freq_score = word_freq_score(guess, global_freq, positional_freq)
    score = composite_score(
        entropy, max_entropy, vowel_count(guess), distinct_count(guess), freq_score, len(guess)
    )
    return score, entropy, vowel_count(guess)


def best_guess_composite(
    candidates: list[str],
    guess_pool: list[str],
    global_freq: dict[str, float],
    positional_freq: np.ndarray,
) -> tuple[str, float, int]:
    if not candidates:
        raise ValueError("aucun candidat restant")
    candidates_arr = words_to_matrix(candidates)
    best: tuple[float, str, float, int] | None = None
    for guess in guess_pool:
        score, entropy, vowels = score_guess(guess, candidates, candidates_arr, global_freq, positional_freq)
        if best is None or score > best[0]:
            best = (score, guess, entropy, vowels)
    _, guess, entropy, vowels = best
    return guess, entropy, vowels


def build_tree(
    candidates: list[str],
    guess_pool: list[str],
    global_freq: dict[str, float],
    positional_freq: np.ndarray,
    max_depth: int = 4,
    depth: int = 1,
) -> dict:
    if len(candidates) == 1:
        return {"solved": candidates[0]}

    guess, entropy, vowels = best_guess_composite(candidates, guess_pool, global_freq, positional_freq)
    node = {"guess": guess, "entropy": entropy, "vowels": vowels, "branches": {}}
    if depth >= max_depth:
        return node

    candidates_arr = words_to_matrix(candidates)
    codes = pattern_codes(guess, candidates_arr)
    remaining_pool = [w for w in guess_pool if w != guess] or candidates

    for code in np.unique(codes):
        group = [w for w, c in zip(candidates, codes) if c == code]
        node["branches"][int(code)] = build_tree(
            group, remaining_pool, global_freq, positional_freq, max_depth, depth + 1
        )
    return node
