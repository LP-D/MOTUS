#!/usr/bin/env python3
"""Validation ciblée, via l'API Tuzmo, des candidats de premier coup du cache racine
(coup 1 + 9 replis, caches composite et entropy_pure) : chaque mot est soit accepté
(-> data/known_valid_words.json), soit refusé INVALID_WORD (-> liste noire). Ce ne
sont que quelques milliers de requêtes, pas l'extraction complète du dictionnaire
(289 606 requêtes, hors scope).

Contraintes serveur prises en compte (constatées le 23/09/2026) :
- un seul invité pour tout le run (GET /api/me, puis un seul cookie) : la création
  d'invités en série déclenche un 429 ;
- 1,5 à 2,5 s entre CHAQUE requête (débit validé par le stress test initial) ;
- arrêt d'urgence sur HTTP 429, Retry-After ou latence > 5 s (max observé 0,49 s
  sur 2 000 requêtes le 24/09/2026) ;
- /infinite garde une partie en cours par invité : l'état est relu dans chaque
  réponse (une victoire enchaîne sur le mot suivant dans la même session), et une
  partie dont le groupe n'a plus rien à tester est close par giveup (sinon elle
  serait resservie à la création suivante) ; la solution révélée est journalisée ;
- le serveur choisit le groupe (lettre, longueur) de chaque partie : la couverture
  des 130 groupes dépend du tirage (problème du collectionneur), d'où un budget.

Classement profond (`--compute-rankings`, calcul local sans requête) : les `--depth`
meilleurs coups 1 de chaque groupe déjà tiré, par stratégie. Pour chaque stratégie,
les 10 premiers mots non refusés doivent être confirmés ; un refus fait entrer le
suivant du classement dans la même partie, sans attendre le recalcul du cache
(~11 min pour 78 groupes). Les deux stratégies sont testées en alternance par rang,
pour que leurs caches soient validés à parts égales.

Chaque mot tiré par le serveur est ajouté au journal des tirages
(`data/group_draws.jsonl`, cf. motus_solver.draws) ; un groupe jamais observé
jusque-là est signalé dans la synthèse.

Journal reprenable : chaque requête est ajoutée à `--log` ; un mot déjà présent
dans known_valid / la liste noire n'est jamais retesté.

Fin de tour (`--end-round`, local) : solutions révélées hors corpus ajoutées au
corpus, puis recalcul, en un seul passage par groupe, des entrées de cache dont un
candidat est refusé ou dont les 10 premiers ne suivent plus le classement ; le même
calcul donne le nouveau classement profond du groupe.

    python scripts/validate_root_candidates.py --compute-rankings --workers 7
    python scripts/validate_root_candidates.py --max-requests 2000
    python scripts/validate_root_candidates.py --end-round --workers 7
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from motus_solver.blocklist import add_to_blocklist, load_blocklist  # noqa: E402
from motus_solver.cache import (  # noqa: E402
    ROOT_ALTERNATIVES,
    cache_key,
    load_cache,
    rank_groups,
    refresh_entries,
    save_cache,
)
from motus_solver.draws import annotate_draw_status, load_draw_counts, record_draw  # noqa: E402

BASE_URL = "https://www.tusmo.xyz"
DATA = ROOT_DIR / "data"
DEFAULT_LOG = DATA / "root_validation" / "validation_log.jsonl"
DEFAULT_RANKINGS = DATA / "root_validation" / "deep_rankings.json"
GAP_S = (1.5, 2.5)
THROTTLE_LATENCY_S = 5.0
MAX_ATTEMPTS = 6
TARGET = ROOT_ALTERNATIVES + 1  # coup 1 + replis du cache racine
DEPTH = 25
# parties d'affilée sans rien à tester avant arrêt : les candidats restants sont
# alors dans des groupes que le serveur ne tire (presque) jamais
IDLE_SESSIONS_STOP = 80
CACHE_FILES = {"composite": "root_cache.json", "entropy_pure": "root_cache_entropy_pure.json"}


class Throttled(RuntimeError):
    pass


def ranked_lists(caches: dict[str, dict], deep: dict[str, dict[str, list[str]]] | None = None
                 ) -> dict[str, dict[str, list[str]]]:
    """Par groupe puis par stratégie : le classement des coups 1, profond s'il a été
    calculé, sinon coup 1 + replis du cache."""
    out: dict[str, dict[str, list[str]]] = {}
    for strategy, cache in caches.items():
        for key, entry in cache.items():
            deep_list = ((deep or {}).get(strategy) or {}).get(key)
            out.setdefault(key, {})[strategy] = deep_list or [e["word"] for e in (entry, *entry.get("alternatives", ()))]
    return out


def needed_words(rankings: dict[str, list[str]], blocklist: set[str], target: int = TARGET) -> list[str]:
    """Les `target` premiers mots non refusés de chaque stratégie, entrelacés par
    rang (1er composite, 1er entropy_pure, 2e composite...) : un refus fait entrer le
    mot suivant du classement."""
    per = [[w for w in ranked if w not in blocklist][:target] for ranked in rankings.values()]
    out: list[str] = []
    for i in range(max(map(len, per), default=0)):
        for words in per:
            if i < len(words) and words[i] not in out:
                out.append(words[i])
    return out


def next_candidate(rankings: dict[str, list[str]], known_valid: set[str], blocklist: set[str],
                   target: int = TARGET) -> str | None:
    return next((w for w in needed_words(rankings, blocklist, target) if w not in known_valid), None)


def load_caches() -> dict[str, dict]:
    return {s: load_cache(DATA / f) for s, f in CACHE_FILES.items()}


def session_state(body: dict) -> dict | None:
    session = body.get("session", body) if isinstance(body, dict) else None
    return session if isinstance(session, dict) and "id" in session else None


def compute_rankings(args) -> dict:
    """Classement profond des groupes déjà tirés et pas encore entièrement confirmés
    (calcul local, aucune requête réseau)."""
    from motus_solver.corpus import Corpus

    known_valid = load_blocklist(DATA / "known_valid_words.json")
    blocklist = load_blocklist(DATA / "known_invalid_words.json")
    lists = ranked_lists(load_caches())
    drawn = load_draw_counts(DATA / "group_draws.jsonl")
    keys = sorted(k for k, r in lists.items() if drawn.get(k) and next_candidate(r, known_valid, blocklist))
    corpus = Corpus.from_file(DATA / "corpus_fr.txt")
    started = time.time()
    rankings = {s: rank_groups(corpus, keys, blocklist, strategy=s, depth=args.depth, workers=args.workers)
                for s in CACHE_FILES}
    out = {"computed_at": time.time(), "seconds": round(time.time() - started, 1), "depth": args.depth,
           "blocklist_size": len(blocklist), "groups": keys, "rankings": rankings}
    Path(args.rankings).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: v for k, v in out.items() if k != "rankings"}


def add_revealed_to_corpus() -> list[str]:
    """Solutions révélées absentes du corpus : ajoutées au fichier du corpus (même
    format que dashboard.bot_runner._record_revealed). Retourne les mots ajoutés."""
    from motus_solver.corpus import Corpus

    corpus_path = DATA / "corpus_fr.txt"
    corpus = Corpus.from_file(corpus_path)
    added = []
    for line in (DATA / "revealed_solutions.jsonl").read_text(encoding="utf-8").splitlines():
        answer = json.loads(line)["answer"].upper()
        if corpus.add_word(answer):
            added.append(answer)
    if added:
        with corpus_path.open("a", encoding="utf-8") as f:
            f.write("".join(w + "\n" for w in added))
    return added


def stale_keys(cache: dict, blocklist: set[str], deep: dict[str, list[str]], target: int = TARGET) -> list[str]:
    """Entrées à recalculer : un candidat refusé, ou des 10 premiers qui ne suivent
    plus le classement profond (liste noire à jour)."""
    keys = []
    for key, entry in cache.items():
        top = [e["word"] for e in (entry, *entry.get("alternatives", ()))]
        window = [w for w in deep.get(key, ()) if w not in blocklist][:target]
        if any(w in blocklist for w in top) or (key in deep and window != top):
            keys.append(key)
    return sorted(keys)


def end_round(args) -> dict:
    """Fin de tour de validation : calcul local, aucune requête réseau."""
    from motus_solver.corpus import Corpus

    added = add_revealed_to_corpus()
    corpus = Corpus.from_file(DATA / "corpus_fr.txt")
    blocklist = load_blocklist(DATA / "known_invalid_words.json")
    counts = load_draw_counts(DATA / "group_draws.jsonl")
    rankings_path = Path(args.rankings)
    stored = json.loads(rankings_path.read_text(encoding="utf-8")) if rankings_path.exists() else {"rankings": {}}
    report = {"added_to_corpus": added}
    started = time.time()
    for strategy, name in CACHE_FILES.items():
        cache = load_cache(DATA / name)
        deep = stored["rankings"].setdefault(strategy, {})
        keys = stale_keys(cache, blocklist, deep)
        deep.update(refresh_entries(cache, corpus, blocklist, keys, workers=args.workers, strategy=strategy,
                                    depth=args.depth))
        report[strategy] = {"refreshed": keys, "uncertain": len(annotate_draw_status(cache, counts))}
        save_cache(cache, DATA / name)
    stored.update({"computed_at": time.time(), "blocklist_size": len(blocklist), "depth": args.depth})
    rankings_path.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
    report["seconds"] = round(time.time() - started, 1)
    return report


class Client:
    def __init__(self, log_path: Path, max_requests: int):
        self.http = requests.Session()
        self.http.headers.update({"Content-Type": "application/json"})
        self.log_path = log_path
        self.max_requests = max_requests
        self.sent = 0
        self.last_t = 0.0
        self.latencies: list[float] = []

    def call(self, method: str, path: str, payload: dict | None = None, kind: str = "") -> tuple[int, dict]:
        if self.sent >= self.max_requests:
            raise StopIteration("budget de requêtes atteint")
        wait = self.last_t + random.uniform(*GAP_S) - time.time()
        if wait > 0:
            time.sleep(wait)
        started = time.time()
        try:
            resp = self.http.request(method, BASE_URL + path, json=payload, timeout=THROTTLE_LATENCY_S)
        except requests.Timeout as exc:
            raise Throttled(f"aucune réponse en {THROTTLE_LATENCY_S:.0f}s sur {method} {path}") from exc
        latency = time.time() - started
        self.last_t = time.time()
        self.sent += 1
        self.latencies.append(latency)
        limits = {k: v for k, v in resp.headers.items() if k.lower().startswith(("retry-after", "x-ratelimit"))}
        try:
            body = resp.json()
        except ValueError:
            body = {}
        entry = {"t": started, "kind": kind, "method": method, "path": path, "status": resp.status_code,
                 "latency_s": round(latency, 4), "rate_limit_headers": limits,
                 "payload": payload, "error": body.get("error") if isinstance(body, dict) else None}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if resp.status_code == 429 or any(k.lower() == "retry-after" for k in limits) or latency > THROTTLE_LATENCY_S:
            raise Throttled(f"HTTP {resp.status_code}, en-têtes {limits}, {latency:.1f}s sur {method} {path}")
        return resp.status_code, body


def record_provenance(words: list[str], path: Path) -> None:
    """Trace l'origine de chaque mot mis en liste noire (même fichier que les
    cycles précédents : mot -> provenance)."""
    if not words:
        return
    provenance = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    day = time.strftime("%d/%m/%Y")
    for word in words:
        provenance.setdefault(word, f"INVALID_WORD vérifié serveur (validate_root_candidates, {day})")
    path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def run(args) -> dict:
    known_valid_path, blocklist_path = DATA / "known_valid_words.json", DATA / "known_invalid_words.json"
    revealed_path, draws_path = DATA / "revealed_solutions.jsonl", DATA / "group_draws.jsonl"
    known_valid, blocklist = load_blocklist(known_valid_path), load_blocklist(blocklist_path)
    # chemin par défaut relu à l'exécution (DATA remplaçable, cf. tests)
    rankings_path = Path(getattr(args, "rankings", None) or DATA / "root_validation" / "deep_rankings.json")
    deep = json.loads(rankings_path.read_text(encoding="utf-8"))["rankings"] if rankings_path.exists() else None
    groups = ranked_lists(load_caches(), deep)
    draw_counts = load_draw_counts(draws_path)
    idle_stop = getattr(args, "idle_sessions_stop", IDLE_SESSIONS_STOP)

    def pending(key: str) -> str | None:
        return next_candidate(groups[key], known_valid, blocklist) if key in groups else None

    initial_pending = {k for k in groups if pending(k)}
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    client = Client(log_path, args.max_requests)
    tested = {"valid": [], "invalid": []}
    sessions = idle = unplayable = 0
    draws: list[str] = []
    first_observations: list[str] = []
    revealed: list[str] = []
    stop_reason = None

    def note_draw(session: dict, continuation: bool) -> str:
        key = cache_key(session["firstLetter"], session["wordLength"])
        first = not draw_counts.get(key)
        record_draw(draws_path, session["firstLetter"], session["wordLength"], "validate_root_candidates",
                    session_id=session.get("id"), continuation=continuation, first_observation=first)
        draw_counts[key] += 1
        draws.append(key)
        if first:
            first_observations.append(key)
        return key

    try:
        client.call("GET", "/api/me", kind="guest")
        while any(pending(k) for k in groups):
            if idle >= idle_stop:
                stop_reason = f"{idle} parties d'affilée sans candidat à tester (groupes restants rarement tirés)"
                break
            status, body = client.call("POST", "/api/game", {"lang": "fr", "mode": "infinite"}, kind="create")
            session = session_state(body)
            if status != 200 or session is None:
                stop_reason = f"création de partie impossible (HTTP {status}, {body})"
                break
            sessions += 1
            if session.get("status") != "playing":
                unplayable += 1
                if unplayable >= 2:  # jamais de boucle de créations inutiles
                    stop_reason = f"parties créées non jouables (status={session.get('status')!r})"
                    break
                continue
            unplayable = 0
            key = note_draw(session, continuation=False)
            tested_here = 0
            # une partie peut enchaîner plusieurs mots (victoire -> mot suivant)
            while session is not None and session.get("status") == "playing":
                word = pending(key)
                if word is None or len(session.get("guesses") or []) >= MAX_ATTEMPTS - 1:
                    status, body = client.call("POST", f"/api/game/{session['id']}/giveup", {}, kind="giveup")
                    answer = ((body.get("session") or {}).get("answer") if isinstance(body, dict) else None)
                    if answer:
                        answer = answer.upper()
                        revealed.append(answer)
                        with revealed_path.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"t": time.time(), "letter": session["firstLetter"],
                                                "length": session["wordLength"], "answer": answer,
                                                "source": "validate_root_candidates"}, ensure_ascii=False) + "\n")
                        # une solution est par définition un mot accepté par le jeu
                        known_valid = add_to_blocklist(answer, known_valid_path)
                    session = None
                    break
                previous_guesses = len(session.get("guesses") or [])  # relu AVANT la requête
                status, body = client.call("POST", f"/api/game/{session['id']}/guess", {"guess": word}, kind="guess")
                error = body.get("error") if isinstance(body, dict) else None
                if error == "INVALID_WORD":
                    blocklist = add_to_blocklist(word, blocklist_path)
                    tested["invalid"].append(word)
                elif error is None and status == 200:
                    known_valid = add_to_blocklist(word, known_valid_path)  # même format de fichier
                    tested["valid"].append(word)
                else:  # GAME_OVER, NOT_FOUND... : mot non jugé, retesté plus tard
                    session = None
                    break
                tested_here += 1
                session = session_state(body) or session
                if (error is None and session.get("status") == "playing"
                        and len(session.get("guesses") or []) <= previous_guesses):
                    key = note_draw(session, continuation=True)  # mot trouvé : le suivant commence
            idle = 0 if tested_here else idle + 1
    except Throttled as exc:
        stop_reason = f"ARRÊT D'URGENCE : {exc}"
    except StopIteration as exc:
        stop_reason = str(exc)
    else:
        stop_reason = stop_reason or "tous les candidats en attente ont été testés"
    record_provenance(tested["invalid"], DATA / "known_invalid_words_provenance.json")
    still_pending = sorted(k for k in groups if pending(k))
    n_tested = len(tested["valid"]) + len(tested["invalid"])
    return {
        "stop_reason": stop_reason, "requests": client.sent, "sessions": sessions,
        "latency_max_s": round(max(client.latencies, default=0), 3),
        "latency_median_s": round(sorted(client.latencies)[len(client.latencies) // 2], 3) if client.latencies else None,
        "deep_rankings": deep is not None,
        "groups_pending_at_start": len(initial_pending), "tested": n_tested,
        "valid": len(tested["valid"]), "invalid": len(tested["invalid"]),
        "rejection_rate_pct": round(100 * len(tested["invalid"]) / n_tested, 1) if n_tested else None,
        "groups_total": len(groups), "groups_fully_validated": len(groups) - len(still_pending),
        "groups_still_pending": still_pending,
        "draws": len(draws), "distinct_groups_drawn": len(set(draws)),
        "first_observations": first_observations, "revealed": revealed,
        "invalid_words": tested["invalid"], "valid_words": tested["valid"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-requests", type=int, default=2000)
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--summary", default=str(DATA / "root_validation" / "summary.json"))
    parser.add_argument("--rankings", default=str(DEFAULT_RANKINGS))
    parser.add_argument("--idle-sessions-stop", type=int, default=IDLE_SESSIONS_STOP)
    parser.add_argument("--compute-rankings", action="store_true",
                        help="Calcule le classement profond (local, aucune requête) puis s'arrête.")
    parser.add_argument("--end-round", action="store_true",
                        help="Fin de tour : corpus, caches et classement profond recalculés (local).")
    parser.add_argument("--depth", type=int, default=DEPTH)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.end_round:
        print(json.dumps(end_round(args), ensure_ascii=False))
        return
    if args.compute_rankings:
        print(json.dumps(compute_rankings(args), ensure_ascii=False))
        return
    summary = run(args)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("invalid_words", "valid_words", "revealed")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
