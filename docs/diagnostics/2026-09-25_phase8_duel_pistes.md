# Phase 8 : trois pistes pour le duel simulé (25/09/2026)

Tout se passe dans le simulateur local (`motus_solver/duel.py`) : aucune requête vers
Tuzmo. Tournois avec `scripts/duel_tournament.py` :
- mêmes mots pour chaque paire, tirés parmi les 2 289 solutions réelles ;
- séries de 100 duels entre les mêmes instances, pour que les profils d'adversaire
  puissent s'apprendre.

## Les trois modèles

| Modèle | Piste | Principe |
|---|---|---|
| `entropy_pure_infos` | infos adverses | Lit les couleurs de l'adversaire (sans ses lettres) et pondère les candidats. Apprend ses ouvertures : un bot est reconnu à son cache racine après 2 duels (58 ouvertures de composite prédites sur 58). |
| `entropy_pure_pression` | pression | Adversaire à une lettre de trouver, ou déjà gagnant : plus de mot sonde, chaque coup doit pouvoir gagner. |
| `entropy_pure_tempo` | vitesse / précision | Mot déjà accepté par le jeu préféré jusqu'à 10 % d'entropie en moins (2 % par défaut) : moins de refus, donc moins de temps perdu. |

Les trois ont aussi la riposte (`chase_aware`). Code : `motus_solver/agents.py` et
`motus_solver/inference.py`.

## Résultats

Tournoi à 9 modèles, vitesse « rapide », 1 000 duels par paire, soit 36 000 duels
(`data/duel_runs/tournoi_pistes_rapide.json`).

| Modèle | Elo | Victoires |
|---|---|---|
| entropy_pure_infos | 1527 | 54,2 % |
| entropy_pure_sondes | 1524 | 53,8 % |
| entropy_pure | 1521 | 53,2 % |
| entropy_pure_pression | 1520 | 53,1 % |
| entropy_pure_chasse | 1519 | 53,0 % |
| entropy_pure_tempo | 1517 | 52,6 % |
| entropy_pure_sans_fin | 1510 | 51,5 % |
| composite | 1484 | 47,4 % |
| aleatoire | 1378 | 31,2 % |

Face-à-face utiles (mêmes mots) :

| Duel | Score | p |
|---|---|---|
| **infos contre composite** | **60,9 %** | < 0,001 |
| entropy_pure contre composite | 54,1 % | 0,01 |
| infos contre entropy_pure | 51,2 % | 0,47 |
| pression contre entropy_pure | 50,8 % | 0,64 |
| tempo contre entropy_pure | 49,1 % | 0,59 |

Tempo à la vitesse « instantané », où un refus pèse le plus (2 000 duels par paire,
`tournoi_tempo_instantane.json`) :
- sondes 51,7 %, tempo 50,1 %, entropy_pure 48,2 % ;
- refus par duel identiques pour tempo et la base (0,18).

## Lecture

- **Infos adverses : la seule piste qui paie**, et seulement contre un adversaire
  dont l'ouverture diffère de la sienne. Contre composite, elle ajoute ~7 points au
  gain d'entropy_pure (60,9 % contre 54,1 %). Contre un autre entropy_pure,
  l'adversaire joue le même premier mot : ses couleurs n'apprennent rien de plus.
  Contre un humain, dont les ouvertures varient, c'est la piste la plus prometteuse.
  Le bot `entropy_pure_infos` de la page /duel retient tes ouvertures
  (`data/duel_profiles.json`).
- **Pression : aucun effet mesurable.** Le cas « adversaire à une lettre » est rare au
  moment où un mot sonde serait joué.
- **Tempo : aucun effet mesurable.** Les bots entropy ne subissent que 0,18 refus par
  duel. Parmi les 20 meilleurs coups, un mot déjà accepté à 10 % près est rarement
  disponible quand il ne l'était pas à 2 %.
- **98 % des duels se terminent par « ne peut plus faire mieux ».** Le nombre d'essais
  décide. À essais égaux, c'est le premier à trouver dans le temps qui gagne. Entre
  deux bots de même stratégie, le résultat est donc un tirage sur la vitesse, ce qui
  explique pourquoi les variantes d'entropy_pure restent toutes entre 52 et 54 %.

## Suites possibles

- **Infos** : pondérer aussi les 2e et 3e lignes adverses en simulant ses coups
  suivants (un bot reconnu rejoue de façon prévisible).
- **Pression** : déclencher plus tôt, par exemple quand l'adversaire a moins de
  candidats estimés que soi.
- **Tempo** : mesurer contre des adversaires humains simulés plus lents, où le
  premier arrivé à essais égaux compte davantage.
