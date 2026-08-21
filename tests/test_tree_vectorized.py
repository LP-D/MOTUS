"""Non-régression : la version vectorisée de `best_guess_composite` (batch numpy,
sans boucle Python par candidat) doit produire un résultat strictement identique à
l'ancienne implémentation (boucle Python appelant `score_guess` par guess)."""
from __future__ import annotations

import math

from motus_solver.corpus import Corpus
from motus_solver.scoring import letter_frequencies, positional_frequencies, vowel_count
from motus_solver.tree import best_guess_composite, score_guess
from motus_solver.feedback import words_to_matrix

CORPUS_PATH = "data/corpus_fr.txt"


def naive_best_guess_composite(candidates, guess_pool, global_freq, positional_freq):
    """Réplique exacte de l'ancienne implémentation de `best_guess_composite`
    (boucle Python par guess, appelant `score_guess` un par un) : sert de référence
    pour prouver que la version vectorisée est un pur changement d'implémentation."""
    candidates_arr = words_to_matrix(candidates)
    best = None
    for guess in guess_pool:
        score, entropy, vowels = score_guess(
            guess, candidates, candidates_arr, global_freq, positional_freq
        )
        if best is None or score > best[0]:
            best = (score, guess, entropy, vowels)
    _, guess, entropy, vowels = best
    return guess, entropy, vowels


def _assert_matches(naive, vectorized):
    naive_guess, naive_entropy, naive_vowels = naive
    vec_guess, vec_entropy, vec_vowels = vectorized
    assert vec_guess == naive_guess
    assert math.isclose(vec_entropy, naive_entropy, rel_tol=1e-9, abs_tol=1e-9)
    assert vec_vowels == naive_vowels


def test_vectorized_matches_naive_on_small_synthetic_corpus():
    words = ["RIVER", "RIVAL", "RIVET", "ROBOT", "ROUGE", "RADIO", "RAPIDE", "REVEIL", "REGIME"]
    corpus = Corpus(sorted(set(words)))
    global_freq = letter_frequencies(corpus)

    for letter, length in [("R", 5), ("R", 6)]:
        candidates = corpus.subset(letter, length)
        positional_freq = positional_frequencies(corpus, length)
        naive = naive_best_guess_composite(candidates, candidates, global_freq, positional_freq)
        vectorized = best_guess_composite(candidates, candidates, global_freq, positional_freq)
        _assert_matches(naive, vectorized)


def test_vectorized_matches_naive_on_real_corpus_small_group():
    corpus = Corpus.from_file(CORPUS_PATH)
    global_freq = letter_frequencies(corpus)

    # Petit groupe réel (n=416), assez rapide pour recalculer la référence naïve
    # à chaque run de la suite de tests.
    letter, length = "D", 5
    candidates = corpus.subset(letter, length)
    positional_freq = positional_frequencies(corpus, length)

    naive = naive_best_guess_composite(candidates, candidates, global_freq, positional_freq)
    vectorized = best_guess_composite(candidates, candidates, global_freq, positional_freq)
    _assert_matches(naive, vectorized)


def test_vectorized_matches_frozen_naive_reference_on_r9_group():
    """Le groupe (R, 9) (n=21070, le plus dense du corpus) est celui utilisé pour le
    profiling de move1 (329.15s avec l'ancienne implémentation). Recalculer la
    référence naïve à chaque run de la suite serait beaucoup trop lent : on compare
    donc contre la valeur figée capturée lors de ce profiling (ancienne
    implémentation, exécutée une fois hors suite de tests, cf. rapport de
    vectorisation)."""
    corpus = Corpus.from_file(CORPUS_PATH)
    global_freq = letter_frequencies(corpus)

    letter, length = "R", 9
    candidates = corpus.subset(letter, length)
    assert len(candidates) == 21070
    positional_freq = positional_frequencies(corpus, length)

    frozen_naive_guess, frozen_naive_entropy = "RECOUINAS", 8.97259231189938
    vectorized = best_guess_composite(candidates, candidates, global_freq, positional_freq)

    assert vectorized[0] == frozen_naive_guess
    assert math.isclose(vectorized[1], frozen_naive_entropy, rel_tol=1e-9, abs_tol=1e-9)
    # le nombre de voyelles ne dépend que du mot lui-même (indépendant de l'algo)
    assert vectorized[2] == vowel_count(frozen_naive_guess)
