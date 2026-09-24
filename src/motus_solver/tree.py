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


def top_guesses_composite(
    candidates: list[str],
    guess_pool: list[str],
    global_freq: dict[str, float],
    positional_freq: np.ndarray,
    k: int,
) -> list[tuple[str, float, float, int]]:
    """Les `k` meilleurs coups selon le MÊME scoring composite que
    `best_guess_composite` (inchangé), classés par score décroissant, égalités
    départagées par l'ordre de `guess_pool` : le 1er élément est toujours
    identique au résultat de `best_guess_composite`.

    Sert au cache racine : si le meilleur coup 1 est refusé par le dictionnaire
    du jeu, le suivant est disponible immédiatement au lieu d'un recalcul complet
    (mesuré : ~290 s par rejet sur R,9). Retourne [(mot, score, entropie, voyelles)]."""
    if not candidates:
        raise ValueError("aucun candidat restant")
    candidates_arr = words_to_matrix(candidates)
    letter_counts = candidate_letter_counts(candidates_arr)
    batch_size = _batch_size_for(len(candidates), len(candidates[0]))
    pool: list[tuple[float, int, str, float, int]] = []
    for start in range(0, len(guess_pool), batch_size):
        chunk = guess_pool[start : start + batch_size]
        scores, entropies, vowels = score_guesses_batch(
            chunk, candidates, candidates_arr, global_freq, positional_freq, letter_counts
        )
        keep = np.argsort(-scores, kind="stable")[:k]
        pool.extend(
            (float(scores[i]), start + int(i), chunk[int(i)], float(entropies[i]), int(vowels[i])) for i in keep
        )
        pool.sort(key=lambda item: (-item[0], item[1]))
        del pool[k:]
    return [(word, score, entropy, vowels) for score, _, word, entropy, vowels in pool]


def _entropy_batch(
    guesses: list[str], candidates_arr: np.ndarray, n: int, length: int, letter_counts: np.ndarray
) -> np.ndarray:
    """Entropie de Shannon (bits) des patterns de réponse pour tout un lot de
    guesses, vectorisé — même infrastructure que `score_guesses_batch`, sans les
    composantes voyelles/lettres-distinctes/fréquence du scoring composite."""
    guesses_arr = words_to_matrix(guesses)
    b = guesses_arr.shape[0]
    codes = pattern_codes_batch(guesses_arr, candidates_arr, letter_counts)  # (B, N)

    n_codes = 3**length
    flat_idx = (np.arange(b, dtype=np.int64)[:, None] * n_codes + codes).ravel()
    hist = np.bincount(flat_idx, minlength=b * n_codes).reshape(b, n_codes).astype(np.float64)
    probs = hist / n
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(probs > 0, probs * np.log2(probs), 0.0)
    return -terms.sum(axis=1)


def best_guess_entropy_pure(
    candidates: list[str], guess_pool: list[str] | None = None
) -> tuple[str, float]:
    """Stratégie alternative "entropie pure" : score uniquement l'entropie de
    Shannon des patterns de réponse (information-théorique), sans les composantes
    voyelles/lettres-distinctes/fréquence du scoring composite existant
    (`best_guess_composite`, non modifié — les deux stratégies coexistent).

    `guess_pool` par défaut = `candidates` (jamais un corpus externe complet) :
    garantit structurellement qu'aucune suggestion n'est hors des candidats
    validés restants. Les égalités d'entropie sont départagées en préférant un mot
    qui appartient lui-même à `candidates` (heuristique de fin de partie : un tel
    mot peut directement être la solution, contrairement à un mot du `guess_pool`
    qui ne serait qu'un "mot sonde" sans être lui-même une réponse possible) — un
    no-op par construction quand `guess_pool` vaut son défaut, mais utile si un
    appelant fournit un `guess_pool` plus large.

    Retourne (guess, entropy).
    """
    if not candidates:
        raise ValueError("aucun candidat restant")
    if guess_pool is None:
        guess_pool = candidates
    candidates_set = set(candidates)
    candidates_arr = words_to_matrix(candidates)
    letter_counts = candidate_letter_counts(candidates_arr)
    n = len(candidates)
    length = len(candidates[0])

    batch_size = _batch_size_for(n, length)
    best: tuple[float, bool, str] | None = None
    for start in range(0, len(guess_pool), batch_size):
        chunk = guess_pool[start : start + batch_size]
        entropies = _entropy_batch(chunk, candidates_arr, n, length, letter_counts)
        for guess, entropy in zip(chunk, entropies):
            key = (float(entropy), guess in candidates_set)
            if best is None or key > (best[0], best[1]):
                best = (key[0], key[1], guess)

    return best[2], best[0]


def top_guesses_entropy_pure(
    candidates: list[str], guess_pool: list[str] | None = None, k: int = 10
) -> list[tuple[str, float]]:
    """Les `k` meilleurs coups selon l'entropie pure, classés comme
    `best_guess_entropy_pure` (inchangée) : entropie décroissante, puis mot
    appartenant aux candidats, puis ordre de `guess_pool` — le 1er élément est
    toujours le résultat de `best_guess_entropy_pure`.

    Parité avec `top_guesses_composite` : sert au cache racine de la stratégie
    entropie pure (repli immédiat si un coup 1 est refusé par le jeu, au lieu d'un
    recalcul complet). Retourne [(mot, entropie)]."""
    if not candidates:
        raise ValueError("aucun candidat restant")
    if guess_pool is None:
        guess_pool = candidates
    candidates_set = set(candidates)
    candidates_arr = words_to_matrix(candidates)
    letter_counts = candidate_letter_counts(candidates_arr)
    n = len(candidates)
    length = len(candidates[0])
    batch_size = _batch_size_for(n, length)
    pool: list[tuple[float, bool, int, str]] = []
    for start in range(0, len(guess_pool), batch_size):
        chunk = guess_pool[start : start + batch_size]
        entropies = _entropy_batch(chunk, candidates_arr, n, length, letter_counts)
        pool.extend(
            (float(e), guess in candidates_set, start + i, guess) for i, (guess, e) in enumerate(zip(chunk, entropies))
        )
        pool.sort(key=lambda item: (-item[0], not item[1], item[2]))
        del pool[k:]
    return [(guess, entropy) for entropy, _in_cands, _idx, guess in pool]


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
