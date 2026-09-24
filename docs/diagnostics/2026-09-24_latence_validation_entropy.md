# Vérification de latence, validation ciblée des premiers coups, run entropy_pure (24/09/2026)

Stratégie par défaut : **composite, inchangée**. Aucun arrêt d'urgence sur ce cycle
(0 × 429, aucun `Retry-After`, latence maximale 0,49 s sur 2 000 requêtes).

## Tâche 0 : push

Commit `273e32c` (parité entropy_pure, surveillance H8, révélation par giveup).

## Tâche 1 : vérification de latence : GO

Un seul mot joué hors compteur (`data/latency_checks/`), plus de 10 h après l'arrêt
d'urgence du run 6.

| Appel | HTTP | Latence | En-têtes de limitation |
|---|---|---|---|
| GET /api/me (création de l'invité) | 200 | 0,224 s | aucun |
| GET /api/me (page) | 200 | 0,028 s | aucun |
| POST /api/game | 200 | 0,031 s | aucun |
| POST …/guess (×2) | 200 | 0,027 / 0,074 s | aucun |

Résultat : SUICIDEE trouvé en 2 essais. Latence normale, les tâches 2 et 3 sont
autorisées.

## Tâche 2 : validation ciblée des premiers coups

Outil : `scripts/validate_root_candidates.py` ; journal complet dans
`data/root_validation/`.
- Un seul invité, 1,5 à 2,5 s entre chaque requête, arrêt d'urgence intégré.
- Une partie est close par `giveup` quand il n'y reste rien à tester.
- Tests : `tests/test_validate_root_candidates.py`.

| Mesure | Valeur |
|---|---|
| Requêtes | 2 000 en 68,9 min (budget atteint) : 1 `/api/me`, 364 créations, 1 272 coups, 363 `giveup` |
| Réponses | 2 000 × HTTP 200 ; latence médiane 0,058 s, p95 0,081 s, max 0,487 s ; 0 en-tête de limitation |
| Candidats testés (coup 1 et replis, composite ∪ entropy_pure) | 1 272 sur 2 098 en attente |
| **Refusés** | **398 / 1 272 = 31,3 %** |
| Groupes entièrement validés | 57 / 130 (826 candidats restés en attente) |
| Groupes tirés par le serveur en 364 parties | **86 distincts seulement** |

### Taux de rejet avant et après

**Avant, au premier coup en jeu réel** (logs des runs) :

| Run | Contexte | Rejet au coup 1 |
|---|---|---|
| 1 | | 44 % |
| 2 | | 47 % |
| 3 | | 29 % |
| 4 et 5 | groupes déjà appris | 0 % |
| 6 | groupes jamais joués | 50 % |
| entropy_pure | | 17 % |

Sur les candidats de premier coup non validés : 31,3 %.

**Après** (caches recalculés, préférence pour les mots prouvés valides) :

| Stratégie | Groupes avec un coup 1 prouvé valide | Tirages observés couverts | Rejet au coup 1 estimé |
|---|---|---|---|
| composite | 91 / 130 | 100 % | **≈ 0 %** |
| entropy_pure | 87 / 130 | 99,7 % | **≈ 0,1 %** |

Le rejet estimé est pondéré par la fréquence de tirage observée.

**Réserve** : l'objectif « les 10 candidats de chaque groupe tous confirmés » n'est
atteint que pour 7 groupes sur 130 (4 en entropy_pure). Recalculer un groupe dont
un repli est refusé fait entrer de nouveaux replis non testés. Et les ~44 groupes
jamais tirés ne peuvent pas être validés tant que le serveur ne les propose pas. En
pratique, le solveur choisit d'abord un mot prouvé valide : chaque groupe réellement
tiré commence donc par un coup sûr. Il reste 826 candidats en attente ; le script
reprend là où il s'est arrêté.

### Mises à jour de données

- Liste noire : 210 → **611** mots (INVALID_WORD vérifiés, provenance tracée).
- Mots prouvés valides : **1 457**.
- `refresh_blocklisted_entries` recalcule aussi un groupe dont un **repli** est refusé
  (test ajouté). Entrées recalculées : 78 composite, 66 entropy_pure. Aucun mot refusé
  ne reste parmi les candidats.
- **Solutions révélées par les 363 `giveup`** : 360 mots distincts.
  - 11 étaient absents du corpus, pourtant courants : AUTONOMES, COUPABLES, TACTILES,
    PYGMEES, TRACAGE, BOUZOUKI, EBOUEUSE, ENTRAIDEE, LAICISMES, LUDDISME, MODALISEE.
    Ils ont été ajoutés au corpus (241 351 mots) et aux mots valides.
  - Le corpus couvre donc environ **97 %** du réservoir de solutions.
  - 3 doublons en 363 tirages : ordre de grandeur du réservoir ≈ 22 000 mots
    (paradoxe des anniversaires, estimation peu précise).
- Pas d'extraction complète du dictionnaire (hors scope).

## Tâche 3 : run entropy_pure en conditions réelles (10 mots)

Log : `data/improvement_runs/entropy_pure/`. Même code et mêmes métriques que le run 5.

| Métrique | Run 5, composite (référence) | Run entropy_pure |
|---|---|---|
| Trouvés | 10/10 | 9/10 (INTRANETS hors corpus, révélé par `giveup`, ajouté au corpus) |
| Erreurs | 0 | 0 |
| Temps total | 80,2 s | **65,9 s** |
| Temps moyen par mot | 8,02 s | **6,59 s** |
| Essais moyens (mots trouvés) | 2,9 | **2,33** |
| Rejets | **1** | 3 (INTRES et ISAIRE au coup 1 en I6, GABES au coup 2 en G5) |
| Chargement par mot | 1,16 s | 0,81 s |
| Latence maximale | 1,06 s | 0,36 s |

- **Mêmes mots rejoués hors ligne en composite** : 3,44 essais, contre 2,33 réels en
  entropy_pure. GAGES aurait demandé 10 essais en composite, donc un échec en jeu
  réel ; sans GAGES : 2,63 contre 2,25.
- **Signal à surveiller, sans être rédhibitoire** : 3 rejets contre 1. Deux d'entre eux
  touchent des coups 1 entropy_pure encore jamais joués, un coût d'apprentissage désormais
  couvert par la validation ciblée. Le troisième est un rejet au coup 2. La latence
  n'est pas dégradée.
- **Limite** : les mots tirés diffèrent d'un run à l'autre, et 10 mots ne suffisent pas
  à conclure. **Pas de bascule** : la décision te revient.
