# Cycle d'amélioration en jeu réel — /infinite (23/09/2026)

Protocole : jouer 10 mots, analyser les logs, corriger, puis recommencer ; au moins
5 runs. Arrêt dès qu'un run ne remonte aucune erreur et que son temps moyen par mot
est inférieur à celui du run précédent. Outil : `scripts/run_improvement_cycle.py`
(chemin de production du bot, un seul invité par run). Logs complets :
`data/improvement_runs/run_NN_games.jsonl` et `run_NN_summary.json`.

Le débit n'a jamais été accéléré : au moins 1,5 à 2,5 s entre deux requêtes, et autant
entre deux parties. Aucun 429 ; latence maximale observée : 1,08 s.

## Résultats par run

| Run | Trouvés | Erreurs | Temps moyen / mot (s) | Essais moyens | Rejets (parties avec rejet au coup 1) | Solveur (s/mot) | Chargement (s/mot) | Trio + bot vs bot seul (essais, simulé) |
|---|---|---|---|---|---|---|---|---|
| 1 | 10/10 | 0 | **100.37** | 2.8 | 12 (3) | 90.35 | 1.52 | 4 vs 3 |
| 2 | 10/10 | 0 | **11.64** | 3.1 | 11 (2) | 0.03 | 1.88 | 4.1 vs 3.2 |
| 3 | 10/10 | 0 | **20.18** | 3.3 | 8 (3) | 0.02 | 10.08 | 4.2 vs 3.1 |
| 4 | 8/10 | 1 | **9.77** | 3.0 | 6 (0) | 0.04 | 1.58 | 4.25 vs 3.38 |
| 5 | 10/10 | 0 | **8.02** | 2.9 | 1 (0) | 0.01 | 1.16 | 4.4 vs 3.0 |

Critère d'arrêt atteint au run 5 : 0 erreur, et 8,02 s par mot contre 9,77 s au run 4.

## Corrections, avec la preuve qui les a motivées

1. **Coups de repli précalculés** (`cache.ROOT_ALTERNATIVES`, `tree.top_guesses_composite`).
   - *Preuve (run 1)* : la recherche du solveur représentait 90 s sur 100 s par mot.
     ROTATOIRE (R9) a pris 892 s : 3 rejets au coup 1, chacun suivi d'un recalcul
     complet sur 21 000 candidats (~290 s).
   - *Correction* : le cache racine stocke les 10 meilleurs premiers coups, et le
     solveur prend le premier encore candidat. Le mot 1 choisi reste identique ;
     `best_guess_composite` et les poids ne sont pas modifiés.
   - *Résultat* : solveur < 0,05 s par mot à partir du run 2.
2. **Chargement sans `networkidle`** : attente du bouton « C'est parti » ou du plateau.
   - *Preuve (run 2)* : 1,5 à 1,9 s de chargement par mot.
3. **Mots déjà acceptés préférés au coup 1** (`data/known_valid_words.json`).
   - *Preuve (run 2)* : 9 rejets sur 11 au coup 1. En B8, les 6 premiers coups de
     repli ont tous été refusés.
   - *Correction* : parmi le classement du coup 1, un mot déjà accepté par le serveur
     passe en premier.
   - *Résultat* : 0 rejet au coup 1 aux runs 4 et 5.
4. **Régression corrigée (run 3).** Le chargement était remonté à ~11 s par mot, car
   l'écran « C'est parti » n'apparaît qu'au premier chargement d'un invité conservé.
   L'attente du bouton seul butait donc sur le timeout de 10 s. Correction : attendre
   le bouton **ou** le plateau. Le test échoue avec l'ancienne version (contre-épreuve).
5. **Invité créé avant le premier chargement** (`BotRunner._ensure_guest`).
   - *Preuve (run 4)* : `NOT_FOUND` au 1er coup du 1er mot, comme H8.
   - *Cause* : sur 22 chargements tracés, `GET /api/me` et `POST /api/game` partent à
     0–8 ms d'écart. Sur un invité neuf, les deux requêtes créent chacune un invité,
     et la partie peut appartenir à l'autre.
   - *Correction* : un seul `GET /api/me` avant la première page.
   - *Résultat* : aucun `NOT_FOUND` au run 5.

Entre les runs, les entrées du cache dont le mot racine venait d'être refusé ont été
recalculées (`scripts/build_root_cache.py --refresh-blocklisted`).

## Hypothèses testées et écartées

- **« Les mots du dictionnaire Excel sont des mots que Tuzmo accepte »** : faux. Sur
  112 mots acceptés, seuls 9 figurent dans l'univers de 9 445 mots des trios Excel. À
  l'inverse, 104 des 191 mots refusés y figurent.
- **Accélérer la saisie** : sans effet. La saisie (~1,5 s) reste sous l'écart de débit
  imposé (1,5–2,5 s), qui fixe à ~2 s le coût d'une soumission. Les délais
  anti-détection sont donc restés inchangés.

## Trios Excel comparés au bot (48 mots comparables, simulation sur la solution réelle)

Ouvrir avec le trio, puis laisser le bot finir, demande en moyenne **4,2 essais**,
contre **3,1** pour le bot seul. Le trio est meilleur 1 fois, pire 39 fois, égal 8 fois.
Les 2 autres mots des 50 joués n'ont pas pu être comparés : solution inconnue. Après 3 coups, le trio laisse parfois moins de candidats que le bot, mais
il impose 3 coups non adaptatifs, alors que le bot trouve souvent dès le 2e ou le 3e.

## Non traité

- Deux mots hors corpus (A6 en matrice, P8 au run 4). Le bot les abandonne proprement
  via ↻, mais ce bouton ne révèle pas la solution : impossible de l'ajouter au corpus.
- Rejets aux coups 2 et suivants (candidats rares du corpus Wiktionnaire) : environ
  1 par mot aux runs 1 à 4, 1 au total au run 5. La stratégie `entropy_pure`, meilleure
  en simulation, n'a pas été activée : ce choix de stratégie par défaut reste à toi.
