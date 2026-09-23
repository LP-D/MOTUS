# Diagnostic : coups "ambigus" en cascade (23/09/2026)

## Symptôme rapporté

13e itération `/infinite` : 3 coups acceptés (FOUIRAS, FETAMES, FASEYES), puis 9 coups
consécutifs "ambigus", avec un clavier apparemment bloqué. Trois hypothèses étaient
envisagées : serveur ralenti, timeout ou parsing local mal calibré, requête jamais envoyée.

Les logs de cette partie ne sont pas présents sur cette machine (données locales,
gitignorées). Le diagnostic a donc été refait sur **3 nouvelles parties instrumentées**
(`scripts/diagnose_guess_pipeline.py`, moniteur réseau passif `bot/network_monitor.py`).
Pour chaque coup, on a relevé : code HTTP, latence réelle, en-têtes de limitation,
mot **réellement** présent dans le corps de la requête, et réponse serveur.

Preuve brute : [`2026-09-23_cascade_evidence.jsonl`](2026-09-23_cascade_evidence.jsonl).

## Verdict (sur preuve)

| Hypothèse | Verdict | Preuve |
|---|---|---|
| Serveur ralenti | **Écartée** | 26 requêtes `/guess` : toutes HTTP 200, latence 74–303 ms, aucun en-tête `Retry-After` / `X-RateLimit-*` |
| Requête jamais envoyée | **Écartée** | Chaque appui sur Entrée a bien produit un `POST /guess` |
| Timeout/parsing local | **Proche, mais la cause exacte est autre** | La requête part, mais avec le **mauvais mot** (voir ci-dessous) |

**Cause réelle.** Après un vrai rejet serveur (`INVALID_WORD`), Tuzmo **ne vide pas la
ligne**. Le bot tapait le mot suivant, mais les clics étaient ignorés (ligne pleine :
c'est le « clavier bloqué »). Entrée renvoyait alors **l'ancien mot**, qui était rejeté
à nouveau. L'ancien client attribuait ce rejet au **nouveau** mot et le mettait en
liste noire, en cascade jusqu'à épuisement des candidats.

Extrait (partie E, 9 lettres) :

```
coup voulu    décision bot   mot RÉELLEMENT envoyé   réponse serveur
ENTOURAIS     accepté        ENTOURAIS               200, result
EMBLAYENT     rejeté         EMBLAYENT               200, INVALID_WORD  <- vrai rejet
EMPLANTEE     rejeté         EMBLAYENT               200, INVALID_WORD  <- faux rejet
EGALEMENT     rejeté         EMBLAYENT               200, INVALID_WORD  <- faux rejet
ETALEMENT     rejeté         EMBLAYENT               200, INVALID_WORD  <- faux rejet
... 19 faux rejets d'affilée (ECLATANTE, EPANCHENT, EMBETANTE...), fin : candidats épuisés
```

La même chose se produit sur la partie H, 7 lettres : HAUTAIS est vraiment rejeté, puis
HAUTINS et HAUTAIN sont faussement rejetés.

**Contamination de la liste noire.** `data/known_invalid_words.json` contenait 57 mots,
dont des mots manifestement valides : PIERRES, GAGNANTES, SCOUTISME, GOUTERAIS, GITAN,
PETRINS… On y reconnaît des grappes typiques de la cascade : PIERR\*, GI\*\*\*.

Non reproduit : le « > 20 s par coup ». Ici, chaque coup ambigu prenait environ 2,3 s.
Le blocage long vu sur l'autre machine n'a pas pu être observé. Le nouveau délai de clic
borné (5 s) le ferait remonter en erreur explicite au lieu de le laisser pendre.

## Correctif (`bot/tuzmo_client.py`)

La **réponse serveur** devient la source de vérité, à la place de l'état DOM de la ligne :

1. La ligne est vidée (retour arrière) après tout coup non accepté.
2. Le mot réellement envoyé est vérifié dans le corps de la requête. S'il diffère du
   mot voulu : `GuessInputError`, **jamais** de liste noire.
3. Seul un `INVALID_WORD` portant sur ce mot exact lève `WordRejectedError`. C'est le
   seul cas où un mot peut entrer en liste noire.
4. Si Entrée ne déclenche aucune requête : `GuessNotSentError`, pas de liste noire.
5. Le feedback est lu dans la réponse API (`result`) et non dans le DOM. Cela supprime
   aussi une course de lecture après une victoire (/infinite remplace le plateau par le
   mot suivant).
6. Garde de débit dans le client : au moins 1,5–2,5 s entre deux requêtes (débit
   validé). S'y ajoutent 1,5–2,5 s entre deux parties, dans `BotRunner._run`.
7. Arrêt d'urgence (`ThrottlingDetectedError`) sur HTTP 429, `Retry-After` ou latence
   > 10 s. Le dashboard passe alors en statut `throttled` et la boucle s'arrête.

Tests de non-régression : `tests/test_tuzmo_client_submission.py`. Le faux Tusmo y
reproduit le comportement observé (ligne non vidée après rejet). Voir aussi
`tests/test_solved_confirmation.py::test_input_error_is_retried_and_never_blocklisted`.

## Données corrigées

- **Liste noire.** Seuls les mots dont le rejet est prouvé ont été conservés. Les
  sources fiables sont : la validation API directe, et le rejet au premier coup sur
  une ligne vierge (les anciens coups 1 du cache, dont BOUERAIS et RECOUINAS). Cela
  fait 15 mots. S'y ajoutent 17 mots rejetés pendant la matrice, avec vérification
  serveur (AINOUE, COURAIE, NOISEAU…) : 32 mots au total. Les 42 autres sont en
  quarantaine dans `data/known_invalid_words_quarantine.json`, pour audit. Un mot
  réellement invalide sera re-rejeté par le serveur et remis en liste noire, cette fois
  avec preuve.
- **Import de `motus_trios_fusionne.xlsm`.** La feuille « Mots interdits » a été importée
  dans la liste noire : 106 mots refusés en jeu manuel, ajoutés un par un entre le 21/08
  et le 17/09/2026, donc sans cascade possible. Aucun ne recoupait la liste noire ni la
  quarantaine. Liste noire finale : 138 mots. La provenance de chaque mot est tracée dans
  `data/known_invalid_words_provenance.json`. Aucun coup 1 du cache n'était concerné.
- **Cache racine.** Les entrées dont le coup 1 est un mot confirmé invalide ont été
  recalculées : 14 en composite, 1 en entropy_pure. Commande :
  `python scripts/build_root_cache.py --refresh-blocklisted`. Sans ce recalcul, leur
  groupe retombait à chaque partie sur le repli dynamique. Durées mesurées : 19 s par
  rejet sur C,7, 93 s sur E,9, environ 6 min sur R,9. Plus aucune entrée du cache ne
  pointe vers un mot de la liste noire.
- **Risque résiduel.** Certains nouveaux coups 1 n'ont jamais été soumis au serveur,
  par exemple RESOUCIAT (R,9) et BOURIATE (B,8, en quarantaine). S'ils sont invalides,
  le premier rejet coûtera un repli dynamique, plusieurs minutes sur R,9. Relancer
  ensuite `--refresh-blocklisted` rend ce coût non récurrent.

## Matrice de cas (boucle autonome `scripts/run_case_matrix.py`)

La boucle utilise le vrai chemin de production (`BotRunner._play_one_game`, client
corrigé). Elle a joué 12 parties et s'est arrêtée sur « couverture complète ». Preuve
brute : [`2026-09-23_matrix_games.jsonl`](2026-09-23_matrix_games.jsonl).

| Cas | Résultat | Preuve |
|---|---|---|
| A. Lettre et longueur détectées | 12/12 conformes | Valeurs DOM identiques à `firstLetter`/`wordLength` de `POST /api/game` |
| B. Lettres répétées | 8 parties vérifiables, 0 écart | `pattern_string(coup, solution)` identique au pattern serveur pour chaque coup (PAROISSE, LIQUEUR, POLLUEUR, DEPORTER, EMPLOYE, NEGATIONS…) |
| C. Mot court / mot long | OK | M5 (MOCHE) et N9 (NEGATIONS) résolus. R,9 n'a pas été tiré en direct : mesuré hors ligne seulement |
| D. Mot racine rejeté au coup 1 | 3 occurrences, OK | NOISEAU, AINOUE, COURAIE rejetés ; le mot de repli est bien envoyé (0,8 à 19 s de calcul) |
| E. Rejets en cascade | 3 occurrences, OK | Jusqu'à 3 rejets confirmés d'affilée ; chaque requête porte le mot voulu ; aucun doublon ; 0 coup mal transmis sur les 12 parties |
| F. Défaite réelle | OK | M8 perdue volontairement : `finished: {won: false, tries: 6}`, aucune requête après le 6e coup, partie suivante saine |

Deux issues hors matrice :
- **A6, candidats épuisés.** Aucun des 1 804 mots A6 du corpus n'est compatible avec
  le retour serveur (ANURIE → `201210`). La solution est hors corpus : c'est une limite
  connue du corpus, pas un bug.
- **H8, `NOT_FOUND` (HTTP 404) sur le 1er coup.** Le bot l'a géré proprement : partie
  en `game_error`, rien en liste noire, boucle poursuivie. La cause n'est pas élucidée,
  car les appels API complets n'étaient pas encore tracés pour ce run. Hypothèse non
  prouvée : un effet précurseur de la limite de création d'invités décrite ci-dessous.

## Arrêt d'urgence : 429 « guest creation rate limited »

Le run d'investigation suivant a échoué dès le chargement de la 1re partie : le
plateau ne s'affichait pas. Une sonde de chargement a montré :

```
GET  /api/me   -> 429 {"message":"guest creation rate limited"}
POST /api/game -> 429 {"message":"guest creation rate limited"}
```

Toute activité live a été arrêtée immédiatement, comme prévu par les consignes. Ce
n'est **ni** un ralentissement serveur, **ni** un problème de débit des coups : la
latence de `/guess` est restée sous 0,6 s, sans aucun 429.

Cause : chaque partie ouvrait un contexte navigateur neuf, sans cookie, donc Tuzmo
créait un nouvel invité anonyme à chaque fois. Cela fait environ 18 créations en
~45 min dans cette session, sans compter les parties jouées sur l'autre machine. Le
stress test initial réutilisait un seul cookie : il n'a jamais sollicité ce chemin.

Correctif (hors ligne, **non revalidé en direct**) : `BotRunner._run_games` réutilise
désormais un seul contexte, donc un seul invité, pour toute la boucle. De plus, un 429
au chargement lève `ThrottlingDetectedError` (statut `throttled`) au lieu d'un timeout
Playwright muet. Tests : `tests/test_guest_rate_limit.py`. À valider en direct une fois
la limite levée : le rechargement de `/infinite` avec le même invité démarre-t-il bien
une nouvelle partie après une victoire ou une défaite ?

## Contexte persistant : analyse de H8 et validation en faible volume

**Code du site** (modules JS publics, lus sans jouer). `startGame` appelle
`POST /api/game`, puis rejoue les `session.guesses` renvoyés. Si la session n'est
pas `playing`, il affiche la fenêtre de fin, dont le bouton « Rejouer » relance
`POST /api/game`. Le mode `/infinite` ne propose pas « Abandonner » ; il a un bouton
↻ (`run-reset` → `POST /api/game/{id}/reset`), avec une confirmation de 3 s quand
le score est > 0.

Conséquence : avec un invité conservé, le serveur peut renvoyer la partie en cours.
Une partie insoluble (solution hors corpus) serait donc reprise sans fin. Garde-fous
ajoutés (`tests/test_persistent_session.py`) :
- une session reprise est rejouée dans le solveur ;
- une session terminée au chargement déclenche un seul clic « Rejouer », sinon arrêt ;
- sur « candidats épuisés », le mot est clos via ↻ ;
- la boucle s'arrête si l'abandon échoue ou si deux parties reprises se suivent.

**H8** (`NOT_FOUND` sur le 1er coup) s'est produit avec un contexte neuf, donc
**avant** le contexte persistant, qui n'en est pas la cause. Sur les 9 chargements de
la validation : `/api/me` toujours en 200, un seul `POST /api/game` par chargement
(l'hypothèse de deux créations concurrentes n'est pas étayée), aucun `NOT_FOUND`.
L'explication la plus cohérente reste une création d'invité déjà dégradée : H8 était
le 13e invité neuf, trois chargements avant le 429. Elle n'est pas prouvée. Avec le
contexte persistant, un run ne crée plus qu'un seul invité.

**Validation** (3 runs, 9 parties en tout, preuve :
[`2026-09-23_validation_contexte_persistant.jsonl`](2026-09-23_validation_contexte_persistant.jsonl)) :

| Constat | Preuve |
|---|---|
| Après une victoire, la même session continue avec le mot suivant | même `session_id` d'une partie à l'autre, 0 coup repris |
| L'abandon via ↻ fonctionne, confirmation comprise | `POST …/reset` 200 ; la partie suivante a une nouvelle session, non reprise |
| Après une défaite réelle, le chargement donne directement une nouvelle partie | nouvelle session `playing` ; « Rejouer » n'a pas été nécessaire |
| Cas A (9/9), B, C (5 et 9 lettres), D, E, F | tous validés |
| Débit | 0 × 429, latence max 0,95 s |

Deux défauts trouvés et corrigés pendant la validation :
1. Il n'y a pas de bouton « Abandonner » sur `/infinite`. L'abandon passe désormais par ↻.
2. L'attente de 3 s entre les deux clics de ↻ laissait expirer la confirmation. Elle
   est maintenant de 0,5 s ; le test échoue avec l'ancienne attente (contre-épreuve).

Les nouveaux mots racine invalides découverts (RETOUAI, GUIORE, FAURIONES) ont été
recalculés dans le cache ; leurs remplaçants avaient déjà été acceptés en direct.
