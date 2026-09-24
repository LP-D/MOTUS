# Cycle d'amélioration n° 2 : tâches 0 à 5 (23/09/2026)

## Raison de l'arrêt : ARRÊT D'URGENCE (throttling), pas la condition normale

- **Quand** : run 6 (1er run du cycle), partie 6, C7, 1er coup CAIROTE.
- **Ce qui s'est passé** : `POST /api/game/5a2d01b0…/guess` a bien été envoyé
  (t = 656,819), puis **aucune réponse ni échec réseau en 10 s**. Les appels juste avant
  (`GET /api/me` et `POST /api/game`) avaient répondu en ~0,15 s, et toutes les requêtes
  des 5 parties précédentes en moins de 0,17 s.
- **Signaux absents** : aucun HTTP 429, aucun `Retry-After`. Le signal est donc une
  « latence anormale sur une requête simple » (> 10 s), que la consigne classe comme
  throttling.
- **Décision** : boucle arrêtée immédiatement, aucune requête de diagnostic envoyée
  ensuite. Blocage serveur ou incident réseau ponctuel : impossible à trancher sans
  nouvelles requêtes. Pas de reprise sans accord.
- **Preuve** : `data/improvement_runs/run_06_games.jsonl` (dernière ligne, `api_calls`)
  et `data/dashboard_bot_log.jsonl` (événement `throttled`).

## Tâche 5 : runs

| Run | Trouvés | Erreurs | Temps total (s) | Temps moyen / mot (s) | Essais moyens | Rejets (dont coup 1) | Solveur (s/mot) | Chargement (s/mot) | H8 |
|---|---|---|---|---|---|---|---|---|---|
| 5 (référence, cycle précédent) | 10/10 | 0 | 80.2 | 8.02 | 2.9 | 1 (0) | 0.01 | 1.16 | 0 |
| **6** | 5/6 (arrêt) | 1 (throttling) | 61.5 (6 mots) | 10.25 | 3.0 | 5 (3 parties) | 0.00 | **0.87** | 0 |

Le minimum de 5 runs n'a pas été atteint : l'arrêt d'urgence est intervenu au 1er run.
Sur les 5 mots terminés, le chargement tombe à 0,87 s par mot (1,16 s au run 5). Les
rejets restants sont des mots racine jamais testés (C9 : COURIATES, COUTAINES,
COULAINES ; C6 : COUIER ; R8 : RETOUAIS), chacun payé une seule fois par groupe.

## Tâche 0 : push

Commit `9d4c96b` (cycle d'amélioration n° 1, 100 tests).

## Tâche 1 : H8 (« partie introuvable »)

Surveillance ajoutée à chaque partie (`h8` dans les logs de cycle) :
- NOT_FOUND oui ou non, et à quel appel ;
- 1re partie du contexte, invité pré-créé ou non, cookie présent avant le chargement ;
- identifiants de session créés comparés à ceux visés par les coups ;
- écart d'envoi entre `/api/me` et `/api/game`.

Tout NOT_FOUND compte comme une erreur de run. Tests : `tests/test_improvement_cycle.py`.

Depuis le correctif (invité créé avant le 1er chargement) : run 5 (10 parties),
validation de la tâche 4 (2 parties), run 6 (6 parties). Soit **0 NOT_FOUND et
0 incohérence d'identifiant en 18 parties sur 3 contextes**. C'est encore insuffisant
pour une course : **pas déclaré résolu**, surveillance maintenue.

## Tâche 2 : parité du cache pour `entropy_pure`

- **Constat** : le cache entropy_pure n'avait **pas** de coups de repli, et le solveur
  du bot ne savait pas jouer cette stratégie.
- **Fait** :
  - `tree.top_guesses_entropy_pure` : même départage que `best_guess_entropy_pure`,
    dont le 1er élément est toujours le résultat.
  - Entrées entropy_pure avec 9 coups de repli.
  - `Solver(strategy="entropy_pure")` : cache, puis repli en cascade, puis mots connus
    valides ; recalcul dynamique vectorisé en dernier recours.
  - Option `BotRunner.strategy`, **composite par défaut**.
- **Cache reconstruit** (864 s) : 130/130 entrées avec 8 à 9 replis, aucun mot en liste
  noire.
- **Tests** : repli sans aucun recalcul, 1er coup identique à la référence, stratégie
  composite par défaut.
- **Comparatif** : il existe déjà sur le **corpus complet** (241 339 mots, pas un
  échantillon), `data/simulation_report.xlsx`. Temps réel mesuré : entropy_pure 1 685 s ;
  composite ~43 min au total, run interrompu puis repris sur checkpoint. La parité ne
  modifie pas ces chiffres : hors ligne, aucun mot n'est refusé, donc les replis ne
  servent jamais. Seuls ~25 mots racine composite et 3 entropy_pure ont changé depuis
  (exclusion de la liste noire) : écart marginal, relance possible sur demande.

| Seuil | 5-6 composite | 5-6 entropy_pure | 7-8 composite | 7-8 entropy_pure | 9 composite | 9 entropy_pure |
|---|---|---|---|---|---|---|
| ≤ 2 | 37,82 % | **44,47 %** | 59,60 % | **66,08 %** | 71,97 % | **77,05 %** |
| ≤ 3 | 77,51 % | **81,89 %** | 90,06 % | **92,29 %** | 94,70 % | **95,72 %** |
| ≤ 4 | 92,06 % | **93,73 %** | 97,13 % | **97,77 %** | 98,61 % | **98,90 %** |
| ≤ 5 | 96,93 % | **97,69 %** | 99,04 % | **99,29 %** | 99,52 % | **99,63 %** |
| ≤ 6 | 98,90 % | **99,20 %** | 99,73 % | **99,80 %** | 99,87 % | **99,88 %** |
| Échec | 1,10 % | **0,80 %** | 0,27 % | **0,20 %** | 0,13 % | **0,12 %** |

`entropy_pure` est meilleure sur toutes les tranches et à tous les seuils. **Non activée**,
et poids composites **inchangés** : la décision te revient.

## Tâche 3 : stress test et extraction du dictionnaire

- **Prémisse vérifiée, et fausse.** Le stress test initial utilisait un seul
  `requests.Session()` (`scripts/stress_test_dictionary_endpoint.py`, ligne 271) pour ses
  1 600 requêtes, donc un seul cookie, c'est-à-dire déjà le modèle « invité persistant ».
  Son résultat s'applique tel quel : aucun signal sur 1 600 requêtes à 1,5–2,5 s. La seule
  limite découverte depuis porte sur la **création d'invités**, sans effet sur une
  extraction faite avec un seul invité.
- **Non relancé** : il n'apporterait aucune information et ajouterait ~1 h de charge sur
  le site.
- **Extraction complète non lancée** : 289 606 requêtes, soit 160,9 h de trafic continu
  vers le site. Il faut ton accord explicite, d'autant qu'un arrêt d'urgence vient de se
  produire.
- **Alternative ciblée, proposée mais non lancée** : ne valider que les mots racine et
  leurs replis (130 × 10 = 1 300 mots, soit ~2 000 requêtes avec les rotations de
  session, ~1 h 10). C'est la source principale des rejets restants.

## Tâche 4 : révélation de la solution pour les mots hors corpus

- **Endpoint** : `POST /api/game/{id}/giveup` (code du site : `api.giveUp`, fetch JSON,
  corps `{}`). /infinite n'affiche pas ce bouton.
- **Validé en direct** : HTTP 200, solution révélée (VERBAUX, abandon forcé en
  validation). La partie suivante démarre dans une nouvelle session, sans reprise.
- **Mécanisme standard** (`BotRunner._abandon`) : sur « candidats épuisés », le bot
  appelle `giveup`. La solution est alors :
  - journalisée dans `data/revealed_solutions.jsonl` ;
  - ajoutée au corpus (mémoire et `data/corpus_fr.txt`) si elle en était absente ;
  - ajoutée aux mots connus valides ;
  - inscrite dans les stats de distribution des solutions.

  Repli sur le bouton ↻ si `giveup` échoue. Tests : `tests/test_persistent_session.py`,
  `tests/test_dashboard_stats.py`.
- **A6 et P8 : non récupérables.** Ces parties appartenaient à des invités depuis
  abandonnés, et `giveup` exige la session et le cookie de leur propriétaire.

## Comparatif trio Excel, cumulé (runs 1 à 6)

53 mots comparables : trio puis bot, **4,19 essais** ; bot seul, **3,09**. Le trio est
meilleur 1 fois, pire 43 fois, égal 9 fois.
