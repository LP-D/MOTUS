# Phase 2 : 5 runs alternés composite / entropy_pure (24/09/2026)

Stratégie par défaut : **composite, inchangée**. La décision de bascule revient à
l'utilisateur.

## Conditions

- **Démarrage** : après la phase 1, interrompue proprement par un arrêt d'urgence
  (voir `2026-09-24_arret_urgence_validation.md`), puis un **contrôle de latence GO**
  22 min après l'arrêt.
  - Contrôle : un mot hors compteur (`data/latency_checks/run_02`), toutes les
    requêtes en HTTP 200, 0,008 à 0,08 s, aucun en-tête de limitation. PREPARER trouvé
    en 4 essais.
- **Runs** :
  - 5 × 10 mots sur /infinite, en alternance stricte : composite, entropy_pure,
    composite, entropy_pure, composite (`data/improvement_runs/alternated/`) ;
  - même code pour les 5 runs ;
  - caches des deux stratégies validés à parts égales en phase 1.
- **Journal par mot** :
  - lettre, longueur, stratégie ;
  - temps de chargement, de recherche solveur, de saisie et de réponse serveur ;
  - latence API ;
  - essais ;
  - pour chaque rejet : l'erreur serveur, la provenance (cache racine avec son rang, ou
    calcul dynamique) et si le mot était déjà prouvé valide ;
  - nombre de tirages antérieurs du groupe.
- **Aucun arrêt d'urgence** : latence API maximale 0,27 s. H8 : 0 NOT_FOUND.
- **Erreurs** : **0 sur les 5 runs**. Aucun correctif appliqué entre les runs (voir
  « Rejets »).
- **Groupes non observés** : aucun tiré dans ces 50 mots. Les 32 groupes restent au
  statut incertain.

## Runs

| Run | Stratégie | Trouvés | Temps total | s / mot | Essais moyens | Rejets (dont coup 1) | Latence max |
|---|---|---|---|---|---|---|---|
| 1 | composite | 9/10 (OISIVETES hors corpus, révélé) | 72,6 s | 7,26 | 2,89 | 4 (0) | 0,11 s |
| 2 | entropy_pure | 10/10 | 79,7 s | 7,97 | 3,10 | 3 (0) | 0,15 s |
| 3 | composite | 10/10 | 90,0 s | 9,00 | 2,60 | 11 (0) | 0,27 s |
| 4 | entropy_pure | 9/10 (INDIGOS hors corpus, révélé) | 77,6 s | 7,76 | 2,89 | 5 (0) | 0,11 s |
| 5 | composite | 10/10 | 71,7 s | 7,17 | 3,10 | 0 (0) | 0,11 s |

Les deux mots manqués étaient **absents du corpus**, ce qui ne dépend pas de la
stratégie. Ils ont été révélés par `giveup` et ajoutés au corpus.

## Comparatif (`scripts/compare_strategies.py`, `comparison.json`)

### 1. Mesuré en jeu réel (mots différents d'un run à l'autre, non appariés)

| Métrique | composite (runs 1, 3, 5 ; 30 mots) | entropy_pure (runs 2, 4 ; 20 mots) |
|---|---|---|
| Taux de résolution | 29/30 = 96,7 % | 19/20 = 95,0 % |
| Temps moyen par mot | 7,81 s | 7,87 s |
| Temps médian par mot | 6,90 s | 7,08 s |
| Temps moyen, mots sans rejet | 6,63 s | 7,21 s |
| Essais moyens (mots trouvés) | 2,86 | 3,00 |
| Rejets | 15, soit 15,0 % des coups soumis (0,50 par mot) | 8, soit 12,1 % (0,40 par mot) |
| Rejets au coup 1 | **0** | **0** |
| Mots touchés par un rejet | 5 (jusqu'à 6 rejets d'affilée) | 6 (2 au plus) |
| Recherche solveur, moyenne par mot | 0,09 s | 0,005 s |

### 2. Hors ligne, apparié sur les 50 mots tirés en phase 2

Chaque mot est rejoué par les deux stratégies, avec les caches, la liste noire et les
mots valides actuels. Les rejets sont donc exclus.

| | composite | entropy_pure |
|---|---|---|
| Essais moyens | **2,92** | 3,08 |
| Meilleure sur | 13 mots | 8 mots (29 égalités) |
| Résolus en 2 essais ou moins | 24 % | 14 % |

Test du signe : **p = 0,38**, donc pas de différence significative.

### 3. Hors ligne, apparié sur les 788 solutions réelles connues du serveur

Ce sont les 650 solutions révélées en validation et les mots trouvés par le bot : un
échantillon de la vraie distribution des tirages.

| | composite | entropy_pure |
|---|---|---|
| Essais moyens | 3,12 | **3,02** |
| Meilleure sur | 169 mots | **237 mots** (382 égalités) |
| Résolus en 2 essais ou moins | 20,3 % | 22,8 % |
| Plus de 6 essais (échec en jeu) | 7 | **3** |
| Essais moyens, 5 lettres | 3,95 | 3,64 |
| Essais moyens, 6 lettres | 3,32 | 3,22 |
| Essais moyens, 7 lettres | 3,16 | 3,12 |
| Essais moyens, 8 lettres | 3,02 | 2,91 |
| Essais moyens, 9 lettres | 2,84 | 2,73 |

Test du signe : **p = 0,0009**, un écart significatif en faveur d'entropy_pure, à
chaque longueur.

## Lecture

- **Mesuré en jeu** : sur 50 mots, les deux stratégies sont à égalité en temps
  (7,8 s par mot) et en résolution. Les écarts d'essais et de rejets restent dans le
  bruit : un seul mot, REPARLER avec 6 rejets, pèse 24,6 s.
- **Rejets** :
  - **La phase 1 a supprimé les rejets au coup 1** : 0 sur 50 mots, contre 44 à 50 %
    aux premiers runs et 17 % au run entropy_pure isolé.
  - **Les 23 rejets restants tombent tous aux coups 2 et 3**, calculés dynamiquement
    sur le corpus, que la validation ne couvre pas.
  - En composite, ils se concentrent en rafales sur des non-mots du corpus choisis pour
    leur rendement en information : HOULMOISE, HOLQUOISE, RECALTEE, RETACLEE… En
    entropy_pure, ils sont plus diffus.
  - Aucun correctif en cours de phase : modifier le choix des coups 2+ entre deux runs
    aurait faussé la comparaison.
- **Efficacité pure** : la seule mesure statistiquement significative est la plus
  large, les 788 solutions réelles. Entropy_pure y gagne environ 0,1 essai par mot,
  soit environ 0,25 s au débit imposé, avec moitié moins d'échecs au-delà de 6 essais.
  Sur les 50 mots de la phase 2, l'écart inverse n'est pas significatif.
- **Ce qui reste à trancher** : un gain d'environ 0,1 essai par mot justifie-t-il une
  bascule ? Aucun signal de risque propre à entropy_pure n'apparaît : moins de rejets
  par mot, pas de latence ni d'erreur. **Pas de bascule sans décision de
  l'utilisateur.**

## Piste d'amélioration (non appliquée)

Au coup 2 et au-delà, préférer un mot déjà prouvé valide parmi les meilleurs coups de
score quasi égal, comme au coup 1. Il serait utile aussi d'écarter les non-mots
manifestes du corpus. Cela éviterait les rafales de rejets, qui coûtent environ 2,3 s
chacun au débit imposé. Ce changement concerne les deux stratégies et doit être évalué
avant d'être appliqué.
