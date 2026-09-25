from __future__ import annotations

import sys

import click

from .cache import DEFAULT_STRATEGY, STRATEGIES
from .corpus import Corpus
from .solver import Solver

DEFAULT_CORPUS = "data/corpus_fr.txt"


@click.command()
@click.option("--letter", required=True, help="Première lettre du mot à trouver.")
@click.option("--length", required=True, type=int, help="Longueur du mot (5-9).")
@click.option(
    "--corpus-path",
    default=DEFAULT_CORPUS,
    show_default=True,
    help="Fichier dictionnaire (un mot par ligne).",
)
@click.option("--top-n", default=5, show_default=True, help="Nombre de suggestions affichées.")
@click.option("--strategy", type=click.Choice(STRATEGIES), default=DEFAULT_STRATEGY, show_default=True,
              help="Stratégie du solveur (composite disponible en option).")
def main(letter: str, length: int, corpus_path: str, top_n: int, strategy: str) -> None:
    corpus = Corpus.from_file(corpus_path)
    solver = Solver(letter=letter, length=length, corpus=corpus, strategy=strategy)

    while not solver.is_solved():
        suggestions = solver.suggest(top_n=top_n)
        click.echo(f"\n{len(solver.candidates)} candidat(s) restant(s).")
        for i, (word, entropy, vowels) in enumerate(suggestions, start=1):
            click.echo(f"  {i}. {word}  (entropie={entropy:.2f} bits, voyelles={vowels})")

        guess = click.prompt("Mot joué", default=suggestions[0][0]).upper()
        solver.play(guess)

        pattern = click.prompt(f"Retour pour {guess} ({length} chiffres 0/1/2)")
        if len(pattern) != length or any(c not in "012" for c in pattern):
            click.echo("Retour invalide : chiffres 0/1/2 uniquement.", err=True)
            solver.history.pop()
            continue
        solver.update(pattern)

        if not solver.candidates:
            click.echo("Plus aucun candidat ne correspond. Vérifie le retour saisi.", err=True)
            sys.exit(1)

    click.echo(f"\nSolution trouvée : {solver.solution}")


if __name__ == "__main__":
    main()
