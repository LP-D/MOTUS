#!/usr/bin/env python3
"""Chrono isolée de move1 (best_guess_composite) sur le groupe (R, 9) — le plus dense
du corpus (21070 mots), mono-thread, sans parallélisation ni simulation complète.

Objectif : isoler le gain réel de la vectorisation numpy de `best_guess_composite`
sur le pire cas, indépendamment de la parallélisation de `build_root_cache` (le run
à 130 groupes / 7 workers, 772s, ne permet pas de distinguer les deux effets).

Référence : 329.15s avec l'ancienne implémentation (boucle Python par guess,
mono-thread, move1 seul sur R,9) — mesurée avant la vectorisation, cf. session
précédente et tests/test_tree_vectorized.py::test_vectorized_matches_frozen_naive_reference_on_r9_group.

    python scripts/benchmark_move1_r9.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.scoring import letter_frequencies, positional_frequencies  # noqa: E402
from motus_solver.tree import best_guess_composite  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "corpus_fr.txt"
LETTER, LENGTH = "R", 9
NAIVE_REFERENCE_SECONDS = 329.15  # ancienne implémentation, mono-thread, move1 seul


def main() -> None:
    corpus = Corpus.from_file(CORPUS_PATH)
    candidates = corpus.subset(LETTER, LENGTH)
    print(f"Groupe ({LETTER}, {LENGTH}) : {len(candidates)} candidats.")

    global_freq = letter_frequencies(corpus)
    positional_freq = positional_frequencies(corpus, LENGTH)

    start = time.time()
    guess, entropy, vowels = best_guess_composite(candidates, candidates, global_freq, positional_freq)
    elapsed = time.time() - start

    ratio = NAIVE_REFERENCE_SECONDS / elapsed if elapsed > 0 else float("inf")
    print(f"move1 vectorisé (mono-thread) : {elapsed:.2f}s  guess={guess} entropy={entropy}")
    print(f"Référence ancienne implémentation (mono-thread) : {NAIVE_REFERENCE_SECONDS:.2f}s")
    print(f"Ratio de gain : {ratio:.2f}x", "(gain)" if ratio > 1.1 else "(marginal ou nul)")

    # Constat (session de vectorisation) : la vectorisation numpy pure de
    # best_guess_composite ne réduit pas le volume de calcul brut (toujours O(n²) —
    # l'ancienne implémentation appelait déjà pattern_codes en vectorisé numpy par
    # guess, donc le batching sur les guesses n'élimine pas de travail, seulement de
    # la surcharge d'appel Python, marginale ici face au calcul lui-même). Le ratio
    # mesuré ci-dessus est donc attendu proche de 1x sur ce pire cas.
    #
    # Le vrai levier de performance actuel est la parallélisation de
    # `build_root_cache` (multiprocessing.Pool, un process par groupe lettre/longueur,
    # cf. cache.py::_build_root_cache_parallel) : 772s pour les 130 groupes avec 7
    # workers, contre ~46-70 min estimées en séquentiel. C'est un point d'attention
    # pour la suite : si le corpus est élargi (plus de candidats par groupe dense) ou
    # si l'environnement d'exécution dispose de moins de cœurs, ce levier s'amenuise
    # d'autant, et le coût de move1 sur les groupes denses (R, C, E côté longueur 8-9)
    # redeviendra le facteur limitant sans un vrai changement d'algorithme (ex.
    # approximation/échantillonnage de l'entropie, hors scope ici — changerait la
    # logique de scoring) ou une implémentation compilée (numba/Cython).


if __name__ == "__main__":
    main()
