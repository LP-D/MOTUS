# Phase 5 : validation finale par mode (25/09/2026)

Conditions communes :
- stratégie entropy_pure (défaut) avec départage aux coups 2 et suivants ;
- un invité par lancement ;
- 1,5 à 2,5 s entre requêtes ;
- arrêt d'urgence à 5 s.

**Aucun signal de limitation** : aucun 429, aucun `Retry-After`, latence maximale
0,10 s en validation.

## Statut par mode

| Mode | Statut | Base |
|---|---|---|
| **Infini** | **validé** | run de 10 mots, plus 21 parties jouées depuis le dashboard |
| **Quotidien** | **validé** | 1 partie, détection de la limite côté serveur et garde-fou local |
| **Classé** | **non automatisé, par choix** | duels contre de vrais joueurs (voir `2026-09-25_phase1_modes.md`) |

## Quotidien

Données : `data/improvement_runs/modes_validation/daily/`.

- **Partie** : mot du jour **RECTEUR** (R, 7 lettres), trouvé en **3 essais**, 7,4 s, 0 rejet,
  latence maximale 0,06 s.
- **Rechargement dans le même contexte** (même invité) :
  - le serveur renvoie la session terminée (`status: "won"`, `mode: "daily"`) ;
  - le bot s'arrête sur `daily_limit_reached`, sans envoyer aucun coup, sans erreur et
    sans compter de partie.
- **Garde-fou local**, via l'API du dashboard, sans requête vers Tuzmo : un second
  lancement quotidien le même jour est refusé (`started: false`, statut
  `daily_limit`, message « prochaine partie demain »).
- **Statistiques** : la partie apparaît dans le filtre « quotidien » (1 partie),
  séparée des 176 parties « infini ».

## Infini

**Run de 10 mots** (`data/improvement_runs/modes_validation/infinite/`, même format que
les cycles précédents) :

| Métrique | Validation | Phase 2 entropy_pure | Phase 2 composite |
|---|---|---|---|
| Trouvés | 9/10 (CHIMISTES hors corpus, révélé et ajouté) | 19/20 | 29/30 |
| Temps moyen par mot | **6,93 s** | 7,87 s | 7,81 s |
| Essais moyens (mots trouvés) | 2,67 | 3,00 | 2,86 |
| Rejets par mot | 0,30 (3, tous aux coups dynamiques) | 0,40 | 0,50 |
| Rejets au coup 1 | 0 | 0 | 0 |
| Erreurs | 0 | 0 | 0 |

**21 parties lancées depuis le dashboard** (tes lancements de 11:22 et 11:23) :
- 19 trouvées, en 3,0 essais en moyenne ;
- 2 hors corpus (CARACUL, HEBREUX), révélées et ajoutées ;
- 6 rejets, 0 erreur, 0 signal de limitation.

Elles ont validé en direct :
- le plateau miroir ;
- le panneau de latence ;
- les événements ;
- la ligne « mode actif / partie en cours » ;
- les statistiques filtrées par mode.

Ces 10 à 31 mots ne suffisent pas à isoler l'effet du départage sur les rejets : les
mots tirés diffèrent d'un run à l'autre. L'estimation hors ligne reste la référence :
effet faible (voir `2026-09-25_phase0_defaut_departage_validation.md`).

## Boucles lancées depuis le dashboard après la validation (11:41 à 15:09)

Ces lancements ont été faits depuis le dashboard, et non par les scripts de
validation. Ils ne recouvrent pas les runs de validation, terminés à 11:29.
Données : `data/dashboard_stats.json`, `data/dashboard_bot_log.jsonl`,
`data/group_draws.jsonl`.

| Lancement | Stratégie | Parties | Trouvées | Essais moyens | Résolus en ≤ 2 essais |
|---|---|---|---|---|---|
| 11:41, 1 000 demandées | entropy_pure | 97, puis **arrêt d'urgence** | 95 (97,9 %) | 2,91 | 29,9 % |
| 11:57, 10 demandées | entropy_pure | 10 | 10 | 2,60 | 50,0 % |
| 12:06, 100 demandées | entropy_pure | 100 | 98 (98,0 %) | 2,88 | 27,0 % |
| 12:24, 1 000 demandées | composite | 1 000 | 962 (96,2 %) | 3,01 | 22,8 % |

**Arrêt d'urgence à 11:57:10**, après 98 parties : une requête est restée sans
réponse en 5 s.
- Le garde-fou a fonctionné : statut `throttled`, boucle arrêtée, aucune partie
  enchaînée.
- La boucle suivante a été relancée 32 s plus tard depuis le dashboard, sans la pause
  ni le contrôle de latence appliqués après les arrêts précédents.
- Les 1 110 parties suivantes n'ont montré aucun autre signal de limitation.

**Comparaison des stratégies en jeu réel.** Elle n'est pas appariée (les mots tirés
diffèrent) mais va dans le même sens que le rejeu hors ligne :
- entropy_pure : 207 parties, 2,89 essais, 98,1 % trouvées ;
- composite : 1 000 parties, 3,01 essais, 96,2 % trouvées ;
- les échecs composite se répartissent en 34 mots hors corpus et 4 parties perdues
  après 6 essais.

**Effets de bord de ces parties** :
- **Groupes tirés pour la 1re fois** : K5, K6, K8, U8 et Q8. Il reste **25 groupes
  non observés** (au lieu de 30) : I5, K7, K9, Q5, U7, et toutes les longueurs de W,
  X, Y et Z. Les caches sont ré-annotés (`scripts/group_draws.py --annotate`).
- **38 solutions révélées**, toutes absentes du corpus, puis ajoutées.
- **474 mots refusés** (INVALID_WORD confirmé serveur, environ 0,4 par partie) : liste
  noire de 977 à 1 451 mots, provenance tracée.
  - Parmi eux, 10 coups 1 composite et 6 entropy_pure des groupes nouvellement tirés
    (K5, K6, K8, Q8) : leurs candidats n'avaient jamais pu être validés. Au premier
    tirage, ces groupes ont donc subi des rejets au coup 1, comme prévu pour un groupe
    non observé.
  - Entrées recalculées localement (`--end-round`) : aucun mot refusé ne reste dans les
    caches.

**Défaut corrigé** grâce à ces parties : en quotidien, le bot signalait « groupe
jamais observé » (R7 au mot du jour). Or ce statut ne concerne que /infinite. Le
signalement est désormais réservé à /infinite, avec un test qui échoue sans le
correctif.

## Limitations connues

- **Mode classé non automatisé**, sans exploration Socket.IO en file réelle ni vue
  duel dans le dashboard. C'est un choix : les adversaires sont de vrais joueurs.
- **Corpus** : 38 solutions sur 1 207 parties du dashboard manquaient au corpus,
  soit **environ 3 %**. S'y ajoutent CHIMISTES, CARACUL et HEBREUX en validation, et
  26 sur 650 lors de la validation des coups 1.
  - Ce sont surtout des pluriels ou formes fléchies absents alors que le singulier y
    est.
  - Chaque solution révélée est ajoutée automatiquement au corpus.
- **Rejets aux coups 2 et suivants** : ce sont des non-mots du corpus proposés
  dynamiquement, de l'ordre de 0,3 à 0,5 par mot. La validation des coups 1 ne les
  couvre pas, et le départage ne les réduit qu'à la marge.
- **Groupes jamais observés** : 25 groupes (/infinite) restent au statut incertain.
  S'ils sortent, leur coup 1 n'est pas validé (environ 31 % de rejet attendu).
- **Quotidien** : une seule partie par jour, par construction. La validation porte
  sur un seul mot du jour ; la page d'une partie du jour déjà terminée a été traitée
  par la session (API), sans dépendre de son affichage.
- **Usage prolongé** : aucun test sur plusieurs jours ou semaines (détection
  comportementale), comme déjà noté dans `docs/tuzmo_site_notes.md` §4.
