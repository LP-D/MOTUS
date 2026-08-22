"""Tâche 2 : stratégie alternative 'entropie pure', ajoutée sans toucher à
`best_guess_composite` (poids inchangés)."""
from __future__ import annotations

from motus_solver.corpus import Corpus
from motus_solver.feedback import entropy_from_codes, pattern_codes, words_to_matrix
from motus_solver.tree import best_guess_composite, best_guess_entropy_pure

WORDS = ["CHAT", "CHIC", "CHOC", "CRAN", "CRUE", "CLAN", "CLIC", "CRIC", "CRUS", "CHUT"]


def make_corpus() -> Corpus:
    return Corpus(sorted(set(WORDS)))


def naive_best_entropy(candidates: list[str], guess_pool: list[str]) -> tuple[str, float]:
    """Référence non vectorisée : boucle Python directe sur entropy_from_codes."""
    candidates_arr = words_to_matrix(candidates)
    best = None
    for guess in guess_pool:
        codes = pattern_codes(guess, candidates_arr)
        entropy = entropy_from_codes(codes)
        if best is None or entropy > best[1]:
            best = (guess, entropy)
    return best


def test_matches_naive_reference_on_default_guess_pool():
    corpus = make_corpus()
    candidates = corpus.subset("C", 4)
    naive_guess, naive_entropy = naive_best_entropy(candidates, candidates)
    guess, entropy = best_guess_entropy_pure(candidates)
    assert guess == naive_guess
    assert abs(entropy - naive_entropy) < 1e-9


def test_default_guess_pool_is_restricted_to_candidates_only():
    """Sans guess_pool explicite, la suggestion doit toujours être un candidat
    restant — jamais un mot du corpus externe complet."""
    corpus = make_corpus()
    candidates = corpus.subset("C", 4)
    guess, _entropy = best_guess_entropy_pure(candidates)
    assert guess in candidates


def test_tie_break_prefers_word_belonging_to_candidates():
    """En cas d'égalité d'entropie entre un mot des candidats et un mot externe au
    guess_pool, le mot appartenant aux candidats doit l'emporter (heuristique de
    fin de partie)."""
    candidates = ["CHIC", "CRIC"]
    external_pool = ["CRAN", "CHIC", "CRIC"]  # CRAN hors candidats, placé en 1er

    candidates_arr = words_to_matrix(candidates)
    entropies = {w: entropy_from_codes(pattern_codes(w, candidates_arr)) for w in external_pool}
    assert len(set(entropies.values())) == 1  # les 3 sont bien à égalité

    guess, _entropy = best_guess_entropy_pure(candidates, guess_pool=external_pool)
    assert guess in candidates


def test_raises_on_empty_candidates():
    try:
        best_guess_entropy_pure([])
        assert False, "devrait lever ValueError"
    except ValueError:
        pass


def test_best_guess_composite_untouched_and_coexists_with_entropy_pure():
    """`best_guess_composite` n'est pas modifié par l'ajout de la nouvelle
    stratégie : reste appelable avec la même signature, produit un candidat
    valide. Les deux stratégies coexistent comme deux fonctions indépendantes."""
    corpus = make_corpus()
    global_freq = {}
    import numpy as np

    positional_freq = np.zeros((4, 26))
    candidates = corpus.subset("C", 4)

    composite_guess, _, _ = best_guess_composite(candidates, candidates, global_freq, positional_freq)
    entropy_guess, _ = best_guess_entropy_pure(candidates)
    assert composite_guess in candidates
    assert entropy_guess in candidates
