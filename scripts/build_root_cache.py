#!/usr/bin/env python3
"""Précalcule le cache du coup 1 (motus_solver.cache.build_root_cache) pour tout le
corpus et l'écrit en JSON.

Chaque groupe (lettre imposée, longueur) est indépendant : ce script parallélise leur
calcul via multiprocessing (`--workers`), utile sur un corpus élargi où quelques
groupes très denses (ex. R,9) dominent le temps total.

Script autonome : ne dépend ni de la CLI (`motus_solver.cli`) ni du bot Playwright.

    python scripts/build_root_cache.py --workers 8
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.cache import build_root_cache, save_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "root_cache.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (mp.cpu_count() or 2) - 1),
        help="1 = séquentiel (comportement d'origine). >1 = un process par groupe (lettre, longueur).",
    )
    args = parser.parse_args()

    corpus = Corpus.from_file(args.corpus_path)
    n_groups = len({(w[0], len(w)) for w in corpus})
    print(f"{len(corpus)} mot(s), {n_groups} groupe(s) (lettre, longueur) à précalculer, workers={args.workers}.")

    start = time.time()
    cache = build_root_cache(corpus, workers=args.workers)
    elapsed = time.time() - start

    save_cache(cache, args.output)
    print(f"{len(cache)} entrée(s) de cache écrites dans {args.output} en {elapsed:.1f}s.")


if __name__ == "__main__":
    main()
