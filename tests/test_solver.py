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


def _near_tie_solver(strategy, known_valid, near_tie=0.02):
    from motus_solver.corpus import Corpus as _Corpus

    words = ["RATER", "RIVER", "RAYER", "RAVER", "RASER", "RIRES", "ROBOT", "RUSES", "RONDE", "REINE"]
    # sans cache racine : coup calculé dynamiquement, comme aux coups 2 et suivants
    return Solver(letter="R", length=5, corpus=_Corpus(words), strategy=strategy, known_valid=known_valid,
                  near_tie=near_tie)


def test_near_tie_prefers_known_valid_word_at_dynamic_moves():
    """Phase 0 du 25/09/2026 : à score quasi égal, un mot déjà accepté passe devant."""
    for strategy in ("entropy_pure", "composite"):
        plain = _near_tie_solver(strategy, known_valid=set(), near_tie=0)
        ranked = plain.suggest(top_n=len(plain.candidates))
        best, runner_up = ranked[0][0], ranked[1][0]
        # sans mot valide connu : ordre strictement inchangé
        assert _near_tie_solver(strategy, known_valid=set()).suggest(top_n=1)[0][0] == best
        # le 2e, s'il est dans la tolérance et connu valide, passe en tête
        wide = _near_tie_solver(strategy, known_valid={runner_up}, near_tie=1.0)
        assert wide.suggest(top_n=2)[0][0] == runner_up and wide.suggest(top_n=2)[1][0] == best
        # hors tolérance : jamais préféré à un meilleur coup (entropie strictement supérieure)
        if strategy == "entropy_pure" and ranked[0][1] > ranked[1][1]:
            strict = _near_tie_solver(strategy, known_valid={runner_up}, near_tie=1e-12)
            assert strict.suggest(top_n=1)[0][0] == best


def test_near_tie_never_changes_entropy_or_scores():
    solver = _near_tie_solver("entropy_pure", known_valid=set(), near_tie=0)
    ranked = solver.suggest(top_n=5)
    promoted = _near_tie_solver("entropy_pure", known_valid={ranked[-1][0]}, near_tie=1.0).suggest(top_n=5)
    assert sorted(ranked) == sorted(promoted)  # mêmes coups, mêmes valeurs : seul l'ordre change


def test_near_tie_threshold_is_relative_to_best_value():
    from motus_solver.corpus import Corpus as _Corpus

    solver = Solver(letter="R", length=5, corpus=_Corpus(["RATER", "RIVER"]), known_valid={"RONDE", "RUSES"},
                    near_tie=0.02)
    ranked = [("RATER", 0, 0), ("RIVER", 0, 0), ("RONDE", 0, 0), ("RUSES", 0, 0)]
    # RONDE à 1,5 % du meilleur : dans la tolérance de 2 %, remonté en tête
    assert [w for w, *_ in solver._known_valid_first_among_near_ties(ranked, [1.0, 0.99, 0.985, 0.5])] == [
        "RONDE", "RATER", "RIVER", "RUSES"]
    # RONDE à 3 % : hors tolérance, ordre inchangé (RUSES encore plus loin)
    assert solver._known_valid_first_among_near_ties(ranked, [1.0, 0.99, 0.97, 0.5]) == ranked
    # le meilleur déjà connu valide : rien ne bouge
    solver.known_valid = {"RATER", "RONDE"}
    assert solver._known_valid_first_among_near_ties(ranked, [1.0, 1.0, 1.0, 1.0]) == ranked
