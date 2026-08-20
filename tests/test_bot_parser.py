import pytest

from bot.parser import cell_state, row_pattern


def test_cell_state_absent():
    assert cell_state("cell cell--absent", None) == "0"


def test_cell_state_present():
    assert cell_state("cell cell--revealed", "cell__mark cell__mark--present anim-pop") == "1"


def test_cell_state_correct():
    assert cell_state("cell cell--revealed", "cell__mark cell__mark--correct anim-stamp") == "2"


def test_cell_state_unknown_raises():
    with pytest.raises(ValueError):
        cell_state("cell cell--revealed", None)


def test_row_pattern_matches_real_epargne_sample():
    # Échantillon réel capturé dans data/tuzmo_dom_sample.html (essai "EPARGNE").
    cells = [
        ("cell cell--revealed", "cell__mark cell__mark--correct anim-stamp"),
        ("cell cell--absent", None),
        ("cell cell--revealed", "cell__mark cell__mark--present anim-pop"),
        ("cell cell--revealed", "cell__mark cell__mark--present anim-pop"),
        ("cell cell--absent", None),
        ("cell cell--revealed", "cell__mark cell__mark--present anim-pop"),
        ("cell cell--revealed", "cell__mark cell__mark--present anim-pop"),
    ]
    assert row_pattern(cells) == "2011011"
