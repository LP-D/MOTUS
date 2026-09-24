# Arrêt d'urgence pendant la validation ciblée (24/09/2026, 20:59)

## Faits

- **Contexte** : phase 1, tour 1 de `scripts/validate_root_candidates.py`, un seul
  invité, 1,5 à 2,5 s entre requêtes.
- **Déclencheur** : 1 398e requête, `POST /api/game/fe37982d-…/giveup` (clôture
  d'une partie sans rien à tester). **Aucune réponse en 5 s**, le délai d'arrêt
  d'urgence abaissé ce jour de 10 s à 5 s.
- **Réponse** : arrêt immédiat, aucune requête ensuite.
- **Avant le signal** :

| Mesure | Valeur |
|---|---|
| Requêtes répondues | 1 397, toutes HTTP 200 (20:11:30 à 20:59:29) |
| Latence | médiane 0,051 s, p95 0,065 s, max 0,355 s |
| 200 dernières requêtes | médiane 0,051 s, max 0,083 s, aucune dérive |
| En-têtes de limitation (`Retry-After`, `X-RateLimit-*`) | aucun |
| HTTP 429 | aucun |

## Lecture

Aucun signe précurseur : pas de hausse de latence, pas de 429, pas d'en-tête de
limitation. C'est le même profil que l'arrêt du run 6 (23/09) : un appel isolé sans
réponse au milieu d'un trafic normal. Une coupure réseau ponctuelle reste plausible,
une limitation côté serveur aussi. Impossible de trancher sans relancer des
requêtes, ce qui n'est pas fait.

## Conséquences

- **Phase 1 arrêtée** : pas de second tour de validation. Le mot en cours n'a pas été
  jugé et sera retesté plus tard ; aucune donnée n'est perdue.
- La partie `fe37982d` est peut-être restée ouverte sur l'invité de ce tour. Sans
  conséquence : chaque exécution crée son propre invité.
- **Mise à jour locale des caches** (fin de tour, aucune requête), qui a aussi servi
  de pause.
- **Avant la phase 2** : contrôle de latence sur un seul mot, hors compteur, comme
  après l'arrêt du run 6. La phase 2 ne démarre que sur un résultat GO.
