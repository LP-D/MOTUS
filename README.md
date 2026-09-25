# motus-solver

Solveur Motus assisté par entropie (numpy), un bot Tuzmo (Playwright), et un
dashboard web local pour piloter/observer le bot en temps réel.

## Structure du projet

- **`src/motus_solver/`** — le solveur, indépendant de toute UI :
  - `corpus.py` — chargement/normalisation du dictionnaire (`data/corpus_fr.txt`).
  - `feedback.py` — codage vectorisé numpy des retours Motus (0/1/2 par position).
  - `scoring.py` — composantes du score composite (entropie, voyelles, lettres
    distinctes, fréquence) et leurs poids (0.40/0.25/0.20/0.15 — non modifiables
    sans revalider tous les tests de non-régression).
  - `tree.py` — `best_guess_composite` (calcul du meilleur coup, vectorisé) et
    l'arbre de décision adaptatif profondeur 4.
  - `endgame.py` — fin de partie par mot sonde : quand le coup habituel risque de
    faire perdre (ex. PA?ES, 8 candidats pour 4 essais) ou teste les lettres une par
    une, joue un mot (candidat ou non) qui teste plusieurs lettres possibles d'un coup.
  - `cache.py` — précalcul (parallélisable) du coup 1 par couple (lettre, longueur).
  - `solver.py` — `Solver`, la classe utilisée par la CLI, le bot et le dashboard.
  - `cli.py` — interface en ligne de commande interactive.
  - `duel.py` / `agents.py` — simulateur local du duel classé (règles du site,
    modèles de bot, profils de vitesse). Aucune requête vers Tuzmo.
- **`bot/`** — intégration Tuzmo (Playwright) :
  - `tuzmo_client.py` — lecture/écriture du DOM du jeu (plateau, clavier, feedback,
    détection des mots rejetés par le dictionnaire de validation du jeu).
  - `timing.py` — instrumentation (timestamps par étape d'un coup, log JSON).
  - `run_bot.py` — résout le mot du jour (`/daily`, une partie par jour).
  - `save_auth_state.py` — sauvegarde de session (voir Authentification ci-dessous).
- **`dashboard/`** — dashboard web local (FastAPI + page unique vanilla JS) :
  - `backend.py` — endpoints start/stop/status/logs + WebSocket temps réel.
  - `bot_runner.py` — pilote une partie du bot dans un thread, pousse les
    événements (coup proposé, rejeté, feedback reçu, latence par étape) au backend.
  - `static/index.html` — page du dashboard (plateau miroir, contrôles, latence).
- **`scripts/`** — outils autonomes (aucun ne dépend de la CLI ni du bot) :
  build/nettoyage de corpus, comparaison de sources, précalcul du cache,
  simulation/benchmark de résolution, benchmark de latence bot, analyse de
  faisabilité duel, interrogation directe du dictionnaire de validation Tuzmo,
  lancement du dashboard.
- **`data/`** — corpus, cache, logs, rapports générés (pas de secrets committables
  à l'exception de `tuzmo_auth_state.json`, à ne jamais versionner — cf. `.gitignore`).
- **`docs/`** — notes de validation (ex. vérification manuelle d'entrées du corpus).
- **`tests/`** — suite pytest (module solveur + logique de retry du bot).

Architecture à 3 modules découplés (solveur / CLI / bot) : le dashboard est une
4e couche, au-dessus du bot, qui ne modifie ni le solveur ni sa logique de jeu.

## Installation (première fois)

```bash
pip install -e .
playwright install chromium
```

Le solveur seul (`motus-solve`, `pytest`) ne nécessite que la première commande.
Chromium n'est requis que pour le bot et le dashboard (Playwright pilote un
navigateur réel).

## Authentification Tuzmo

Le mode `/infinite` (utilisé par le bot pour jouer/mesurer sans dépendre du mot du
jour) est jouable anonymement — aucune connexion requise. Un compte devient
nécessaire pour les fonctionnalités liées au compte (stats, éventuel futur mode
duel classé — non automatisé à ce stade). Pour persister une session connectée :

```bash
python bot/save_auth_state.py
```

Ouvre un navigateur visible, laisse le temps de se connecter manuellement (bouton
"Compte"), puis appuie sur Entrée dans le terminal pour sauvegarder la session dans
`data/tuzmo_auth_state.json`. Le bot et le dashboard la chargent automatiquement si
le fichier existe (sinon ils tournent en anonyme). À relancer si la session expire
ou après une déconnexion volontaire.

## Lancer le bot seul (CLI)

Résout le mot du jour (une seule partie possible par jour sur `/daily`) :

```bash
python bot/run_bot.py
```

Affiche chaque coup proposé, gère automatiquement les mots rejetés par le
dictionnaire de validation du jeu (recalcul dynamique sur le sous-corpus amputé,
jusqu'à 20 tentatives par coup avant abandon).

Pour résoudre un mot manuellement (sans navigateur, juste le solveur) :

```bash
motus-solve --letter S --length 6
```

## Lancer le dashboard

```bash
python scripts/run_dashboard.py
```

Démarre le backend local sur **http://127.0.0.1:8765** et ouvre cette URL dans le
navigateur par défaut. Local uniquement (pas d'exposition réseau, pas
d'authentification dashboard — mono-utilisateur). Le navigateur du dashboard (onglet
normal) et celui piloté par Playwright pour jouer sont deux instances Chromium
indépendantes : aucun conflit.

Dans le dashboard :
- **Mode de jeu** : infini (par défaut) ou quotidien (une partie par jour ; un
  second lancement le même jour est refusé proprement). Le mode classé n'est pas
  automatisé : ses duels opposent de vrais joueurs (voir `bot/modes.py`).
- **Stratégie** : entropy_pure (par défaut) ou composite.
- **Suivi en direct** : boutons Démarrer / Arrêter, plateau miroir mis à jour en
  temps réel (WebSocket), latence par étape du dernier coup, mode actif et état de
  la partie en cours.
- **Événements** : une phrase par étape (coup proposé, retour, mot refusé, mot sonde
  de fin de partie, solution révélée et ajoutée au dictionnaire...). Le détail brut
  de chaque événement s'affiche au survol.
- **Mot introuvable** : si la solution n'est pas dans le dictionnaire, le bot
  abandonne (`giveup`) et Tuzmo révèle la solution, ajoutée au dictionnaire. Si
  l'abandon échoue, il grille les essais restants : la réponse au 6e essai raté
  contient la solution. Après une défaite normale (6 essais), la solution renvoyée
  par le serveur est aussi enregistrée.
- **Statistiques et solutions filtrées par mode** : infini, quotidien, ou tous modes
  sur demande explicite.

Lancer le bot directement dans un mode, sans passer par le bouton :

```bash
python scripts/run_dashboard.py --mode infinite --games 10 --autostart
python scripts/run_dashboard.py --mode daily --autostart
python scripts/run_dashboard.py --port 8766   # second dashboard, autre port
```

## Duel classé simulé (local)

Le vrai mode classé n'est pas automatisé : les CGU de Tuzmo interdisent
l'automatisation (voir `docs/tuzmo_site_notes.md` §4). Le simulateur reproduit ses
règles en local, sans aucune requête vers le site. Règles relevées dans le code
public du site :
- même mot pour les deux joueurs, 6 essais chacun ;
- le premier qui trouve lance un chrono de 2 minutes ;
- l'autre doit trouver en strictement moins d'essais ;
- si personne ne trouve, c'est une égalité ;
- chacun voit les couleurs adverses, pas les lettres.

Les mots sont tirés parmi les solutions réelles connues.

- **Jouer contre un bot** : page **http://127.0.0.1:8765/duel**, lien « duel simulé »
  du dashboard. Choisis le modèle et sa vitesse (instantané, rapide, humain, lent).
  Ton bilan contre chaque bot est gardé dans `data/duel_history.jsonl`.
- **Compétition entre modèles** : `scripts/duel_tournament.py`. Chaque paire joue les
  mêmes mots ; le script sort un classement Elo, les victoires, les essais et le temps.
  Résultats dans `data/duel_runs/`.

```bash
python scripts/duel_tournament.py --duels 1000 --speed rapide
python scripts/duel_tournament.py --agents entropy_pure@instantane,entropy_pure_chasse@humain --duels 500
```

Modèles disponibles :
- `entropy_pure` et ses variantes : `_chasse` (riposte), `_sondes`, `_sans_fin`,
  `_pression`, `_tempo`, `_infos` (lit les couleurs adverses, apprend les ouvertures) ;
- `composite` ;
- `aleatoire`.

Résultats : `docs/diagnostics/2026-09-25_phase8_duel_pistes.md`. Pour ajouter un
modèle : une classe `Agent` et une entrée dans `AGENTS` (`src/motus_solver/agents.py`).

## Tests

```bash
pytest
```

`tests/test_tree_vectorized.py` inclut une comparaison contre le groupe le plus
dense du corpus (~21 000 mots) : lent (~5 min), exclu du run rapide au besoin
(`pytest --ignore=tests/test_tree_vectorized.py`).

## Dictionnaire de validation Tuzmo

Le corpus du solveur (`data/corpus_fr.txt`, ~241 000 mots) et le dictionnaire réel
utilisé par Tuzmo pour valider un coup sont **deux choses distinctes** — un mot
peut être un bon coup selon le scoring composite sans être reconnu par le jeu
("Mot inconnu"), typiquement des conjugaisons ou formes rares issues du Wiktionnaire.

Investigation (inspection réseau de `/infinite`) : la validation est **côté
serveur**, pas un dictionnaire embarqué côté client. Chaque coup déclenche :

```
POST https://www.tusmo.xyz/api/game/{session_id}/guess
Content-Type: application/json
{"guess": "<MOT>"}

-> 200 {"session": {...}, "result": [...]}       si le mot est valide
-> 200 {"session": {...}, "error": "INVALID_WORD"} sinon
```

(session créée via `POST /api/game`, cookie `tusmo_token` HttpOnly, aucune
authentification par compte requise pour `/infinite`).

**Extraction complète abandonnée** : un test de montée en charge de l'endpoint
(1600 requêtes, paliers 100/500/1000, aucun signal de rate-limiting — cf.
`data/dictionary_extraction_stress_report.json`) a validé un débit sûr (1.5-2.5s
entre requêtes), mais l'extraction complète du corpus (~241k mots, ~290k requêtes)
prendrait ~161h en continu — trop long pour être une stratégie viable
(`scripts/query_tuzmo_dictionary.py`/`scripts/stress_test_dictionary_endpoint.py`
restent disponibles si un jour pertinents, mais ne sont plus le chemin retenu).

**Approche retenue : liste noire persistante, alimentée par le jeu réel.** Le bot
et le dashboard gèrent le décalage corpus/dictionnaire à l'exécution : un mot
rejeté ("Mot inconnu") est (1) retiré du sous-corpus de la partie en cours — le
solveur recalcule dynamiquement une nouvelle proposition (cf.
`bot/tuzmo_client.py::WordRejectedError`, `tests/test_benchmark_retry.py`) — et (2)
ajouté définitivement à `data/known_invalid_words.json`
(`motus_solver.blocklist`), jamais reproposé lors d'une partie future, quel que
soit l'entry point (CLI, bot, dashboard, benchmark) — `Solver(..., blocklist=...)`
filtre les candidats dès la construction, et `build_root_cache(..., blocklist=...)`
permet de régénérer un cache racine qui ne recommande plus ces mots. La liste
noire grandit organiquement au fil du jeu, sans opération de masse.

**Seul un rejet confirmé par le serveur alimente la liste noire.** Depuis le
23/09/2026, le client lit la réponse de `POST /api/game/{id}/guess` et vérifie le mot
réellement envoyé. Seul un `INVALID_WORD` portant sur ce mot exact compte comme rejet.
Avant ce correctif, une ligne non vidée après un vrai rejet faisait renvoyer l'ancien
mot : chaque nouveau mot était alors faussement rejeté, en cascade. Détail et preuve :
`docs/diagnostics/2026-09-23_cascade_rejets.md`. Les entrées douteuses sont en
quarantaine dans `data/known_invalid_words_quarantine.json`. Les entrées du cache
racine devenues invalides se recalculent avec
`python scripts/build_root_cache.py --refresh-blocklisted`.

## Origine de l'algorithme

`feedback.py` (pattern_codes, entropy_from_codes) est adapté de `encoder_retours()`
/ du calcul d'entropie de `MOTUS_OPTIMISEUR_KAGGLEHUB.ipynb` (même logique
vectorisée numpy). `tree.py` (build_tree, best_guess_composite) et la formule de
`composite_score` dans `scoring.py` ont été écrits à partir des poids (0.40
entropie / 0.25 voyelles / 0.20 lettres distinctes / 0.15 fréquence) et de la
description de l'arbre récursif fournis dans la spécification initiale.
