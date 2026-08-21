from __future__ import annotations

import math

import numpy as np

from .feedback import (
    ALPHABET,
    LETTER_TO_INDEX,
    candidate_letter_counts,
    entropy_from_codes,
    pattern_codes,
    pattern_codes_batch,
    words_to_matrix,
)
from .scoring import VOWELS, composite_score, distinct_count, vowel_count, word_freq_score

_VOWEL_LOOKUP = np.array([letter in VOWELS for letter in ALPHABET], dtype=np.float64)

# Cible mémoire pour le tableau (batch, candidats, 26) alloué par lot dans
# `_score_guesses_batch` (int16 -> 2 octets/élément) : borne la taille de lot pour
# rester raisonnable même sur les plus gros groupes (lettre, longueur) du corpus.
_BATCH_MEMORY_TARGET_BYTES = 150_000_000


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


def _batch_size_for(n_candidates: int, length: int) -> int:
    """Nombre de guesses traités simultanément par lot : borné pour que le tableau
    (batch, n_candidates, 26) de `pattern_codes_batch` reste sous ~150 Mo, quelle que
    soit la densité du groupe (lettre, longueur)."""
    per_guess_bytes = max(1, n_candidates) * 26 * 4  # float32
    return max(1, min(4096, _BATCH_MEMORY_TARGET_BYTES // per_guess_bytes))


def score_guesses_batch(
    guesses: list[str],
    candidates: list[str],
    candidates_arr: np.ndarray,
    global_freq: dict[str, float],
    positional_freq: np.ndarray,
    letter_counts: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Version vectorisée de `score_guess` sur tout un lot de guesses à la fois :
    aucune boucle Python par guess ni par candidat (seules des boucles bornées sur les
    26 lettres / la longueur du mot subsistent, indépendantes du nombre de candidats).

    Retourne (scores, entropies, vowels), un triplet de tableaux (len(guesses),).
    """
    length = len(candidates[0])
    n = len(candidates)
    guesses_arr = words_to_matrix(guesses)
    b = guesses_arr.shape[0]
    if letter_counts is None:
        letter_counts = candidate_letter_counts(candidates_arr)

    codes = pattern_codes_batch(guesses_arr, candidates_arr, letter_counts)  # (B, N)

    n_codes = 3**length
    flat_idx = (np.arange(b, dtype=np.int64)[:, None] * n_codes + codes).ravel()
    hist = np.bincount(flat_idx, minlength=b * n_codes).reshape(b, n_codes).astype(np.float64)
    probs = hist / n
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(probs > 0, probs * np.log2(probs), 0.0)
    entropy = -terms.sum(axis=1)

    vowels = _VOWEL_LOOKUP[guesses_arr].sum(axis=1)
    sorted_letters = np.sort(guesses_arr, axis=1)
    distinct = 1 + np.sum(np.diff(sorted_letters, axis=1) != 0, axis=1)

    presence = (guesses_arr[:, :, None] == np.arange(26)[None, None, :]).any(axis=1)  # (B, 26)
    global_freq_arr = np.array([global_freq.get(letter, 0.0) for letter in ALPHABET])
    global_component = (presence * global_freq_arr[None, :]).sum(axis=1) / length
    positional_component = positional_freq[np.arange(length), guesses_arr].sum(axis=1) / length
    freq_score = 0.5 * global_component + 0.5 * positional_component

    max_entropy = math.log2(n) if n > 1 else 1.0
    h_norm = entropy / max_entropy if max_entropy > 0 else np.zeros(b)
    vowel_norm = vowels / length
    distinct_norm = distinct / length
    scores = 0.40 * h_norm + 0.25 * vowel_norm + 0.20 * distinct_norm + 0.15 * freq_score

    return scores, entropy, vowels.astype(np.int64)


def best_guess_composite(
    candidates: list[str],
    guess_pool: list[str],
    global_freq: dict[str, float],
    positional_freq: np.ndarray,
) -> tuple[str, float, int]:
    if not candidates:
        raise ValueError("aucun candidat restant")
    candidates_arr = words_to_matrix(candidates)
    letter_counts = candidate_letter_counts(candidates_arr)

    batch_size = _batch_size_for(len(candidates), len(candidates[0]))
    best: tuple[float, str, float, int] | None = None
    for start in range(0, len(guess_pool), batch_size):
        chunk = guess_pool[start : start + batch_size]
        scores, entropies, vowels = score_guesses_batch(
            chunk, candidates, candidates_arr, global_freq, positional_freq, letter_counts
        )
        idx = int(np.argmax(scores))
        if best is None or scores[idx] > best[0]:
            best = (float(scores[idx]), chunk[idx], float(entropies[idx]), int(vowels[idx]))
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
