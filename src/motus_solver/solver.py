from __future__ import annotations

from dataclasses import dataclass

from .cache import cache_key
from .corpus import Corpus
from .feedback import pattern_codes, pattern_to_code, words_to_matrix
from .scoring import letter_frequencies, positional_frequencies
from .tree import score_guess, top_guesses_entropy_pure


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
        strategy: str = "composite",
    ):
        if strategy not in ("composite", "entropy_pure"):
            raise ValueError(f"stratégie inconnue : {strategy!r}")
        # "composite" (défaut, poids inchangés) ou "entropy_pure" (alternative, non
        # activée par défaut) ; `root_cache` doit être le cache de la même stratégie.
        self.strategy = strategy
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

        if self.strategy == "entropy_pure":
            return [(w, e, 0) for w, e in top_guesses_entropy_pure(self.candidates, k=top_n)]

        candidates_arr = words_to_matrix(self.candidates)
        scored = []
        for guess in self.candidates:
            score, entropy, vowels = score_guess(
                guess, self.candidates, candidates_arr, self._global_freq, self._positional_freq
            )
            scored.append((score, guess, entropy, vowels))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [(guess, entropy, vowels) for _, guess, entropy, vowels in scored[:top_n]]

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
