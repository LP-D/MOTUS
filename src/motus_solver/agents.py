"""Joueurs du simulateur de duel (motus_solver.duel) : stratégies de choix des mots et
profils de vitesse. Chaque stratégie est un « modèle » candidat pour la compétition.

Équité : les mots des duels sont tirés parmi les solutions réelles connues. Le joueur
reçoit donc la liste des mots déjà acceptés privée du mot du duel (leave-one-out), sinon
le départage « mot déjà accepté d'abord » le mènerait droit à la solution (cf.
docs/diagnostics/2026-09-25_phase6_fin_de_partie.md, section 4).
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .blocklist import load_blocklist
from .cache import ROOT_CACHE_FILES, load_cache
from .corpus import Corpus
from .solver import Solver

# Refus du dictionnaire du jeu, simulés pour les bots : un mot jamais accepté par le
# vrai jeu est refusé dans ~30 % des cas (taux mesuré sur les coups 1 non validés).
# Tirage fixe par mot (un mot est dans le dictionnaire ou non, d'un duel à l'autre).
# Un refus ne coûte pas d'essai, seulement du temps.
REJECT_PCT = 30
REJECT_COST_S = 1.5  # réponse du serveur, ligne à effacer (en plus de la frappe)


@dataclass
class SolverContext:
    """Données partagées par tous les joueurs (chargées une fois)."""

    corpus: Corpus
    caches: dict[str, dict]
    blocklist: set[str]
    known_valid: set[str]

    @classmethod
    def load(cls, data_dir: Path) -> "SolverContext":
        return cls(corpus=Corpus.from_file(data_dir / "corpus_fr.txt"),
                   caches={s: load_cache(data_dir / f) for s, f in ROOT_CACHE_FILES.items()},
                   blocklist=load_blocklist(data_dir / "known_invalid_words.json"),
                   known_valid=load_blocklist(data_dir / "known_valid_words.json"))

    def accepts(self, word: str, answer: str) -> bool:
        """Le dictionnaire simulé du jeu accepte-t-il ce coup d'un bot ?"""
        word = word.upper()
        if word == answer or word in self.known_valid:
            return True
        if word in self.blocklist:
            return False
        return zlib.crc32(word.encode()) % 100 >= REJECT_PCT

    def is_playable(self, word: str) -> bool:
        """Mot accepté par le simulateur : dans le corpus (ou déjà accepté par le jeu),
        et jamais refusé par le vrai jeu."""
        word = word.upper()
        if word in self.blocklist:
            return False
        return word in self.known_valid or word in set(self.corpus.subset(word[0], len(word)))


@dataclass
class AgentView:
    """Ce qu'un joueur sait au moment de choisir son coup."""

    letter: str
    length: int
    history: list[tuple[str, str]]  # ses coups : (mot, retour)
    opponent_rows: list[str]  # retours adverses, sans les lettres
    to_beat: int | None  # adversaire gagnant : essais maximum pour encore gagner


class Agent:
    name = "agent"
    description = ""

    def new_game(self, letter: str, length: int, ctx: SolverContext, known_valid: set[str]) -> None:
        raise NotImplementedError

    def choose(self, view: AgentView) -> str:
        raise NotImplementedError

    def reject(self, word: str) -> None:
        """Le jeu a refusé `word` : ne plus le proposer."""
        self.solver.discard(word)


class SolverAgent(Agent):
    """Le solveur du bot (entropy_pure ou composite). `chase_aware` : quand
    l'adversaire a trouvé en n essais, la fin de partie vise à trouver en n-1 essais
    au plus (seule façon de gagner), au lieu de 6."""

    def __init__(self, name: str, strategy: str = "entropy_pure", endgame: bool = True, chase_aware: bool = False,
                 rare_probes: bool = False, description: str = ""):
        self.name, self.strategy, self.endgame, self.chase_aware = name, strategy, endgame, chase_aware
        self.rare_probes = rare_probes
        self.description = description
        self.solver: Solver | None = None
        self._synced = 0

    def new_game(self, letter, length, ctx, known_valid):
        self.solver = Solver(letter=letter, length=length, corpus=ctx.corpus, root_cache=ctx.caches[self.strategy],
                             blocklist=ctx.blocklist, known_valid=known_valid, strategy=self.strategy,
                             endgame=self.endgame, endgame_rare_probes=self.rare_probes)
        self._synced = 0

    def _sync(self, view: AgentView) -> None:
        for word, pattern in view.history[self._synced:]:
            self.solver.play(word)
            self.solver.update(pattern)
        self._synced = len(view.history)

    def choose(self, view):
        self._sync(view)
        if self.chase_aware and view.to_beat is not None:
            self.solver.max_attempts = view.to_beat
        if not self.solver.candidates:
            return _burn_word(self.solver, view)  # solution hors corpus : essais grillés
        return self.solver.suggest(top_n=1)[0][0]


class RandomCandidateAgent(Agent):
    """Joue un mot au hasard parmi ceux encore compatibles : repère « joueur naïf »."""

    name = "aleatoire"
    description = "un mot compatible au hasard (repère naïf)"

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.solver: Solver | None = None
        self._synced = 0

    def new_game(self, letter, length, ctx, known_valid):
        self.solver = Solver(letter=letter, length=length, corpus=ctx.corpus, blocklist=ctx.blocklist, endgame=False)
        self._synced = 0

    def choose(self, view):
        for word, pattern in view.history[self._synced:]:
            self.solver.play(word)
            self.solver.update(pattern)
        self._synced = len(view.history)
        if not self.solver.candidates:
            return _burn_word(self.solver, view)
        return self.rng.choice(self.solver.candidates)


def _burn_word(solver: Solver, view: AgentView) -> str:
    played = {w for w, _ in view.history}
    return next((w for w in solver.pool if w not in played), view.history[-1][0])


# --- modèles disponibles ------------------------------------------------------------
AGENTS = {
    "entropy_pure": lambda: SolverAgent("entropy_pure", "entropy_pure", description="solveur par défaut du bot"),
    "entropy_pure_chasse": lambda: SolverAgent(
        "entropy_pure_chasse", "entropy_pure", chase_aware=True,
        description="défaut + riposte : vise n-1 essais quand l'adversaire a trouvé en n"),
    "entropy_pure_sondes": lambda: SolverAgent(
        "entropy_pure_sondes", "entropy_pure", chase_aware=True, rare_probes=True,
        description="riposte + sondes rares permises pour finir plus vite (refus possibles)"),
    "entropy_pure_sans_fin": lambda: SolverAgent(
        "entropy_pure_sans_fin", "entropy_pure", endgame=False, description="sans mot sonde de fin de partie"),
    "composite": lambda: SolverAgent("composite", "composite", description="score composite (entropie, voyelles...)"),
    "aleatoire": lambda: RandomCandidateAgent(),
}


def make_agent(name: str) -> Agent:
    if name not in AGENTS:
        raise ValueError(f"modèle inconnu : {name!r} (disponibles : {', '.join(AGENTS)})")
    return AGENTS[name]()


# --- vitesse -------------------------------------------------------------------------
@dataclass(frozen=True)
class SpeedProfile:
    """Durée d'un coup = réflexion (log-normale : médiane, dispersion) + frappe."""

    name: str
    first_think: tuple[float, float]  # coup 1 : souvent un mot d'ouverture habituel
    think: tuple[float, float]
    per_letter: float
    description: str = ""

    def duration(self, rng: random.Random, attempt: int, length: int) -> float:
        median, sigma = self.first_think if attempt == 1 else self.think
        return median * math.exp(rng.gauss(0.0, sigma)) + self.per_letter * (length - 1)


SPEEDS = {
    "instantane": SpeedProfile("instantane", (0.3, 0.2), (0.4, 0.3), 0.05, "bot sans délai (~1 s par coup)"),
    "rapide": SpeedProfile("rapide", (2.0, 0.3), (4.0, 0.4), 0.12, "très bon joueur (~5 s par coup)"),
    "humain": SpeedProfile("humain", (5.0, 0.4), (12.0, 0.5), 0.25, "joueur moyen (~13 s par coup)"),
    "lent": SpeedProfile("lent", (8.0, 0.4), (25.0, 0.5), 0.35, "joueur prudent (~27 s par coup)"),
}


@dataclass
class DuelRecord:
    word: str
    names: tuple[str, str]
    speeds: tuple[str, str]
    winner: int | None
    reason: str
    attempts: tuple[int, int]
    solved: tuple[bool, bool]
    solve_times: tuple[float | None, float | None]
    guesses: tuple[list[str], list[str]] = field(default_factory=lambda: ([], []))
    rejections: tuple[int, int] = (0, 0)


MAX_REJECTIONS = 20


def play_duel(word: str, agents: tuple[Agent, Agent], speeds: tuple[SpeedProfile, SpeedProfile],
              ctx: SolverContext, rng: random.Random, chase_seconds: float | None = None) -> DuelRecord:
    """Duel bot contre bot en temps simulé. Chaque joueur choisit son coup avec ce
    qu'il sait au début de sa réflexion ; le coup arrive après sa durée de réflexion
    et de frappe."""
    from .duel import CHASE_SECONDS, Duel

    duel = Duel(word, CHASE_SECONDS if chase_seconds is None else chase_seconds)
    known_valid = ctx.known_valid - {duel.word}
    for agent in agents:
        agent.new_game(duel.letter, duel.length, ctx, known_valid)
    pending: list[tuple[float, str] | None] = [None, None]
    rejections = [0, 0]

    def schedule(i: int, t: float) -> None:
        if not duel.can_play(i):
            pending[i] = None
            return
        me = duel.players[i]
        view = AgentView(duel.letter, duel.length, [(g, p) for _, g, p in me.guesses], duel.opponent_rows(i),
                         duel.to_beat(i))
        delay = speeds[i].duration(rng, me.attempts + 1, duel.length)
        guess = agents[i].choose(view)
        for _ in range(MAX_REJECTIONS):
            if ctx.accepts(guess, duel.word):
                break
            rejections[i] += 1
            agents[i].reject(guess)
            delay += REJECT_COST_S + speeds[i].per_letter * (duel.length - 1)
            guess = agents[i].choose(view)
        pending[i] = (t + delay, guess)

    schedule(0, 0.0)
    schedule(1, 0.0)
    while duel.result is None:
        live = [i for i in (0, 1) if pending[i] is not None]
        if not live:
            break
        i = min(live, key=lambda k: pending[k][0])
        t, guess = pending[i]
        if duel.advance(t) is not None:
            break
        duel.submit(i, guess, t)
        schedule(i, t)
    if duel.result is None and duel.deadline is not None:
        duel.advance(duel.deadline + 1.0)

    res = duel.result
    return DuelRecord(
        word=duel.word, names=(agents[0].name, agents[1].name), speeds=(speeds[0].name, speeds[1].name),
        winner=None if res is None else res.winner, reason="unfinished" if res is None else res.reason,
        attempts=tuple(p.attempts for p in duel.players), solved=tuple(p.solved for p in duel.players),
        solve_times=tuple(None if p.solved_at is None else round(p.solved_at, 2) for p in duel.players),
        guesses=tuple([g for _, g, _ in p.guesses] for p in duel.players),
        rejections=tuple(rejections),
    )
