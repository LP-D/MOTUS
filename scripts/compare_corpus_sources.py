#!/usr/bin/env python3
"""Compare le corpus actuel (data/corpus_fr.txt, dérivé du dataset Kaggle
kartmaan/dictionnaire-francais) au CSV brut du dépôt GitHub source
(Kartmaan/french-language-tools, fichier files/dico.csv).

Applique EXACTEMENT la même normalisation que le pipeline existant
(`motus_solver.corpus.Corpus.from_file` : majuscules, suppression des accents,
ligatures, filtre alpha-ASCII, longueur 5-9) aux deux sources, pour une comparaison
équitable — un mot compte comme "identique" seulement si sa forme normalisée l'est.

Diagnostic uniquement : n'intègre rien au solveur, ne modifie pas data/corpus_fr.txt.

Produit :
- un résumé sur stdout (effectifs, colonnes/structure, gain de couverture)
- data/corpus_comparison_missing_words.csv : mots présents dans le CSV brut GitHub
  normalisé mais absents du corpus actuel, avec (lettre initiale, longueur)
- data/corpus_comparison_by_length.csv : distribution de ces mots manquants par
  (lettre initiale, longueur)

Script autonome : ne dépend ni de la CLI (`motus_solver.cli`) ni du bot Playwright,
uniquement du package `motus_solver`.

    python scripts/compare_corpus_sources.py --github-csv path/vers/dico.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.corpus import Corpus, normalize_word  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CURRENT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_MISSING_OUTPUT = ROOT_DIR / "data" / "corpus_comparison_missing_words.csv"
DEFAULT_DISTRIBUTION_OUTPUT = ROOT_DIR / "data" / "corpus_comparison_by_length.csv"
MIN_LENGTH = 5
MAX_LENGTH = 9


def normalize_words(raw_words: list[str]) -> set[str]:
    """Reproduit le filtrage de `Corpus.from_file` (mêmes règles, même ordre) sur une
    liste de mots déjà extraits (pas de lecture de fichier ligne à ligne ici)."""
    normalized: set[str] = set()
    for raw in raw_words:
        if not raw or not raw.strip():
            continue
        word = normalize_word(raw.strip().split()[0])
        if not (word.isascii() and word.isalpha()):
            continue
        if not (MIN_LENGTH <= len(word) <= MAX_LENGTH):
            continue
        normalized.add(word)
    return normalized


def load_github_words(csv_path: Path) -> tuple[list[str], list[str]]:
    """Lit le CSV brut GitHub, retourne (colonnes, mots bruts de la colonne "Mot")."""
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        columns = next(reader)
        word_col_idx = 0  # colonne "Mot" en première position dans dico.csv
        raw_words = [row[word_col_idx] for row in reader if row]
    return columns, raw_words


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--current-corpus", default=str(DEFAULT_CURRENT_CORPUS))
    parser.add_argument("--github-csv", required=True, help="Chemin vers le dico.csv brut téléchargé.")
    parser.add_argument("--missing-output", default=str(DEFAULT_MISSING_OUTPUT))
    parser.add_argument("--distribution-output", default=str(DEFAULT_DISTRIBUTION_OUTPUT))
    args = parser.parse_args()

    current_corpus = Corpus.from_file(args.current_corpus, min_length=MIN_LENGTH, max_length=MAX_LENGTH)
    current_words = set(current_corpus.words)

    github_path = Path(args.github_csv)
    columns, raw_words = load_github_words(github_path)
    github_words_normalized = normalize_words(raw_words)

    missing = sorted(github_words_normalized - current_words)
    extra_in_current = sorted(current_words - github_words_normalized)

    print("=== Structure ===")
    print(f"Corpus actuel      : {args.current_corpus}")
    print(f"CSV brut GitHub    : {github_path} (colonnes : {columns})")
    print()
    print("=== Effectifs ===")
    print(f"Corpus actuel (normalisé, {MIN_LENGTH}-{MAX_LENGTH} lettres) : {len(current_words)} mots")
    print(f"CSV brut GitHub, lignes brutes                        : {len(raw_words)}")
    print(f"CSV brut GitHub, normalisé ({MIN_LENGTH}-{MAX_LENGTH} lettres)      : {len(github_words_normalized)} mots")
    print()
    print("=== Écart de couverture ===")
    print(f"Présents dans GitHub brut mais absents du corpus actuel : {len(missing)}")
    print(f"Présents dans le corpus actuel mais absents du CSV brut : {len(extra_in_current)}")
    gain_pct = 100 * len(missing) / len(current_words) if current_words else 0.0
    print(f"Gain de couverture potentiel : +{gain_pct:.1f}% de mots en plus")

    distribution = Counter((w[0], len(w)) for w in missing)
    print()
    print("=== Distribution des mots manquants par (lettre initiale, longueur) — top 15 ===")
    for (letter, length), count in distribution.most_common(15):
        print(f"  {letter},{length}: {count}")

    missing_output = Path(args.missing_output)
    missing_output.parent.mkdir(parents=True, exist_ok=True)
    with missing_output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["mot", "lettre_initiale", "longueur"])
        for word in missing:
            writer.writerow([word, word[0], len(word)])
    print(f"\nListe complète des mots manquants : {missing_output}")

    distribution_output = Path(args.distribution_output)
    with distribution_output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["lettre_initiale", "longueur", "nb_mots_manquants"])
        for (letter, length), count in sorted(distribution.items()):
            writer.writerow([letter, length, count])
    print(f"Distribution (lettre, longueur) : {distribution_output}")


if __name__ == "__main__":
    main()
