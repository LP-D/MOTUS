"""Fin de partie : sacrifier un essai sur un mot « sonde » plutôt que risquer de perdre.

Cas typique (les 4 défaites des 1 394 parties du dashboard, 25/09/2026) : il ne
reste qu'une ou deux lettres à trouver, par exemple PA?ES avec PAGES, PALES, PAMES,
PANES, PATES, PAVES... et 4 essais. Jouer les candidats un par un ne teste qu'une
lettre par essai : la partie est perdue si la solution arrive trop tard. Un mot
sonde (même première lettre, même longueur, pas forcément candidat) qui contient
plusieurs des lettres possibles les teste toutes en un seul essai.

Deux régimes, candidats supposés équiprobables :
- plus de candidats que d'essais restants (risque de perdre) : maximiser la
  probabilité de gagner ; le coup habituel est gardé s'il garantit la victoire ;
- sinon, petits ensembles (<= MAX_CANDIDATES_SAFE) : victoire déjà garantie, on
  minimise le nombre moyen d'essais (ETA?ES à 5 candidats : une sonde, puis la
  solution, au lieu de jusqu'à 5 essais).
Le coup habituel du solveur est gardé sauf si un autre fait strictement mieux ; à
valeur égale, un candidat (victoire immédiate possible) puis un mot déjà accepté par
le jeu (pas de rejet) sont préférés.

`solve(cellule, r)` — cellule = candidats encore possibles, r = essais restants —
renvoie (P, E) : probabilité de gagner et essais consommés en moyenne (une défaite
compte ses r essais). Pour un coup g :
    P = [g candidat]/k + somme sur les autres classes c de |c|/k * P(c, r-1)
    E = 1 + somme sur les autres classes c de |c|/k * E(c, r-1)
Les retours de tous les coups examinés sur tous les candidats sont calculés une seule
fois ; la récursion ne fait ensuite que des sélections de colonnes. Elle est bornée
(coups les plus discriminants, budget de nœuds) ; hors budget, l'estimation
« candidats un par un » est utilisée (pessimiste).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .feedback import candidate_letter_counts, pattern_codes_batch, words_to_matrix

MAX_ATTEMPTS = 6
# Risque de perdre : analyse jusqu'à cette taille (au-delà, début de partie :
# l'entropie suffit, le risque est négligeable).
MAX_CANDIDATES = 200
# Victoire déjà garantie : analyse (gain de vitesse seulement) jusqu'à cette taille.
MAX_CANDIDATES_SAFE = 30
# Sondes : présélection grossière (lettres qui départagent les candidats), puis les
# PROBE_POOL_SIZE qui séparent le mieux les candidats ; INNER_PROBES dans la récursion.
PROBE_PREFILTER = 1500
PROBE_POOL_SIZE = 300
INNER_PROBES = 60
# Coups examinés par nœud de la récursion (triés par nombre de classes décroissant).
GUESSES_PER_NODE = 10
# Coups examinés à la racine : candidats, sondes déjà acceptées, autres sondes.
ROOT_GUESSES = 40
NODE_BUDGET = 800
# Gain minimal d'essais moyens pour remplacer le coup habituel (sinon bruit de l'estimation).
MIN_GAIN_ATTEMPTS = 0.05
# Mot jamais accepté par le jeu : environ 30 % de rejet (coups 1 non validés), soit un
# aller-retour de ~2 s sans perdre d'essai. Compté comme 0,15 essai à probabilité égale.
UNKNOWN_WORD_PENALTY = 0.15
# Régime vitesse : sondes limitées aux mots déjà acceptés par le jeu. Les sondes les
# plus discriminantes sont souvent des mots rares : validation en jeu réel du
# 25/09/2026, TULENG, TALUEE puis TELNET refusés d'affilée (~6 s) pour 0,3 essai
# espéré. En régime risque, toutes les sondes restent permises.
SPEED_PROBES_KNOWN_ONLY = True
_EPS = 1e-9


def _classes(codes: np.ndarray) -> np.ndarray:
    """Nombre de retours distincts par ligne de `codes` (G, N)."""
    if codes.shape[1] <= 1:
        return np.ones(codes.shape[0], dtype=np.int64)
    s = np.sort(codes, axis=1)
    return 1 + np.sum(np.diff(s, axis=1) != 0, axis=1)


def _linear(k: int, r: int) -> tuple[float, float]:
    """(P, E) en jouant les candidats un par un : estimation pessimiste."""
    if k <= r:
        return 1.0, (k + 1) / 2
    return r / k, (sum(range(1, r + 1)) + (k - r) * r) / k


def _better(a: tuple[float, float], b: tuple[float, float], gain: float = 0.0) -> bool:
    """a strictement meilleur que b : probabilité de gagner, puis essais moyens."""
    if a[0] > b[0] + _EPS:
        return True
    return abs(a[0] - b[0]) <= _EPS and a[1] < b[1] - gain - _EPS


def _codes(guess_arr: np.ndarray, cand_arr: np.ndarray) -> np.ndarray:
    counts = candidate_letter_counts(cand_arr)
    step = max(1, 2_000_000 // max(1, cand_arr.shape[0] * cand_arr.shape[1]))
    return np.concatenate([pattern_codes_batch(guess_arr[s:s + step], cand_arr, counts)
                           for s in range(0, guess_arr.shape[0], step)], axis=0)


@dataclass
class EndgameChoice:
    word: str
    probe: bool  # True : le mot n'est pas un candidat (essai sacrifié)
    p_win: float  # probabilité de gagner avec ce coup
    expected_attempts: float  # essais moyens restants avec ce coup (celui-ci compris)
    p_default: float  # mêmes mesures pour le coup habituel du solveur
    expected_default: float
    default: str
    candidates: int
    attempts_left: int
    reason: str  # "risque" (victoire non garantie) ou "vitesse"
    letters: list[str] = field(default_factory=list)  # lettres encore incertaines testées par le coup


class EndgamePlanner:
    def __init__(self, candidates: list[str], pool: list[str], attempts_left: int,
                 known_valid: set[str] | frozenset[str] = frozenset(),
                 speed_known_only: bool = SPEED_PROBES_KNOWN_ONLY):
        self.cands = list(candidates)
        self.n = len(self.cands)
        self.length = len(self.cands[0])
        self.attempts_left = attempts_left
        self.known_valid = known_valid
        self.speed_known_only = speed_known_only
        cand_set = set(self.cands)
        self.pool_words = [w for w in pool if w not in cand_set]
        self.cand_arr = words_to_matrix(self.cands)
        self.win_code = 3 ** self.length - 1
        # régime vitesse : minimiser aussi les essais moyens ; sinon probabilité seule
        self.speed = True
        self._memo: dict[tuple[tuple[int, ...], int], tuple[float, float]] = {}
        self._nodes = 0
        self.guess_words: list[str] = []
        self.codes_all: np.ndarray | None = None  # (coups examinés, candidats)
        self._inner_probes: np.ndarray = np.zeros(0, dtype=np.int64)

    @property
    def nodes(self) -> int:
        return self._nodes

    # --- préparation ---------------------------------------------------------------
    def _prefilter(self) -> list[str]:
        """Présélection grossière des sondes : somme, sur les lettres du mot, de leur
        pouvoir de départage des candidats (présence et position)."""
        if len(self.pool_words) <= PROBE_PREFILTER:
            return self.pool_words
        n = self.n
        onehot = np.zeros((n, self.length, 26), dtype=np.int32)
        onehot[np.arange(n)[:, None], np.arange(self.length)[None, :], self.cand_arr] = 1
        pos_count = onehot.sum(axis=0)  # (L, 26)
        presence = (onehot.sum(axis=1) > 0).sum(axis=0)  # (26,)
        split_pos = np.minimum(pos_count, n - pos_count)
        split_presence = np.minimum(presence, n - presence)
        arr = words_to_matrix(self.pool_words)
        probe_presence = np.zeros((len(self.pool_words), 26), dtype=np.int32)
        probe_presence[np.arange(len(arr))[:, None], arr] = 1
        score = probe_presence @ split_presence + split_pos[np.arange(self.length)[None, :], arr].sum(axis=1)
        keep = np.argsort(-score, kind="stable")[:PROBE_PREFILTER]
        return [self.pool_words[i] for i in keep]

    def _prepare(self) -> None:
        """Retours de tous les coups examinés (candidats, puis sondes retenues) sur
        tous les candidats, calculés une seule fois."""
        if self.codes_all is not None:
            return
        cand_codes = _codes(self.cand_arr, self.cand_arr)
        probes = self._prefilter()
        if probes:
            probe_arr = words_to_matrix(probes)
            probe_codes = _codes(probe_arr, self.cand_arr)
            keep = np.argsort(-_classes(probe_codes), kind="stable")[:PROBE_POOL_SIZE]
            probes = [probes[i] for i in keep]
            self.codes_all = np.concatenate([cand_codes, probe_codes[keep]], axis=0)
        else:
            self.codes_all = cand_codes
        self.guess_words = self.cands + probes
        self._inner_probes = np.arange(self.n, self.n + min(INNER_PROBES, len(probes)))

    # --- évaluation --------------------------------------------------------------
    def _value(self, codes_row: np.ndarray, cell: np.ndarray, r: int) -> tuple[float, float]:
        """(P, E) si l'on joue le coup dont `codes_row` donne le retour sur chaque
        candidat de `cell`, avec `r` essais restants (celui-ci compris)."""
        k = len(cell)
        p, e = 0.0, 1.0
        order = np.argsort(codes_row, kind="stable")
        bounds = np.flatnonzero(np.diff(codes_row[order])) + 1
        for group in np.split(order, bounds):
            if codes_row[group[0]] == self.win_code:
                p += 1.0 / k
                continue
            sp, se = self.solve(cell[group], r - 1)
            p += len(group) / k * sp
            e += len(group) / k * se
        return p, e

    def _done(self, value: tuple[float, float], k: int) -> bool:
        """Inutile de chercher mieux : victoire garantie (et, en régime vitesse, un
        candidat qui isole tous les autres)."""
        if value[0] < 1.0 - _EPS:
            return False
        return not self.speed or value[1] <= (2 * k - 1) / k + _EPS

    def solve(self, cell: np.ndarray, r: int) -> tuple[float, float]:
        k = len(cell)
        if r <= 0:
            return 0.0, 0.0
        if k == 1:
            return 1.0, 1.0
        if r == 1:
            return 1.0 / k, 1.0
        if k <= r and not self.speed:
            return _linear(k, r)
        key = (tuple(cell.tolist()), r)
        if key in self._memo:
            return self._memo[key]
        if self._nodes >= NODE_BUDGET:
            return _linear(k, r)
        self._nodes += 1

        guesses = np.concatenate([cell, self._inner_probes])  # candidats de la cellule, puis sondes
        codes = self.codes_all[np.ix_(guesses, cell)]
        classes = _classes(codes)
        is_cand = np.arange(len(guesses)) < k
        # les plus discriminants d'abord ; à égalité, les candidats (victoire immédiate)
        order = np.lexsort((~is_cand, -classes))[:GUESSES_PER_NODE]
        best = _linear(k, r)
        for g in order:
            value = self._value(codes[g], cell, r)
            if _better(value, best):
                best = value
            if self._done(best, k):
                break
        self._memo[key] = best
        return best

    # --- choix du coup -----------------------------------------------------------
    def choose(self, default: str) -> EndgameChoice | None:
        """Coup à jouer à la place de `default` (coup habituel du solveur), ou None
        pour garder `default`."""
        r = self.attempts_left
        if r < 2 or self.n < 3:
            return None
        if self.n <= r:
            if self.n > MAX_CANDIDATES_SAFE:
                return None
        elif self.n > MAX_CANDIDATES:
            return None
        self.speed = self.n <= MAX_CANDIDATES_SAFE
        self._prepare()
        root = np.arange(self.n)
        if default in self.cands:
            default_row = self.codes_all[self.cands.index(default)]
        else:
            default_row = _codes(words_to_matrix([default]), self.cand_arr)[0]
        default_value = self._value(default_row, root, r)
        if self._done(default_value, self.n):
            return None

        def key(value: tuple[float, float], word: str) -> tuple[float, float]:
            if not self.speed:
                return value[0], 0.0  # risque de perdre : seule la probabilité compte
            return value[0], value[1] + (0.0 if word in self.known_valid else UNKNOWN_WORD_PENALTY)

        best_value, best_word, best_key = default_value, default, key(default_value, default)
        classes = _classes(self.codes_all)
        ranked = np.argsort(-classes, kind="stable")
        cands = [i for i in ranked if i < self.n][:ROOT_GUESSES]
        known = [i for i in ranked if i >= self.n and self.guess_words[i] in self.known_valid][:ROOT_GUESSES]
        others = [i for i in ranked if i >= self.n and self.guess_words[i] not in self.known_valid][:ROOT_GUESSES]
        if self.speed_known_only and default_value[0] >= 1.0 - _EPS:
            others = []  # victoire déjà garantie : pas de refus à risquer pour gagner du temps
        # candidats (victoire immédiate possible), puis sondes déjà acceptées par le jeu
        for i in cands + known + others:
            word = self.guess_words[i]
            if word == default:
                continue
            value = self._value(self.codes_all[i], root, r)
            k = key(value, word)
            if _better(k, best_key, MIN_GAIN_ATTEMPTS if best_word == default else 0.0):
                best_value, best_word, best_key = value, word, k
            if self._done(best_value, self.n):
                break
        if best_word == default:
            return None
        return EndgameChoice(word=best_word, probe=best_word not in self.cands, p_win=round(best_value[0], 4),
                             expected_attempts=round(best_value[1], 3), p_default=round(default_value[0], 4),
                             expected_default=round(default_value[1], 3), default=default, candidates=self.n,
                             attempts_left=r, reason="vitesse" if default_value[0] >= 1.0 - _EPS else "risque",
                             letters=self.tested_letters(best_word))

    def tested_letters(self, word: str) -> list[str]:
        """Lettres du mot qui ne sont pas communes à tous les candidats : celles que
        le coup permet réellement de trancher (pour l'affichage)."""
        common = set(self.cands[0])
        for c in self.cands[1:]:
            common &= set(c)
        possible = set("".join(self.cands)) - common
        return sorted(set(word) & possible)
