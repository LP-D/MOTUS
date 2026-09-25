"""Duel classé simulé, toi contre un bot, en temps réel (page /duel du dashboard).

Tout est local : le mot est tiré parmi les solutions réelles connues, le moteur de
règles est motus_solver.duel, le bot est un modèle de motus_solver.agents. Aucune
requête n'est envoyée à Tuzmo.

Le bot joue « en direct » : chaque coup est décidé au début de sa réflexion, avec ce
qu'il voit alors (tes couleurs, ton éventuelle victoire), puis posé après la durée
tirée de son profil de vitesse. L'état avance à chaque requête du navigateur.
"""
from __future__ import annotations

import json
import random
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from motus_solver.agents import (  # noqa: E402
    AGENTS,
    MAX_REJECTIONS,
    REJECT_COST_S,
    SPEEDS,
    AgentView,
    SolverContext,
    make_agent,
)
from motus_solver.duel import CHASE_SECONDS, Duel, DuelError  # noqa: E402
from motus_solver.inference import OpponentProfile  # noqa: E402

DATA = ROOT_DIR / "data"
HISTORY = DATA / "duel_history.jsonl"
# ton profil vu par les bots qui apprennent (entropy_pure_infos) : tes mots d'ouverture
PROFILES = DATA / "duel_profiles.json"
HUMAN_NAME = "toi"
COUNTDOWN_S = 3.0
HUMAN, BOT = 0, 1
MAX_SESSIONS = 20

router = APIRouter()
_ctx: SolverContext | None = None
_ctx_lock = threading.Lock()
_sessions: dict[str, "LiveDuel"] = {}
_profiles: dict[str, OpponentProfile] | None = None


def profiles() -> dict[str, OpponentProfile]:
    global _profiles
    if _profiles is None:
        raw = json.loads(PROFILES.read_text(encoding="utf-8")) if PROFILES.exists() else {}
        _profiles = {k: OpponentProfile.from_dict(v) for k, v in raw.items()}
    return _profiles


def _save_profiles() -> None:
    try:
        PROFILES.write_text(json.dumps({k: v.to_dict() for k, v in profiles().items()}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    except OSError:
        pass


def context() -> SolverContext:
    global _ctx
    with _ctx_lock:
        if _ctx is None:
            _ctx = SolverContext.load(DATA)
        return _ctx


def answer_pool() -> list[str]:
    words = {json.loads(line)["answer"].upper()
             for line in (DATA / "revealed_solutions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    stats = DATA / "dashboard_stats.json"
    if stats.exists():
        words |= {g["solution"].upper() for g in json.loads(stats.read_text(encoding="utf-8")) if g.get("solution")}
    return sorted(words)


class LiveDuel:
    def __init__(self, word: str, bot: str, speed: str, ctx: SolverContext):
        self.id = uuid.uuid4().hex[:12]
        self.duel = Duel(word)
        self.bot_name, self.speed_name = bot, speed
        self.agent = make_agent(bot)
        self.agent.opponent = HUMAN_NAME
        if hasattr(self.agent, "profiles"):
            self.agent.profiles = profiles()  # mémoire partagée d'un duel à l'autre
        self.speed = SPEEDS[speed]
        self.ctx = ctx
        self.rng = random.Random()
        self.agent.new_game(self.duel.letter, self.duel.length, ctx, ctx.known_valid - {self.duel.word})
        self.started = time.time() + COUNTDOWN_S
        self.pending: tuple[float, str] | None = None
        self.bot_rejections = 0
        self.saved = False
        self.lock = threading.Lock()
        self._schedule_bot(0.0)

    def clock(self) -> float:
        return time.time() - self.started

    def _schedule_bot(self, t: float) -> None:
        if not self.duel.can_play(BOT):
            self.pending = None
            return
        me = self.duel.players[BOT]
        view = AgentView(self.duel.letter, self.duel.length, [(g, p) for _, g, p in me.guesses],
                         self.duel.opponent_rows(BOT), self.duel.to_beat(BOT))
        delay = self.speed.duration(self.rng, me.attempts + 1, self.duel.length)
        guess = self.agent.choose(view)
        for _ in range(MAX_REJECTIONS):
            if self.ctx.accepts(guess, self.duel.word):
                break
            self.bot_rejections += 1
            self.agent.reject(guess)
            delay += REJECT_COST_S + self.speed.per_letter * (self.duel.length - 1)
            guess = self.agent.choose(view)
        self.pending = (t + delay, guess)

    def tick(self) -> None:
        """Pose les coups du bot arrivés à échéance, puis fait avancer l'horloge."""
        now = self.clock()
        while self.pending is not None and self.pending[0] <= now and self.duel.result is None:
            t, guess = self.pending
            if self.duel.advance(t) is not None:
                break
            self.duel.submit(BOT, guess, t)
            self._schedule_bot(t)
        if now >= 0:
            self.duel.advance(now)
        if self.duel.result is not None and not self.saved:
            self._save()

    def human_guess(self, word: str) -> tuple[bool, str]:
        now = self.clock()
        if now < 0:
            return False, "attends le départ"
        word = word.upper()
        if not word.isalpha() or len(word) != self.duel.length or word[0] != self.duel.letter:
            return False, f"{self.duel.length} lettres, commençant par {self.duel.letter}"
        if not self.ctx.is_playable(word):
            return False, "Mot inconnu"
        try:
            self.duel.submit(HUMAN, word, now)
        except DuelError as exc:
            return False, str(exc)
        if self.pending is None and self.duel.can_play(BOT):
            self._schedule_bot(now)
        return True, ""

    def _save(self) -> None:
        self.saved = True
        human_words = [g for _, g, _ in self.duel.players[HUMAN].guesses]
        if hasattr(self.agent, "profiles"):
            self.agent.observe(HUMAN_NAME, self.duel.letter, self.duel.length, human_words)
            _save_profiles()
        res = self.duel.result
        entry = {"t": time.time(), "bot": self.bot_name, "speed": self.speed_name, "word": self.duel.word,
                 "winner": {HUMAN: "toi", BOT: "bot", None: "nul"}[res.winner], "reason": res.reason,
                 "attempts": [p.attempts for p in self.duel.players],
                 "solve_times": [None if p.solved_at is None else round(p.solved_at, 2) for p in self.duel.players],
                 "guesses": [[g for _, g, _ in p.guesses] for p in self.duel.players],
                 "bot_rejections": self.bot_rejections}
        try:
            with HISTORY.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def state(self) -> dict:
        d = self.duel
        now = self.clock()
        ended = d.result is not None
        human, bot = d.players[HUMAN], d.players[BOT]
        return {
            "id": self.id, "letter": d.letter, "length": d.length, "bot": self.bot_name, "speed": self.speed_name,
            "clock": round(now, 2), "countdown": max(0.0, round(-now, 2)),
            "me": {"rows": [{"word": g, "pattern": p} for _, g, p in human.guesses], "solved": human.solved,
                   "failed": human.failed},
            # couleurs seulement pendant la partie, comme sur Tuzmo ; lettres à la fin
            "bot_side": {"rows": [{"word": g if ended else None, "pattern": p} for _, g, p in bot.guesses],
                         "solved": bot.solved, "failed": bot.failed,
                         "thinking": self.pending is not None and not ended},
            "first_solver": {HUMAN: "toi", BOT: "bot", None: None}[d.first_solver],
            "chase_left": None if d.deadline is None or ended else round(max(0.0, d.deadline - now), 1),
            "to_beat": d.to_beat(HUMAN),
            "result": None if not ended else {
                "winner": {HUMAN: "toi", BOT: "bot", None: "nul"}[d.result.winner], "reason": d.result.reason,
                "word": d.word, "bot_rejections": self.bot_rejections},
        }


class NewDuelPayload(BaseModel):
    bot: str = "entropy_pure"
    speed: str = "humain"
    length: int | None = None


class GuessPayload(BaseModel):
    word: str


@router.get("/api/duel/options")
def options() -> JSONResponse:
    return JSONResponse({
        "bots": [{"name": n, "description": make_agent(n).description} for n in AGENTS],
        "speeds": [{"name": s.name, "description": s.description} for s in SPEEDS.values()],
        "chase_seconds": CHASE_SECONDS,
    })


@router.post("/api/duel/new")
def new_duel(payload: NewDuelPayload) -> JSONResponse:
    if payload.bot not in AGENTS or payload.speed not in SPEEDS:
        return JSONResponse({"error": "bot ou vitesse inconnus"}, status_code=400)
    words = [w for w in answer_pool() if payload.length is None or len(w) == payload.length]
    if not words:
        return JSONResponse({"error": "aucun mot de cette longueur"}, status_code=400)
    ctx = context()
    live = LiveDuel(random.choice(words), payload.bot, payload.speed, ctx)
    if len(_sessions) >= MAX_SESSIONS:
        _sessions.pop(next(iter(_sessions)))
    _sessions[live.id] = live
    return JSONResponse(live.state())


def _get(duel_id: str) -> LiveDuel | None:
    return _sessions.get(duel_id)


@router.get("/api/duel/{duel_id}")
def get_duel(duel_id: str) -> JSONResponse:
    live = _get(duel_id)
    if live is None:
        return JSONResponse({"error": "duel introuvable"}, status_code=404)
    with live.lock:
        live.tick()
        return JSONResponse(live.state())


@router.post("/api/duel/{duel_id}/guess")
def guess(duel_id: str, payload: GuessPayload) -> JSONResponse:
    live = _get(duel_id)
    if live is None:
        return JSONResponse({"error": "duel introuvable"}, status_code=404)
    with live.lock:
        live.tick()
        ok, error = live.human_guess(payload.word)
        live.tick()
        body = live.state()
        if not ok:
            body["error"] = error
        return JSONResponse(body, status_code=200 if ok else 422)


@router.get("/api/duel-history")
def history() -> JSONResponse:
    """Ton bilan contre chaque bot (victoires, nulles, défaites)."""
    rows = []
    if HISTORY.exists():
        rows = [json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_bot: dict[str, dict] = {}
    for r in rows:
        b = by_bot.setdefault(f"{r['bot']}@{r['speed']}", {"toi": 0, "nul": 0, "bot": 0})
        b[r["winner"]] += 1
    return JSONResponse({"duels": len(rows), "by_bot": by_bot, "last": rows[-10:][::-1]})
