"""Fin de partie par mot sonde (motus_solver.endgame).

Situation des 4 défaites du 25/09/2026 : une seule lettre inconnue (PA?ES), plus de
candidats que d'essais. Jouer les candidats un par un perd ; un mot sonde qui teste
plusieurs lettres d'un coup garantit la victoire.
"""
from __future__ import annotations

import pytest

from motus_solver.corpus import Corpus
from motus_solver.endgame import EndgamePlanner
from motus_solver.feedback import pattern_string
from motus_solver.solver import Solver

CANDIDATES = ["PACES", "PAGES", "PALES", "PAMES", "PANES", "PATES", "PAVES", "PAYES"]
# mots jouables hors candidats : chacun teste plusieurs des lettres possibles
PROBES = ["PLMNT", "PVYCK"]


def play(target: str, endgame: bool, max_attempts: int = 4, first: str = "PAGES") -> int | None:
    solver = Solver("P", 5, Corpus(CANDIDATES + PROBES), max_attempts=max_attempts, endgame=endgame)
    guess = first  # coup 1 imposé : laisse 7 candidats pour 3 essais
    for attempt in range(1, max_attempts + 1):
        pattern = pattern_string(guess, target)
        if pattern == "22222":
            return attempt
        solver.play(guess)
        solver.update(pattern)
        if attempt < max_attempts:
            guess = solver.suggest(top_n=1)[0][0]
    return None


def test_candidates_one_by_one_lose_some_words():
    assert any(play(t, endgame=False) is None for t in CANDIDATES)


@pytest.mark.parametrize("target", CANDIDATES)
def test_probe_guarantees_the_win(target):
    assert play(target, endgame=True) is not None


def test_planner_picks_a_probe_testing_several_letters():
    remaining = [w for w in CANDIDATES if w != "PAGES"]
    choice = EndgamePlanner(remaining, CANDIDATES + PROBES, attempts_left=3).choose(default="PACES")
    assert choice is not None and choice.probe
    assert choice.word in PROBES
    assert choice.p_win == 1.0 and choice.p_default < 1.0
    assert choice.reason == "risque"
    assert len(choice.letters) >= 3


def test_default_kept_when_it_already_guarantees_the_win():
    # 2 candidats, 3 essais : le coup habituel gagne toujours, rien à changer
    assert EndgamePlanner(["PAGES", "PALES"], CANDIDATES + PROBES, attempts_left=3).choose("PAGES") is None


def test_speed_mode_replaces_one_by_one_testing():
    """Victoire garantie (5 candidats, 5 essais) mais une sonde finit plus vite."""
    remaining = ["PALES", "PAMES", "PANES", "PATES", "PAVES"]
    choice = EndgamePlanner(remaining, remaining + PROBES, attempts_left=5, known_valid={"PLMNT"}).choose("PALES")
    assert choice is not None and choice.reason == "vitesse" and choice.word == "PLMNT"
    assert choice.expected_attempts < choice.expected_default


def test_speed_mode_never_risks_a_rejection_on_an_unknown_probe():
    """Victoire garantie : une sonde jamais acceptée par le jeu (souvent un mot rare,
    refusé) n'est pas jouée juste pour gagner du temps."""
    remaining = ["PALES", "PAMES", "PANES", "PATES", "PAVES"]
    assert EndgamePlanner(remaining, remaining + PROBES, attempts_left=5).choose("PALES") is None


def test_known_valid_probe_preferred_at_equal_value():
    remaining = ["PALES", "PAMES", "PANES", "PATES"]
    probes = ["PLMNT", "PTNML"]  # même pouvoir de séparation
    choice = EndgamePlanner(remaining, remaining + probes, attempts_left=2, known_valid={"PTNML"}).choose("PALES")
    assert choice is not None and choice.word == "PTNML"


def test_discard_removes_rejected_word_from_candidates_and_probes():
    solver = Solver("P", 5, Corpus(CANDIDATES + PROBES))
    solver.discard("plmnt")
    solver.discard("PAGES")
    assert "PLMNT" not in solver.pool and "PAGES" not in solver.pool
    assert "PAGES" not in solver.candidates
