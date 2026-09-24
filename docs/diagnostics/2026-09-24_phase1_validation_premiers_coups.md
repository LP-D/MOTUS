# Phase 1 : suite de la validation ciblée des premiers coups (24/09/2026)

Stratégie par défaut : **composite, inchangée**.

**Arrêt d'urgence au tour 1** (une requête sans réponse en 5 s, détail dans
`2026-09-24_arret_urgence_validation.md`) : la phase s'arrête là, sans second tour.

## Ce qui a changé dans l'outil

`scripts/validate_root_candidates.py` :

- **Classement profond** (`--compute-rankings`, calcul local) : les 25 meilleurs coups
  1 de chaque groupe déjà tiré, pour chacune des deux stratégies. Quand un candidat est
  refusé, le suivant du classement est testé dans la même partie. Avant, il fallait
  attendre le recalcul du cache.
- **Alternance par rang** : 1er composite, 1er entropy_pure, 2e composite… Les deux
  caches sont ainsi validés à parts égales, ce qui prépare la comparaison de la phase 2.
- **Fin de tour** (`--end-round`, local) :
  - ajoute au corpus les solutions révélées qui en manquaient ;
  - recalcule les entrées de cache dont un candidat est refusé ou dont l'ordre ne suit
    plus le classement ;
  - produit, dans le même calcul, le nouveau classement profond.
- **Autres changements** :
  - arrêt quand 80 parties d'affilée n'ont rien à tester ;
  - une solution révélée est ajoutée d'office aux mots valides ;
  - arrêt d'urgence abaissé à **5 s**, dans le script comme dans le bot (voir plus bas).

Tests : `tests/test_validate_root_candidates.py` (11), `tests/test_group_draws.py`.

## Groupes jamais tirés : « non observé, statut incertain »

- **Journal des tirages** : `data/group_draws.jsonl`, une ligne par mot tiré.
  - Historique repris : 363 parties de validation et 104 parties du bot, soit 467
    tirages.
  - Il est alimenté ensuite par la validation (`validate_root_candidates`) et par le
    bot (`dashboard/bot_runner.py`), sans doublon et sans compter les reprises de partie.
- **Statut dans les caches** : chaque entrée des deux caches porte `draw_status`
  (`observe` ou `non_observe_incertain`) et `draws_observed`.
  - C'est **purement informatif** : le coup 1 et les replis de ces groupes restent
    utilisés tels quels, avec le même repli dynamique qu'avant (test dédié).
- **Signalement** : si le bot tombe sur un groupe jamais observé, il émet l'événement
  `unobserved_group_drawn`, repris dans la synthèse des runs
  (`unobserved_groups_drawn`).

**Le tour 1 justifie cette prudence** : 8 des 40 groupes jamais observés ont été
tirés pour la première fois, dont V9 (2 147 mots dans le corpus), H9, H7 et P5.

| | Avant | Après |
|---|---|---|
| Tirages observés | 467 | 759 |
| Groupes observés | 90 | 98 |
| **Non observés, statut incertain** | 40 | **32** |

Les 32 restants :
- les 25 groupes des lettres K, W, X, Y et Z ;
- I5, Q5, Q8, U6, U7, U8 et U9.

Leur exclusion par le serveur est plausible mais **non établie**. Sur 759 tirages,
un groupe peu fréquent peut ne jamais sortir.

Rectification : le cycle précédent annonçait 44 groupes non vus, un chiffre calculé
sur les seuls 363 tirages de validation. Avec les 104 parties du bot, il y en avait
40 au début de cette phase.

## Tour 1 : mesures

| Mesure | Valeur |
|---|---|
| Requêtes | 1 397 répondues en 48 min : 1 `/api/me`, 291 créations, 815 coups, 290 `giveup` ; puis 1 sans réponse, qui a déclenché l'arrêt |
| Latence | médiane 0,051 s, p95 0,065 s, max 0,355 s ; aucun 429, aucun en-tête de limitation |
| Candidats testés | 815 : 527 valides, **288 refusés (35,3 %)** |
| Tirages | 292 (dont 1 enchaînement après victoire), 86 groupes distincts |
| Solutions révélées | 290, toutes distinctes ; **15 hors corpus**, ajoutées au corpus |
| Mots prouvés valides | 1 457 → **2 267** |
| Liste noire | 611 → **899** |
| Entrées recalculées en fin de tour | 52 composite, 55 entropy_pure (16 min de calcul local) |

Le taux de rejet (35,3 %) est un peu plus élevé qu'au cycle précédent (31,3 %). On
teste désormais plus loin dans le classement, où les mots rares sont plus nombreux.

Les 15 solutions ajoutées au corpus :
- LOUCHEES, CREATINES, DEMENCES, GOURER, LIGOTAGES ;
- DODELINEE, LADITE, FRIABLES, FLEMARDE, HUITIEMES ;
- COVALENTS, HURLEUSE, VIVIDITES, FOURNILS, FLINGUEUR.

Au total, 26 des 650 solutions révélées en validation étaient hors corpus (4 %).

## Résultat

**Mesuré** sur les caches après la fin de tour, les mots valides et la liste noire
actuels :

| | Avant la phase 1 | Après |
|---|---|---|
| **Groupes à 10 candidats confirmés**, composite | 7 | **64** |
| **Groupes à 10 candidats confirmés**, entropy_pure | 4 | **59** |
| Groupes à 10 confirmés dans les deux stratégies | — | 55 |
| Groupes observés dont le coup 1 est prouvé valide, composite | 90 / 90 | **98 / 98** |
| Groupes observés dont le coup 1 est prouvé valide, entropy_pure | 87 / 90 | **97 / 98** (seul Q9 manque, tiré 1 fois) |
| Candidats encore non validés (composite / entropy_pure) | 701 / 781 | 437 / 463 |
| Mot refusé encore présent dans un cache | 0 | 0 |

- Tous les groupes à 10 confirmés sont des groupes observés.
- 34 groupes observés restent incomplets en composite, et 39 en entropy_pure.
  - La plupart n'ont été tirés qu'une à trois fois.
  - Beaucoup n'ont plus qu'un candidat à tester. Ce sont des replis entrés au recalcul
    de fin de tour, faute de second tour.
- Seul X7 compte moins de 10 mots dans le corpus.

**Estimé** : rejet au coup 1 pondéré par la fréquence de tirage observée. Un groupe
dont le coup 1 n'est pas prouvé valide compte pour 35,3 %.

| Stratégie | Rejet au coup 1 estimé, groupes observés |
|---|---|
| composite | **0 %** |
| entropy_pure | **0,05 %** (Q9 seul) |

Réserves :
- **Groupes non observés** : aucun de leurs coups 1 n'est validé. Si l'un d'eux
  sort, le rejet au coup 1 y serait d'environ 35 %, et sa fréquence de tirage est
  inconnue.
- **Coups 2 et suivants** : ils ne sont pas couverts par cette validation, car ils
  sont calculés dynamiquement. Leur taux de rejet se mesure en jeu (phase 2).
- **Rappel des taux mesurés au coup 1 en jeu réel** : 44 et 47 % aux runs 1 et 2,
  0 % aux runs 4 et 5, 50 % au run 6, 17 % au run entropy_pure isolé.
