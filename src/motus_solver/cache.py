from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

from .corpus import Corpus
from .feedback import ALPHABET
from .scoring import letter_frequencies, positional_frequencies
from .tree import best_guess_entropy_pure, top_guesses_composite

STRATEGIES = ("composite", "entropy_pure")
# Coups 1 de repli stockés par groupe (composite) : si le meilleur coup est refusé
# par le dictionnaire du jeu, le suivant est pris instantanément au lieu d'un
# recalcul complet (run d'amélioration n° 1 : 3 rejets d'affilée sur R,9 =
# 3 x ~290 s de recalcul ; 4 x ~7,5 s sur A,7).
ROOT_ALTERNATIVES = 9


def cache_key(letter: str, length: int) -> str:
    return f"{letter.upper()}_{length}"


def _compute_entry(
    letter: str,
    length: int,
    corpus: Corpus,
    global_freq: dict[str, float],
    positional_freq,
    blocklist: set[str] | None = None,
    strategy: str = "composite",
) -> dict | None:
    candidates = corpus.subset(letter, length)
    if blocklist:
        candidates = [w for w in candidates if w not in blocklist]
    if not candidates:
        return None
    if strategy == "entropy_pure":
        # Coup 1 = mot du corpus qui maximise l'entropie de Shannon pure sur ce
        # couple (lettre, longueur) — même principe de cache que le composite (le
        # coup 1 ne dépend que de (lettre, longueur), donc calculable une fois pour
        # toutes), mais un fichier séparé (cf. build_root_cache) puisque les deux
        # stratégies ne choisissent pas forcément le même mot.
        guess, entropy = best_guess_entropy_pure(candidates)
        return {"word": guess, "entropy": entropy}
    ranked = top_guesses_composite(candidates, candidates, global_freq, positional_freq, k=ROOT_ALTERNATIVES + 1)
    (guess, _score, entropy, vowels), rest = ranked[0], ranked[1:]
    return {
        "word": guess, "entropy": entropy, "vowels": vowels,
        # classement calculé sur le groupe complet (hors liste noire) : si un coup
        # est refusé, le suivant est quasi identique à un recalcul sans ce mot
        # (1 mot retiré sur des centaines/milliers), sans en payer le coût
        "alternatives": [{"word": w, "entropy": e, "vowels": v} for w, _s, e, v in rest],
    }


def build_root_cache(
    corpus: Corpus,
    workers: int = 1,
    blocklist: set[str] | None = None,
    strategy: str = "composite",
) -> dict[str, dict]:
    """Précalcule le meilleur premier coup pour chaque couple (lettre imposée, longueur).

    Le coup 1 ne dépend que de (lettre, longueur) : il est donc identique à chaque
    partie partageant ces paramètres et peut être mis en cache une fois pour toutes.

    `blocklist` (mots confirmés rejetés par le dictionnaire de validation du jeu réel,
    cf. motus_solver.blocklist) exclut ces mots du calcul du meilleur coup, pour ne
    plus jamais les recommander une fois régénéré.

    `strategy` : `"composite"` (défaut, inchangé — `best_guess_composite`, entrées
    `{word, entropy, vowels}`) ou `"entropy_pure"` (`best_guess_entropy_pure`,
    entrées `{word, entropy}`, pas de `vowels`). Les deux caches sont indépendants
    (fichiers séparés, cf. `scripts/simulate_resolution_rate.py`) — ce paramètre ne
    modifie ni les poids ni le comportement de la stratégie composite existante.

    Chaque groupe (lettre, longueur) est indépendant des autres : `workers > 1`
    parallélise leur calcul via multiprocessing (un process par cœur), utile sur un
    corpus élargi où quelques groupes très denses dominent le temps total. Résultat
    strictement identique quel que soit `workers` (même calcul, juste réparti).
    """
    lengths = sorted({len(word) for word in corpus})
    if workers <= 1:
        global_freq = letter_frequencies(corpus)
        cache: dict[str, dict] = {}
        for length in lengths:
            positional_freq = positional_frequencies(corpus, length)
            for letter in ALPHABET:
                entry = _compute_entry(
                    letter, length, corpus, global_freq, positional_freq, blocklist, strategy
                )
                if entry is not None:
                    cache[cache_key(letter, length)] = entry
        return cache
    return _build_root_cache_parallel(corpus, lengths, workers, blocklist, strategy)


# --- état par processus worker, peuplé une fois par _init_worker (multiprocessing) ---
_CORPUS: Corpus | None = None
_GLOBAL_FREQ: dict[str, float] | None = None
_POSITIONAL_FREQ_CACHE: dict[int, object] = {}
_BLOCKLIST: set[str] | None = None
_STRATEGY: str = "composite"


def _init_worker(words: list[str], blocklist: set[str] | None = None, strategy: str = "composite") -> None:
    global _CORPUS, _GLOBAL_FREQ, _BLOCKLIST, _STRATEGY
    _CORPUS = Corpus(words)
    _GLOBAL_FREQ = letter_frequencies(_CORPUS)
    _BLOCKLIST = blocklist
    _STRATEGY = strategy


def _positional_freq_cached(length: int):
    if length not in _POSITIONAL_FREQ_CACHE:
        _POSITIONAL_FREQ_CACHE[length] = positional_frequencies(_CORPUS, length)
    return _POSITIONAL_FREQ_CACHE[length]


def _compute_group(item: tuple[str, int]) -> tuple[str, int, dict | None]:
    letter, length = item
    entry = _compute_entry(
        letter, length, _CORPUS, _GLOBAL_FREQ, _positional_freq_cached(length), _BLOCKLIST, _STRATEGY
    )
    return letter, length, entry


def _build_root_cache_parallel(
    corpus: Corpus,
    lengths: list[int],
    workers: int,
    blocklist: set[str] | None = None,
    strategy: str = "composite",
) -> dict[str, dict]:
    items = [(letter, length) for length in lengths for letter in ALPHABET]
    cache: dict[str, dict] = {}
    with mp.Pool(
        processes=workers, initializer=_init_worker, initargs=(corpus.words, blocklist, strategy)
    ) as pool:
        for letter, length, entry in pool.imap_unordered(_compute_group, items):
            if entry is not None:
                cache[cache_key(letter, length)] = entry
    return cache


def refresh_blocklisted_entries(
    cache: dict[str, dict],
    corpus: Corpus,
    blocklist: set[str],
    workers: int = 1,
    strategy: str = "composite",
) -> list[str]:
    """Recalcule uniquement les entrées dont le mot est en liste noire (mot refusé
    par le vrai dictionnaire du jeu) — même calcul que `build_root_cache` avec
    `blocklist`, limité à ces groupes. Évite qu'un groupe dont le coup 1 en cache
    est invalide retombe à chaque partie sur le repli dynamique, très lent sur
    les groupes denses (mesuré : ~93s sur E,9 ; davantage sur R,9).

    Modifie `cache` en place, retourne les clés recalculées."""
    stale = sorted(key for key, entry in cache.items() if entry["word"] in blocklist)
    if not stale:
        return []
    items = [(key.split("_")[0], int(key.split("_")[1])) for key in stale]
    if workers <= 1:
        global_freq = letter_frequencies(corpus)
        results = [
            (letter, length, _compute_entry(
                letter, length, corpus, global_freq, positional_frequencies(corpus, length), blocklist, strategy
            ))
            for letter, length in items
        ]
    else:
        with mp.Pool(
            processes=min(workers, len(items)), initializer=_init_worker,
            initargs=(corpus.words, blocklist, strategy),
        ) as pool:
            results = list(pool.imap_unordered(_compute_group, items))
    for letter, length, entry in results:
        key = cache_key(letter, length)
        if entry is None:
            cache.pop(key, None)
        else:
            cache[key] = entry
    return stale


def save_cache(cache: dict[str, dict], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def load_cache(path: str | Path) -> dict[str, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
