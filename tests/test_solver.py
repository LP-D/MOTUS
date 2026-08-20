from motus_solver.corpus import Corpus
from motus_solver.feedback import pattern_string
from motus_solver.solver import Solver

FAKE_WORDS = ["RIVER", "RIVAL", "RIVET", "ROBOT", "ROUGE", "RADIO", "RAPIDE", "REVEIL", "REGIME"]


def make_corpus() -> Corpus:
    return Corpus(sorted(set(FAKE_WORDS)))


def test_solver_converges_to_unique_solution():
    corpus = make_corpus()
    solution = "RIVER"
    solver = Solver(letter="R", length=5, corpus=corpus)

    for _ in range(10):
        if solver.is_solved():
            break
        guess = solver.suggest(top_n=1)[0][0]
        solver.play(guess)
        solver.update(pattern_string(guess, solution))

    assert solver.is_solved()
    assert solver.solution == solution


def test_solver_suggest_returns_sorted_tuples():
    corpus = make_corpus()
    solver = Solver(letter="R", length=5, corpus=corpus)
    suggestions = solver.suggest(top_n=3)
    assert 1 <= len(suggestions) <= 3
    for word, entropy, vowels in suggestions:
        assert word in solver.candidates
        assert entropy >= 0
        assert vowels >= 0
