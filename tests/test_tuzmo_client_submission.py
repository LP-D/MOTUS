"""Non-régression du correctif "coups ambigus en cascade" (diagnostic du 23/09/2026).

Preuve réseau ayant motivé le correctif (scripts/diagnose_guess_pipeline.py,
docs/diagnostics/2026-09-23_cascade_rejets.md) : après un vrai rejet serveur
(EMBLAYENT -> INVALID_WORD), Tuzmo ne vide PAS la ligne. Les lettres du mot
suivant étaient ignorées (ligne pleine) et Entrée renvoyait EMBLAYENT 19 fois de
suite ; l'ancien client attribuait chaque rejet au nouveau mot (EGALEMENT,
ETALEMENT, ECLATANTE... mis en liste noire à tort).

`FakeTusmo` reproduit fidèlement ce comportement observé (ligne conservée après
rejet, Entrée sans requête si la ligne est incomplète) et publie chaque requête
dans un NetworkMonitor, comme le vrai navigateur.
"""
from __future__ import annotations

import time

import pytest

import bot.tuzmo_client as tc
from bot.network_monitor import ApiCall, NetworkMonitor
from bot.tuzmo_client import (
    BACKSPACE_KEY,
    ENTER_KEY,
    GameStateError,
    GuessInputError,
    GuessNotSentError,
    ThrottlingDetectedError,
    TuzmoClient,
    WordRejectedError,
)
from motus_solver.feedback import pattern_string

CODE_TO_API = {"2": "correct", "1": "present", "0": "absent"}


class FakeButton:
    def __init__(self, label, game):
        self.label, self.game = label, game

    def text_content(self):
        return self.label

    def click(self, timeout=None):
        self.game.press(self.label)


class FakeTusmo:
    """Page + serveur Tusmo factices, comportement calqué sur l'observation réelle."""

    def __init__(self, target, invalid=(), status=200, headers=None, latency=0.1,
                 backspace_works=True, drop_letters=0, send_requests=True):
        self.target = target
        self.invalid = set(invalid)
        self.status, self.headers, self.latency = status, headers or {}, latency
        self.backspace_works = backspace_works
        self.drop_letters = drop_letters  # nb de clics lettres perdus (clavier bloqué)
        self.send_requests = send_requests
        self.row = target[0]
        self.monitor = NetworkMonitor(self)
        self.sent: list[str] = []

    # --- API "Page" minimale utilisée par TuzmoClient / NetworkMonitor ---
    def on(self, event, handler):
        pass

    def query_selector_all(self, selector):
        labels = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + [ENTER_KEY, BACKSPACE_KEY]
        return [FakeButton(label, self) for label in labels]

    def wait_for_timeout(self, ms):
        pass

    def wait_for_function(self, *args, **kwargs):
        return None

    # --- logique de jeu ---
    def press(self, label):
        if label == BACKSPACE_KEY:
            if self.backspace_works and len(self.row) > 1:
                self.row = self.row[:-1]
        elif label == ENTER_KEY:
            self._enter()
        elif len(self.row) < len(self.target):
            if self.drop_letters > 0:
                self.drop_letters -= 1
                return
            self.row += label

    def _enter(self):
        if len(self.row) < len(self.target) or not self.send_requests:
            return  # rejet côté client ("pas assez de lettres") : AUCUNE requête
        word = self.row
        self.sent.append(word)
        now = time.time()
        call = ApiCall(kind="guess", method="POST", url="https://www.tusmo.xyz/api/game/x/guess",
                       guess=word, t_sent=now - self.latency, t_received=now,
                       status=self.status, rate_limit_headers=dict(self.headers))
        if self.status != 200:
            call.body = {}
        elif word in self.invalid:
            call.body = {"error": "INVALID_WORD"}  # ligne NON vidée (comportement réel)
        else:
            pattern = pattern_string(word, self.target)
            call.body = {"result": [CODE_TO_API[c] for c in pattern]}
            self.row = self.target[0]  # coup accepté : nouvelle ligne
        self.monitor.calls.append(call)


def make_client(game: FakeTusmo) -> TuzmoClient:
    return TuzmoClient(game, monitor=game.monitor, min_request_gap_s=(0.0, 0.0))


FAST = {"letter_delay": (0, 0), "enter_delay": (0, 0)}


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    monkeypatch.setattr(tc, "REQUEST_SENT_TIMEOUT_S", 0.2)


def test_word_after_real_rejection_is_really_sent_not_the_stale_one():
    """Scénario exact du diagnostic : EMBLAYENT rejeté, puis EGALEMENT doit être
    réellement envoyé (et accepté), pas EMBLAYENT une seconde fois."""
    game = FakeTusmo(target="ENTRAINES", invalid={"EMBLAYENT"})
    client = make_client(game)

    with pytest.raises(WordRejectedError) as rejected:
        client.submit_guess("EMBLAYENT", **FAST)
    assert rejected.value.word == "EMBLAYENT"

    pattern = client.submit_guess("EGALEMENT", **FAST)
    assert game.sent == ["EMBLAYENT", "EGALEMENT"]
    assert pattern == pattern_string("EGALEMENT", "ENTRAINES")


def test_no_cascade_over_many_consecutive_candidates():
    """Jamais deux fois le même mot envoyé après un rejet : chaque candidat
    successif est réellement soumis au serveur (plus de cascade)."""
    game = FakeTusmo(target="ENTRAINES", invalid={"EMBLAYENT", "EMPLANTEE", "EMPLANTAT"})
    client = make_client(game)
    for word in ["EMBLAYENT", "EMPLANTEE", "EMPLANTAT"]:
        with pytest.raises(WordRejectedError):
            client.submit_guess(word, **FAST)
    client.submit_guess("EGALEMENT", **FAST)
    assert game.sent == ["EMBLAYENT", "EMPLANTEE", "EMPLANTAT", "EGALEMENT"]


def test_stale_word_sent_raises_input_error_never_blames_new_word():
    """Si la ligne ne peut pas être vidée (ex. retour arrière inopérant), le
    serveur reçoit l'ancien mot : le client doit lever GuessInputError, JAMAIS
    WordRejectedError sur le nouveau mot (sinon liste noire à tort)."""
    game = FakeTusmo(target="ENTRAINES", invalid={"EMBLAYENT"}, backspace_works=False)
    client = make_client(game)
    with pytest.raises(WordRejectedError):
        client.submit_guess("EMBLAYENT", **FAST)

    with pytest.raises(GuessInputError) as err:
        client.submit_guess("EGALEMENT", **FAST)
    assert err.value.intended == "EGALEMENT"
    assert err.value.sent == "EMBLAYENT"


def test_dropped_letter_clicks_mean_no_request_and_not_a_rejection():
    """Clics perdus (clavier bloqué) -> ligne incomplète -> Entrée n'envoie rien :
    GuessNotSentError, pas un rejet du mot."""
    game = FakeTusmo(target="ENTRAINES", drop_letters=2)
    client = make_client(game)
    with pytest.raises(GuessNotSentError):
        client.submit_guess("EGALEMENT", **FAST)
    assert game.sent == []

    # la ligne partielle est nettoyée avant le nouvel essai, qui aboutit
    pattern = client.submit_guess("EGALEMENT", **FAST)
    assert game.sent == ["EGALEMENT"]
    assert len(pattern) == 9


def test_feedback_comes_from_server_result_including_repeated_letters():
    """Le pattern retourné est celui du serveur (ici un mot à lettres répétées)."""
    game = FakeTusmo(target="PIERRES")
    client = make_client(game)
    pattern = client.submit_guess("PERRIER", **FAST)
    assert pattern == pattern_string("PERRIER", "PIERRES")
    assert client.read_feedback() == pattern


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 429},
        {"headers": {"Retry-After": "30"}},
        {"latency": 11.0},
    ],
    ids=["http_429", "retry_after", "latency_over_10s"],
)
def test_throttling_signals_raise_emergency_stop(kwargs):
    game = FakeTusmo(target="ENTRAINES", **kwargs)
    client = make_client(game)
    with pytest.raises(ThrottlingDetectedError):
        client.submit_guess("EGALEMENT", **FAST)


def test_other_server_error_is_game_state_error_not_rejection():
    game = FakeTusmo(target="ENTRAINES")
    client = make_client(game)
    original = game._enter

    def game_over_enter():
        original()
        game.monitor.calls[-1].body = {"error": "GAME_OVER"}

    game._enter = game_over_enter
    with pytest.raises(GameStateError):
        client.submit_guess("EGALEMENT", **FAST)


def test_client_never_sends_faster_than_validated_rate(monkeypatch):
    """Le client attend au besoin pour respecter l'écart minimal entre deux
    requêtes (débit validé) — jamais plus rapide."""
    slept = []
    monkeypatch.setattr(tc.time, "sleep", lambda s: slept.append(s))
    game = FakeTusmo(target="ENTRAINES", invalid={"EMBLAYENT"})
    client = TuzmoClient(game, monitor=game.monitor, min_request_gap_s=(2.0, 2.0))
    with pytest.raises(WordRejectedError):
        client.submit_guess("EMBLAYENT", **FAST)
    client.submit_guess("EGALEMENT", **FAST)
    # la 2e requête arrive juste après la 1re : le client a dû attendre ~2s
    assert any(s > 1.5 for s in slept)
