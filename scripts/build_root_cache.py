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

from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import STRATEGIES, build_root_cache, load_cache, refresh_blocklisted_entries, save_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.draws import annotate_draw_status, load_draw_counts  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "root_cache.json"
DEFAULT_OUTPUT_ENTROPY_PURE = ROOT_DIR / "data" / "root_cache_entropy_pure.json"
DEFAULT_BLOCKLIST = ROOT_DIR / "data" / "known_invalid_words.json"
# statut de tirage de chaque groupe (observé / non observé, statut incertain),
# informatif uniquement, cf. motus_solver.draws
DEFAULT_DRAWS = ROOT_DIR / "data" / "group_draws.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS))
    parser.add_argument(
        "--output",
        default=None,
        help="Défaut : data/root_cache.json (composite) ou data/root_cache_entropy_pure.json "
        "(--strategy entropy_pure) — fichiers séparés, jamais mélangés.",
    )
    parser.add_argument(
        "--strategy",
        choices=STRATEGIES,
        default="composite",
        help="Stratégie de suggestion précalculée (défaut composite, inchangé).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (mp.cpu_count() or 2) - 1),
        help="1 = séquentiel (comportement d'origine). >1 = un process par groupe (lettre, longueur).",
    )
    parser.add_argument(
        "--refresh-blocklisted",
        action="store_true",
        help="Ne recalcule que les entrées du cache existant dont le mot est dans la liste "
        "noire (data/known_invalid_words.json), au lieu de tout reconstruire.",
    )
    args = parser.parse_args()
    output = args.output or (str(DEFAULT_OUTPUT) if args.strategy == "composite" else str(DEFAULT_OUTPUT_ENTROPY_PURE))

    corpus = Corpus.from_file(args.corpus_path)
    if args.refresh_blocklisted:
        cache = load_cache(output)
        blocklist = load_blocklist(DEFAULT_BLOCKLIST)
        start = time.time()
        refreshed = refresh_blocklisted_entries(cache, corpus, blocklist, workers=args.workers, strategy=args.strategy)
        annotate_draw_status(cache, load_draw_counts(DEFAULT_DRAWS))  # une entrée recalculée perd son statut
        save_cache(cache, output)
        print(f"[{args.strategy}] {len(refreshed)} entrée(s) recalculée(s) en {time.time() - start:.1f}s : "
              + ", ".join(f"{k} -> {cache.get(k, {}).get('word')}" for k in refreshed))
        return

    n_groups = len({(w[0], len(w)) for w in corpus})
    print(
        f"[{args.strategy}] {len(corpus)} mot(s), {n_groups} groupe(s) (lettre, longueur) à "
        f"précalculer, workers={args.workers}."
    )

    # la liste noire (mots refusés par le vrai jeu) est toujours exclue : ni coup 1
    # ni coup de repli ne doit être un mot déjà connu comme refusé
    blocklist = load_blocklist(DEFAULT_BLOCKLIST)
    start = time.time()
    cache = build_root_cache(corpus, workers=args.workers, strategy=args.strategy, blocklist=blocklist)
    elapsed = time.time() - start
    annotate_draw_status(cache, load_draw_counts(DEFAULT_DRAWS))

    save_cache(cache, output)
    print(f"[{args.strategy}] {len(cache)} entrée(s) de cache écrites dans {output} en {elapsed:.1f}s.")


if __name__ == "__main__":
    main()
