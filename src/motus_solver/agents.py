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
from .cache import cache_key
from .corpus import Corpus
from .feedback import words_to_matrix
from .inference import OpponentProfile, candidate_weights, effective_candidates, weighted_entropy
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
    opponent: str | None = None  # nom de l'adversaire du duel en cours (profil)

    def new_game(self, letter: str, length: int, ctx: SolverContext, known_valid: set[str]) -> None:
        raise NotImplementedError

    def observe(self, opponent: str, letter: str, length: int, opponent_guesses: list[str]) -> None:
        """Fin de duel : la grille adverse (lettres comprises) est visible au récapitulatif."""

    def choose(self, view: AgentView) -> str:
        raise NotImplementedError

    def reject(self, word: str) -> None:
        """Le jeu a refusé `word` : ne plus le proposer."""
        self.solver.discard(word)


class SolverAgent(Agent):
    """Le solveur du bot (entropy_pure ou composite), avec des options de duel :

    - `chase_aware` (riposte) : l'adversaire a trouvé en n essais, la fin de partie
      vise n-1 essais au plus (seule façon de gagner), au lieu de 6 ;
    - `pressure` : l'adversaire est à `pressure_missing` lettre(s) ou moins de trouver
      (d'après ses couleurs) : plus de mot sonde, chaque coup doit pouvoir gagner ;
    - `near_tie` (tempo) : écart d'entropie toléré pour préférer un mot déjà accepté
      par le jeu. Plus large = moins de refus (du temps gagné), un peu moins
      d'information. 0,02 par défaut."""

    def __init__(self, name: str, strategy: str = "entropy_pure", endgame: bool = True, chase_aware: bool = False,
                 rare_probes: bool = False, pressure: bool = False, pressure_missing: int = 1,
                 near_tie: float | None = None, description: str = ""):
        self.name, self.strategy, self.endgame, self.chase_aware = name, strategy, endgame, chase_aware
        self.rare_probes, self.pressure, self.pressure_missing, self.near_tie = (
            rare_probes, pressure, pressure_missing, near_tie)
        self.description = description
        self.solver: Solver | None = None
        self._synced = 0

    def new_game(self, letter, length, ctx, known_valid):
        self.solver = Solver(letter=letter, length=length, corpus=ctx.corpus, root_cache=ctx.caches[self.strategy],
                             blocklist=ctx.blocklist, known_valid=known_valid, strategy=self.strategy,
                             endgame=self.endgame, endgame_rare_probes=self.rare_probes, near_tie=self.near_tie)
        self._synced = 0

    def _sync(self, view: AgentView) -> None:
        for word, pattern in view.history[self._synced:]:
            self.solver.play(word)
            self.solver.update(pattern)
        self._synced = len(view.history)

    def opponent_close(self, view: AgentView) -> bool:
        if view.to_beat is not None:
            return True
        return bool(view.opponent_rows) and view.opponent_rows[-1].count("2") >= view.length - self.pressure_missing

    def _prepare(self, view: AgentView) -> None:
        self._sync(view)
        if self.chase_aware and view.to_beat is not None:
            self.solver.max_attempts = view.to_beat
        if self.pressure:
            self.solver.endgame = self.endgame and not self.opponent_close(view)

    def choose(self, view):
        self._prepare(view)
        if not self.solver.candidates:
            return _burn_word(self.solver, view)  # solution hors corpus : essais grillés
        return self.solver.suggest(top_n=1)[0][0]


class InferenceAgent(SolverAgent):
    """Solveur qui lit aussi les couleurs adverses (motus_solver.inference) et apprend,
    duel après duel, les mots d'ouverture de chaque adversaire.

    Quand ces couleurs rendent certains candidats nettement plus probables, le coup
    maximise l'entropie pondérée (+ chance de gagner tout de suite) parmi les
    candidats. Sinon, coup habituel du solveur (fin de partie comprise)."""

    MAX_CANDIDATES = 1500  # au-delà, calcul trop long pour un gain faible (début de partie)
    INFORMATIVE = 0.9  # candidats effectifs < 90 % des candidats : poids jugés informatifs
    WIN_BONUS = 1.0  # bits accordés à la probabilité de gagner tout de suite

    def __init__(self, *args, profiles: dict[str, OpponentProfile] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profiles = profiles if profiles is not None else {}
        self.last_weights: dict[str, float] | None = None

    def new_game(self, letter, length, ctx, known_valid):
        super().new_game(letter, length, ctx, known_valid)
        key = cache_key(letter, length)
        roots = [e["word"] for cache in ctx.caches.values() for e in
                 ([cache[key], *cache[key].get("alternatives", ())] if cache.get(key) else [])]
        group = [w for w in self.solver.pool if w in known_valid]
        self.vocabulary = list(dict.fromkeys(roots + group))
        profile = self.profiles.get(self.opponent) if self.opponent else None
        self.predicted_opener = profile.predicted_opener(letter, length, ctx.caches) if profile else None

    def observe(self, opponent, letter, length, opponent_guesses):
        self.profiles.setdefault(opponent, OpponentProfile()).observe(letter, length, opponent_guesses)

    def choose(self, view):
        self._prepare(view)
        self.last_weights = None
        cands = self.solver.candidates
        if not cands:
            return _burn_word(self.solver, view)
        if not view.opponent_rows or len(cands) <= 1 or len(cands) > self.MAX_CANDIDATES:
            return self.solver.suggest(top_n=1)[0][0]
        weights = candidate_weights(cands, view.opponent_rows, self.vocabulary, self.predicted_opener)
        if effective_candidates(weights) > self.INFORMATIVE * len(cands):
            return self.solver.suggest(top_n=1)[0][0]
        self.last_weights = dict(zip(cands, weights.tolist()))
        if self.solver.attempts_left <= 1:
            return cands[int(weights.argmax())]  # dernier essai utile : le plus probable
        arr = words_to_matrix(cands)
        scores = weighted_entropy(arr, arr, weights) + self.WIN_BONUS * weights
        return cands[int(scores.argmax())]


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
    "entropy_pure_pression": lambda: SolverAgent(
        "entropy_pure_pression", "entropy_pure", chase_aware=True, pressure=True,
        description="riposte + pression : plus de mot sonde quand l'adversaire est à une lettre"),
    "entropy_pure_tempo": lambda: SolverAgent(
        "entropy_pure_tempo", "entropy_pure", chase_aware=True, near_tie=0.10,
        description="riposte + tempo : mots déjà acceptés préférés jusqu'à 10 % d'entropie en moins"),
    "entropy_pure_infos": lambda: InferenceAgent(
        "entropy_pure_infos", "entropy_pure", chase_aware=True,
        description="riposte + lit les couleurs adverses, apprend les ouvertures de l'adversaire"),
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
    for i, agent in enumerate(agents):
        agent.opponent = agents[1 - i].name
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
