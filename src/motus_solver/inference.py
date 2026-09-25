"""Déduction à partir des couleurs adverses, pour le duel simulé (motus_solver.duel).

En duel, on voit les retours de l'adversaire sans ses lettres. Si l'on devine quels
mots il a pu jouer, chaque retour pèse sur les candidats : une solution S est d'autant
plus probable que des mots plausibles w donnent exactement ce retour contre S.

    poids(S) = produit sur ses lignes i de ( somme_w pi(w) * [retour(w, S) = ligne_i] + eps )

- pi : mots plausibles de l'adversaire, uniformes par défaut (mots déjà acceptés par
  le jeu dans ce groupe et mots d'ouverture des caches racine) ;
- son profil (`OpponentProfile`) : les premiers mots observés lors des duels
  précédents. S'ils correspondent à un cache racine (adversaire bot), son ouverture
  est prévisible sur tout groupe : forte probabilité sur ce mot ;
- eps : garde-fou, un mot joué hors de ce vocabulaire n'élimine jamais un candidat.

Les lignes suivantes dépendent de ce que l'adversaire a appris : on les traite comme
indépendantes (approximation).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .cache import cache_key
from .feedback import candidate_letter_counts, pattern_codes_batch, pattern_to_code, words_to_matrix

EPS_REL = 0.05  # plancher de vraisemblance, relatif au meilleur candidat de la ligne
PREDICTED_OPENER_WEIGHT = 0.9  # masse sur l'ouverture prévue par le profil
MIN_OBSERVATIONS = 2
MATCH_RATE = 0.8
_BLOCK = 2_000_000  # éléments (coups x candidats) par lot


@dataclass
class OpponentProfile:
    """Premiers mots d'un adversaire, observés à la fin des duels (le récapitulatif
    montre sa grille)."""

    openers: dict[str, list[str]] = field(default_factory=dict)  # groupe -> premiers mots

    def observe(self, letter: str, length: int, guesses: list[str]) -> None:
        if guesses:
            self.openers.setdefault(cache_key(letter, length), []).append(guesses[0].upper())

    @property
    def observations(self) -> int:
        return sum(len(v) for v in self.openers.values())

    def predicted_opener(self, letter: str, length: int, caches: dict[str, dict]) -> str | None:
        key = cache_key(letter, length)
        seen = self.openers.get(key)
        if seen:
            return max(set(seen), key=seen.count)  # même groupe déjà vu : son mot habituel
        if self.observations < MIN_OBSERVATIONS:
            return None
        for cache in caches.values():  # bot reconnu : ses ouvertures suivent un cache racine
            hits = sum(1 for k, words in self.openers.items() for w in words
                       if (cache.get(k) or {}).get("word") == w)
            if hits / self.observations >= MATCH_RATE and cache.get(key):
                return cache[key]["word"]
        return None

    def to_dict(self) -> dict:
        return {"openers": self.openers}

    @classmethod
    def from_dict(cls, data: dict) -> "OpponentProfile":
        return cls(openers={k: list(v) for k, v in (data or {}).get("openers", {}).items()})


def _codes(guess_arr: np.ndarray, cand_arr: np.ndarray) -> np.ndarray:
    counts = candidate_letter_counts(cand_arr)
    step = max(1, _BLOCK // max(1, cand_arr.shape[0]))
    return np.concatenate([pattern_codes_batch(guess_arr[s:s + step], cand_arr, counts)
                           for s in range(0, guess_arr.shape[0], step)], axis=0)


def candidate_weights(candidates: list[str], opponent_rows: list[str], vocabulary: list[str],
                      first_row_opener: str | None = None) -> np.ndarray:
    """Poids normalisés des candidats d'après les retours adverses (voir module)."""
    n = len(candidates)
    weights = np.ones(n)
    if not opponent_rows or not vocabulary:
        return weights / n
    cand_arr = words_to_matrix(candidates)
    vocab = list(dict.fromkeys(vocabulary + ([first_row_opener] if first_row_opener else [])))
    codes = _codes(words_to_matrix(vocab), cand_arr)  # (V, N)
    uniform = np.full(len(vocab), 1.0 / len(vocab))
    for i, row in enumerate(opponent_rows):
        prior = uniform
        if i == 0 and first_row_opener:
            prior = uniform * (1 - PREDICTED_OPENER_WEIGHT)
            prior[vocab.index(first_row_opener)] += PREDICTED_OPENER_WEIGHT
        likelihood = prior @ (codes == pattern_to_code(row))
        weights *= likelihood + EPS_REL * max(likelihood.max(), 1e-12)
        weights /= weights.sum()
    return weights


def weighted_entropy(guess_arr: np.ndarray, cand_arr: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Entropie (bits) du retour de chaque coup, candidats pondérés par `weights`."""
    n = cand_arr.shape[0]
    counts = candidate_letter_counts(cand_arr)
    out = []
    step = max(1, _BLOCK // max(1, n))
    for s in range(0, guess_arr.shape[0], step):
        codes = pattern_codes_batch(guess_arr[s:s + step], cand_arr, counts)  # (B, N)
        b = codes.shape[0]
        order = np.argsort(codes, axis=1, kind="stable")
        sorted_codes = np.take_along_axis(codes, order, axis=1)
        sorted_w = weights[order]
        change = np.ones_like(sorted_codes, dtype=bool)
        change[:, 1:] = sorted_codes[:, 1:] != sorted_codes[:, :-1]
        starts = np.flatnonzero(change.ravel())
        mass = np.add.reduceat(sorted_w.ravel(), starts)
        rows = starts // n
        terms = np.where(mass > 0, -mass * np.log2(np.where(mass > 0, mass, 1.0)), 0.0)
        h = np.zeros(b)
        np.add.at(h, rows, terms)
        out.append(h)
    return np.concatenate(out)


def effective_candidates(weights: np.ndarray) -> float:
    w = weights[weights > 0]
    return float(2 ** (-(w * np.log2(w)).sum()))
