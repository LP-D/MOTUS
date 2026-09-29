from __future__ import annotations

import json
import random

import pytest

from motus_solver.agents import SPEEDS, SolverContext, TusmoAgent, make_agent, play_duel
from motus_solver.corpus import Corpus
from motus_solver.feedback import pattern_string
from motus_solver.tusmo_list import (
    GroupEvidence,
    TrainingGame,
    TusmoAdvisor,
    TusmoModel,
    estimate_membership,
    load_evidence,
)

WORDS = ["PAGES", "PALES", "PAMES", "PANES", "PATES", "PAVES", "PAYES"]


def game(answer, *guesses, left):
    return TrainingGame(answer, [(g, pattern_string(g, answer), n) for g, n in zip(guesses, left)])


def test_membership_follows_counts_and_known_answers():
    # liste de 4 mots dont PAVES ; après PAGES (22022), 3 solutions restent : PAVES + 2 des 5 autres PA_ES
    ev = GroupEvidence(size=4, games=[game("PAVES", "PAGES", left=[3])], answers={"PAVES"})
    p = dict(zip(WORDS, estimate_membership(WORDS, ev)))
    assert p["PAVES"] == 1.0
    assert p["PAGES"] == pytest.approx(1.0, abs=0.05)  # le seul mot libre hors de l'ensemble du coup
    assert all(p[w] == pytest.approx(0.4, abs=0.05) for w in ("PALES", "PAMES", "PANES", "PATES", "PAYES"))


def test_count_matched_by_known_answers_excludes_the_others():
    ev = GroupEvidence(size=2, games=[game("PAVES", "PAGES", left=[1])], answers={"PAVES"})
    p = dict(zip(WORDS, estimate_membership(WORDS, ev)))
    assert p["PALES"] == 0.0 and p["PAYES"] == 0.0


def test_forget_drops_the_answer_and_its_games():
    ev = GroupEvidence(size=4, games=[game("PAVES", "PAGES", left=[1])], answers={"PAVES"})
    p = estimate_membership(WORDS, ev, forget={"PAVES"})
    assert p == pytest.approx([4 / 7] * 7)


def test_advisor_prefers_a_possible_solution_among_ties_then_finds_it():
    membership = [1.0 if w in ("PAGES", "PAVES") else 0.0 for w in WORDS]
    import numpy as np

    adv = TusmoAdvisor("P", 5, WORDS, np.array(membership), WORDS)
    assert adv.choose() in ("PAGES", "PAVES")  # 1 bit pour tout mot qui les sépare : un candidat gagne peut-être
    adv.update("PAGES", pattern_string("PAGES", "PAVES"))
    assert adv.candidates()[0] == ["PAVES"] and adv.choose() == "PAVES"


def test_advisor_plays_tusmo_first_move_when_known():
    import numpy as np

    adv = TusmoAdvisor("P", 5, WORDS, np.ones(len(WORDS)), WORDS, best_first="PALES")
    assert adv.choose() == "PALES"
    adv.discard("PALES")  # refusé par le jeu : calcul classique
    assert adv.choose() != "PALES"


def test_load_evidence_reads_training_log(tmp_path):
    rec = {"first_letter": "P", "word_len": 5, "strategy": "entropy_pure", "status": "won",
           "moves": [{"word": "PAGES", "pattern": "22022", "candidates_left_tusmo": 1},
                     {"word": "PAVES", "pattern": "22222", "candidates_left_tusmo": 1}],
           "report": {"answer": "PAVES", "moves": [
               {"word": "PAGES", "percent": 90, "bestWord": "PALES", "candidatesBefore": 3},
               {"word": "PAVES", "percent": 100, "bestWord": "PAVES", "candidatesBefore": 1}]}}
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    ev = load_evidence(log, [("P", 5, "PATES")])["P_5"]
    assert ev.size == 3 and ev.best_first == "PALES" and ev.answers == {"PAVES", "PATES"}
    assert ev.games[0].moves[0] == ("PAGES", "22022", 1)


def small_context():
    ev = GroupEvidence(size=2, games=[], answers={"PAVES", "PAGES"})
    return SolverContext(corpus=Corpus(WORDS), caches={"entropy_pure": {}, "composite": {}}, blocklist=set(),
                         known_valid=set(WORDS), tusmo=TusmoModel({"P_5": ev}))


def test_tusmo_agent_forgets_the_duel_word():
    ctx = small_context()
    agent = TusmoAgent()
    agent.new_game("P", 5, ctx, ctx.known_valid - {"PAVES"})  # comme play_duel
    p = dict(zip(agent.advisor.universe, agent.advisor.membership))
    assert p["PAGES"] == 1.0 and p["PAVES"] < 1.0


def test_tusmo_agent_plays_a_full_duel_against_the_solver():
    ctx = small_context()
    rec = play_duel("PAVES", (make_agent("tusmo_conseil"), make_agent("entropy_pure")),
                    (SPEEDS["instantane"], SPEEDS["instantane"]), ctx, random.Random(0))
    assert rec.names == ("tusmo_conseil", "entropy_pure")
    assert rec.solved[0] and rec.guesses[0][-1] == "PAVES"


def test_words_accepted_in_training_are_never_rejected_by_the_simulator():
    ev = GroupEvidence(accepted={"PXQZW"})
    ctx = SolverContext(corpus=Corpus(WORDS), caches={}, blocklist=set(), known_valid=set(),
                        tusmo=TusmoModel({"P_5": ev}))
    assert ctx.accepts("PXQZW", "PAVES") and ctx.is_playable("PXQZW")
