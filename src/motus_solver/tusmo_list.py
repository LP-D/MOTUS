"""Liste de solutions de Tusmo, estimée, et conseiller « comme Tusmo ».

Le mode Entraînement de Tusmo note chaque coup (`percent`) : c'est l'entropie du
retour du mot sur les solutions encore possibles de SA liste, divisée par celle du
meilleur mot (vérifié sur le journal : à 3 candidats, un mot qui les coupe en 2 + 1
vaut 0,918 / 1,585 = 58 %, la valeur observée ; à 4, (3, 1) vaut 41 %, (2, 2) 50 %).
Le meilleur mot (`bestWord`) maximise donc cette entropie. Il se distingue du solveur
par la liste : ~360 solutions par groupe (lettre, longueur) contre des milliers de
mots dans le corpus.

Cette liste n'est pas publique. On l'estime mot par mot (probabilité d'appartenance)
avec ce que révèle le journal d'entraînement (scripts/scrape_tusmo_training.py) :
- les solutions connues (réponses de l'entraînement et de /infinite, même liste) ;
- la taille de la liste du groupe (candidats avant le coup 1) ;
- après chaque coup, le nombre de solutions encore compatibles : une contrainte
  « exactement c mots de la liste parmi les mots du corpus compatibles ».
Les contraintes sont ajustées par itérations proportionnelles (IPF) : dans chaque
ensemble, la masse des mots non encore confirmés est ramenée au nombre attendu.
Une contrainte satisfaite par les seules solutions connues exclut tous les autres
mots de l'ensemble (probabilité 0).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .cache import cache_key
from .feedback import pattern_codes, pattern_to_code, words_to_matrix
from .inference import weighted_entropy

RESULT_CODES = {"correct": "2", "present": "1", "absent": "0"}
IPF_PASSES = 40
TIE_REL = 1e-6  # écart relatif d'entropie en dessous duquel deux coups sont à égalité


@dataclass
class TrainingGame:
    answer: str | None  # None : partie non terminée (solution hors corpus)
    moves: list[tuple[str, str, int]]  # (mot, retour 0/1/2, solutions Tusmo restantes après le coup)


@dataclass
class GroupEvidence:
    size: int | None = None  # taille de la liste de Tusmo pour le groupe
    games: list[TrainingGame] = field(default_factory=list)
    answers: set[str] = field(default_factory=set)
    best_first: str | None = None  # coup 1 conseillé par Tusmo (déterministe par groupe)
    best_words: set[str] = field(default_factory=set)  # mots conseillés : acceptés par Tusmo
    accepted: set[str] = field(default_factory=set)  # mots joués ou conseillés en entraînement


def load_evidence(training_log: Path, extra_answers: list[tuple[str, int, str]] = ()) -> dict[str, GroupEvidence]:
    """Indices par groupe (clé `cache_key`) tirés du journal d'entraînement, plus des
    solutions connues d'ailleurs (lettre, longueur, mot), ex. /infinite."""
    groups: dict[str, GroupEvidence] = {}
    if training_log.exists():
        for line in training_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            ev = groups.setdefault(cache_key(rec["first_letter"], rec["word_len"]), GroupEvidence())
            report = rec.get("report") or {}
            moves = [(m["word"], m["pattern"], m["candidates_left_tusmo"]) for m in rec["moves"]
                     if m.get("pattern") and m.get("candidates_left_tusmo") is not None]
            ev.games.append(TrainingGame(answer=report.get("answer"), moves=moves))
            ev.accepted.update(w for w, _, _ in moves)
            if report.get("answer"):
                ev.answers.add(report["answer"].upper())
            for i, m in enumerate(report.get("moves", ())):
                if i == 0:
                    ev.size = m["candidatesBefore"]
                    ev.best_first = m["bestWord"]
                ev.accepted.add(m["bestWord"])
                if m["candidatesBefore"] > 1:
                    ev.best_words.add(m["bestWord"])
    for letter, length, word in extra_answers:
        groups.setdefault(cache_key(letter, length), GroupEvidence()).answers.add(word.upper())
    return groups


def estimate_membership(universe: list[str], ev: GroupEvidence, forget: set[str] = frozenset(),
                        passes: int = IPF_PASSES) -> np.ndarray:
    """Probabilité que chaque mot de `universe` soit dans la liste de Tusmo. `forget` :
    solutions à ignorer (et les parties où elles étaient la réponse) — leave-one-out
    des duels simulés, dont le mot est tiré parmi les solutions connues."""
    n = len(universe)
    index = {w: i for i, w in enumerate(universe)}
    known = np.zeros(n, dtype=bool)
    for w in ev.answers - set(forget):
        if w in index:
            known[index[w]] = True
    size = ev.size if ev.size is not None else max(int(known.sum()), 1)
    arr = words_to_matrix(universe)
    masks, targets = [np.ones(n, dtype=bool)], [size]
    for game in ev.games:
        if game.answer in forget:
            continue
        mask = np.ones(n, dtype=bool)
        for word, pattern, left in game.moves:
            mask &= pattern_codes(word, arr) == pattern_to_code(pattern)
            masks.append(mask.copy())
            targets.append(left)
    free = n - int(known.sum())
    p = np.full(n, max(size - int(known.sum()), 0) / free if free else 0.0)
    p[known] = 1.0
    open_masks = [(m & ~known, max(t - int((m & known).sum()), 0)) for m, t in zip(masks, targets)]
    for m, t in open_masks:
        if t == 0:
            p[m] = 0.0
    for _ in range(passes):
        for m, t in open_masks:
            s = p[m].sum()
            if t and s > 0:
                p[m] = np.minimum(p[m] * (t / s), 1.0)
    p[known] = 1.0
    return p


class TusmoAdvisor:
    """Conseiller « comme Tusmo » pour une partie : maximise l'entropie du retour sur
    les solutions estimées de la liste Tusmo (pondérées par leur probabilité). À
    égalité, le candidat le plus probable (qui peut gagner tout de suite)."""

    def __init__(self, letter: str, length: int, universe: list[str], membership: np.ndarray,
                 guess_pool: list[str], best_first: str | None = None):
        self.letter, self.length = letter.upper(), length
        self.universe = list(universe)
        self.arr = words_to_matrix(self.universe)
        self.membership = membership.astype(float)
        self.alive = np.ones(len(universe), dtype=bool)  # compatibles avec les retours
        self.pool = list(dict.fromkeys(guess_pool))
        self.best_first = best_first
        self.history: list[tuple[str, str]] = []

    def update(self, word: str, pattern: str) -> None:
        self.history.append((word.upper(), pattern))
        self.alive &= pattern_codes(word.upper(), self.arr) == pattern_to_code(pattern)

    def discard(self, word: str) -> None:
        """Mot refusé par le jeu : ni coup possible ni solution."""
        word = word.upper()
        self.pool = [w for w in self.pool if w != word]
        if word == self.best_first:
            self.best_first = None
        if word in self.universe:
            self.membership[self.universe.index(word)] = 0.0

    def candidates(self) -> tuple[list[str], np.ndarray]:
        """Solutions encore possibles et leurs poids normalisés. Si l'estimation n'en
        garde aucune (solution hors estimation), tous les mots compatibles, à parts égales."""
        idx = np.flatnonzero(self.alive)
        weights = self.membership[idx]
        if weights.sum() <= 0:
            weights = np.ones(len(idx))
        keep = weights > 0
        idx, weights = idx[keep], weights[keep]
        return [self.universe[i] for i in idx], weights / weights.sum() if len(idx) else weights

    def ranked(self, top_n: int = 5) -> list[tuple[str, float]]:
        """Meilleurs coups (mot, entropie en bits), du meilleur au moins bon."""
        cands, weights = self.candidates()
        if not cands:
            return []
        played = {w for w, _ in self.history}
        if len(cands) == 1:
            return [(cands[0], 0.0)]
        if not self.history and self.best_first and self.best_first not in played:
            return [(self.best_first, float("nan"))]
        pool = [w for w in dict.fromkeys(self.pool + cands) if w not in played]
        h = weighted_entropy(words_to_matrix(pool), words_to_matrix(cands), weights)
        win = np.zeros(len(pool))
        pos = {w: i for i, w in enumerate(pool)}
        for c, wt in zip(cands, weights):
            win[pos[c]] = wt
        best = h.max()
        # tri : entropie (à TIE_REL près), puis chance de gagner tout de suite
        key = np.where(h >= best - abs(best) * TIE_REL, best, h)
        order = np.lexsort((-win, -key))
        return [(pool[i], float(h[i])) for i in order[:top_n]]

    def choose(self) -> str | None:
        ranked = self.ranked(top_n=1)
        return ranked[0][0] if ranked else None


class TusmoModel:
    """Indices de tous les groupes, chargés une fois ; fabrique un conseiller par partie."""

    def __init__(self, evidence: dict[str, GroupEvidence]):
        self.evidence = evidence
        self._cache: dict[tuple[str, frozenset], np.ndarray] = {}
        # mots acceptés par le vrai jeu en entraînement (joués ou conseillés par Tusmo)
        self.accepted: frozenset[str] = frozenset(w for ev in evidence.values() for w in ev.accepted)

    @classmethod
    def load(cls, data_dir: Path) -> "TusmoModel":
        extra = []
        rev = data_dir / "revealed_solutions.jsonl"
        if rev.exists():
            extra += [(r["letter"], r["length"], r["answer"]) for r in
                      map(json.loads, filter(str.strip, rev.read_text(encoding="utf-8").splitlines()))]
        stats = data_dir / "dashboard_stats.json"
        if stats.exists():
            extra += [(g["letter"], g["length"], g["solution"]) for g in json.loads(stats.read_text(encoding="utf-8"))
                      if g.get("solution") and g.get("mode", "infinite") == "infinite"]
        return cls(load_evidence(data_dir / "tusmo_training_log.jsonl", extra))

    def group(self, letter: str, length: int) -> GroupEvidence:
        return self.evidence.get(cache_key(letter, length), GroupEvidence())

    def advisor(self, letter: str, length: int, corpus_words: list[str], blocklist: set[str] = frozenset(),
                forget: set[str] = frozenset()) -> TusmoAdvisor:
        ev = self.group(letter, length)
        forget = frozenset(w.upper() for w in forget)
        universe = sorted({w for w in corpus_words if w not in blocklist} | (ev.answers - forget))
        key = (cache_key(letter, length), forget & ev.answers)
        if key not in self._cache:
            self._cache[key] = estimate_membership(universe, ev, forget=forget)
        pool = [w for w in universe if w not in blocklist] + sorted(ev.best_words - forget)
        return TusmoAdvisor(letter, length, universe, self._cache[key].copy(), pool, ev.best_first)
