"""Non-régression tâche 3 : en cas de rejet ("Mot inconnu"), le bot doit recalculer
dynamiquement une nouvelle proposition sur le sous-corpus amputé (via
`suggest_and_submit_with_retry`), pas s'arrêter après une liste figée de 5 candidats."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.cache import build_root_cache
from motus_solver.corpus import Corpus
from motus_solver.solver import Solver

from scripts.benchmark_bot_latency import suggest_and_submit_with_retry

WORDS = ["CHAT", "CHIC", "CHOC", "CRAN", "CRUE", "CLAN", "CLIC", "CRIC", "CRUS", "CHUT"]


def make_solver() -> Solver:
    corpus = Corpus(sorted(set(WORDS)))
    return Solver(letter="C", length=4, corpus=corpus)


def make_solver_with_root_cache() -> Solver:
    corpus = Corpus(sorted(set(WORDS)))
    cache = build_root_cache(corpus)
    return Solver(letter="C", length=4, corpus=corpus, root_cache=cache)


def test_cascading_rejections_propose_different_candidates_until_accepted():
    """3 rejets successifs puis acceptation : le solveur doit proposer 4 mots
    réellement différents (recalcul dynamique), pas retenter le même mot."""
    solver = make_solver()
    tried: list[str] = []

    def fake_submit(word: str) -> bool:
        tried.append(word)
        return len(tried) >= 4  # les 3 premières propositions sont rejetées, la 4e acceptée

    accepted = suggest_and_submit_with_retry(solver, fake_submit)

    assert accepted == tried[-1]
    assert len(tried) == 4
    assert len(set(tried)) == 4  # 4 propositions distinctes, pas de répétition du même mot rejeté

    for rejected_word in tried[:-1]:
        assert rejected_word not in solver.candidates  # retirés du sous-corpus
    assert accepted in solver.candidates  # le mot accepté reste (sera joué ensuite par l'appelant)


def test_all_candidates_rejected_returns_none_and_empties_pool():
    solver = make_solver()
    n_initial = len(solver.candidates)
    tried: list[str] = []

    def always_reject(word: str) -> bool:
        tried.append(word)
        return False

    result = suggest_and_submit_with_retry(solver, always_reject)

    assert result is None
    assert len(tried) == n_initial
    assert len(set(tried)) == n_initial  # chaque candidat du pool tenté une seule fois, jamais deux fois
    assert solver.candidates == []


def test_max_retries_caps_attempts_without_exhausting_pool():
    solver = make_solver()
    n_initial = len(solver.candidates)

    result = suggest_and_submit_with_retry(solver, lambda word: False, max_retries=2)

    assert result is None
    assert len(solver.candidates) == n_initial - 2


def test_cascading_rejections_with_root_cache_still_vary_candidates():
    """Reproduit précisément le bug découvert via le dashboard : avec un root_cache,
    le coup 1 est d'abord servi par le cache — si ce mot est rejeté, la proposition
    SUIVANTE doit être différente (calcul dynamique), pas le même mot en boucle
    (self.history reste vide pendant toute la boucle de retry, donc rien ne
    "désactive" naturellement le raccourci cache sans le garde-fou dans Solver.suggest)."""
    solver = make_solver_with_root_cache()
    cached_word = solver.suggest(top_n=1)[0][0]  # confirme qu'on part bien du cache
    tried: list[str] = []

    def fake_submit(word: str) -> bool:
        tried.append(word)
        return len(tried) >= 3  # les 2 premières (dont le mot en cache) rejetées

    accepted = suggest_and_submit_with_retry(solver, fake_submit)

    assert tried[0] == cached_word  # la 1re tentative vient bien du cache
    assert len(set(tried)) == len(tried)  # jamais deux fois le même mot, y compris après le cache
    assert accepted == tried[-1]
    assert accepted != cached_word
