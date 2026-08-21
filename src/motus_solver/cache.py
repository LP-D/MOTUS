from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

from .corpus import Corpus
from .feedback import ALPHABET
from .scoring import letter_frequencies, positional_frequencies
from .tree import best_guess_composite


def cache_key(letter: str, length: int) -> str:
    return f"{letter.upper()}_{length}"


def _compute_entry(
    letter: str,
    length: int,
    corpus: Corpus,
    global_freq: dict[str, float],
    positional_freq,
    blocklist: set[str] | None = None,
) -> dict | None:
    candidates = corpus.subset(letter, length)
    if blocklist:
        candidates = [w for w in candidates if w not in blocklist]
    if not candidates:
        return None
    guess, entropy, vowels = best_guess_composite(candidates, candidates, global_freq, positional_freq)
    return {"word": guess, "entropy": entropy, "vowels": vowels}


def build_root_cache(
    corpus: Corpus, workers: int = 1, blocklist: set[str] | None = None
) -> dict[str, dict]:
    """Précalcule le meilleur premier coup pour chaque couple (lettre imposée, longueur).

    Le coup 1 ne dépend que de (lettre, longueur) : il est donc identique à chaque
    partie partageant ces paramètres et peut être mis en cache une fois pour toutes.

    `blocklist` (mots confirmés rejetés par le dictionnaire de validation du jeu réel,
    cf. motus_solver.blocklist) exclut ces mots du calcul du meilleur coup, pour ne
    plus jamais les recommander une fois régénéré.

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
                entry = _compute_entry(letter, length, corpus, global_freq, positional_freq, blocklist)
                if entry is not None:
                    cache[cache_key(letter, length)] = entry
        return cache
    return _build_root_cache_parallel(corpus, lengths, workers, blocklist)


# --- état par processus worker, peuplé une fois par _init_worker (multiprocessing) ---
_CORPUS: Corpus | None = None
_GLOBAL_FREQ: dict[str, float] | None = None
_POSITIONAL_FREQ_CACHE: dict[int, object] = {}
_BLOCKLIST: set[str] | None = None


def _init_worker(words: list[str], blocklist: set[str] | None = None) -> None:
    global _CORPUS, _GLOBAL_FREQ, _BLOCKLIST
    _CORPUS = Corpus(words)
    _GLOBAL_FREQ = letter_frequencies(_CORPUS)
    _BLOCKLIST = blocklist


def _positional_freq_cached(length: int):
    if length not in _POSITIONAL_FREQ_CACHE:
        _POSITIONAL_FREQ_CACHE[length] = positional_frequencies(_CORPUS, length)
    return _POSITIONAL_FREQ_CACHE[length]


def _compute_group(item: tuple[str, int]) -> tuple[str, int, dict | None]:
    letter, length = item
    entry = _compute_entry(letter, length, _CORPUS, _GLOBAL_FREQ, _positional_freq_cached(length), _BLOCKLIST)
    return letter, length, entry


def _build_root_cache_parallel(
    corpus: Corpus, lengths: list[int], workers: int, blocklist: set[str] | None = None
) -> dict[str, dict]:
    items = [(letter, length) for length in lengths for letter in ALPHABET]
    cache: dict[str, dict] = {}
    with mp.Pool(
        processes=workers, initializer=_init_worker, initargs=(corpus.words, blocklist)
    ) as pool:
        for letter, length, entry in pool.imap_unordered(_compute_group, items):
            if entry is not None:
                cache[cache_key(letter, length)] = entry
    return cache


def save_cache(cache: dict[str, dict], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def load_cache(path: str | Path) -> dict[str, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
