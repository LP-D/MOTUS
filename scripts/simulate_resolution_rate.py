#!/usr/bin/env python3
"""Simulation exhaustive du solveur Motus sur tout le corpus.

Pour chaque mot du corpus, simule une partie complète (le mot est la cible, l'arbre
de décision est le joueur) et enregistre le nombre de coups nécessaires (borné à 6 ;
au-delà = échec). Agrège les résultats par tranche de longueur et exporte un tableau
(taux de résolution en <=2..<=6 coups, taux d'échec) dans un sheet dédié d'un
classeur Excel multi-sheet.

`--strategy` sélectionne l'algorithme de suggestion : `composite` (défaut, actuel,
`best_guess_composite` — utilise `--root-cache` s'il existe), `entropy_pure`
(`best_guess_entropy_pure` — utilise `--root-cache-entropy-pure` s'il existe, un
fichier séparé du cache composite puisque les deux stratégies ne choisissent pas
forcément le même mot pour un même (lettre, longueur) ; générer ce cache via
`python scripts/build_root_cache.py --strategy entropy_pure`), ou `both` (lance
les deux, séquentiellement, et exporte un tableau comparatif côte à côte). Sans
cache dédié, chaque stratégie recalcule dynamiquement son coup 1 (comportement
d'origine, juste plus lent à grande échelle). N'implique aucune décision sur la
stratégie par défaut du solveur/bot — diagnostic uniquement.

Script autonome : ne dépend ni de la CLI (`motus_solver.cli`) ni du bot Playwright,
uniquement du package `motus_solver`. Utilisable directement :

    python scripts/simulate_resolution_rate.py --limit 2000 --workers 4 --strategy both
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.cache import cache_key, load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.export import add_sheet, open_or_create_workbook, save_workbook  # noqa: E402
from motus_solver.feedback import pattern_codes, pattern_string, pattern_to_code, words_to_matrix  # noqa: E402
from motus_solver.scoring import letter_frequencies, positional_frequencies  # noqa: E402
from motus_solver.tree import best_guess_composite, best_guess_entropy_pure  # noqa: E402

MAX_ATTEMPTS = 6
THRESHOLDS = [2, 3, 4, 5, 6]
STRATEGIES = ("composite", "entropy_pure")

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "simulation_report.xlsx"
DEFAULT_ROOT_CACHE = ROOT_DIR / "data" / "root_cache.json"
DEFAULT_ROOT_CACHE_ENTROPY_PURE = ROOT_DIR / "data" / "root_cache_entropy_pure.json"
DEFAULT_CHECKPOINT = ROOT_DIR / "data" / "simulation_checkpoint.json"


def _best_guess(strategy: str, candidates: list[str], global_freq: dict, positional_freq) -> str:
    if strategy == "composite":
        guess, _entropy, _vowels = best_guess_composite(candidates, candidates, global_freq, positional_freq)
        return guess
    if strategy == "entropy_pure":
        guess, _entropy = best_guess_entropy_pure(candidates)
        return guess
    raise ValueError(f"stratégie inconnue : {strategy!r} (attendu : {STRATEGIES})")

# État par processus worker, rempli une fois par _init_worker (évite de repayer le
# coût de letter_frequencies()/positional_frequencies() à chaque mot simulé).
_CORPUS: Corpus | None = None
_GLOBAL_FREQ: dict[str, float] | None = None
_ROOT_CACHE: dict[str, dict] = {}
_POSITIONAL_FREQ_CACHE: dict[int, object] = {}


_STRATEGY = "composite"


def _init_worker(corpus_path: str, root_cache_path: str | None, strategy: str = "composite") -> None:
    global _CORPUS, _GLOBAL_FREQ, _ROOT_CACHE, _STRATEGY
    _CORPUS = Corpus.from_file(corpus_path)
    _GLOBAL_FREQ = letter_frequencies(_CORPUS)
    _ROOT_CACHE = load_cache(root_cache_path) if root_cache_path else {}
    _STRATEGY = strategy


def _positional_freq(length: int):
    if length not in _POSITIONAL_FREQ_CACHE:
        _POSITIONAL_FREQ_CACHE[length] = positional_frequencies(_CORPUS, length)
    return _POSITIONAL_FREQ_CACHE[length]


def simulate_one(
    guess1: str,
    candidates_init: list[str],
    target: str,
    global_freq: dict[str, float],
    positional_freq,
    strategy: str = "composite",
) -> int:
    """Joue une partie complète contre `target`, en partant du coup 1 `guess1`.

    Reproduit exactement la boucle play()/update()/is_solved() de `Solver` (utilisée
    par le bot et la CLI) : la partie est gagnée dès que le sous-corpus filtré par le
    feedback cumulé ne contient plus qu'un seul mot. Retourne le nombre de coups joués,
    ou MAX_ATTEMPTS + 1 si la cible n'a pas été isolée en MAX_ATTEMPTS coups (échec).
    `strategy` sélectionne l'algorithme utilisé pour les coups 2+ (le coup 1,
    `guess1`, est déjà déterminé par l'appelant — cf. `simulate_group`).
    """
    candidates = candidates_init
    guess = guess1
    for attempt in range(1, MAX_ATTEMPTS + 1):
        target_code = pattern_to_code(pattern_string(guess, target))
        codes = pattern_codes(guess, words_to_matrix(candidates))
        candidates = [w for w, c in zip(candidates, codes) if c == target_code]

        if len(candidates) == 1:
            return attempt
        if attempt == MAX_ATTEMPTS:
            break
        guess = _best_guess(strategy, candidates, global_freq, positional_freq)
    return MAX_ATTEMPTS + 1


def simulate_group(
    item: tuple[tuple[str, int], list[str]],
) -> tuple[str, int, list[tuple[str, int, int]]]:
    (letter, length), targets = item
    candidates_init = _CORPUS.subset(letter, length)
    positional_freq = _positional_freq(length)

    # `_ROOT_CACHE` est chargé (dans `_init_worker`, via `run_strategy_simulation`)
    # avec le cache correspondant à `_STRATEGY` — composite et entropie pure ont
    # chacun leur propre fichier (mots potentiellement différents pour le même
    # (lettre, longueur)), jamais mélangés.
    cached = _ROOT_CACHE.get(cache_key(letter, length))
    if cached is not None:
        guess1 = cached["word"]
    else:
        guess1 = _best_guess(_STRATEGY, candidates_init, _GLOBAL_FREQ, positional_freq)

    results = [
        (
            target,
            length,
            simulate_one(guess1, candidates_init, target, _GLOBAL_FREQ, positional_freq, _STRATEGY),
        )
        for target in targets
    ]
    return letter, length, results


def make_buckets(lengths: list[int], bucket_size: int) -> list[tuple[int, int]]:
    lengths = sorted(set(lengths))
    return [
        (chunk[0], chunk[-1])
        for chunk in (lengths[i : i + bucket_size] for i in range(0, len(lengths), bucket_size))
    ]


def bucket_label(bucket: tuple[int, int]) -> str:
    lo, hi = bucket
    return f"{lo}" if lo == hi else f"{lo}-{hi}"


def bucket_for_length(length: int, buckets: list[tuple[int, int]]) -> tuple[int, int]:
    for lo, hi in buckets:
        if lo <= length <= hi:
            return (lo, hi)
    raise ValueError(f"longueur {length} hors des tranches {buckets}")


def load_checkpoint(path: str | Path) -> dict[str, list[list]]:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(checkpoint: dict[str, list[list]], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint), encoding="utf-8")


def checkpoint_records(checkpoint: dict[str, list[list]]) -> list[tuple[str, int, int]]:
    return [(word, length, attempts) for rows in checkpoint.values() for word, length, attempts in rows]


def aggregate(
    records: list[tuple[str, int, int]], buckets: list[tuple[int, int]]
) -> tuple[list[str], list[list]]:
    per_bucket: dict[tuple[int, int], list[int]] = defaultdict(list)
    for _word, length, attempts in records:
        per_bucket[bucket_for_length(length, buckets)].append(attempts)

    totals = {b: len(per_bucket[b]) for b in buckets}
    headers = ["Seuil"] + [f"{bucket_label(b)} lettres (n={totals[b]})" for b in buckets]

    def rate(bucket: tuple[int, int], predicate) -> float | None:
        total = totals[bucket]
        if total == 0:
            return None
        return round(100 * sum(1 for a in per_bucket[bucket] if predicate(a)) / total, 2)

    rows = [
        [f"<= {t} coups"] + [rate(b, lambda a, t=t: a <= t) for b in buckets] for t in THRESHOLDS
    ]
    rows.append(["Échec (> 6 coups)"] + [rate(b, lambda a: a > MAX_ATTEMPTS) for b in buckets])
    return headers, rows


def build_comparative_table(
    records_by_strategy: dict[str, list[tuple[str, int, int]]], buckets: list[tuple[int, int]]
) -> tuple[list[str], list[list]]:
    """Tableau comparatif côte à côte : pour chaque tranche de longueur, une paire
    de colonnes (une par stratégie) à chaque seuil de coups."""
    per_strategy = {s: aggregate(records, buckets) for s, records in records_by_strategy.items()}
    strategies = list(records_by_strategy)

    headers = ["Seuil"]
    for b in buckets:
        for s in strategies:
            bucket_header = per_strategy[s][0][1 + buckets.index(b)]
            headers.append(f"{bucket_header} [{s}]")

    rows = []
    n_threshold_rows = len(THRESHOLDS) + 1  # + ligne échec
    for row_idx in range(n_threshold_rows):
        label = per_strategy[strategies[0]][1][row_idx][0]
        row = [label]
        for b_idx in range(len(buckets)):
            for s in strategies:
                row.append(per_strategy[s][1][row_idx][1 + b_idx])
        rows.append(row)
    return headers, rows


def run_strategy_simulation(
    strategy: str,
    corpus: Corpus,
    groups: dict[tuple[str, int], list[str]],
    buckets: list[tuple[int, int]],
    args,
) -> list[tuple[str, int, int]]:
    """Lance la simulation complète pour une stratégie donnée (checkpoint et export
    intermédiaire dédiés, suffixés par la stratégie) et retourne les enregistrements."""
    checkpoint_path = _strategy_path(args.checkpoint, strategy, len(_active_strategies(args)) > 1)
    root_cache_path = None
    if not args.no_root_cache:
        root_cache_path = args.root_cache if strategy == "composite" else args.root_cache_entropy_pure
    if root_cache_path and not Path(root_cache_path).exists():
        root_cache_path = None

    checkpoint: dict[str, list[list]] = {} if args.fresh else load_checkpoint(checkpoint_path)
    if checkpoint:
        print(f"[{strategy}] Checkpoint chargé : {len(checkpoint)} groupe(s) déjà simulé(s), repris tel quel.")

    work_items = [(key, targets) for key, targets in groups.items() if cache_key(*key) not in checkpoint]
    skipped = len(groups) - len(work_items)
    if skipped:
        print(f"[{strategy}] {skipped} groupe(s) déjà dans le checkpoint, non recalculé(s).")

    remaining_words = sum(len(targets) for _, targets in work_items)
    print(f"[{strategy}] {remaining_words} mot(s) à simuler sur {len(work_items)} groupe(s) restant(s).")
    start = time.time()

    def export_snapshot() -> None:
        headers, rows = aggregate(checkpoint_records(checkpoint), buckets)
        sheet_name = _strategy_sheet_name(args.sheet_name, strategy, len(_active_strategies(args)) > 1)
        workbook = open_or_create_workbook(args.output)
        add_sheet(workbook, sheet_name, headers, rows)
        save_workbook(workbook, args.output)

    if work_items:
        with mp.Pool(
            processes=args.workers,
            initializer=_init_worker,
            initargs=(args.corpus_path, root_cache_path, strategy),
        ) as pool:
            for i, (letter, length, group_results) in enumerate(
                pool.imap_unordered(simulate_group, work_items), start=1
            ):
                checkpoint[cache_key(letter, length)] = [list(r) for r in group_results]
                save_checkpoint(checkpoint, checkpoint_path)

                if i % 10 == 0 or i == len(work_items):
                    total_words = sum(len(rows) for rows in checkpoint.values())
                    print(f"[{strategy}]   groupes traités : {i}/{len(work_items)} ({total_words} mots cumulés)")

                if args.export_every and i % args.export_every == 0:
                    export_snapshot()

    print(f"[{strategy}] Simulation terminée en {time.time() - start:.1f}s.")
    export_snapshot()
    return checkpoint_records(checkpoint)


def _active_strategies(args) -> list[str]:
    return list(STRATEGIES) if args.strategy == "both" else [args.strategy]


def _strategy_path(base_path: str, strategy: str, multi: bool) -> str:
    if not multi:
        return base_path
    p = Path(base_path)
    return str(p.with_name(f"{p.stem}_{strategy}{p.suffix}"))


def _strategy_sheet_name(base_name: str, strategy: str, multi: bool) -> str:
    return f"{base_name}_{strategy}" if multi else base_name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS))
    parser.add_argument(
        "--root-cache",
        default=str(DEFAULT_ROOT_CACHE),
        help="Cache JSON du coup 1 pour la stratégie composite ; ignoré s'il n'existe pas.",
    )
    parser.add_argument(
        "--root-cache-entropy-pure",
        default=str(DEFAULT_ROOT_CACHE_ENTROPY_PURE),
        help="Cache JSON du coup 1 pour la stratégie entropie pure (fichier séparé du "
        "cache composite) ; ignoré s'il n'existe pas. Générer via "
        "`python scripts/build_root_cache.py --strategy entropy_pure`.",
    )
    parser.add_argument(
        "--no-root-cache",
        action="store_true",
        help="Ignore les deux caches (composite et entropie pure) et recalcule le coup 1 dynamiquement.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Classeur Excel de sortie (multi-sheet).")
    parser.add_argument("--sheet-name", default="Simulation", help="Nom du sheet à écrire/remplacer.")
    parser.add_argument(
        "--bucket-size", type=int, default=2, help="Nombre de longueurs consécutives regroupées par tranche."
    )
    parser.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 2) - 1))
    parser.add_argument(
        "--limit", type=int, default=None, help="Limite le nombre total de mots simulés (tests rapides)."
    )
    parser.add_argument(
        "--checkpoint",
        default=str(DEFAULT_CHECKPOINT),
        help="Fichier JSON des résultats déjà calculés par groupe (lettre, longueur). "
        "Un groupe déjà présent n'est pas recalculé : une interruption/un crash en cours "
        "de run ne fait perdre au plus que le groupe en cours, pas tout le travail déjà fait.",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="Ignore le checkpoint existant et repart de zéro."
    )
    parser.add_argument(
        "--export-every",
        type=int,
        default=10,
        help="Réexporte le sheet Excel tous les N groupes traités (0 = seulement à la fin).",
    )
    parser.add_argument(
        "--strategy",
        choices=[*STRATEGIES, "both"],
        default="composite",
        help="Stratégie de suggestion : composite (défaut, cache racine si dispo), "
        "entropy_pure (best_guess_entropy_pure, toujours dynamique), ou both "
        "(les deux, séquentiellement, + tableau comparatif côte à côte).",
    )
    args = parser.parse_args()

    corpus = Corpus.from_file(args.corpus_path)
    all_words = list(corpus)
    if args.limit:
        all_words = all_words[: args.limit]
        print(f"--limit actif : {args.limit} mot(s) sur {len(corpus)} simulés (échantillon tronqué).")

    groups: dict[tuple[str, int], list[str]] = defaultdict(list)
    for word in all_words:
        groups[(word[0], len(word))].append(word)

    lengths_present = sorted({len(w) for w in corpus})
    buckets = make_buckets(lengths_present, args.bucket_size)

    strategies = _active_strategies(args)
    records_by_strategy = {s: run_strategy_simulation(s, corpus, groups, buckets, args) for s in strategies}

    if len(strategies) == 1:
        print(f"Tableau exporté : {args.output} (sheet '{args.sheet_name}')")
        return

    headers, rows = build_comparative_table(records_by_strategy, buckets)
    comparative_sheet = f"{args.sheet_name}_comparatif"
    workbook = open_or_create_workbook(args.output)
    add_sheet(workbook, comparative_sheet, headers, rows)
    save_workbook(workbook, args.output)
    print(f"Tableau comparatif exporté : {args.output} (sheet '{comparative_sheet}')")


if __name__ == "__main__":
    main()
