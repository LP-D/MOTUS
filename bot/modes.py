"""Modes de jeu Tuzmo : un gestionnaire par mode pour ce qui en dépend (page à
ouvrir, partie déjà terminée au chargement, enchaînement entre parties).

Tout le reste est commun à tous les modes et réutilisé tel quel :
- le solveur (entropy_pure par défaut, composite en option) ;
- le repli dynamique sur rejet ;
- les listes de mots valides et refusés ;
- la surveillance réseau et l'arrêt d'urgence.

Mode classé (/ranked) : duels contre de vrais joueurs, avec ligues et points de
classement. Il n'est volontairement pas automatisé : faire jouer un solveur face à
des adversaires qui croient affronter une personne revient à tricher en compétition.
`handler_for(GameMode.RANKED)` lève `ModeNotSupportedError`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

BASE_URL = "https://www.tusmo.xyz"


class GameMode(str, Enum):
    DAILY = "daily"
    INFINITE = "infinite"
    RANKED = "ranked"


class ModeNotSupportedError(RuntimeError):
    pass


# Issue du chargement d'une page, selon la session renvoyée par POST /api/game
PLAYABLE = "playable"
RESTARTABLE = "restartable"  # partie terminée, bouton "Rejouer" disponible (/infinite)
DAILY_LIMIT = "daily_limit_reached"  # mot du jour déjà joué par cet invité
NOT_PLAYABLE = "not_playable"


@dataclass(frozen=True)
class ModeHandler:
    mode: GameMode
    path: str
    # une partie terminée peut être relancée sur la même page ("Rejouer")
    restart_finished: bool
    # nombre maximal de parties par lancement (None : illimité)
    max_games: int | None
    # repli ↻ (reset du run) si le giveup échoue : propre à /infinite
    reset_fallback: bool

    @property
    def url(self) -> str:
        return BASE_URL + self.path

    def classify_loaded_session(self, session: dict | None) -> str:
        """Que faire de la session renvoyée au chargement de la page."""
        status = (session or {}).get("status")
        if session is None or status in (None, "playing"):
            return PLAYABLE
        if self.mode is GameMode.DAILY:
            return DAILY_LIMIT
        return RESTARTABLE if self.restart_finished else NOT_PLAYABLE

    def games_allowed(self, requested: int) -> int:
        return requested if self.max_games is None else min(requested, self.max_games)


_HANDLERS = {
    GameMode.INFINITE: ModeHandler(GameMode.INFINITE, "/infinite", restart_finished=True, max_games=None,
                                   reset_fallback=True),
    # un seul mot par jour : une partie par lancement, puis arrêt propre
    GameMode.DAILY: ModeHandler(GameMode.DAILY, "/daily", restart_finished=False, max_games=1,
                                reset_fallback=False),
}


def handler_for(mode: GameMode | str) -> ModeHandler:
    mode = GameMode(mode)
    if mode is GameMode.RANKED:
        raise ModeNotSupportedError(
            "mode classé non automatisé : duels contre de vrais joueurs (ligues, points de "
            "classement) ; un solveur face à eux reviendrait à tricher en compétition")
    return _HANDLERS[mode]


SUPPORTED_MODES = tuple(_HANDLERS)
