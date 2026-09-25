from __future__ import annotations

from dataclasses import dataclass

from .cache import DEFAULT_STRATEGY, STRATEGIES, cache_key
from .corpus import Corpus
from .feedback import pattern_codes, pattern_to_code, words_to_matrix
from .scoring import letter_frequencies, positional_frequencies
from .tree import score_guess, top_guesses_entropy_pure

# Départage des coups calculés dynamiquement (coups 2 et suivants, et repli du coup 1
# quand tous les candidats en cache sont refusés) : parmi les coups dont le score est
# à moins de NEAR_TIE_REL (écart relatif) du meilleur, un mot déjà accepté par le jeu
# passe en premier. Évite les rafales de rejets sur des non-mots du corpus (phase 2 du
# 24/09/2026 : 23 rejets, tous à ces coups-là). Ni les poids du composite ni le calcul
# d'entropie ne changent : seul l'ordre entre coups quasi égaux est touché.
# Valeur retenue sur rejeu hors ligne de 788 solutions réelles (cf.
# docs/diagnostics/2026-09-25_phase0_defaut_departage_validation.md).
NEAR_TIE_REL = 0.02
NEAR_TIE_POOL = 20  # coups examinés au plus pour le départage


@dataclass
class Move:
    word: str
    pattern: str


class Solver:
    def __init__(
        self,
        letter: str,
        length: int,
        corpus: Corpus,
        root_cache: dict[str, dict] | None = None,
        blocklist: set[str] | None = None,
        known_valid: set[str] | None = None,
        strategy: str = DEFAULT_STRATEGY,
        near_tie: float | None = None,
    ):
        if strategy not in STRATEGIES:
            raise ValueError(f"stratégie inconnue : {strategy!r}")
        # "entropy_pure" (défaut depuis le 25/09/2026) ou "composite" (poids
        # inchangés) ; `root_cache` doit être le cache de la même stratégie
        # (cache.ROOT_CACHE_FILES).
        self.strategy = strategy
        self.near_tie = NEAR_TIE_REL if near_tie is None else near_tie
        self.letter = letter.upper()
        # Mots déjà ACCEPTÉS par le vrai jeu : au coup 1, préférés parmi le coup
        # racine et ses replis (scores quasi égaux), pour ne pas payer un rejet.
        self.known_valid: set[str] = known_valid or set()
        self.length = length
        self.corpus = corpus
        self.root_cache = root_cache
        candidates = corpus.subset(self.letter, length)
        if blocklist:
            # Mots déjà confirmés rejetés par le dictionnaire de validation du jeu
            # réel (cf. motus_solver.blocklist) : jamais reproposés, quelle que soit
            # la partie — évite de redécouvrir le même rejet à chaque coup 1.
            candidates = [w for w in candidates if w not in blocklist]
        self.candidates: list[str] = candidates
        if not self.candidates:
            raise ValueError(f"aucun candidat pour letter={letter!r} length={length}")
        self.history: list[Move] = []
        self._global_freq = letter_frequencies(corpus)
        self._positional_freq = positional_frequencies(corpus, length)

    def suggest(self, top_n: int = 5) -> list[tuple[str, float, int]]:
        if not self.history and self.root_cache is not None:
            cached = self.root_cache.get(cache_key(self.letter, self.length))
            # Le mot en cache n'est utilisé que s'il est toujours un candidat valide :
            # un appelant peut avoir retiré ce mot de self.candidates (ex. rejeté par
            # le jeu réel comme "Mot inconnu") sans que l'historique du solveur ait
            # changé — sans ce garde-fou, le cache renverrait indéfiniment le même
            # mot déjà écarté au lieu de retomber sur le calcul dynamique.
            # Puis les coups de repli précalculés du groupe (cf. cache.ROOT_ALTERNATIVES) :
            # un coup 1 refusé ne relance pas un calcul complet (jusqu'à ~290 s sur R,9).
            # Parmi ceux encore candidats, un mot déjà accepté par le jeu passe en
            # premier (run d'amélioration n° 2 : 9 rejets au coup 1 sur 11, et un
            # rafraîchissement du cache pouvait remplacer un coup 1 prouvé valide par
            # un mot jamais testé). Sinon, ordre du classement composite.
            if cached is not None:
                ranked = [e for e in (cached, *cached.get("alternatives", ())) if e["word"] in self.candidates]
                if ranked:
                    chosen = next((e for e in ranked if e["word"] in self.known_valid), ranked[0])
                    return [(chosen["word"], chosen["entropy"], chosen.get("vowels", 0))]

        pool = max(top_n, NEAR_TIE_POOL) if self.near_tie and self.known_valid else top_n
        if self.strategy == "entropy_pure":
            ranked = [(w, e, 0) for w, e in top_guesses_entropy_pure(self.candidates, k=pool)]
            values = [e for _, e, _ in ranked]
        else:
            candidates_arr = words_to_matrix(self.candidates)
            scored = []
            for guess in self.candidates:
                score, entropy, vowels = score_guess(
                    guess, self.candidates, candidates_arr, self._global_freq, self._positional_freq
                )
                scored.append((score, guess, entropy, vowels))
            scored.sort(key=lambda item: item[0], reverse=True)
            ranked = [(guess, entropy, vowels) for _, guess, entropy, vowels in scored[:pool]]
            values = [score for score, *_ in scored[:pool]]
        return self._known_valid_first_among_near_ties(ranked, values)[:top_n]

    def _known_valid_first_among_near_ties(self, ranked: list, values: list[float]) -> list:
        """Remonte en tête le premier mot déjà accepté par le jeu parmi les coups à
        moins de `near_tie` (écart relatif) du meilleur score ; sinon, ordre inchangé."""
        if not ranked or not self.near_tie or not self.known_valid:
            return ranked
        threshold = values[0] - abs(values[0]) * self.near_tie
        for i, (item, value) in enumerate(zip(ranked, values)):
            if value < threshold:
                break
            if item[0] in self.known_valid:
                return ranked if i == 0 else [item, *ranked[:i], *ranked[i + 1:]]
        return ranked

    def play(self, word: str) -> None:
        self.history.append(Move(word=word.upper(), pattern=""))

    def update(self, pattern: str) -> None:
        if not self.history or self.history[-1].pattern:
            raise ValueError("appelle play(word) avant update(pattern)")
        guess = self.history[-1].word
        self.history[-1] = Move(word=guess, pattern=pattern)

        target_code = pattern_to_code(pattern)
        candidates_arr = words_to_matrix(self.candidates)
        codes = pattern_codes(guess, candidates_arr)
        self.candidates = [w for w, c in zip(self.candidates, codes) if c == target_code]

    def is_solved(self) -> bool:
        return len(self.candidates) == 1

    @property
    def solution(self) -> str | None:
        return self.candidates[0] if self.is_solved() else None
