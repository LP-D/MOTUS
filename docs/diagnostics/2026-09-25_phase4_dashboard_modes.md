# Phase 4 : dashboard multi-modes (25/09/2026)

## Interface (`dashboard/static/index.html`)

- **Sélecteur de mode** : Infini, Quotidien (1 partie par jour, marqué « déjà joué »
  le cas échéant), et « Classé (non automatisé) » affiché mais désactivé.
- En quotidien, le nombre de parties est figé à 1.
- **Sélecteur de stratégie** : entropy_pure (par défaut) ou composite.
- **État courant** :
  - mode actif ;
  - partie en cours (lettre, longueur, coup en cours) puis son issue : trouvée,
    perdue, hors corpus, mot du jour déjà joué, arrêt d'urgence ;
  - statut « quotidien déjà joué » distinct d'une erreur ;
  - refus affichés en clair (partie du jour déjà jouée, mode non automatisé).
- **Statistiques et solutions filtrées par mode** : infini par défaut, quotidien, ou
  tous modes sur demande explicite. Le nombre de parties enregistrées par mode est
  affiché. Au démarrage d'une partie, le filtre suit le mode joué.
- **Panneau de latence et plateau miroir** : inchangés. Ils reposent sur les
  événements de coup, communs à tous les modes.
- **Classé non automatisé**, donc pas de vue « duel » : pas de chrono de 45 s, pas de
  contrainte n-1, pas de panneau adverse (voir `2026-09-25_phase1_modes.md`).

## Backend (`dashboard/backend.py`)

- **`POST /api/start`** : reçoit `mode` et `strategy` en plus de `iterations`.
  - En quotidien, le nombre de parties est ramené à 1.
  - `ranked` est refusé avec une explication.
  - Une stratégie inconnue renvoie HTTP 400.
- **`GET /api/status`** : `mode`, `strategy`, `current_game`, `supported_modes`,
  `strategies`, `daily_played_today`.
- **`GET /bot/stats?mode=`** et **`/bot/stats/solutions?mode=`** : `infinite` par
  défaut, `daily` ou `all`. Un mode inconnu renvoie HTTP 400. Les runs `/infinite` et
  les parties du jour ne sont jamais agrégés sans le demander.

## Lanceur unifié (`scripts/run_dashboard.py`)

Il démarre toujours dashboard et bot ensemble. Nouvelles options :
- `--mode infinite|daily` ;
- `--games N` ;
- `--strategy` ;
- `--autostart` : démarre le bot dès que le dashboard répond, par la même API que le
  bouton, donc avec les mêmes garde-fous (débit, arrêt d'urgence, une partie
  quotidienne par jour) ;
- `--no-browser`.

## Vérifications

- **Tests** : `tests/test_dashboard_modes.py` (7), sans aucune partie réelle.
  - état exposé par `/api/status` ;
  - quotidien plafonné à une partie ;
  - classé refusé ;
  - quotidien déjà joué ;
  - stratégie inconnue ;
  - suivi de la partie en cours ;
  - filtre des statistiques.
- **Rendu vérifié dans un navigateur** (dashboard local, bot non démarré) :
  - sélecteurs présents ;
  - passage en quotidien : nombre de parties figé à 1, classé désactivé ;
  - statistiques quotidiennes vides, 155 parties en infini ;
  - aucune erreur console ;
  - en-tête qui passe à la ligne sur une fenêtre étroite.
