# Phase 0 : entropy_pure par défaut, départage des coups 2+, second tour de validation (25/09/2026)

Aucun arrêt d'urgence pendant cette phase : 850 requêtes, latence maximale 0,44 s,
aucun 429, aucun en-tête de limitation.

## 1. entropy_pure devient la stratégie par défaut

- **Motif** : décision de l'utilisateur, prise sur le rejeu apparié de 788 solutions
  réelles (3,02 contre 3,12 essais, p = 0,0009 ; voir
  `2026-09-24_phase2_alternance_composite_entropy.md`).
- **Où** : une seule constante, `motus_solver.cache.DEFAULT_STRATEGY = "entropy_pure"`,
  plus `ROOT_CACHE_FILES` (un cache racine par stratégie). Elle est reprise par :
  - `Solver` ;
  - `BotRunner` (dashboard) ;
  - `scripts/run_improvement_cycle.py` (`--strategy`) ;
  - `bot/run_bot.py` ;
  - `scripts/diagnose_guess_pipeline.py` ;
  - la CLI `motus_solver.cli` (nouvelle option `--strategy`).
- **Composite conservé** comme option, avec des poids inchangés.
- **Rejeu hors ligne** (`run_improvement_cycle.simulate`) : il suit désormais la
  stratégie du run, au lieu d'un composite implicite.
- **Tests** : `test_default_strategy_is_entropy_pure_and_composite_stays_available`,
  `test_bot_runner_and_cycle_script_follow_default_strategy`.

## 2. Départage aux coups 2 et suivants

### Règle

- Parmi les coups dont le score est à moins de **2 % (écart relatif)** du meilleur, le
  premier mot déjà prouvé valide (`known_valid_words.json`) passe en tête. Sinon,
  l'ordre reste inchangé.
- **Où elle s'applique** : partout où le coup est calculé dynamiquement, donc aux coups
  2 et suivants, et au repli du coup 1 si ses 10 candidats en cache sont refusés.
- **Ce qui ne change pas** : ni les poids du composite ni le calcul d'entropie ; seul
  l'ordre entre coups quasi égaux bouge.
- **Pas de validation proactive** des candidats à ces coups.
- **Code** : `Solver._known_valid_first_among_near_ties`, constantes `NEAR_TIE_REL` et
  `NEAR_TIE_POOL` (20 coups examinés au plus).
- **Tests** : `tests/test_solver.py` :
  - le seuil est relatif ;
  - un meilleur coup hors tolérance est toujours conservé ;
  - sans mot valide connu, l'ordre est inchangé ;
  - les valeurs ne sont jamais modifiées.

### Choix de la tolérance : rejeu hors ligne

**Premier essai, biaisé, écarté.** Les 824 solutions connues étaient rejouées en
retirant seulement le mot cible des mots valides.
- Or les autres solutions y restaient. À égalité stricte entre deux candidats, le
  départage choisissait donc toujours celui qui n'était pas la cible.
- Résultat faussé : entropy_pure paraissait dégradé (17 mots pires, 0 meilleur à 1 %).

**Évaluation retenue, hors échantillon.**
- Les mots valides sont figés au commit `796d50b` (1 457 mots), avant la phase 1.
- On rejoue les **667 solutions tirées après ce commit**.
- Chaque cible est donc « connue valide » exactement comme en jeu réel (2,8 % d'entre
  elles l'étaient).

| | Essais moyens | Meilleur / pire (test du signe) | Coups dynamiques sur mots validés | Plus de 6 essais |
|---|---|---|---|---|
| entropy_pure, sans départage | 3,022 | | 1,9 % | 2 |
| entropy_pure, égalité stricte | 3,028 | 4 / 7 (p = 0,55) | 2,7 % | 2 |
| entropy_pure, 1 % | 3,028 | 4 / 7 (p = 0,55) | 2,7 % | 2 |
| **entropy_pure, 2 % (retenu)** | 3,028 | 4 / 8 (p = 0,39) | **2,8 %** | 2 |
| composite, sans départage | 3,132 | | 3,7 % | 4 |
| composite, égalité stricte | 3,132 | 0 / 0 | 3,7 % | 4 |
| composite, 1 % | 3,135 | 3 / 2 (p = 1) | 4,0 % | 5 |
| **composite, 2 % (retenu)** | 3,138 | 3 / 4 (p = 1) | **4,3 %** | 5 |

### Lecture

- **Coût** : aucun coût mesurable sur l'efficacité, quelle que soit la tolérance. On
  retient 2 %, la plus large testée, qui maximise la part de coups sûrs.
- **Bénéfice, à dire franchement : modeste.** Aux coups 2+, les candidats quasi à
  égalité sont rarement des mots déjà validés.
  - Le départage fait passer environ 1 % des coups dynamiques sur un mot sûr.
  - Il ne suffira pas à supprimer les rafales de rejets observées en composite
    (REPARLER, HOMICIDES) : aucun mot validé n'y était proche du meilleur score.
  - L'effet grandit avec la liste des mots valides (2 799 mots désormais, contre 1 457
    dans l'évaluation) et se mesurera en jeu (phase 5).

## 3. Second tour de validation des coups 1

Mêmes outil, débit et arrêt d'urgence (5 s) qu'au tour 1 (`--max-requests 2000`).

| Mesure | Valeur |
|---|---|
| Requêtes | 850 (336 parties) |
| Latence | médiane 0,049 s, max 0,44 s ; aucun 429, aucun en-tête de limitation |
| Arrêt | propre : 80 parties d'affilée sans rien à tester |
| Candidats testés | 177 : 122 valides, **55 refusés (31,1 %)** |
| Groupes observés pour la 1re fois | U6, U9 |
| Solutions révélées hors corpus (ajoutées) | 11 : COUCHAGES, ESSORAGES, CABRAGE, RACISTES, MANIABLES, STAGNANTS, SOIGNABLE, ECHEANTE, DEMELANTS, GOUTEURS, PONCEURS |
| Entrées recalculées en fin de tour | 13 par stratégie |
| Mots prouvés valides / liste noire | 2 362 → 2 799 / 922 → 977 (provenance complète) |

| | Avant | Après |
|---|---|---|
| Groupes à 10 candidats confirmés, composite | 64 | **79** |
| Groupes à 10 candidats confirmés, entropy_pure | 59 | **79** |
| Groupes observés dont le coup 1 est prouvé valide | 98/98 et 97/98 | **100/100 et 100/100** |
| Candidats restants, composite / entropy_pure | 437 / 463 | 377 / 389 |
| Groupes non observés (statut incertain) | 32 | **30** |

**Pourquoi « les 437 candidats restants » ne pouvaient pas tous être testés.** 319
d'entre eux appartenaient aux 32 groupes jamais tirés par le serveur. Ils ne sont
testables que si ces groupes finissent par sortir. Parmi les 118 candidats testables,
une partie reste dans des groupes tirés une ou deux fois sur 1 147 tirages : l'arrêt
après 80 parties sans rien à tester l'a acté.

**Estimation** : rejet au coup 1 de **0 %** pour les deux stratégies sur les groupes
observés, pondéré par la fréquence de tirage.
- Hors de cette estimation : les 30 groupes non observés (environ 31 % s'ils sortaient).
- Hors aussi : les coups 2 et suivants.
