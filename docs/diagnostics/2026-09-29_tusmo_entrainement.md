# Entraînement Tusmo : ce que révèle la note des coups, et un bot qui joue comme Tusmo conseille (29/09/2026)

Données : `data/tusmo_training_log.jsonl`, rempli par `scripts/scrape_tusmo_training.py`
(compte Pro d'essai, 28-29/09/2026) : 5 616 parties terminées jouées par
`entropy_pure`, puis 40 parties jouées par le nouveau joueur `tusmo_conseil`.

## 1. La note de Tusmo, c'est une entropie relative

Pour chaque coup, Tusmo donne `percent`. C'est l'entropie du retour du mot, calculée sur
les solutions encore possibles de SA liste, divisée par l'entropie du meilleur mot. Les
valeurs observées quand il reste peu de candidats (coups non gagnants) correspondent
exactement à cette formule :

| candidats | découpage | entropie / meilleur | note observée |
|---|---|---|---|
| 2 | (1, 1) | 1 / 1 | 100 % (980 coups) |
| 3 | (2, 1) | 0,918 / 1,585 | 58 % (131 coups) |
| 4 | (3, 1) | 0,811 / 2 | 41 % |
| 4 | (2, 2) | 1 / 2 | 50 % |
| 4 | (2, 1, 1) | 1,5 / 2 | 75 % (84 coups) |
| 4 | (3, 1), meilleur à 1,5 bit | 0,811 / 1,5 | 54 % |

Quand il ne reste qu'une solution, tout autre mot vaut 0 %. À égalité, le mot conseillé
(`bestWord`) est une solution encore possible : 100 % des cas à 2 candidats, et c'est
la réponse une fois sur deux.

Conséquence : Tusmo conseille exactement ce que calcule `entropy_pure`, mais sur une
autre liste.

## 2. La liste de solutions de Tusmo

- Elle est fixe : 26 430 mots sur les 107 groupes (lettre, longueur) vus, environ 360
  par groupe. Pour comparaison, le corpus du solveur contient 1 887 mots en B-6 (323 chez
  Tusmo) et 5 180 en R-7 (398).
- `/infinite` utilise la même liste : 191 de ses 1 132 réponses ont déjà été vues en
  entraînement, pour 177 attendues si les deux listes sont identiques.
- Le coup 1 conseillé est toujours le même pour un groupe donné (107 groupes sur 107).
- 7 % des mots conseillés sont absents du corpus. Tusmo accepte donc des coups dans un
  dictionnaire plus large que sa liste de solutions.
- 117 parties sur 4 800 sont restées inachevées : la solution n'était pas dans le corpus.

Sur une partie, le solveur perd des coups sur des mots qui ne peuvent pas être la
solution. Exemple S-6 (SAFRAN) : après le coup 1, il restait 1 solution chez Tusmo et
10 candidats pour le solveur, d'où deux coups à 0 %.

## 3. Estimer la liste, puis jouer comme Tusmo

`src/motus_solver/tusmo_list.py` estime pour chaque mot du corpus la probabilité qu'il
soit dans la liste de Tusmo. Il utilise :
- les solutions connues ;
- la taille de la liste du groupe ;
- après chaque coup, le nombre de solutions restantes (`candidatesLeft`). Cela fait
  environ 14 000 contraintes « exactement c mots de la liste parmi les mots compatibles ».

L'ajustement se fait par itérations proportionnelles (IPF). Une contrainte déjà
satisfaite par les seules solutions connues exclut tous les autres mots de l'ensemble.

Le joueur (`TusmoAdvisor`, modèle de duel `tusmo_conseil`) procède ainsi :
- coup 1 : le mot conseillé par Tusmo pour le groupe (observé) ;
- ensuite : entropie maximale sur les solutions estimées, pondérées par leur probabilité ;
- à égalité : le candidat le plus probable.

Limite : on connaît 16 % de la liste. Quand la réponse est inconnue, soit 84 % des cas
réels, l'estimation sous-évalue le nombre de solutions en fin de partie (environ 0,6 fois
le vrai nombre à 2-5 candidats, validation leave-one-out sur 80 parties).

## 4. Fidélité mesurée en direct sur Tusmo

| joueur | parties | coups à 100 % | note moyenne | essais moyens |
|---|---|---|---|---|
| `entropy_pure` (solveur actuel) | 5 616 | 62 % | 90,3 % | 2,99 |
| `tusmo_conseil`, sans note préalable | 30 | 87 % | 92,6 % | 2,83 |
| `tusmo_conseil --preview` | 10 | 96 % | 96,2 % | 2,60 |

`--preview` fait noter par Tusmo jusqu'à 3 conseils avant chaque coup, et le premier à
100 % est joué. Sur ces 10 parties, le premier conseil était déjà à 100 % à chaque coup
noté (8 sur 8). Il a fallu 1 à 3 notes par partie, et la limite de Tusmo
(`TOO_MANY_PREVIEWS`) n'a pas été atteinte. Le seul coup raté (0 %) vient d'une fin de
partie où Tusmo n'avait plus qu'une solution et le bot plusieurs.

## 5. Duel simulé contre nos modèles

Tournoi local, vitesse « rapide », 300 duels par paire
(`data/duel_runs/tournoi_tusmo_conseil_rapide.json`) :

| paire | bilan |
|---|---|
| tusmo_conseil vs entropy_pure | 153 - 1 - 146 |
| tusmo_conseil vs entropy_pure_chasse | 160 - 1 - 139 |

Essais moyens (mots trouvés) : 2,71 pour `tusmo_conseil`, contre 2,82 pour
`entropy_pure` et 2,93 pour `entropy_pure_chasse`.

Les écarts de victoires restent dans le bruit à 300 duels : la paire
entropy_pure / _chasse passe de 104-96 à 133-166 d'un tournoi à l'autre. Le nombre
d'essais est le signal net.

Le simulateur refusait ces mots dans 30 % des cas alors que le vrai jeu les accepte
(9 348 mots joués ou conseillés en entraînement). Avant ce correctif, `tusmo_conseil`
perdait 0,44 refus par duel et finissait derrière (98-102 contre entropy_pure). Ces
mots comptent désormais comme valides pour tous les joueurs.

Biais qui pénalise encore `tusmo_conseil` : le mot du duel est tiré parmi les
solutions connues, et le bot l'oublie (leave-one-out). Il joue donc toujours le cas
« réponse inconnue », qui ne représente que 84 % des parties réelles.

## Pistes

- Continuer à remplir le journal : chaque partie affine la liste estimée.
- Donner au solveur du bot la liste estimée (pondération des candidats). C'est le gain
  direct mesuré ici : environ 0,2 essai par partie en moins sur Tusmo.
- Ajouter les mots joués en entraînement à `known_valid_words.json`, et les mots refusés
  à la liste noire.
