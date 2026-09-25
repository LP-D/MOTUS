# Phase 1 : abstraction multi-modes (25/09/2026)

## Portée

- **Modes automatisés** : **quotidien** (`/daily`) et **infini** (`/infinite`).
- **Mode classé** (`/ranked`) : **non automatisé**. Ses duels opposent de vrais
  joueurs, avec ligues et points de classement.
  - Faire jouer un solveur contre des adversaires qui croient affronter une
    personne revient à tricher en compétition.
  - Les phases 2 et 3 de la demande ne sont donc pas réalisées : exploration du canal
    Socket.IO en file réelle, puis automatisation du classé.
  - Les parties propres au classé des phases 4 et 5 ne le sont pas non plus.
  - L'énumération contient `RANKED`, mais le refus est explicite : exception
    `ModeNotSupportedError`, événement `mode_refused`.

## Conception

`bot/modes.py` :
- **Énumération** : `GameMode` (`DAILY`, `INFINITE`, `RANKED`).
- **Un `ModeHandler` par mode**, qui fixe :
  - la page à ouvrir ;
  - ce que signifie une session déjà terminée au chargement ;
  - le nombre de parties par lancement ;
  - la présence du repli ↻.

`dashboard/bot_runner.py` s'appuie dessus :
- `BotRunner.mode`, avec `/infinite` par défaut (comportement historique inchangé) ;
- `start(iterations, mode=...)` ;
- page ouverte selon le mode ;
- la session est examinée avant de lire le plateau ;
- arrêt propre `daily_limit_reached` (statut `daily_limit`) ;
- en quotidien, pas de clic « Rejouer » ni de repli ↻ ;
- garde-fou local « une partie quotidienne par jour », puisque le bot crée un invité
  neuf à chaque lancement.

**Réutilisé tel quel dans tous les modes** :
- le solveur (entropy_pure par défaut, composite en option) ;
- le repli dynamique sur rejet ;
- les listes de mots valides et refusés ;
- la révélation par `giveup` ;
- la surveillance réseau et l'arrêt d'urgence (429, `Retry-After`, latence > 5 s).

**Traçabilité par mode** :
- **Statistiques** : chaque partie enregistre `mode`. Les parties d'avant ce
  changement comptent comme `infinite`, ce qu'elles étaient toutes.
- **Tirages** : le journal enregistre `mode`, et le statut observé ou incertain
  des groupes ne compte que les tirages `/infinite`, car le mot du jour n'est pas
  tiré de la même façon.
- **Événements** : `game_started` porte le mode.
- **Script de run** : `scripts/run_improvement_cycle.py --mode daily|infinite`. En
  quotidien, il joue la partie puis recharge la page une fois pour vérifier que la
  limite est détectée (`daily_limit_check`).

Exploration du quotidien : `docs/tuzmo_site_notes.md` §1, « Quotidien ».

## Tests

`tests/test_game_modes.py` (13 tests) :
- gestionnaires par mode ;
- classification des sessions chargées ;
- refus du classé, par le gestionnaire comme par le bot ;
- mot du jour déjà joué : arrêt propre sans erreur ni partie comptée, détecté sans
  lire le plateau ;
- une session terminée reste refusée en `/infinite` ;
- mode enregistré dans les stats et dans les tirages ;
- pas de repli ↻ en quotidien ;
- statistiques séparées par mode ;
- garde-fou local du jour.
