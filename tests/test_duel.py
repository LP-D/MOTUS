"""Règles du duel classé simulé (motus_solver.duel) et duels bot contre bot."""
from __future__ import annotations

import random

import pytest

from motus_solver.agents import SPEEDS, AgentView, SolverAgent, SolverContext, play_duel
from motus_solver.corpus import Corpus
from motus_solver.duel import Duel, DuelError

WORD = "PAGES"


def test_first_solver_wins_when_other_cannot_do_better():
    duel = Duel(WORD)
    duel.submit(0, "PALES", 1.0)
    duel.submit(1, "PAMES", 2.0)
    duel.submit(0, "PAGES", 3.0)  # trouvé en 2 : l'autre, à 1 essai, devrait trouver en 1
    assert duel.result.winner == 0 and duel.result.reason == "cannot_catch_up"


def test_chaser_wins_with_strictly_fewer_attempts():
    duel = Duel(WORD)
    for t, w in enumerate(["PALES", "PAMES", "PANES"], 1):
        duel.submit(0, w, t)
    duel.submit(0, "PAGES", 4.0)  # trouvé en 4
    assert duel.result is None and duel.deadline == pytest.approx(124.0)
    assert duel.to_beat(1) == 3
    duel.submit(1, "PATES", 10.0)
    duel.submit(1, "PAGES", 20.0)  # trouvé en 2 < 4
    assert duel.result.winner == 1 and duel.result.reason == "fewer_attempts"


def test_equal_attempts_lose_for_the_chaser():
    duel = Duel(WORD)
    duel.submit(0, "PALES", 1.0)
    duel.submit(1, "PAMES", 1.5)
    duel.submit(0, "PAGES", 2.0)  # trouvé en 2 ; l'autre a déjà joué 1 essai : trouver au 2e ne suffirait pas
    assert duel.result.winner == 0


def test_game_stops_as_soon_as_catching_up_is_impossible():
    duel = Duel(WORD)
    for t, w in enumerate(["PALES", "PAMES", "PAGES"], 1):
        duel.submit(0, w, t)  # trouvé en 3
    duel.submit(1, "PANES", 5.0)
    assert duel.result is None
    duel.submit(1, "PATES", 6.0)  # 2 essais ratés : trouver en 3 ne suffirait plus
    assert duel.result.winner == 0 and duel.result.reason == "cannot_catch_up"
    with pytest.raises(DuelError):
        duel.submit(1, "PAGES", 7.0)


def test_chase_clock_expires_after_two_minutes():
    duel = Duel(WORD)
    for t, w in enumerate(["PALES", "PAMES", "PANES", "PAGES"], 1):
        duel.submit(0, w, t)  # trouvé en 4 à t = 4
    duel.submit(1, "PATES", 50.0)
    duel.advance(124.5)
    assert duel.result.winner == 0 and duel.result.reason == "chase_timeout"


def test_nobody_finds_is_a_draw_and_only_solver_wins():
    wrong = ["PALES", "PAMES", "PANES", "PATES", "PAVES", "PAYES"]
    duel = Duel(WORD)
    for i, w in enumerate(wrong):
        duel.submit(0, w, i)
        duel.submit(1, w, i + 0.5)
    assert duel.result.winner is None and duel.result.reason == "nobody"

    duel = Duel(WORD)
    for i, w in enumerate(wrong):
        duel.submit(0, w, i)
    duel.submit(1, "PAGES", 30.0)  # l'adversaire a échoué : trouver suffit
    assert duel.result.winner == 1 and duel.result.reason == "only_solver"


def test_opponent_sees_colours_not_letters():
    duel = Duel(WORD)
    duel.submit(0, "PALES", 1.0)
    assert duel.opponent_rows(1) == ["22022"]


def test_word_format_is_enforced():
    duel = Duel(WORD)
    with pytest.raises(DuelError):
        duel.submit(0, "MAGES", 1.0)
    with pytest.raises(DuelError):
        duel.submit(0, "PAGE", 1.0)


def _ctx() -> SolverContext:
    words = [f"PA{c}ES" for c in "GLMNTVY"] + ["PLMNT", "PVYCK"]
    return SolverContext(corpus=Corpus(words), caches={"entropy_pure": {}, "composite": {}}, blocklist=set(),
                         known_valid=set(words))


def test_chase_aware_agent_targets_fewer_attempts():
    """L'adversaire a trouvé en 3 : il faut trouver en 2 au plus. L'agent qui en tient
    compte joue un candidat (seule chance de gagner) au lieu d'un mot sonde."""
    ctx = _ctx()
    view = AgentView("P", 5, [("PAGES", "22022")], [], to_beat=2)
    aware = SolverAgent("a", chase_aware=True)
    aware.new_game("P", 5, ctx, ctx.known_valid)
    assert aware.choose(view) in {f"PA{c}ES" for c in "LMNTVY"}


def test_bot_duel_runs_to_a_result():
    ctx = _ctx()
    rec = play_duel("PAVES", (SolverAgent("a"), SolverAgent("b", endgame=False)),
                    (SPEEDS["instantane"], SPEEDS["lent"]), ctx, random.Random(3))
    assert rec.reason in {"fewer_attempts", "cannot_catch_up", "chase_timeout", "only_solver", "nobody"}
    assert any(rec.solved)


# --- pistes de la compétition : infos adverses, pression, tempo ----------------------

def test_opponent_profile_recognises_a_bot_opener():
    from motus_solver.inference import OpponentProfile

    caches = {"entropy_pure": {"P_5": {"word": "PAGES"}, "R_5": {"word": "RIVER"}, "T_5": {"word": "TAPIS"}}}
    profile = OpponentProfile()
    profile.observe("P", 5, ["PAGES", "PALES"])
    profile.observe("R", 5, ["RIVER"])
    assert profile.predicted_opener("P", 5, caches) == "PAGES"  # groupe déjà vu
    assert profile.predicted_opener("T", 5, caches) == "TAPIS"  # bot reconnu : son cache racine


def test_opponent_colours_weight_the_candidates():
    from motus_solver.inference import candidate_weights

    cands = ["PAGES", "PALES", "PAMES"]
    # l'adversaire a ouvert avec PLMNT (prévu par son profil) et obtenu « L bien placé »
    row = "20000"  # pattern de PLMNT contre PAGES : seul P est bon
    w = candidate_weights(cands, [row], ["PLMNT", "PAGES"], first_row_opener="PLMNT")
    assert w.argmax() == 0 and w[0] > 0.8
    assert w.min() > 0  # jamais d'élimination stricte


def test_pressure_disables_probes_when_opponent_is_one_letter_away():
    ctx = _ctx()
    agent = SolverAgent("p", chase_aware=True, pressure=True)
    agent.new_game("P", 5, ctx, ctx.known_valid)
    view = AgentView("P", 5, [("PAGES", "22022")], ["22202"], to_beat=None)
    agent._prepare(view)
    assert agent.solver.endgame is False
    calm = AgentView("P", 5, [("PAGES", "22022")], ["20000"], to_beat=None)
    agent._prepare(calm)
    assert agent.solver.endgame is True


def test_tempo_prefers_known_valid_words_more_widely():
    from motus_solver.agents import make_agent

    assert make_agent("entropy_pure_tempo").near_tie == 0.10
