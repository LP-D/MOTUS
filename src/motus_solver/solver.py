from __future__ import annotations

from dataclasses import dataclass

from .cache import DEFAULT_STRATEGY, STRATEGIES, cache_key
from .corpus import Corpus
from .endgame import MAX_ATTEMPTS, EndgameChoice, EndgamePlanner
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
_FREQ_CACHE: dict = {}  # fréquences du dernier corpus vu (composite)


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
        max_attempts: int = MAX_ATTEMPTS,
        endgame: bool = True,
        endgame_rare_probes: bool = False,
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
        # Mots jouables du groupe, candidats ou non : réservoir des mots sondes de fin
        # de partie (cf. motus_solver.endgame). Tuzmo n'impose pas de jouer un mot
        # compatible avec les retours précédents.
        self.pool: list[str] = list(candidates)
        self.max_attempts = max_attempts
        self.endgame = endgame
        # régime vitesse de la fin de partie : sondes jamais acceptées par le jeu permises
        # (refus possibles, sans perte d'essai) ; False pour le bot en jeu réel
        self.endgame_rare_probes = endgame_rare_probes
        # dernier choix de fin de partie qui a remplacé le coup habituel (affichage)
        self.last_endgame: EndgameChoice | None = None
        if not self.candidates:
            raise ValueError(f"aucun candidat pour letter={letter!r} length={length}")
        self.history: list[Move] = []
        # fréquences du corpus : composite seulement, calculées au 1er besoin (0,35 s
        # par partie, ~5 % d'une partie, payées jusqu'ici même en entropy_pure)
        self._freqs: tuple[dict[str, float], object] | None = None

    def suggest(self, top_n: int = 5) -> list[tuple[str, float, int]]:
        self.last_endgame = None
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
                    guess, self.candidates, candidates_arr, *self._frequencies()
                )
                scored.append((score, guess, entropy, vowels))
            scored.sort(key=lambda item: item[0], reverse=True)
            ranked = [(guess, entropy, vowels) for _, guess, entropy, vowels in scored[:pool]]
            values = [score for score, *_ in scored[:pool]]
        ranked = self._known_valid_first_among_near_ties(ranked, values)[:top_n]
        return self._endgame_first(ranked)

    def _frequencies(self) -> tuple[dict[str, float], object]:
        if self._freqs is None:
            # même corpus d'une partie à l'autre dans une boucle du bot : calcul unique
            corpus_key = (id(self.corpus), len(self.corpus))
            if _FREQ_CACHE.get("corpus") != corpus_key:
                _FREQ_CACHE.clear()
                _FREQ_CACHE.update(corpus=corpus_key, letters=letter_frequencies(self.corpus))
            if self.length not in _FREQ_CACHE:
                _FREQ_CACHE[self.length] = positional_frequencies(self.corpus, self.length)
            self._freqs = (_FREQ_CACHE["letters"], _FREQ_CACHE[self.length])
        return self._freqs

    @property
    def attempts_left(self) -> int:
        return self.max_attempts - len(self.history)

    def _endgame_first(self, ranked: list) -> list:
        """Plus de candidats que d'essais restants : si le coup habituel ne garantit
        pas la victoire, joue le coup (souvent un mot sonde hors candidats) qui
        maximise la probabilité de gagner (cf. motus_solver.endgame)."""
        if not self.endgame or not self.history or not ranked:
            return ranked
        choice = EndgamePlanner(self.candidates, self.pool, self.attempts_left, self.known_valid,
                                speed_known_only=not self.endgame_rare_probes).choose(ranked[0][0])
        if choice is None:
            return ranked
        self.last_endgame = choice
        return [(choice.word, choice.p_win, 0), *[item for item in ranked if item[0] != choice.word]]

    def discard(self, word: str) -> None:
        """Mot refusé par le jeu : retiré des candidats et des mots sondes."""
        word = word.upper()
        if word in self.candidates:
            self.candidates.remove(word)
        if word in self.pool:
            self.pool.remove(word)

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
