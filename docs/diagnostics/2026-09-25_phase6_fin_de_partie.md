# Phase 6 : fin de partie par mot sonde, analyse des runs, composite ou entropy_pure (25/09/2026)

Données : les 1 394 parties du dashboard (`data/dashboard_stats.json`), dont 1 240 le
25/09 (1 000 en composite, 240 en entropy_pure), le journal
`data/dashboard_bot_log.jsonl` (4 129 coups acceptés, 602 refusés), les tirages
(`data/group_draws.jsonl`, qui donnent la stratégie de chaque partie) et les
1 034 solutions révélées.

## 1. Défauts trouvés dans les runs

| Défaut | Effet observé | Correctif |
|---|---|---|
| Fin de partie à une lettre inconnue jouée candidat par candidat | Les **4 défaites** du 25/09 : PARIE, PAYES, PANES, PATES, PALES, PAMES (PA?ES) ; DE?ALE ; GA??EE ; ETA?ES | Mot sonde (section 2) |
| Solution ignorée après une défaite | Les 4 défaites n'ont pas de solution enregistrée | Lue dans la réponse au 6e coup (section 3) |
| Rejeu hors ligne biaisé en faveur de la base | Validations précédentes trop optimistes (section 4) | Leave-one-out |
| Fréquences du corpus recalculées à chaque partie | 0,35 s perdues par partie en entropy_pure (≈ 5 % d'une partie), où elles ne servent pas | Calcul au premier besoin, gardé d'une partie à l'autre |

Rien d'anormal par ailleurs :
- aucun mot refusé deux fois (la liste noire fonctionne) ;
- aucun mot rejoué dans une même partie ;
- aucun faux rejet ;
- aucune erreur depuis le 23/09.

Les refus se concentrent au coup 2 (398 sur 589), sur des mots rares du corpus.

**Observation, sans correctif** : après l'arrêt d'urgence de 11:57, la boucle
suivante a été relancée 32 s plus tard. Le garde-fou arrête la boucle mais n'impose
pas de délai avant une relance.

## 2. Fin de partie par mot sonde (`src/motus_solver/endgame.py`)

Tuzmo n'impose pas de jouer un mot compatible avec les retours précédents : ses seuls
codes d'erreur sont `INVALID_WORD`, `GAME_OVER` et `BAD_LENGTH`. La première lettre
est pré-remplie, donc tout mot joué commence par elle.

**Principe** : lister les mots encore possibles et les lettres qui les départagent,
puis jouer un mot (candidat ou non) qui teste plusieurs de ces lettres d'un coup.

**Deux régimes** (candidats supposés équiprobables) :
- **Risque** : plus de candidats que d'essais (≤ 200 candidats). Le coup habituel est
  gardé s'il garantit la victoire. Sinon, on joue le coup qui maximise la probabilité
  de gagner, quitte à sacrifier un essai.
- **Vitesse** : victoire déjà garantie, ≤ 30 candidats. Le coup habituel est remplacé
  seulement si un autre coup fait gagner au moins 0,05 essai en moyenne. Les sondes
  sont alors limitées aux mots déjà acceptés par le jeu (section « Validation en jeu
  réel »). Exemple ETA?ES : une sonde, puis la solution, soit 3 essais au lieu de 6.

**Priorités à valeur égale** :
1. un candidat (il peut gagner tout de suite) ;
2. un mot déjà accepté par le jeu (pas de refus à payer).

**Calcul** : récursion sur (candidats restants, essais restants).
- Les retours de tous les coups examinés sur tous les candidats sont calculés une
  seule fois.
- La récursion est bornée : 10 coups par nœud, 800 nœuds au plus.
- Coût mesuré : +0,03 s par partie en moyenne, 0,37 s au pire sur un coup
  (entropy_pure).

### Rejeu hors ligne sur 2 289 solutions réelles

Commande : `python scripts/compare_endgame.py`. Sorties :
`data/improvement_runs/endgame/comparison.json` et `comparison_rows.jsonl`.
Rejeu en leave-one-out (section 4). Les refus du jeu ne sont pas simulés.

| Variante | Défaites | Essais moyens (défaite = 7) | Gagnés en 5-6 essais | Calcul / partie |
|---|---|---|---|---|
| entropy_pure | 7 | 3,031 | 86 | 0,006 s |
| **entropy_pure + fin de partie** | **0** | **3,026** | **64** | 0,032 s |
| composite | 8 | 3,092 | 97 | 0,013 s |
| composite + fin de partie | 0 | 3,100 | 82 | 0,045 s |

- Défaites entropy_pure sans sonde : CARRE, CARNE, GAVES, GAZES, MORNE, PAYEE, RAPES.
  Toutes des fins de partie à une lettre près.
- Taux de défaite de la base : **0,31 %**, contre 0,32 % mesuré en jeu réel (4 sur
  1 240). Le rejeu est donc réaliste.
- En composite, la fin de partie coûte +0,008 essai en moyenne : c'est le prix des
  essais sacrifiés dans des parties que la base aurait gagnées par chance. En
  entropy_pure (défaut), elle supprime les défaites sans rien coûter.

**Réglages testés** sur les 939 mots où la fin de partie intervient. Première version,
où le régime vitesse permettait aussi les sondes jamais testées :

| Réglage | entropy_pure | composite |
|---|---|---|
| vitesse ≤ 30 candidats, gain ≥ 0,05 | **3,004** | **3,076** |
| risque seul | 3,031 | 3,100 |
| gain ≥ 0,25 | 3,011 | 3,083 |
| gain ≥ 0,5 | 3,026 | 3,093 |
| vitesse ≤ 10 candidats | 3,011 | 3,087 |

Ce gain de 0,03 essai (≈ 0,06 s par partie) ne tenait pas en jeu réel : les sondes les
plus discriminantes sont souvent des mots rares, donc refusés. Chaque refus coûte ~2 s.

### Validation en jeu réel (/infinite, entropy_pure, 20 + 20 parties)

- **Version 1** (sondes jamais testées permises en régime vitesse) :
  - 20/20 trouvés, 2,70 essais en moyenne, 8 coups de fin de partie, 0 erreur ;
  - mais sur T·6, TULENG, TALUEE puis TELNET ont été refusés d'affilée avant TIPULE,
    soit ~6 s perdues pour 0,3 essai espéré.
- **Version retenue** (régime vitesse limité aux mots déjà acceptés) :
  - 19/20 trouvés, 2,68 essais en moyenne, 0 erreur ;
  - le 20e mot, TREBUCHEE, est hors corpus : révélé par `giveup` et ajouté ;
  - 7 coups de fin de partie, tous sur un mot déjà accepté ou un candidat ;
  - 6 refus, tous sur des coups habituels (coups 2-3, plus de 30 candidats) et
    **aucun sur une sonde**.
- Plusieurs remplacements ont une espérance égale : ARSENIE au lieu d'ARIETTE,
  DEMAGOGIE au lieu de DEGANTERA. Le coup habituel n'avait jamais été accepté par le
  jeu (+0,15) : on joue le mot sûr, sans risque de refus.

## 3. Solution quand le mot n'est pas trouvé

Le code du site (vue de jeu) montre que la réponse de `/guess` au 6e essai raté
contient `session.status: "lost"` et `session.answer`. Le site l'affiche dans son
récapitulatif.

- **Défaite en 6 essais** : la solution est lue dans cette réponse, puis enregistrée
  dans les stats, dans `revealed_solutions.jsonl` (`revealed_by: "défaite"`), dans les
  mots valides et, si elle manque, dans le corpus.
- **Mot absent du dictionnaire** (plus aucun candidat) : `giveup` reste la méthode
  principale. Il donne le même résultat que griller les essais, en une requête au lieu
  de cinq au plus.
  - Si `giveup` échoue, le bot grille maintenant les essais restants avec des mots
    jouables (déjà acceptés d'abord). La réponse au 6e essai donne la solution
    (`gave_up`, `method: "essais"`).
  - Le bouton ↻ ne sert plus qu'en dernier recours (/infinite).

## 4. Biais des rejeux hors ligne précédents

Toutes les solutions connues sont dans `known_valid_words.json`, et le solveur joue
d'abord un mot déjà accepté à égalité (départage du 25/09). En rejeu, il tombait
donc sur la solution en fin de partie, ce qui n'arrive pas pour un mot tiré pour la
première fois. En jeu réel, seules **6,4 %** des solutions du 25/09 étaient déjà
connues au moment du tirage.

Effet sur le rejeu d'entropy_pure :

| Rejeu | Défaites | Essais moyens |
|---|---|---|
| avec la solution dans les mots valides (biaisé) | 0 | 2,69 |
| leave-one-out (réaliste) | 7 | 3,03 |

Les validations hors ligne précédentes ont le même biais. C'est le cas du départage
(`2026-09-25_phase0_defaut_departage_validation.md`) et de la comparaison des
stratégies de la phase 2. Leurs gains absolus sont surestimés. Le classement
entropy_pure devant composite tient toujours (section 5).

## 5. Composite ou entropy_pure : lequel est le plus rapide

**En jeu réel, le 25/09** (non apparié : mots différents) :

| | entropy_pure (240 parties) | composite (1 000 parties) |
|---|---|---|
| Essais moyens (trouvées) | **2,88** | 3,01 |
| Mots refusés par partie | **0,32** | 0,40 |
| Temps moyen par partie | **6,52 s** | 7,05 s |
| Temps de calcul du solveur | < 0,03 s par coup | < 0,03 s par coup |

**Hors ligne, apparié, leave-one-out** (2 289 mots) :
- entropy_pure fait mieux sur 637 mots, moins bien sur 506 ;
- écart de −0,06 essai (p = 0,0001), et −0,07 avec la fin de partie (604 contre 456).

**Verdict : entropy_pure est le plus rapide**, d'environ 0,5 s par partie (≈ 7 %).
- Le temps est presque tout en entrée-sortie : ~2 s par coup accepté, dont ~1,5 s
  de délai de frappe (réglable dans le dashboard) et ~0,13 s de réponse serveur.
  Le calcul du solveur est négligeable pour les deux stratégies.
- La vitesse se gagne donc en coups et en refus évités. entropy_pure gagne sur les
  deux et reste le défaut.

## 6. Dashboard : section Événements

Chaque événement s'affiche en phrase, avec le détail brut au survol. Exemples :
- « Coup 2 : PLATS (21 mots possibles) » ;
- « PARIE : 2 bien placées, 1 mal placée », précédé de carrés colorés ;
- « Fin de partie : 6 mots possibles pour 3 essais. PIGNY (mot sonde) teste G, N, Y
  d'un coup au lieu de PANES : chances de gagner 67 % → 100 % » ;
- « Coup 3 : plus aucun mot de notre dictionnaire ne correspond, la solution n'y est
  pas. On abandonne pour que Tuzmo la donne », puis « Abandon : Tuzmo révèle la
  solution CHIMISTES », puis « CHIMISTES ajouté au dictionnaire pour les prochaines
  parties » ;
- « Perdu après 6 essais. Tuzmo a donné la solution : PAVES ».

`endgame_move` et `not_solved` sont maintenant persistés dans
`data/dashboard_bot_log.jsonl`, pour suivre la fin de partie en jeu réel.

## 7. Non fait : mode classé

Le mode classé reste non automatisé. Cela couvre le jeu en duel, le délai d'envoi
aléatoire destiné à masquer le bot à l'adversaire et les fiches de profil des
adversaires. Les duels opposent de vrais joueurs qui engagent leur classement (voir
`2026-09-25_phase1_modes.md`).

## Tests

- `tests/test_endgame.py` : PA?ES perdu candidat par candidat, gagné avec sonde pour
  les 8 solutions possibles ; coup habituel gardé quand il suffit ; régime vitesse ;
  sonde déjà acceptée préférée ; `Solver.discard`.
- `tests/test_persistent_session.py` : solution enregistrée après une défaite ; essais
  grillés quand `giveup` échoue.
