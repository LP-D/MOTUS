#!/usr/bin/env python3
"""Construit data/corpus_fr.txt à partir d'un ou plusieurs fichiers lexique bruts (un mot par ligne)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.corpus import Corpus  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Fichier(s) lexique source.")
    parser.add_argument("--output", type=Path, default=Path("data/corpus_fr.txt"))
    parser.add_argument("--min-length", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=9)
    args = parser.parse_args()

    words: set[str] = set()
    for input_path in args.inputs:
        corpus = Corpus.from_file(input_path, min_length=args.min_length, max_length=args.max_length)
        words.update(corpus.words)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    print(f"{len(words)} mots écrits dans {args.output}")


if __name__ == "__main__":
    main()
