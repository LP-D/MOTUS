from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_tusmo_training as stt  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.feedback import pattern_string  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

NAMES = {"2": "correct", "1": "present", "0": "absent"}


class FakeTrainingAPI:
    """Imite /api/training : solution fixe, mots refusés, note 100 si le coup est le
    meilleur mot déclaré, 50 sinon."""

    def __init__(self, answer, invalid=(), best="", max_tries=6):
        self.answer, self.invalid, self.best = answer, set(invalid), best
        self.session = {"id": "s1", "wordLen": len(answer), "maxTries": max_tries, "preview": True,
                        "firstLetter": answer[0], "guesses": [], "status": "playing"}
        self.calls = []

    def guess(self, session_id, word):
        self.calls.append(("guess", word))
        if word in self.invalid:
            return {"error": "INVALID_WORD"}
        result = [NAMES[c] for c in pattern_string(word, self.answer)]
        won = word == self.answer
        self.session["guesses"].append({"word": word, "result": result, "percent": 100 if word == self.best else 50,
                                        "winning": won})
        if won:
            self.session["status"] = "won"
        elif len(self.session["guesses"]) >= self.session["maxTries"]:
            self.session["status"] = "lost"
        return dict(self.session)

    def preview(self, session_id, word):
        self.calls.append(("preview", word))
        return {"error": "TOO_MANY_PREVIEWS"}

    def report(self, session_id):
        self.calls.append(("report", None))
        return {"won": self.session["status"] == "won", "answer": self.answer,
                "tries": len(self.session["guesses"]),
                "moves": [{"word": g["word"], "percent": g["percent"], "bestWord": self.best, "candidatesBefore": 3}
                          for g in self.session["guesses"]]}


WORDS = ["RASOIR", "RASSIS", "RESAIT", "RIVAGE", "ROULER"]


def make_solver():
    return Solver(letter="R", length=6, corpus=Corpus(WORDS), strategy="entropy_pure", endgame=False)


def test_result_to_pattern():
    assert stt.result_to_pattern(["correct", "present", "absent"]) == "210"


def test_play_game_wins_and_fetches_report():
    api = FakeTrainingAPI("RASSIS")
    record = stt.play_game(api, make_solver(), dict(api.session))
    assert record["status"] == "won"
    assert record["moves"][-1]["word"] == "RASSIS" and record["moves"][-1]["pattern"] == "222222"
    assert record["report"]["answer"] == "RASSIS"
    assert api.calls[-1] == ("report", None)


def test_rejected_word_is_discarded_and_replaced_in_same_move():
    solver = make_solver()
    first = solver.suggest(top_n=1)[0][0]
    api = FakeTrainingAPI("RASSIS", invalid={first})
    record = stt.play_game(api, solver, dict(api.session))
    assert record["rejected"] == [first]
    assert first not in [m["word"] for m in record["moves"]]
    assert record["status"] == "won"


def test_preview_stops_after_too_many_previews():
    api = FakeTrainingAPI("RASSIS")
    stt.play_game(api, make_solver(), dict(api.session), use_preview=True)
    assert [c for c in api.calls if c[0] == "preview"] == [api.calls[0]]


def test_summarize_uses_tusmo_reports():
    api = FakeTrainingAPI("RASSIS", best="RASSIS")
    record = stt.play_game(api, make_solver(), dict(api.session))
    text = stt.summarize([record, {"report": None, "moves": []}])
    assert "parties terminées : 1 (gagnées : 1, 100%)" in text
    assert "coup 1 : note moyenne" in text
    assert stt.summarize([]) == "aucune partie terminée."
