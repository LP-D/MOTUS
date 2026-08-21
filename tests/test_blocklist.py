from motus_solver.blocklist import add_to_blocklist, load_blocklist, save_blocklist
from motus_solver.corpus import Corpus
from motus_solver.solver import Solver

FAKE_WORDS = ["RIVER", "RIVAL", "RIVET", "ROBOT", "ROUGE", "RADIO"]


def make_corpus() -> Corpus:
    return Corpus(sorted(set(FAKE_WORDS)))


def test_load_blocklist_missing_file_returns_empty_set(tmp_path):
    assert load_blocklist(tmp_path / "missing.json") == set()


def test_save_and_load_blocklist_roundtrip(tmp_path):
    path = tmp_path / "blocklist.json"
    save_blocklist({"FOO", "BAR"}, path)
    assert load_blocklist(path) == {"FOO", "BAR"}


def test_add_to_blocklist_persists_and_normalizes_case(tmp_path):
    path = tmp_path / "blocklist.json"
    words = add_to_blocklist("river", path)
    assert words == {"RIVER"}
    assert load_blocklist(path) == {"RIVER"}

    # un second ajout d'un mot déjà présent (autre casse) ne duplique rien
    words = add_to_blocklist("RIVER", path)
    assert words == {"RIVER"}


def test_add_to_blocklist_accumulates_across_calls(tmp_path):
    path = tmp_path / "blocklist.json"
    add_to_blocklist("RIVER", path)
    add_to_blocklist("RIVAL", path)
    assert load_blocklist(path) == {"RIVER", "RIVAL"}


def test_solver_excludes_blocklisted_words_from_candidates():
    corpus = make_corpus()
    solver = Solver(letter="R", length=5, corpus=corpus, blocklist={"RIVER", "RIVAL"})
    assert "RIVER" not in solver.candidates
    assert "RIVAL" not in solver.candidates
    assert set(solver.candidates) == {"RIVET", "ROBOT", "ROUGE", "RADIO"}


def test_solver_without_blocklist_keeps_all_candidates():
    corpus = make_corpus()
    solver = Solver(letter="R", length=5, corpus=corpus)
    assert len(solver.candidates) == len(FAKE_WORDS)


def test_solver_raises_if_blocklist_removes_all_candidates():
    corpus = Corpus(["RIVER"])
    try:
        Solver(letter="R", length=5, corpus=corpus, blocklist={"RIVER"})
        assert False, "devrait lever ValueError (aucun candidat restant)"
    except ValueError:
        pass
