"""Tâche 2 : la victoire (et donc la solution enregistrée) doit être confirmée
UNIQUEMENT par le pattern du coup réellement soumis et accepté par Tuzmo (toutes
les cases "correct" — la seule information qui provienne effectivement du serveur
via le DOM), jamais par `Solver.is_solved()` seul, qui n'est qu'une déduction
locale (le pool de candidats restants s'est réduit à un par élimination) pouvant
être vraie sans que le mot effectivement joué soit le bon mot — cf. le correctif
dans dashboard/bot_runner.py::_play_one_game et bot/run_bot.py.

Utilise un TuzmoClient factice piloté par un "vrai mot cible" fixe : le pattern
qu'il renvoie est calculé avec `motus_solver.feedback.pattern_string`, exactement
comme le ferait le vrai jeu — donc ces tests exercent la vraie logique de
`_play_one_game` (Solver réel inclus), sans navigateur ni réseau."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bot_runner  # noqa: E402

from bot.tuzmo_client import WordRejectedError  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.feedback import pattern_string  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402


def make_client_cls(true_target: str, reject_words: frozenset[str] = frozenset()):
    """Fabrique une classe TuzmoClient factice fermée sur `true_target` : le
    feedback renvoyé est toujours calculé contre ce mot fixe, comme le ferait le
    vrai serveur Tuzmo contre le mot secret réel de la partie. `reject_words` :
    mots systématiquement rejetés ("Mot inconnu"), comme un mot réellement hors du
    dictionnaire de validation de Tuzmo (rejeté à chaque tentative, pas juste une
    fois — cohérent avec la vraie liste noire persistante)."""

    class ScriptedClient:
        def __init__(self, page):
            self.last_guess: str | None = None

        def get_first_letter(self) -> str:
            return true_target[0]

        def get_word_length(self) -> int:
            return len(true_target)

        def submit_guess(self, word, timer=None, timeout=None, letter_delay=None, enter_delay=None):
            word = word.upper()
            if word in reject_words:
                raise WordRejectedError(word)
            self.last_guess = word

        def read_feedback(self, timer=None) -> str:
            return pattern_string(self.last_guess, true_target)

    return ScriptedClient


def _patch_runner_io(monkeypatch, tmp_path):
    """Isole les effets de bord disque (jamais le vrai data/dashboard_bot_log.jsonl
    ni data/known_invalid_words.json ne doivent être touchés par ces tests)."""
    monkeypatch.setattr(bot_runner, "DEFAULT_LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(bot_runner, "DEFAULT_BLOCKLIST", tmp_path / "blocklist.json")


def test_solved_requires_actual_winning_pattern_not_just_candidate_elimination(monkeypatch, tmp_path):
    """Avec l'ancien code (`if solver.is_solved():`), ce scénario déclarait une
    victoire dès le 1er coup si celui-ci ne laissait qu'un seul candidat restant
    par élimination — même si ce coup n'était PAS le mot correct. Corpus à 2 mots
    conçu pour forcer exactement ce cas : quel que soit le mot proposé en premier
    par le solveur, le pattern réel (contre la vraie cible fixe) élimine l'autre
    candidat sans être lui-même gagnant, sauf si le solveur a deviné juste du
    premier coup (auquel cas c'est une vraie victoire, également correcte)."""
    true_target = "RIVER"
    corpus = Corpus(["RATER", "RIVER"])
    monkeypatch.setattr(bot_runner, "TuzmoClient", make_client_cls(true_target))
    _patch_runner_io(monkeypatch, tmp_path)

    recorded_calls = []
    monkeypatch.setattr(bot_runner, "record_game", lambda **kwargs: recorded_calls.append(kwargs))

    runner = bot_runner.BotRunner()
    result, blocklist = runner._play_one_game(page=None, corpus=corpus, root_cache=None, blocklist=set())

    assert result["solved"] is True
    assert result["outcome"] == "solved"
    # jamais résolu avec un mot autre que la vraie cible, même si l'élimination de
    # candidats aurait pu le laisser croire après un seul coup perdant
    assert len(recorded_calls) == 1
    assert recorded_calls[0]["solved"] is True
    assert recorded_calls[0]["solution"] == true_target


def test_rejected_guess_never_recorded_as_solution_and_fallback_still_confirms_correctly(monkeypatch, tmp_path):
    """Combine le fallback de rejet (recalcul dynamique + liste noire) ET la
    confirmation finale de la solution sur la même partie simulée : le mot rejeté
    ne doit jamais apparaître comme solution, la liste noire doit le contenir, et
    la partie doit malgré tout se conclure correctement sur le vrai mot cible."""
    words = ["RATER", "RIVER", "RAYER"]
    corpus = Corpus(words)
    # Détermine le tout 1er coup que le solveur proposera réellement (même corpus,
    # aucun historique) pour désigner CE mot précis comme "hors dictionnaire" —
    # garantit un rejet dès le 1er essai sans dépendre de l'ordre de scoring
    # composite (dont le détail interne n'est pas censé être testé ici) ni risquer
    # de rejeter accidentellement le vrai mot cible lui-même.
    probe = Solver(letter=words[0][0], length=len(words[0]), corpus=corpus, root_cache=None, blocklist=None)
    first_pick = probe.suggest(top_n=1)[0][0]
    true_target = next(w for w in words if w != first_pick)

    client_cls = make_client_cls(true_target, reject_words=frozenset({first_pick}))
    monkeypatch.setattr(bot_runner, "TuzmoClient", client_cls)
    _patch_runner_io(monkeypatch, tmp_path)

    recorded_calls = []
    monkeypatch.setattr(bot_runner, "record_game", lambda **kwargs: recorded_calls.append(kwargs))

    runner = bot_runner.BotRunner()
    result, blocklist = runner._play_one_game(page=None, corpus=corpus, root_cache=None, blocklist=set())

    assert result["solved"] is True
    assert len(recorded_calls) == 1
    call = recorded_calls[0]
    assert call["solved"] is True
    assert call["solution"] == true_target

    # le mot rejeté a bien été ajouté à la liste noire retournée...
    assert blocklist == {first_pick}
    # ...et n'est jamais le mot enregistré comme solution ni comme coup accepté
    assert call["solution"] != first_pick
    assert first_pick not in call["guesses"]
