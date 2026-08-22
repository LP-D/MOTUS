# Notes d'exploration du site Tuzmo

## -1. Diagnostic : partie "auto-lancée" observée sans commande explicite (21/08/2026)

**Symptôme observé deux fois pendant cette session** : `POST /api/start` répondait
`{"started": false, ...}` car une partie était déjà en cours (`status: "running"`)
alors qu'aucun clic ni appel `/api/start` n'avait été fait dans le tour de
conversation en cours.

**Audit du code** (`dashboard/bot_runner.py`, `dashboard/static/index.html`) :
`BotRunner.start()` est le **seul** point d'entrée qui peut faire passer le statut à
`"running"` et démarrer le thread de jeu — recherché exhaustivement, aucun autre
appel à `threading.Thread(...)`, aucun `setInterval`/callback JS n'appelant
`/api/start` automatiquement. Concrètement :
- Chargement de la page (`connectWebSocket(); refreshStatus(); ...` en bas de
  `index.html`) : uniquement des `GET` (`/api/status`, `/bot/config/typing_delay`,
  `/bot/stats`, `/bot/stats/solutions`) — aucun `POST /api/start`.
- Reconnexion WebSocket (`connectWebSocket`) : relit les événements poussés par le
  runner, ne déclenche jamais rien côté serveur.
- `setInterval(refreshStatus, 5000)` : lit `/api/status` (GET), ne peut pas
  déclencher un démarrage.

**Hypothèse retenue** (cohérente avec les deux occurrences, toutes deux survenues
en plein milieu de tests interactifs répétés dans la même session, sur le même
processus dashboard laissé tourner en tâche de fond) : `BotRunner` est un
**singleton de durée de vie du processus** (`runner = BotRunner()` en bas de
`bot_runner.py`) — un appel `/api/start` précédent (fait manuellement pendant les
tests, ex. via curl/PowerShell ou un clic antérieur) qui n'avait pas encore atteint
`status: "idle"`/`"stopped"` au moment de la vérification suivante apparaît comme
"déjà en cours" sans que la commande qui l'a lancé soit visible dans le tour de
conversation courant. **Ce n'est pas un déclenchement spontané côté bot ni côté
serveur Tuzmo** — juste un état résiduel d'un appel explicite antérieur, non
encore terminé/arrêté, sur un processus long-lived.

**Correctifs apportés** (défensifs, pas une correction de bug puisqu'aucun bug de
démarrage implicite n'a été trouvé) :
1. `BotRunner.start()` journalise désormais un événement `start_requested` à
   **chaque** appel (accepté ou refusé), avec `status_before`,
   `current_iteration`/`total_iterations` avant décision, et `requested_iterations`
   — persisté dans `data/dashboard_bot_log.jsonl` (déjà exposé par
   `GET /api/logs`), pour reconstituer après coup la chronologie exacte si
   l'anomalie devait se reproduire. Voir `tests/test_bot_runner_lifecycle.py`
   (régression : aucun démarrage implicite à l'instanciation, chaque tentative
   journalisée avec l'état antérieur correct, une tentative refusée ne perturbe
   pas la boucle en cours).
2. Le frontend (`index.html`, clic sur "Démarrer") reflète maintenant le résultat
   réel de `/api/start` (`data.started`) au lieu de supposer inconditionnellement
   `setStatus('running')` — un refus affiche désormais "déjà en cours" plutôt que
   de masquer silencieusement la même situation qu'un démarrage réussi.

**Recommandation pour les tests futurs** : toujours appeler `/api/stop` et
attendre `status: "idle"`/`"stopped"` avant de terminer une session de tests
manuels sur le dashboard, puisque le runner ne s'auto-arrête ni ne s'auto-expire.

Exploration réseau/DOM approfondie, au-delà de ce qui était déjà documenté (endpoint
de validation `/guess`, structure `/infinite`). **Documentation uniquement** — rien
n'est implémenté ici, conformément à la tâche. Le mode duel classé n'a jamais été
réellement rejoint (voir section dédiée) : aucun joueur réel n'a été affecté par
cette exploration.

## 0. Convention couleur "bien placé"/"mal placé" (vérification bloquante, 21/08/2026)

**Confirmé empiriquement** (partie `/infinite` réelle, mot `COMMENCER`,
`getComputedStyle()` sur les cases révélées) : Tusmo affiche le **"bien placé" en
rouge** (`cell__mark--correct` → `rgb(209, 72, 72)`) et le **"mal placé" en
jaune/or** (`cell__mark--present` → `rgb(229, 185, 83)`) — **pas** la convention
Wordle standard (vert = bien placé).

**Aucun impact sur le code** : `bot/parser.py::cell_state()` ne lit jamais de
couleur, uniquement le nom de classe CSS (`cell__mark--correct`/`--present`), qui
correspond mot pour mot aux valeurs littérales `"correct"`/`"present"`/`"absent"`
renvoyées par l'API serveur elle-même (`POST /api/game/{id}/guess`). La convention
rouge/jaune est un choix de style visuel de Tuzmo, découplé du nom de classe
sémantique sur lequel repose le parsing. Figé par
`tests/test_color_convention.py` (comparaison DOM observé ↔ réponse API réelle
pour la même soumission).

## 1. Endpoints API observés

### Solo (`/infinite`, déjà connu + complété)

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/api/me` | GET | Identité de session (`{id, isGuest, tier, shopEnabled, ownedEmotes, coupons}`) |
| `/api/game` | POST | Crée une session de partie (`{lang, mode}` → session + 1er mot) |
| `/api/game/{id}/guess` | POST | Soumet un coup (`{guess}`) → feedback ou `{error}` |

**Nouveau** : la réponse de `/guess` sur le coup **gagnant** contient un bloc
`finished` non documenté jusqu'ici :

```json
"finished": {
  "won": true, "tries": 4, "score": 1,
  "word": {"firstLetter": "R", "wordLen": 5},
  "durationMs": 103810
},
"unlocked": ["first_win"]
```

- `durationMs` : le **serveur mesure lui-même la durée de la partie**, pas
  seulement le client. Pertinent pour le duel classé : le timer de 45s après la
  victoire adverse est probablement basé sur une mesure serveur similaire, pas
  purement côté client (donc pas manipulable en trafiquant l'horloge locale).
- `unlocked` : système de succès/déblocages (`first_win` observé).
- En mode `/infinite`, gagner **enchaîne automatiquement sur le mot suivant** dans
  la même session (`wordIndex` s'incrémente, `guesses: []` repart à vide,
  `firstLetter`/`wordLength` changent) — pas besoin de recréer une session à
  chaque mot pour jouer plusieurs mots d'affilée, seulement en cas d'échec ou de
  script qui préfère une session fraîche par mot (ce que fait le code actuel).
- Le mot cible réel n'est **jamais renvoyé en clair**, y compris en cas d'échec ou
  de victoire (`word` ne donne que `firstLetter`/`wordLen`).

### Duel classé (`/ranked`) — nouveaux endpoints, page chargée sans rejoindre de partie

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/api/rank/fr` | GET | Ligue/division/LP du joueur, matchs de placement, historique |
| `/api/duels/fr` | GET | Historique récent des duels (`{"rows": [...]}`, vide pour un invité) |
| `socket.io/...` | GET/POST (polling → upgrade websocket) | Canal temps réel (voir §3) |

Réponse `/api/rank/fr` observée (compte invité) :
```json
{"league": "bronze", "division": 3, "lp": 0, "placement": {"games": 0, "total": 5}, "history": []}
```
→ Système de ligues façon jeu compétitif (bronze/argent/or...), 5 matchs de
placement requis avant classement définitif. La page affiche aussi un compteur
live "N en recherche · M en duel" (alimenté par le canal temps réel, §3).

## 2. Stabilité des sélecteurs DOM

Le jeu utilise **Tailwind CSS + classes BEM personnalisées pour les éléments de
jeu** (`board`, `board--stage`, `board-row`, `cell`, `cell--absent`,
`cell--revealed`, `cell--dot`, `cell__mark`, `cell__mark--present`,
`cell__mark--correct`, `keyboard`) — **pas de CSS-in-JS à hash aléatoire**
(type `styled-components`/`sc-xxxxx` ou CSS Modules `_hash_`). C'est le signe d'un
choix délibéré de nommage stable plutôt qu'un artefact de build qui changerait à
chaque déploiement.

**Mais** : aucun attribut `data-testid` ni équivalent dédié à l'automatisation/aux
tests n'a été trouvé nulle part sur le site. Les sélecteurs actuels du bot
(`bot/tuzmo_client.py`) sont donc **raisonnablement stables face à un simple
redéploiement** (les classes ne sont pas régénérées à chaque build), mais restent
**fragiles face à une refonte visuelle volontaire** (renommage de classes lors d'un
changement de design — déjà arrivé une fois avant cette session, cf. découverte de
`cell--dot`/`anim-shake` non prévus par le code initial). Aucun filet de sécurité
structurel contre ça au-delà de la vérification manuelle périodique déjà pratiquée.

Autre point relevé : une transition anime le changement de mot en `/infinite`
(classes `word-swap-enter-from`/`word-swap-leave-active` observées sur `.board`
juste après une victoire) — source potentielle de flakiness si un futur script
lisait le plateau immédiatement après un enchaînement de mot sans attendre la fin
de cette transition (le code actuel recrée une session à chaque mot, donc n'est pas
concerné aujourd'hui).

## 3. Canal temps réel (WebSocket / Socket.IO)

- **Aucun** WebSocket n'est ouvert sur `/infinite` (solo) — confirmé via
  `performance.getEntriesByType('resource')` (0 connexion `ws`) en plus de
  l'inspection réseau. Le solo est purement HTTP request/response.
- **`/ranked` utilise Socket.IO** (bibliothèque `engine.io`/`socket.io-client`
  confirmée dans le bundle JS). Handshake observé :
  ```json
  {"sid": "...", "upgrades": ["websocket"], "pingInterval": 25000, "pingTimeout": 20000, "maxPayload": 1000000}
  ```
  Démarre en polling HTTP puis **upgrade vers un vrai WebSocket** (`upgrades:
  ["websocket"]`). Utilisé au minimum pour le compteur live "N en recherche · M en
  duel" affiché sur la page ; très probablement aussi pour la recherche
  d'adversaire, le début de match, et la synchronisation d'état/timer pendant un
  duel (non vérifié — voir limite ci-dessous).
- **Non vérifié délibérément** : le contenu exact des messages Socket.IO pendant
  une recherche/un duel réel (quels événements, quelle structure), car cela
  nécessiterait de cliquer "Trouver un adversaire" et donc de **rejoindre une
  vraie file de matchmaking avec un joueur réel** — non fait dans cette
  exploration (cf. contrainte de la tâche, aucune automatisation duel à ce stade).

## 4. Détection anti-bot observable

- **Aucun service de bot-detection tiers détecté** : pas d'en-têtes Cloudflare
  (`cf-ray`, `cf-cache-status`), pas de signature DataDome/PerimeterX/Akamai Bot
  Manager dans les réponses. Hébergement Heroku standard (`Server: Heroku`,
  en-têtes NEL/Report-To = reporting d'erreurs réseau standard, pas de
  l'anti-bot).
- **Aucune bibliothèque de fingerprinting** trouvée dans le bundle JS principal
  (recherche de signatures FingerprintJS, ClientJS, reCAPTCHA/hCaptcha/Turnstile,
  DataDome, PerimeterX, Akamai — aucune correspondance sur 664 Ko de bundle).
- **Cohérent avec le test de charge précédent** (1600 requêtes à 1.5-2.5s,
  data/dictionary_extraction_stress_report.json) : zéro code 429/403, zéro header
  `X-RateLimit-*`/`Retry-After`, latence stable — pas de throttling détecté à ce
  volume/débit.

**Limite importante** : tout ce qui précède couvre un usage **ponctuel et court**
(quelques centaines à ~1600 requêtes sur une session de quelques dizaines de
minutes). Aucune donnée sur :
- un usage **prolongé sur plusieurs jours/semaines** (détection comportementale
  cumulative, pas seulement du rate-limiting instantané) ;
- un risque **spécifique au compte** en duel classé (contrairement à `/infinite`,
  anonyme, le duel classé nécessite probablement un compte identifiable — un
  pattern de jeu non-humain récurrent y serait plus facilement corrélé à un
  historique de compte qu'à une IP isolée) ;
- une éventuelle détection **côté client** (timing des clics, mouvements de
  souris) qui ne laisserait aucune trace réseau observable depuis l'extérieur.

**Conclusion révisée** : le risque de bannissement lié au seul rate-limiting reste
faible (déjà testé). Le risque lié à un **usage prolongé et/ou en duel classé sur
un compte identifié** n'est pas couvert par les tests actuels et reste une
inconnue — à traiter avec prudence si l'automatisation du duel est un jour
envisagée.

## Pistes concrètes pour de futures tâches (non codées ici)

1. **Timer serveur pour le duel** : le champ `durationMs` de `finished` en solo
   suggère un mécanisme de mesure serveur réutilisable en duel — à vérifier avant
   de bâtir une logique de timing côté bot pour le mode duel (éviter de recoder un
   chrono client qui diverge du serveur).
2. **Écoute passive du canal Socket.IO en duel** (sans automatiser la soumission)
   pour documenter la structure exacte des messages temps réel (recherche
   d'adversaire, début de match, état de l'adversaire) — nécessiterait de
   rejoindre un vrai match, donc à faire consciemment et avec l'accord explicite
   de l'utilisateur (implique un joueur réel en face).
3. **`/api/duels/fr` et `/api/rank/fr`** pourraient permettre de suivre
   automatiquement la progression en ligue/LP sans scraper le DOM — utile si un
   futur dashboard veut afficher le classement.
4. **Robustesse des sélecteurs** : envisager une vérification automatique
   périodique (ex. au démarrage du bot, vérifier que les classes CSS attendues
   existent toujours dans le DOM avant de lancer une partie) plutôt qu'une
   découverte au moment de l'échec, étant donné qu'un changement de nommage s'est
   déjà produit une fois.
