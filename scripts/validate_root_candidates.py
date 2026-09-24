#!/usr/bin/env python3
"""Validation ciblée, via l'API Tuzmo, des candidats de premier coup du cache racine
(coup 1 + 9 replis, caches composite et entropy_pure) : chaque mot est soit accepté
(-> data/known_valid_words.json), soit refusé INVALID_WORD (-> liste noire). Ce ne
sont que ~2 000 requêtes, pas l'extraction complète du dictionnaire (289 606
requêtes, hors scope).

Contraintes serveur prises en compte (constatées le 23/09/2026) :
- un seul invité pour tout le run (GET /api/me, puis un seul cookie) : la création
  d'invités en série déclenche un 429 ;
- 1,5 à 2,5 s entre CHAQUE requête (débit validé par le stress test initial) ;
- arrêt d'urgence sur HTTP 429, Retry-After ou latence > 10 s ;
- /infinite garde une partie en cours par invité : l'état est relu dans chaque
  réponse (une victoire enchaîne sur le mot suivant dans la même session), et une
  partie dont le groupe n'a plus rien à tester est close par giveup (sinon elle
  serait resservie à la création suivante) ; la solution révélée est journalisée ;
- le serveur choisit le groupe (lettre, longueur) de chaque partie : la couverture
  des 130 groupes dépend du tirage (problème du collectionneur), d'où un budget.

Journal reprenable : chaque requête est ajoutée à `--log` ; un mot déjà présent
dans known_valid / la liste noire n'est jamais retesté.

    python scripts/validate_root_candidates.py --max-requests 2000
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
from motus_solver.cache import cache_key, load_cache  # noqa: E402

BASE_URL = "https://www.tusmo.xyz"
DATA = ROOT_DIR / "data"
DEFAULT_LOG = DATA / "root_validation" / "validation_log.jsonl"
GAP_S = (1.5, 2.5)
THROTTLE_LATENCY_S = 10.0
MAX_ATTEMPTS = 6


class Throttled(RuntimeError):
    pass


def candidates_by_group(caches: list[dict]) -> dict[str, list[str]]:
    """Coup 1 + replis de chaque groupe, toutes stratégies confondues, dans l'ordre
    du classement (le coup 1 d'abord)."""
    groups: dict[str, list[str]] = {}
    for cache in caches:
        for key, entry in cache.items():
            words = groups.setdefault(key, [])
            for e in (entry, *entry.get("alternatives", ())):
                if e["word"] not in words:
                    words.append(e["word"])
    return groups


def session_state(body: dict) -> dict | None:
    session = body.get("session", body) if isinstance(body, dict) else None
    return session if isinstance(session, dict) and "id" in session else None


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


def run(args) -> dict:
    known_valid_path, blocklist_path = DATA / "known_valid_words.json", DATA / "known_invalid_words.json"
    revealed_path = DATA / "revealed_solutions.jsonl"
    known_valid, blocklist = load_blocklist(known_valid_path), load_blocklist(blocklist_path)
    groups = candidates_by_group([load_cache(DATA / "root_cache.json"), load_cache(DATA / "root_cache_entropy_pure.json")])
    todo = {k: [w for w in ws if w not in known_valid and w not in blocklist] for k, ws in groups.items()}
    initial_todo = sum(len(v) for v in todo.values())
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    client = Client(log_path, args.max_requests)
    tested = {"valid": [], "invalid": []}
    sessions = 0
    unplayable = 0
    stop_reason = None
    try:
        client.call("GET", "/api/me", kind="guest")
        while any(todo.values()):
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
            # une partie peut enchaîner plusieurs mots (victoire -> mot suivant)
            while session is not None and session.get("status") == "playing":
                key = cache_key(session["firstLetter"], session["wordLength"])
                pending = todo.get(key) or []
                if not pending or len(session.get("guesses") or []) >= MAX_ATTEMPTS - 1:
                    status, body = client.call("POST", f"/api/game/{session['id']}/giveup", {}, kind="giveup")
                    answer = ((body.get("session") or {}).get("answer") if isinstance(body, dict) else None)
                    if answer:
                        with revealed_path.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"t": time.time(), "letter": session["firstLetter"],
                                                "length": session["wordLength"], "answer": answer,
                                                "source": "validate_root_candidates"}, ensure_ascii=False) + "\n")
                    session = None
                    break
                word = pending.pop(0)
                status, body = client.call("POST", f"/api/game/{session['id']}/guess", {"guess": word}, kind="guess")
                error = body.get("error") if isinstance(body, dict) else None
                if error == "INVALID_WORD":
                    blocklist = add_to_blocklist(word, blocklist_path)
                    tested["invalid"].append(word)
                elif error is None and status == 200:
                    known_valid = add_to_blocklist(word, known_valid_path)  # même format de fichier
                    tested["valid"].append(word)
                else:  # GAME_OVER, NOT_FOUND... : mot non jugé, remis en file
                    pending.insert(0, word)
                    session = None
                    break
                session = session_state(body) or session
    except Throttled as exc:
        stop_reason = f"ARRÊT D'URGENCE : {exc}"
    except StopIteration as exc:
        stop_reason = str(exc)
    else:
        stop_reason = stop_reason or "tous les candidats en attente ont été testés"
    covered = [k for k, ws in groups.items() if not todo.get(k)]
    n_tested = len(tested["valid"]) + len(tested["invalid"])
    return {
        "stop_reason": stop_reason, "requests": client.sent, "sessions": sessions,
        "latency_max_s": round(max(client.latencies, default=0), 3),
        "latency_median_s": round(sorted(client.latencies)[len(client.latencies) // 2], 3) if client.latencies else None,
        "candidates_pending_at_start": initial_todo, "tested": n_tested,
        "valid": len(tested["valid"]), "invalid": len(tested["invalid"]),
        "rejection_rate_pct": round(100 * len(tested["invalid"]) / n_tested, 1) if n_tested else None,
        "groups_total": len(groups), "groups_fully_validated": len(covered),
        "still_pending": sum(len(v) for v in todo.values()),
        "invalid_words": tested["invalid"], "valid_words": tested["valid"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-requests", type=int, default=2000)
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--summary", default=str(DATA / "root_validation" / "summary.json"))
    args = parser.parse_args()
    summary = run(args)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("invalid_words", "valid_words")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
