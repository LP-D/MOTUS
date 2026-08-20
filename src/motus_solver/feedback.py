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
