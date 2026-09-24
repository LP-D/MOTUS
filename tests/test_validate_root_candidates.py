"""Tâche 2 (validation ciblée des premiers coups) : comportement du script face à un
faux serveur Tuzmo — classement des mots, clôture des parties, arrêts de sécurité."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_root_candidates as vrc  # noqa: E402


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeServer:
    """Parties successives imposées par `draws` ; `invalid` = mots refusés."""

    def __init__(self, draws, invalid, throttle_at=None, create_status="playing"):
        self.draws, self.invalid = list(draws), set(invalid)
        self.throttle_at, self.create_status = throttle_at, create_status
        self.calls, self.session = [], None

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url.replace(vrc.BASE_URL, ""), json))
        if self.throttle_at and len(self.calls) >= self.throttle_at:
            return FakeResponse(429, {"error": "rate"})
        path = url.replace(vrc.BASE_URL, "")
        if path == "/api/me":
            return FakeResponse(200, {"id": "guest"})
        if path == "/api/game":
            letter, length = self.draws.pop(0) if self.draws else ("Z", 5)
            self.session = {"id": f"s{len(self.calls)}", "status": self.create_status, "firstLetter": letter,
                            "wordLength": length, "guesses": []}
            return FakeResponse(200, {"session": dict(self.session)})
        if path.endswith("/giveup"):
            self.session["status"] = "lost"
            return FakeResponse(200, {"session": {**self.session, "answer": "REVEL"}})
        word = json["guess"]
        if word in self.invalid:
            return FakeResponse(200, {"session": dict(self.session), "error": "INVALID_WORD"})
        self.session["guesses"].append({"word": word})
        return FakeResponse(200, {"session": dict(self.session), "result": ["absent"] * len(word)})


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    cache = {"R_5": {"word": "RIVER", "entropy": 1, "alternatives": [{"word": "RATER", "entropy": 1},
                                                                      {"word": "RADIO", "entropy": 1}]}}
    (data / "root_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    (data / "root_cache_entropy_pure.json").write_text(json.dumps(
        {"R_5": {"word": "ROBOT", "entropy": 1, "alternatives": [{"word": "RIVER", "entropy": 1}]}}), encoding="utf-8")
    (data / "known_valid_words.json").write_text(json.dumps(["RADIO"]), encoding="utf-8")
    (data / "known_invalid_words.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(vrc, "DATA", data)
    monkeypatch.setattr(vrc.time, "sleep", lambda s: None)
    args = type("A", (), {"max_requests": 50, "log": str(data / "log.jsonl")})()
    return data, args


def install(monkeypatch, server):
    monkeypatch.setattr(vrc.requests, "Session", lambda: type("S", (), {
        "headers": {}, "request": staticmethod(server.request)})())


def test_candidates_are_classified_and_session_closed(env, monkeypatch):
    data, args = env
    server = FakeServer(draws=[("R", 5)], invalid={"RATER"})
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert set(summary["valid_words"]) == {"RIVER", "ROBOT"}  # RADIO déjà connu : non retesté
    assert summary["invalid_words"] == ["RATER"]
    assert "RATER" in json.loads((data / "known_invalid_words.json").read_text(encoding="utf-8"))
    provenance = json.loads((data / "known_invalid_words_provenance.json").read_text(encoding="utf-8"))
    assert provenance["RATER"].startswith("INVALID_WORD vérifié serveur (validate_root_candidates")
    assert {"RIVER", "ROBOT"} <= set(json.loads((data / "known_valid_words.json").read_text(encoding="utf-8")))
    assert any(url.endswith("/giveup") for _, url, _ in server.calls)  # partie close
    assert summary["groups_fully_validated"] == 1 and summary["rejection_rate_pct"] == pytest.approx(33.3)


def test_throttling_stops_immediately(env, monkeypatch):
    _, args = env
    server = FakeServer(draws=[("R", 5)], invalid=set(), throttle_at=3)
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert summary["stop_reason"].startswith("ARRÊT D'URGENCE")
    assert len(server.calls) == 3  # aucune requête après le 429


def test_unplayable_sessions_never_loop(env, monkeypatch):
    _, args = env
    server = FakeServer(draws=[("R", 5)] * 10, invalid=set(), create_status="lost")
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert "non jouables" in summary["stop_reason"]
    assert summary["requests"] <= 3


def test_rejected_candidate_is_replaced_by_next_deep_ranked_word_in_same_session(env, monkeypatch):
    data, args = env
    # classement profond : RATER refusé -> RAMER (11e rang ici) entre en lice sans recalcul du cache
    deep = {"rankings": {"composite": {"R_5": ["RIVER", "RATER", "RADIO", "RAMER"]},
                         "entropy_pure": {"R_5": ["ROBOT", "RIVER"]}}}
    (data / "root_validation").mkdir()
    (data / "root_validation" / "deep_rankings.json").write_text(json.dumps(deep), encoding="utf-8")
    monkeypatch.setattr(vrc, "TARGET", 3)
    server = FakeServer(draws=[("R", 5)], invalid={"RATER"})
    install(monkeypatch, server)
    summary = vrc.run(args)
    guessed = [p["guess"] for _, url, p in server.calls if url.endswith("/guess")]
    assert guessed == ["RIVER", "ROBOT", "RATER", "RAMER"]  # alternance composite / entropy_pure par rang
    assert summary["deep_rankings"] and summary["groups_fully_validated"] == 1


def test_draws_are_logged_and_first_observation_flagged(env, monkeypatch):
    data, args = env
    (data / "group_draws.jsonl").write_text(json.dumps({"t": 1, "letter": "R", "length": 5, "source": "x"}) + "\n",
                                            encoding="utf-8")
    server = FakeServer(draws=[("R", 5), ("Z", 5)], invalid=set())
    monkeypatch.setattr(vrc, "IDLE_SESSIONS_STOP", 1)
    args.idle_sessions_stop = 1
    install(monkeypatch, server)
    cache = {"R_5": {"word": "RIVER", "entropy": 1, "alternatives": []},
             "Z_5": {"word": "ZEBRE", "entropy": 1, "alternatives": []}}
    (data / "root_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    (data / "root_cache_entropy_pure.json").write_text(json.dumps(cache), encoding="utf-8")
    summary = vrc.run(args)
    lines = [json.loads(line) for line in (data / "group_draws.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["letter"], r["length"], r.get("first_observation")) for r in lines[1:]] == [("R", 5, False), ("Z", 5, True)]
    assert summary["first_observations"] == ["Z_5"]


def test_idle_sessions_stop_when_remaining_groups_are_never_drawn(env, monkeypatch):
    data, args = env
    args.idle_sessions_stop = 3
    known = json.dumps(["RIVER", "RATER", "RADIO", "ROBOT"])
    (data / "known_valid_words.json").write_text(known, encoding="utf-8")
    (data / "root_cache.json").write_text(json.dumps({
        "R_5": {"word": "RIVER", "entropy": 1, "alternatives": []},
        "Z_5": {"word": "ZEBRE", "entropy": 1, "alternatives": []}}), encoding="utf-8")
    server = FakeServer(draws=[("R", 5)] * 10, invalid=set())  # Z_5 jamais tiré
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert "sans candidat à tester" in summary["stop_reason"]
    assert summary["sessions"] == 3 and summary["groups_still_pending"] == ["Z_5"]


def test_revealed_answer_becomes_known_valid(env, monkeypatch):
    data, args = env
    server = FakeServer(draws=[("R", 5)], invalid=set())
    install(monkeypatch, server)
    summary = vrc.run(args)
    assert summary["revealed"] == ["REVEL"]
    assert "REVEL" in json.loads((data / "known_valid_words.json").read_text(encoding="utf-8"))


def test_latency_over_5s_is_an_emergency_stop(env, monkeypatch):
    _, args = env
    server = FakeServer(draws=[("R", 5)], invalid=set())
    install(monkeypatch, server)
    clock = iter([0.0, 0.0, 6.0, 6.0, 6.0])  # 1re requête : 6 s de latence
    monkeypatch.setattr(vrc.time, "time", lambda: next(clock, 100.0))
    summary = vrc.run(args)
    assert summary["stop_reason"].startswith("ARRÊT D'URGENCE") and "6.0s" in summary["stop_reason"]
    assert len(server.calls) == 1


def test_win_starts_next_word_and_logs_a_continuation_draw(env, monkeypatch):
    data, args = env

    class WinningServer(FakeServer):
        def request(self, method, url, json=None, timeout=None):
            path = url.replace(vrc.BASE_URL, "")
            if path.endswith("/guess") and json["guess"] == "RIVER":  # mot trouvé : le suivant commence
                self.calls.append((method, path, json))
                self.session = {"id": self.session["id"], "status": "playing", "firstLetter": "Z",
                                "wordLength": 5, "guesses": []}
                return FakeResponse(200, {"session": dict(self.session), "result": ["correct"] * 5})
            return super().request(method, url, json, timeout)

    server = WinningServer(draws=[("R", 5)], invalid=set())
    install(monkeypatch, server)
    summary = vrc.run(args)
    lines = [json.loads(line) for line in (data / "group_draws.jsonl").read_text(encoding="utf-8").splitlines()]
    # R tiré, RIVER trouvé -> Z enchaîné dans la même partie (puis nouvelles parties)
    assert [(r["letter"], r["continuation"]) for r in lines[:2]] == [("R", False), ("Z", True)]
    assert lines[1]["session_id"] == lines[0]["session_id"]
    assert summary["draws"] == len(lines)


def test_stale_keys_detects_blocklisted_candidate_and_ranking_drift():
    cache = {"R_5": {"word": "RIVER", "alternatives": [{"word": "RATER"}]},
             "S_5": {"word": "SABLE", "alternatives": [{"word": "SALON"}]},
             "T_5": {"word": "TABLE", "alternatives": [{"word": "TALON"}]}}
    deep = {"S_5": ["SALON", "SABLE", "SIROP"], "T_5": ["TABLE", "TALON", "TIRER"]}
    # R_5 : repli refusé ; S_5 : ordre du classement changé ; T_5 : à jour
    assert vrc.stale_keys(cache, {"RATER"}, deep, target=2) == ["R_5", "S_5"]


def test_end_round_refreshes_caches_rankings_and_corpus(env, monkeypatch):
    data, args = env
    words = ["RIVER", "RATER", "RADIO", "ROBOT", "RUSES", "REINE", "RASER", "RUBAN", "RAMER", "REVER",
             "RIRES", "RONDE", "RAPES"]
    (data / "corpus_fr.txt").write_text("\n".join(words) + "\n", encoding="utf-8")
    (data / "known_invalid_words.json").write_text(json.dumps(["RATER"]), encoding="utf-8")
    (data / "revealed_solutions.jsonl").write_text(json.dumps({"answer": "RAGER"}) + "\n", encoding="utf-8")
    (data / "group_draws.jsonl").write_text(json.dumps({"t": 1, "letter": "R", "length": 5, "source": "x"}) + "\n",
                                            encoding="utf-8")
    (data / "root_validation").mkdir()
    args.rankings, args.workers, args.depth = str(data / "root_validation" / "deep.json"), 1, 12
    report = vrc.end_round(args)
    assert report["added_to_corpus"] == ["RAGER"]
    assert "RAGER" in (data / "corpus_fr.txt").read_text(encoding="utf-8").split()
    # composite : RATER (refusé) en cache -> recalculé ; entropy_pure : rien de refusé -> inchangé
    assert report["composite"]["refreshed"] == ["R_5"] and report["entropy_pure"]["refreshed"] == []
    comp = json.loads((data / "root_cache.json").read_text(encoding="utf-8"))["R_5"]
    top = [comp["word"], *(a["word"] for a in comp["alternatives"])]
    assert "RATER" not in top and len(top) == 10  # coup 1 + 9 replis, jamais plus
    ep = json.loads((data / "root_cache_entropy_pure.json").read_text(encoding="utf-8"))["R_5"]
    assert ep["word"] == "ROBOT"
    assert comp["draw_status"] == ep["draw_status"] == "observe"
    deep = json.loads((data / "root_validation" / "deep.json").read_text(encoding="utf-8"))["rankings"]
    assert len(deep["composite"]["R_5"]) == 12 and "RATER" not in deep["composite"]["R_5"]
    assert deep["composite"]["R_5"][:10] == top
