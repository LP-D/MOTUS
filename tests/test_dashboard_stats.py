"""Tâche 2 : agrégation des statistiques de parties (fonction pure, données
simulées -> sortie agrégée correcte), sans dépendre du fichier de persistance."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from stats_store import aggregate_games, aggregate_solutions


def make_game(letter, length, attempts, solved, outcome="solved", guesses=None, solution=None):
    return {
        "letter": letter,
        "length": length,
        "attempts": attempts,
        "solved": solved,
        "outcome": outcome,
        "guesses": guesses or [],
        "solution": solution,
        "timestamp": 0,
    }


def test_aggregate_empty_games_returns_zeroed_overall():
    result = aggregate_games([])
    assert result["overall"] == {"total": 0, "solved": 0, "resolution_rate_pct": None, "histogram_pct": {}}
    assert result["by_length"] == []
    assert result["by_letter"] == []
    assert result["failed_games"] == []


def test_aggregate_overall_resolution_rate_and_histogram():
    games = [
        make_game("R", 5, 2, True),
        make_game("R", 5, 4, True),
        make_game("R", 5, 6, False, outcome="not_solved", guesses=["RIVER", "RIVAL"]),
        make_game("R", 5, 1, False, outcome="candidates_exhausted"),
    ]
    result = aggregate_games(games)
    overall = result["overall"]
    assert overall["total"] == 4
    assert overall["solved"] == 2
    assert overall["resolution_rate_pct"] == 50.0
    assert overall["histogram_pct"]["<= 2"] == 25.0  # 1/4 solved in <=2
    assert overall["histogram_pct"]["<= 4"] == 50.0  # 2/4 solved in <=4
    assert overall["histogram_pct"]["<= 6"] == 50.0  # only 2 solved at all
    assert overall["histogram_pct"]["echec"] == 50.0  # 2/4 unsolved


def test_aggregate_by_length_buckets_default_size_two():
    games = [
        make_game("A", 5, 3, True),
        make_game("B", 6, 2, True),
        make_game("C", 8, 5, False, outcome="not_solved"),
    ]
    result = aggregate_games(games, bucket_size=2)
    labels = {row["length_range"]: row for row in result["by_length"]}
    assert "5-6" in labels
    assert labels["5-6"]["total"] == 2
    assert labels["5-6"]["resolution_rate_pct"] == 100.0
    assert "8" in labels  # bucket restant seul (longueur unique)
    assert labels["8"]["total"] == 1
    assert labels["8"]["resolution_rate_pct"] == 0.0


def test_aggregate_by_letter_breakdown():
    games = [
        make_game("R", 5, 2, True),
        make_game("R", 6, 3, True),
        make_game("P", 7, 6, False, outcome="not_solved"),
    ]
    result = aggregate_games(games)
    by_letter = {row["letter"]: row for row in result["by_letter"]}
    assert by_letter["R"]["total"] == 2
    assert by_letter["R"]["resolution_rate_pct"] == 100.0
    assert by_letter["P"]["total"] == 1
    assert by_letter["P"]["resolution_rate_pct"] == 0.0


def test_aggregate_failed_games_lists_attempted_guesses_not_solved_only():
    games = [
        make_game("R", 5, 2, True),  # résolue, ne doit pas apparaître
        make_game(
            "P", 8, 6, False, outcome="not_solved", guesses=["PAROLE", "PATATE", "PILOTE"]
        ),
        make_game("Z", 5, 0, False, outcome="candidates_exhausted", guesses=[]),
    ]
    result = aggregate_games(games)
    failed = {row["letter"]: row for row in result["failed_games"]}
    assert set(failed) == {"P", "Z"}
    assert failed["P"]["length"] == 8
    assert failed["P"]["guesses_attempted"] == ["PAROLE", "PATATE", "PILOTE"]
    assert failed["Z"]["outcome"] == "candidates_exhausted"


def test_record_game_persists_and_aggregate_reads_it_back(tmp_path):
    from stats_store import aggregate, record_game

    path = tmp_path / "stats.json"
    record_game("R", 5, 2, True, "solved", ["RIVET", "RIVER"], path=path)
    record_game("P", 8, 6, False, "not_solved", ["PAROLE"], path=path)

    result = aggregate(path=path)
    assert result["overall"]["total"] == 2
    assert result["overall"]["solved"] == 1


# --- Tâche 4 : distribution des mots solutions ---


def test_aggregate_solutions_empty_input():
    result = aggregate_solutions([])
    assert result == {
        "n_solutions_recorded": 0,
        "n_distinct_solutions": 0,
        "length_distribution": [],
        "letter_distribution": [],
        "repeated_solutions": [],
        "has_observed_repeats": False,
    }


def test_aggregate_solutions_ignores_unsolved_and_missing_solution_games():
    games = [
        make_game("R", 5, 2, True, solution="RIVET"),
        make_game("P", 8, 6, False, outcome="not_solved", solution=None),  # non résolue
        make_game("Z", 5, 3, True, solution=None),  # résolue mais champ absent (ancien format)
    ]
    result = aggregate_solutions(games)
    assert result["n_solutions_recorded"] == 1
    assert result["n_distinct_solutions"] == 1


def test_aggregate_solutions_length_and_letter_distribution():
    games = [
        make_game("R", 5, 2, True, solution="RIVET"),
        make_game("R", 5, 3, True, solution="RADIO"),
        make_game("P", 7, 4, True, solution="PATATE" + "S"),  # 7 lettres
    ]
    result = aggregate_solutions(games)
    assert result["n_solutions_recorded"] == 3
    length_by_key = {row["length"]: row for row in result["length_distribution"]}
    assert length_by_key[5]["count"] == 2
    assert length_by_key[5]["pct"] == round(100 * 2 / 3, 1)
    assert length_by_key[7]["count"] == 1

    letter_by_key = {row["letter"]: row for row in result["letter_distribution"]}
    assert letter_by_key["R"]["count"] == 2
    assert letter_by_key["P"]["count"] == 1


def test_aggregate_solutions_detects_repeated_words_without_assuming_them():
    games = [
        make_game("R", 5, 2, True, solution="RIVET"),
        make_game("R", 5, 4, True, solution="RIVET"),  # même mot revu
        make_game("R", 5, 3, True, solution="RADIO"),
    ]
    result = aggregate_solutions(games)
    assert result["has_observed_repeats"] is True
    assert result["repeated_solutions"] == [{"word": "RIVET", "count": 2}]
    assert result["n_distinct_solutions"] == 2  # RIVET, RADIO
    assert result["n_solutions_recorded"] == 3  # mais 3 parties au total


def test_aggregate_solutions_no_repeats_reported_honestly():
    """Ne doit jamais affirmer un pool cyclique/limité s'il n'y a aucune répétition
    observée dans les données — se contente de rapporter ce qui a été mesuré."""
    games = [
        make_game("R", 5, 2, True, solution="RIVET"),
        make_game("P", 7, 3, True, solution="PARURE"),
    ]
    result = aggregate_solutions(games)
    assert result["has_observed_repeats"] is False
    assert result["repeated_solutions"] == []
