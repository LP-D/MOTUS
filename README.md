# motus-solver

Solveur Motus assisté par entropie (numpy), indépendant de toute UI, plus un bot Tuzmo optionnel (Playwright).

## Structure

- `src/motus_solver/` : le solver (corpus, feedback, scoring, tree, solver, cli).
- `bot/` : intégration Tuzmo (étape 3, pas encore développée).
- `data/` : dictionnaires et fichiers générés.
- `tests/` : tests pytest.
- `scripts/build_corpus.py` : construit `data/corpus_fr.txt` à partir d'un lexique brut.

## Installation

```bash
pip install -e ".[dev]" 2>/dev/null || pip install -e .
```

## Utilisation

```bash
python scripts/build_corpus.py chemin/vers/lexique.txt --output data/corpus_fr.txt
motus-solve --letter S --length 6
```

## Tests

```bash
pytest
```

## Origine de l'algorithme

`feedback.py` (pattern_codes, entropy_from_codes, exp_remaining) est adapté de
`encoder_retours()` / du calcul d'entropie de `MOTUS_OPTIMISEUR_KAGGLEHUB.ipynb`
(même logique vectorisée numpy, renommée pour coller à l'API demandée).

`tree.py` (build_tree, best_guess_composite) et la formule de `composite_score`
dans `scoring.py` n'existaient pas dans ce notebook (qui optimise des trios fixes,
pas un arbre adaptatif) : ils ont été écrits directement à partir des poids
(0.40 entropie / 0.25 voyelles / 0.20 lettres distinctes / 0.15 fréquence) et de
la description de l'arbre récursif fournis dans la spécification.

## Étape 3 (bot Tuzmo)

Pas commencée. La première tâche sera `scripts/inspect_tuzmo.py` (exploration
Playwright du DOM), à valider avant d'écrire `bot/parser.py`.
