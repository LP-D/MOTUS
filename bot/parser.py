from __future__ import annotations

ABSENT, PRESENT, CORRECT = "0", "1", "2"


def cell_state(cell_class: str, mark_class: str | None) -> str:
    """Traduit les classes CSS d'une case Tusmo en chiffre 0/1/2.

    Structure observée (data/tuzmo_dom_sample.html) :
    - absent  : <div class="cell cell--absent">              (pas de .cell__mark)
    - présent : <div class="cell cell--revealed"><span class="cell__mark cell__mark--present">
    - correct : <div class="cell cell--revealed"><span class="cell__mark cell__mark--correct">
    """
    if "cell--absent" in cell_class:
        return ABSENT
    if mark_class and "cell__mark--correct" in mark_class:
        return CORRECT
    if mark_class and "cell__mark--present" in mark_class:
        return PRESENT
    raise ValueError(f"état de case non reconnu (cell={cell_class!r}, mark={mark_class!r})")


def row_pattern(cells: list[tuple[str, str | None]]) -> str:
    return "".join(cell_state(cell_class, mark_class) for cell_class, mark_class in cells)
