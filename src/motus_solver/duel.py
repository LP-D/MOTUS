"""Simulateur local du duel classé Tuzmo : moteur de règles, sans aucune requête au site.

Règles relevées dans le code public du site (textes `ranked.rules`, 25/09/2026) :
- les deux joueurs cherchent le même mot, première lettre donnée, 6 essais chacun ;
- dès qu'un joueur trouve, l'autre a 2 minutes pour terminer sa grille ;
- pour gagner, il faut trouver en strictement moins d'essais que l'adversaire : à
  essais égaux ou supérieurs, c'est perdu. La partie s'arrête dès que remonter
  devient impossible ;
- si personne ne trouve, la partie est nulle ;
- chacun voit les couleurs de la grille adverse, sans les lettres.

Non modélisé (inconnu ou hors sujet) : le montant des LP par match, le forfait après
45 s de déconnexion, les émotes et la discussion.

Le moteur est piloté par des événements horodatés (secondes depuis le départ) : il
sert aux duels bot contre bot (temps simulé) comme aux duels contre un humain (temps
réel, cf. dashboard/duel_api.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .feedback import pattern_string

MAX_ATTEMPTS = 6
CHASE_SECONDS = 120.0


class DuelError(ValueError):
    """Coup refusé par le moteur (partie finie, joueur déjà terminé, mauvais format)."""


@dataclass
class PlayerState:
    guesses: list[tuple[float, str, str]] = field(default_factory=list)  # (t, mot, retour)
    solved_at: float | None = None

    @property
    def attempts(self) -> int:
        return len(self.guesses)

    @property
    def solved(self) -> bool:
        return self.solved_at is not None

    @property
    def failed(self) -> bool:
        return not self.solved and self.attempts >= MAX_ATTEMPTS

    @property
    def done(self) -> bool:
        return self.solved or self.failed


@dataclass
class DuelResult:
    winner: int | None  # 0, 1, ou None pour une partie nulle
    reason: str  # "fewer_attempts", "cannot_catch_up", "chase_timeout", "only_solver", "nobody"
    t: float


class Duel:
    """Un duel entre les joueurs 0 et 1 sur `word`. Chaque coup est soumis avec son
    horodatage ; les coups doivent arriver dans l'ordre chronologique."""

    def __init__(self, word: str, chase_seconds: float = CHASE_SECONDS):
        self.word = word.upper()
        self.letter = self.word[0]
        self.length = len(self.word)
        self.chase_seconds = chase_seconds
        self.players = (PlayerState(), PlayerState())
        self.first_solver: int | None = None
        self.deadline: float | None = None  # fin du chrono de riposte
        self.result: DuelResult | None = None
        self.now = 0.0

    # --- vue d'un joueur ---------------------------------------------------------
    def opponent_rows(self, player: int) -> list[str]:
        """Ce qu'un joueur voit de l'adversaire : les retours, jamais les lettres."""
        return [pattern for _, _, pattern in self.players[1 - player].guesses]

    def to_beat(self, player: int) -> int | None:
        """Nombre maximal d'essais pour encore gagner (adversaire déjà gagnant), sinon None."""
        if self.first_solver is None or self.first_solver == player:
            return None
        return self.players[self.first_solver].attempts - 1

    def can_play(self, player: int) -> bool:
        return self.result is None and not self.players[player].done

    # --- déroulement -------------------------------------------------------------
    def advance(self, t: float) -> DuelResult | None:
        """Fait avancer l'horloge : le chrono de riposte peut expirer."""
        self.now = max(self.now, t)
        if self.result is None and self.deadline is not None and t > self.deadline:
            self.result = DuelResult(self.first_solver, "chase_timeout", self.deadline)
        return self.result

    def submit(self, player: int, guess: str, t: float) -> str:
        """Joue `guess` pour `player` à l'instant `t` ; renvoie le retour "0/1/2"."""
        guess = guess.upper()
        self.advance(t)
        if self.result is not None:
            raise DuelError("partie terminée")
        me = self.players[player]
        if me.done:
            raise DuelError("grille déjà terminée")
        if len(guess) != self.length or guess[0] != self.letter:
            raise DuelError(f"le mot doit faire {self.length} lettres et commencer par {self.letter}")
        pattern = pattern_string(guess, self.word)
        me.guesses.append((t, guess, pattern))
        if pattern == "2" * self.length:
            me.solved_at = t
        self._resolve(player, t)
        return pattern

    def _resolve(self, player: int, t: float) -> None:
        me, other = self.players[player], self.players[1 - player]
        if me.solved:
            if self.first_solver is None:
                self.first_solver = player
                if other.done or other.attempts >= me.attempts - 1:
                    # l'adversaire a déjà joué assez d'essais : il ne peut plus faire mieux
                    self.result = DuelResult(player, "cannot_catch_up" if not other.failed else "only_solver", t)
                else:
                    self.deadline = t + self.chase_seconds
            else:
                # riposte réussie : forcément en moins d'essais (sinon arrêtée avant)
                self.result = DuelResult(player, "fewer_attempts", t)
            return
        if self.first_solver is not None and self.first_solver != player:
            if me.attempts >= self.players[self.first_solver].attempts - 1:
                self.result = DuelResult(self.first_solver, "cannot_catch_up", t)
            return
        if me.failed and other.failed:
            self.result = DuelResult(None, "nobody", t)

    def summary(self) -> dict:
        return {
            "word": self.word,
            "result": None if self.result is None else {
                "winner": self.result.winner, "reason": self.result.reason, "t": round(self.result.t, 2)},
            "players": [{"attempts": p.attempts, "solved": p.solved,
                         "solved_at": None if p.solved_at is None else round(p.solved_at, 2),
                         "guesses": [g for _, g, _ in p.guesses]} for p in self.players],
        }
