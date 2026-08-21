from motus_solver.corpus import Corpus
from motus_solver.feedback import pattern_string
from motus_solver.solver import Solver

FAKE_WORDS = ["RIVER", "RIVAL", "RIVET", "ROBOT", "ROUGE", "RADIO", "RAPIDE", "REVEIL", "REGIME"]

# Corpus dédié à la preuve d'adaptativité : plusieurs mots C___ partagent assez de
# lettres pour que le coup 1 laisse, selon le retour, un sous-groupe de candidats
# encore ambigu (donc un vrai choix de coup 2, pas un candidat déjà unique).
ADAPTIVITY_WORDS = ["CHAT", "CHIC", "CHOC", "CRAN", "CRUE", "CLAN", "CLIC", "CRIC", "CRUS", "CHUT"]

# Leurres même longueur (4), lettre initiale différente, mais construits pour partager
# beaucoup de lettres avec le pool C___ (R, U, C, S...) : si le filtrage par lettre
# imposée fuitait (ex. substring au lieu de first-letter), ces mots deviendraient de
# bons candidats/suggestions à cause de ce chevauchement — ils ne doivent donc jamais
# apparaître, ce qui rend la vérification de la contrainte non vacueuse.
DECOY_WORDS = ["TRUC", "ARCS", "DRAP"]


def make_corpus() -> Corpus:
    return Corpus(sorted(set(FAKE_WORDS)))


def make_adaptivity_corpus() -> Corpus:
    return Corpus(sorted(set(ADAPTIVITY_WORDS + DECOY_WORDS)))


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


def test_move2_is_recomputed_from_move1_feedback():
    """Le coup 2 doit être recalculé sur le sous-corpus filtré par le retour du coup 1,
    et non fixe : deux retours différents (donnant deux sous-groupes différents) doivent
    produire deux propositions différentes au coup 2 — tout en respectant à chaque étape
    la lettre imposée par la cible (contrainte spécifique Motus)."""
    corpus = make_adaptivity_corpus()
    imposed_letter = "C"

    # Contrôle négatif : le corpus contient bien des leurres qui ne respectent pas la
    # lettre imposée, sinon la vérification startswith() serait vacueusement vraie.
    assert any(not w.startswith(imposed_letter) for w in DECOY_WORDS)
    assert set(DECOY_WORDS).isdisjoint(corpus.subset(imposed_letter, 4))

    solver_a = Solver(letter=imposed_letter, length=4, corpus=corpus)
    solver_b = Solver(letter=imposed_letter, length=4, corpus=corpus)

    # dès l'initialisation, le sous-corpus ne doit contenir aucun leurre hors-lettre
    assert all(w.startswith(imposed_letter) for w in solver_a.candidates)
    assert set(solver_a.candidates).isdisjoint(DECOY_WORDS)

    guess1_a = solver_a.suggest(top_n=1)[0][0]
    guess1_b = solver_b.suggest(top_n=1)[0][0]
    assert guess1_a == guess1_b  # même état initial, même coup 1
    assert guess1_a not in DECOY_WORDS

    solver_a.play(guess1_a)
    solver_b.play(guess1_b)

    # Deux cibles réelles du corpus (respectant la lettre imposée) qui produisent des
    # retours différents pour guess1, et donc deux sous-groupes de candidats distincts
    # (l'un encore ambigu).
    target_a, target_b = "CHIC", "CRIC"
    assert target_a.startswith(imposed_letter) and target_b.startswith(imposed_letter)
    pattern_a = pattern_string(guess1_a, target_a)
    pattern_b = pattern_string(guess1_b, target_b)
    assert pattern_a != pattern_b

    solver_a.update(pattern_a)
    solver_b.update(pattern_b)

    # le filtrage par feedback doit produire des sous-corpus réellement différents,
    # mais qui respectent toujours strictement la lettre imposée et excluent les leurres
    assert solver_a.candidates != solver_b.candidates
    assert all(w.startswith(imposed_letter) for w in solver_a.candidates)
    assert all(w.startswith(imposed_letter) for w in solver_b.candidates)
    assert set(solver_a.candidates).isdisjoint(DECOY_WORDS)
    assert set(solver_b.candidates).isdisjoint(DECOY_WORDS)

    guess2_a = solver_a.suggest(top_n=1)[0][0]
    guess2_b = solver_b.suggest(top_n=1)[0][0]

    assert guess2_a != guess2_b
    # les deux propositions restent cohérentes avec la lettre imposée, et aucun leurre
    # (même très proche lexicalement des candidats) n'a pu se glisser dans le résultat
    assert guess2_a.startswith(imposed_letter) and guess2_a not in DECOY_WORDS
    assert guess2_b.startswith(imposed_letter) and guess2_b not in DECOY_WORDS
