from __future__ import annotations

import numpy as np

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LETTER_TO_INDEX = {letter: i for i, letter in enumerate(ALPHABET)}

ABSENT, PRESENT, CORRECT = 0, 1, 2


def words_to_matrix(words: list[str]) -> np.ndarray:
    if not words:
        raise ValueError("liste de mots vide")
    length = len(words[0])
    return np.asarray([[LETTER_TO_INDEX[c] for c in w] for w in words], dtype=np.int8).reshape(-1, length)


def pattern_codes(guess: str, candidates: np.ndarray) -> np.ndarray:
    """Retour Motus vectorisé (0=absent,1=présent,2=correct) : un entier base-3 par candidat.

    Adapté de encoder_retours() (notebook Kagglehub) : gère les lettres dupliquées en
    réservant d'abord les positions exactes, puis en distribuant les "présent" de gauche
    à droite dans la limite du nombre de lettres restantes.
    """
    n, length = candidates.shape
    guess_idx = np.asarray([LETTER_TO_INDEX[c] for c in guess], dtype=np.int8)
    states = np.zeros((n, length), dtype=np.uint8)

    exact = candidates == guess_idx
    states[exact] = CORRECT

    remaining = np.zeros((n, 26), dtype=np.int16)
    for letter in range(26):
        remaining[:, letter] = np.sum((candidates == letter) & (~exact), axis=1)

    for position in range(length):
        letter = int(guess_idx[position])
        eligible = (~exact[:, position]) & (remaining[:, letter] > 0)
        states[eligible, position] = PRESENT
        remaining[eligible, letter] -= 1

    powers = (3 ** np.arange(length, dtype=np.int64)).reshape(1, -1)
    return np.sum(states.astype(np.int64) * powers, axis=1)


def candidate_letter_counts(candidates: np.ndarray) -> np.ndarray:
    """Occurrences de chaque lettre (0..25) par candidat, indépendant de tout guess.

    Précalcul unique réutilisable pour scorer tout un pool de guesses contre le même
    ensemble de candidats (voir `pattern_codes_batch`).
    """
    n = candidates.shape[0]
    counts = np.zeros((n, 26), dtype=np.int16)
    for letter in range(26):
        counts[:, letter] = np.sum(candidates == letter, axis=1)
    return counts


def pattern_codes_batch(
    guesses: np.ndarray, candidates: np.ndarray, letter_counts: np.ndarray | None = None
) -> np.ndarray:
    """Version batchée de `pattern_codes` : code Motus de chaque guess contre chaque
    candidat en une seule passe vectorisée, sans boucle Python par guess ni par candidat.

    guesses: (B, length) indices de lettres. candidates: (N, length). Retourne un
    tableau (B, N) de codes, où row[b] == pattern_codes(guess_b, candidates) pour
    chaque guess_b — même convention de désambiguïsation des lettres dupliquées
    (réservation des positions exactes, puis distribution des "présent" de gauche à
    droite), donc résultat strictement identique à un appel par guess.

    `letter_counts` (sortie de `candidate_letter_counts`) peut être précalculé une
    fois et réutilisé sur plusieurs batches contre le même `candidates`.
    """
    b, length = guesses.shape
    n = candidates.shape[0]
    if letter_counts is None:
        letter_counts = candidate_letter_counts(candidates)

    exact = guesses[:, None, :] == candidates[None, :, :]  # (B, N, length)
    states = np.zeros((b, n, length), dtype=np.uint8)
    states[exact] = CORRECT

    # remaining[b, n, letter] = occurrences de `letter` dans le candidat n hors
    # positions exactes pour le guess b. exact_at_letter (nb de positions où guess_b
    # et candidat_n valent tous deux `letter`) est un produit matriciel (B,length)
    # @ (length,N) accéléré par BLAS, bien plus rapide que la même réduction exprimée
    # en boucle Python par candidat.
    remaining = np.broadcast_to(letter_counts, (b, n, 26)).astype(np.float32).copy()
    for letter in range(26):
        guess_mask = (guesses == letter).astype(np.float32)  # (B, length)
        if not guess_mask.any():
            continue
        candidate_mask = (candidates == letter).astype(np.float32)  # (N, length)
        remaining[:, :, letter] -= guess_mask @ candidate_mask.T

    # Distribution des "présent" de gauche à droite : boucle bornée par `length`
    # (<=9), chaque itération restant vectorisée sur (B, N).
    for position in range(length):
        letter_per_guess = guesses[:, position]  # (B,)
        remaining_at_letter = np.take_along_axis(
            remaining, np.broadcast_to(letter_per_guess[:, None, None], (b, n, 1)), axis=2
        )[:, :, 0]
        eligible = (~exact[:, :, position]) & (remaining_at_letter > 0.5)
        states[:, :, position][eligible] = PRESENT
        idx_b, idx_n = np.nonzero(eligible)
        remaining[idx_b, idx_n, letter_per_guess[idx_b]] -= 1

    powers = (3 ** np.arange(length, dtype=np.int64)).reshape(1, 1, -1)
    return np.sum(states.astype(np.int64) * powers, axis=2)


def code_to_pattern(code: int, length: int) -> str:
    digits = []
    for _ in range(length):
        digits.append(str(code % 3))
        code //= 3
    return "".join(digits)


def pattern_to_code(pattern: str) -> int:
    return sum(int(digit) * (3 ** i) for i, digit in enumerate(pattern))


def pattern_string(guess: str, solution: str) -> str:
    candidates = words_to_matrix([solution])
    code = int(pattern_codes(guess, candidates)[0])
    return code_to_pattern(code, len(guess))


def entropy_from_codes(codes: np.ndarray, weights: np.ndarray | None = None) -> float:
    n = len(codes)
    if weights is None:
        weights = np.full(n, 1.0 / n)
    _, inverse = np.unique(codes, return_inverse=True)
    masses = np.bincount(inverse, weights=weights)
    masses = masses[masses > 0]
    return float(-np.sum(masses * np.log2(masses)))


def exp_remaining(codes: np.ndarray, weights: np.ndarray | None = None) -> float:
    n = len(codes)
    if weights is None:
        weights = np.full(n, 1.0 / n)
    _, inverse, counts = np.unique(codes, return_inverse=True, return_counts=True)
    group_sizes = counts[inverse]
    return float(np.sum(weights * group_sizes))
