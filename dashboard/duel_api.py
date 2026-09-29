"""Duel classé simulé en temps réel (page /duel du dashboard) : toi contre un bot, ou
deux bots l'un contre l'autre (mode spectateur).

Tout est local : le mot est tiré parmi les solutions réelles connues, le moteur de
règles est motus_solver.duel, les bots sont des modèles de motus_solver.agents. Aucune
requête n'est envoyée à Tuzmo.

Chaque bot joue « en direct » : chaque coup est décidé au début de sa réflexion, avec
ce qu'il voit alors (couleurs adverses, victoire adverse éventuelle), puis posé après
la durée tirée de son profil de vitesse. L'état avance à chaque requête du navigateur.
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
    """Un duel en cours. `bots[i]` est le modèle du joueur i, ou None pour toi (joueur
    0 seulement) ; deux modèles = duel bot contre bot, regardé en spectateur."""

    def __init__(self, word: str, bots: tuple[str | None, str], speeds: tuple[str | None, str],
                 ctx: SolverContext):
        self.id = uuid.uuid4().hex[:12]
        self.duel = Duel(word)
        self.bot_names = list(bots)
        self.speed_names = list(speeds)
        self.names = [HUMAN_NAME if b is None else b for b in bots]
        self.agents = [None if b is None else make_agent(b) for b in bots]
        self.speeds = [None if s is None else SPEEDS[s] for s in speeds]
        self.ctx = ctx
        self.rng = random.Random()
        for i, agent in enumerate(self.agents):
            if agent is None:
                continue
            agent.opponent = self.names[1 - i]
            if hasattr(agent, "profiles"):
                agent.profiles = profiles()  # mémoire partagée d'un duel à l'autre
            agent.new_game(self.duel.letter, self.duel.length, ctx, ctx.known_valid - {self.duel.word})
        self.started = time.time() + COUNTDOWN_S
        self.pending: list[tuple[float, str] | None] = [None, None]
        self.rejections = [0, 0]
        self.saved = False
        self.lock = threading.Lock()
        for i in self.bot_seats:
            self._schedule(i, 0.0)

    # compatibilité : duel contre toi (un seul bot, joueur 1)
    @property
    def versus(self) -> bool:
        return self.agents[HUMAN] is not None

    @property
    def bot_seats(self) -> list[int]:
        return [i for i, a in enumerate(self.agents) if a is not None]

    @property
    def bot_name(self) -> str:
        return self.bot_names[BOT]

    @property
    def speed_name(self) -> str:
        return self.speed_names[BOT]

    @property
    def bot_rejections(self) -> int:
        return self.rejections[BOT]

    def clock(self) -> float:
        return time.time() - self.started

    def _schedule(self, i: int, t: float) -> None:
        if not self.duel.can_play(i):
            self.pending[i] = None
            return
        agent, speed = self.agents[i], self.speeds[i]
        me = self.duel.players[i]
        view = AgentView(self.duel.letter, self.duel.length, [(g, p) for _, g, p in me.guesses],
                         self.duel.opponent_rows(i), self.duel.to_beat(i))
        delay = speed.duration(self.rng, me.attempts + 1, self.duel.length)
        guess = agent.choose(view)
        for _ in range(MAX_REJECTIONS):
            if self.ctx.accepts(guess, self.duel.word):
                break
            self.rejections[i] += 1
            agent.reject(guess)
            delay += REJECT_COST_S + speed.per_letter * (self.duel.length - 1)
            guess = agent.choose(view)
        self.pending[i] = (t + delay, guess)

    def tick(self) -> None:
        """Pose les coups des bots arrivés à échéance (dans l'ordre chronologique),
        puis fait avancer l'horloge."""
        now = self.clock()
        while self.duel.result is None:
            due = [i for i in self.bot_seats if self.pending[i] is not None and self.pending[i][0] <= now]
            if not due:
                break
            i = min(due, key=lambda k: self.pending[k][0])
            t, guess = self.pending[i]
            if self.duel.advance(t) is not None:
                break
            self.duel.submit(i, guess, t)
            self._schedule(i, t)
        if now >= 0:
            self.duel.advance(now)
        if self.duel.result is not None and not self.saved:
            self._save()

    def human_guess(self, word: str) -> tuple[bool, str]:
        if self.versus:
            return False, "duel entre deux bots : rien à jouer"
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
        if self.pending[BOT] is None and self.duel.can_play(BOT):
            self._schedule(BOT, now)
        return True, ""

    def _save(self) -> None:
        self.saved = True
        words = [[g for _, g, _ in p.guesses] for p in self.duel.players]
        learned = False
        for i in self.bot_seats:
            if hasattr(self.agents[i], "profiles"):
                self.agents[i].observe(self.names[1 - i], self.duel.letter, self.duel.length, words[1 - i])
                learned = True
        if learned:
            _save_profiles()
        res = self.duel.result
        entry = {"t": time.time(), "word": self.duel.word, "reason": res.reason,
                 "attempts": [p.attempts for p in self.duel.players],
                 "solve_times": [None if p.solved_at is None else round(p.solved_at, 2) for p in self.duel.players],
                 "guesses": words}
        if self.versus:
            entry.update(mode="bots", bots=self.bot_names, speeds=self.speed_names, winner=res.winner,
                         rejections=self.rejections)
        else:
            entry.update(bot=self.bot_name, speed=self.speed_name, bot_rejections=self.bot_rejections,
                         winner={HUMAN: "toi", BOT: "bot", None: "nul"}[res.winner])
        try:
            with HISTORY.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _versus_state(self) -> dict:
        d = self.duel
        now = self.clock()
        ended = d.result is not None
        return {
            "id": self.id, "mode": "bots", "letter": d.letter, "length": d.length,
            "clock": round(now, 2), "countdown": max(0.0, round(-now, 2)),
            # spectateur : lettres des deux grilles visibles pendant la partie
            "players": [{"name": self.names[i], "speed": self.speed_names[i],
                         "rows": [{"word": g, "pattern": p} for _, g, p in d.players[i].guesses],
                         "solved": d.players[i].solved, "failed": d.players[i].failed,
                         "thinking": self.pending[i] is not None and not ended,
                         "rejections": self.rejections[i]} for i in (0, 1)],
            "first_solver": d.first_solver,
            "chase_left": None if d.deadline is None or ended else round(max(0.0, d.deadline - now), 1),
            "result": None if not ended else {"winner": d.result.winner, "reason": d.result.reason, "word": d.word},
        }

    def state(self) -> dict:
        if self.versus:
            return self._versus_state()
        d = self.duel
        now = self.clock()
        ended = d.result is not None
        human, bot = d.players[HUMAN], d.players[BOT]
        return {
            "id": self.id, "mode": "human", "letter": d.letter, "length": d.length, "bot": self.bot_name,
            "speed": self.speed_name,
            "clock": round(now, 2), "countdown": max(0.0, round(-now, 2)),
            "me": {"rows": [{"word": g, "pattern": p} for _, g, p in human.guesses], "solved": human.solved,
                   "failed": human.failed},
            # couleurs seulement pendant la partie, comme sur Tuzmo ; lettres à la fin
            "bot_side": {"rows": [{"word": g if ended else None, "pattern": p} for _, g, p in bot.guesses],
                         "solved": bot.solved, "failed": bot.failed,
                         "thinking": self.pending[BOT] is not None and not ended},
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
    # mode "bots" : `bot`/`speed` jouent contre `bot2`/`speed2` (toi en spectateur)
    mode: str = "human"
    bot2: str = "tusmo_conseil"
    speed2: str = "humain"


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
    versus = payload.mode == "bots"
    if payload.mode not in ("human", "bots"):
        return JSONResponse({"error": "mode inconnu"}, status_code=400)
    bots, speeds = ((payload.bot, payload.bot2), (payload.speed, payload.speed2)) if versus else \
        ((None, payload.bot), (None, payload.speed))
    if any(b is not None and b not in AGENTS for b in bots) or any(s is not None and s not in SPEEDS for s in speeds):
        return JSONResponse({"error": "bot ou vitesse inconnus"}, status_code=400)
    words = [w for w in answer_pool() if payload.length is None or len(w) == payload.length]
    if not words:
        return JSONResponse({"error": "aucun mot de cette longueur"}, status_code=400)
    ctx = context()
    live = LiveDuel(random.choice(words), bots, speeds, ctx)
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
    """Ton bilan contre chaque bot (victoires, nulles, défaites), et le bilan des duels
    bot contre bot par affiche (modèle@vitesse, quel que soit le côté)."""
    rows = []
    if HISTORY.exists():
        rows = [json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    human = [r for r in rows if r.get("mode") != "bots"]
    by_bot: dict[str, dict] = {}
    for r in human:
        b = by_bot.setdefault(f"{r['bot']}@{r['speed']}", {"toi": 0, "nul": 0, "bot": 0})
        b[r["winner"]] += 1
    versus: dict[str, dict] = {}
    for r in rows:
        if r.get("mode") != "bots":
            continue
        sides = [f"{b}@{s}" for b, s in zip(r["bots"], r["speeds"])]
        a, b = sorted(sides)
        flip = sides[0] != a  # côté gauche du tableau = premier nom par ordre alphabétique
        m = versus.setdefault(f"{a} / {b}", {"a": a, "b": b, "wins_a": 0, "draws": 0, "wins_b": 0, "attempts": [0, 0]})
        if r["winner"] is None:
            m["draws"] += 1
        else:
            m["wins_b" if (r["winner"] == 1) != flip else "wins_a"] += 1
        m["attempts"][0] += r["attempts"][1 if flip else 0]
        m["attempts"][1] += r["attempts"][0 if flip else 1]
    return JSONResponse({"duels": len(human), "by_bot": by_bot, "last": human[-10:][::-1],
                         "versus": list(versus.values()), "versus_duels": len(rows) - len(human)})
