from motus_solver.feedback import (
    code_to_pattern,
    pattern_codes,
    pattern_string,
    pattern_to_code,
    words_to_matrix,
)


def test_pattern_all_correct_when_guess_is_solution():
    assert pattern_string("RIVER", "RIVER") == "22222"


def test_pattern_duplicate_letters_errer_river():
    # ERRER vs RIVER : positions 3,4 exactes (E,R) ; le R restant en position 0-2
    # de RIVER (R,I,V) absorbe le premier R de gauche du guess (position1=présent),
    # le second R (position2) n'a plus de R disponible -> absent.
    assert pattern_string("ERRER", "RIVER") == "01022"


def test_pattern_to_code_roundtrip():
    for pattern in ["00000", "22222", "01210"]:
        code = pattern_to_code(pattern)
        assert code_to_pattern(code, len(pattern)) == pattern


def test_pattern_codes_vectorized_matches_pattern_string():
    solutions = ["RIVER", "LIVRE", "TABLE"]
    arr = words_to_matrix(solutions)
    codes = pattern_codes("ERRER", arr)
    for solution, code in zip(solutions, codes):
        assert code_to_pattern(int(code), 5) == pattern_string("ERRER", solution)
