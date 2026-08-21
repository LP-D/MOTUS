#!/usr/bin/env python3
"""Test de montée en charge de l'endpoint de validation Tuzmo (POST
/api/game/{id}/guess), pour valider un débit sûr AVANT de lancer l'extraction
complète du dictionnaire (`scripts/query_tuzmo_dictionary.py`, potentiellement
des dizaines de milliers de requêtes).

Script séparé et autonome : ne touche à aucun fichier de sortie de
`query_tuzmo_dictionary.py`, n'importe rien du bot de jeu ni du dashboard, et ne
modifie leur comportement en rien.

Paliers : 100, puis 500, puis 1000 requêtes consécutives au débit actuel
(1.5-2.5s entre CHAQUE requête HTTP, création de session incluse), pause de 60s
entre paliers avec log explicite. Le test s'arrête immédiatement (pas de crash
silencieux — log explicite de la raison) dès qu'un signal de blocage apparaît :
HTTP 429, HTTP 403, en-tête Retry-After/X-RateLimit-* signalant une limite,
changement de comportement de session (JWT non reconnu), timeouts répétés, ou
dérive de latence soutenue (pas un simple pic isolé).

Compte utilisé : AUCUN. Le mode /infinite (seul mode exploité ici) ne nécessite
aucune authentification — chaque partie crée une session anonyme via un cookie
JWT éphémère (vérifié par inspection réseau, cf. session précédente : le flux
fonctionne intégralement en curl sans connexion préalable). Il n'y a donc ni
compte principal (IRAM66) ni compte de test à protéger : ce test ne touche à
aucun compte, point.

    python scripts/stress_test_dictionary_endpoint.py --tiers 100,500,1000
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.corpus import Corpus  # noqa: E402

BASE_URL = "https://www.tusmo.xyz"
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "dictionary_extraction_stress_report.json"
DEFAULT_LOG = ROOT_DIR / "data" / "dictionary_extraction_stress_log.jsonl"

MAX_VALID_GUESSES_PER_SESSION = 5  # même prudence que query_tuzmo_dictionary.py
LATENCY_BASELINE_WINDOW = 20  # nb de requêtes pour établir la latence de référence
LATENCY_ALERT_MULTIPLIER = 3.0  # déclenche si la latence soutenue dépasse 3x la base
LATENCY_ALERT_SUSTAINED = 5  # nb de requêtes consécutives au-dessus du seuil
MAX_CONSECUTIVE_TIMEOUTS = 3

RATE_LIMIT_HEADER_PREFIXES = ("x-ratelimit", "retry-after")


@dataclass
class RequestRecord:
    index: int
    kind: str  # "create_session" | "guess"
    status_code: int | None
    latency_s: float
    rate_limit_headers: dict[str, str]
    error: str | None
    timestamp: float = field(default_factory=time.time)


@dataclass
class AbortSignal:
    reason: str
    request_index: int
    details: dict


def extract_rate_limit_headers(headers: requests.structures.CaseInsensitiveDict) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower().startswith(RATE_LIMIT_HEADER_PREFIXES)}


def do_request(
    http: requests.Session, method: str, url: str, index: int, kind: str, **kwargs
) -> tuple[requests.Response | None, RequestRecord, str | None]:
    """Exécute une requête HTTP, retourne (response_ou_None, record, erreur_ou_None).
    Ne lève jamais : un timeout/erreur réseau est capturé et reflété dans le record."""
    start = time.perf_counter()
    try:
        resp = http.request(method, url, timeout=15, **kwargs)
        latency = time.perf_counter() - start
        record = RequestRecord(
            index=index,
            kind=kind,
            status_code=resp.status_code,
            latency_s=round(latency, 3),
            rate_limit_headers=extract_rate_limit_headers(resp.headers),
            error=None,
        )
        return resp, record, None
    except requests.exceptions.RequestException as exc:
        latency = time.perf_counter() - start
        record = RequestRecord(
            index=index,
            kind=kind,
            status_code=None,
            latency_s=round(latency, 3),
            rate_limit_headers={},
            error=f"{exc.__class__.__name__}: {exc}",
        )
        return None, record, record.error


def check_abort_conditions(
    resp: requests.Response | None,
    record: RequestRecord,
    recent_latencies: list[float],
    baseline_latency: float | None,
    consecutive_timeouts: int,
    expected_session_id: str | None,
) -> AbortSignal | None:
    if record.error is not None:
        if consecutive_timeouts + 1 >= MAX_CONSECUTIVE_TIMEOUTS:
            return AbortSignal(
                "timeouts_répétés",
                record.index,
                {"consecutive_timeouts": consecutive_timeouts + 1, "last_error": record.error},
            )
        return None  # un timeout isolé ne suffit pas à conclure

    if record.status_code == 429:
        return AbortSignal("http_429", record.index, {"headers": record.rate_limit_headers})
    if record.status_code == 403:
        return AbortSignal("http_403", record.index, {"headers": record.rate_limit_headers})
    if record.rate_limit_headers:
        return AbortSignal("rate_limit_header_present", record.index, {"headers": record.rate_limit_headers})

    if resp is not None and expected_session_id is not None:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        session = body.get("session") or {}
        if session and session.get("id") not in (None, expected_session_id):
            return AbortSignal(
                "session_id_mismatch", record.index, {"expected": expected_session_id, "got": session.get("id")}
            )
        # une erreur inattendue (ni None, ni "INVALID_WORD", ni "GAME_OVER") sur une
        # requête de guess peut signaler une session invalidée côté serveur.
        # GAME_OVER est un état de jeu légitime (partie déjà terminée — attendu si
        # un mot soumis au hasard a résolu la cible avant nos MAX_VALID_GUESSES_PER_SESSION
        # essais prévus) : géré par une rotation de session dans run_tier, pas une alerte.
        error = body.get("error")
        if error not in (None, "INVALID_WORD", "GAME_OVER"):
            return AbortSignal("unexpected_api_error", record.index, {"error": error})

    if baseline_latency is not None and len(recent_latencies) >= LATENCY_ALERT_SUSTAINED:
        window = recent_latencies[-LATENCY_ALERT_SUSTAINED:]
        if all(latency > baseline_latency * LATENCY_ALERT_MULTIPLIER for latency in window):
            return AbortSignal(
                "latence_soutenue_anormale",
                record.index,
                {"baseline_s": baseline_latency, "recent_s": window},
            )

    return None


def run_tier(
    http: requests.Session,
    corpus: Corpus,
    n_requests: int,
    delay_min: float,
    delay_max: float,
    log_path: Path,
    start_index: int,
) -> tuple[int, AbortSignal | None]:
    """Envoie `n_requests` requêtes (création de session incluse). Retourne
    (nb_requêtes_effectivement_envoyées, signal_abort_ou_None)."""
    latencies: list[float] = []
    baseline_latency: float | None = None
    consecutive_timeouts = 0
    sent = 0
    index = start_index

    while sent < n_requests:
        session_resp, session_record, session_err = do_request(
            http, "POST", f"{BASE_URL}/api/game", index, "create_session",
            json={"lang": "fr", "mode": "infinite"},
        )
        index += 1
        sent += 1
        _log(log_path, session_record)
        latencies.append(session_record.latency_s)
        consecutive_timeouts = consecutive_timeouts + 1 if session_err else 0

        signal = check_abort_conditions(session_resp, session_record, latencies, baseline_latency, consecutive_timeouts, None)
        if signal:
            return sent, signal
        if baseline_latency is None and len(latencies) >= LATENCY_BASELINE_WINDOW:
            baseline_latency = statistics.median(latencies[:LATENCY_BASELINE_WINDOW])

        time.sleep(random.uniform(delay_min, delay_max))

        if session_err or session_resp is None:
            continue
        session = session_resp.json()
        session_id = session["id"]
        letter, length = session["firstLetter"], session["wordLength"]
        candidates = corpus.subset(letter, length)
        random.shuffle(candidates)

        valid_used = 0
        for word in candidates:
            if sent >= n_requests or valid_used >= MAX_VALID_GUESSES_PER_SESSION:
                break
            resp, record, err = do_request(
                http, "POST", f"{BASE_URL}/api/game/{session_id}/guess", index, "guess",
                json={"guess": word},
            )
            index += 1
            sent += 1
            _log(log_path, record)
            latencies.append(record.latency_s)
            consecutive_timeouts = consecutive_timeouts + 1 if err else 0

            signal = check_abort_conditions(resp, record, latencies, baseline_latency, consecutive_timeouts, session_id)
            if signal:
                return sent, signal
            if baseline_latency is None and len(latencies) >= LATENCY_BASELINE_WINDOW:
                baseline_latency = statistics.median(latencies[:LATENCY_BASELINE_WINDOW])

            if not err and resp is not None:
                body = resp.json()
                if body.get("error") is None:
                    valid_used += 1
                elif body.get("error") == "GAME_OVER":
                    # partie déjà terminée (attempt cap atteint, ou cible résolue
                    # par un mot soumis au hasard avant nos MAX_VALID_GUESSES_PER_SESSION
                    # essais prévus) : rotation immédiate vers une nouvelle session
                    # plutôt que de répéter GAME_OVER sur chaque candidat restant.
                    break

            time.sleep(random.uniform(delay_min, delay_max))

    return sent, None


def _log(log_path: Path, record: RequestRecord) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tiers", default="100,500,1000")
    parser.add_argument("--pause-between-tiers", type=float, default=60.0)
    parser.add_argument("--delay-min", type=float, default=1.5)
    parser.add_argument("--delay-max", type=float, default=2.5)
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    args = parser.parse_args()

    tiers = [int(t) for t in args.tiers.split(",")]
    corpus = Corpus.from_file(args.corpus_path)
    log_path = Path(args.log)
    if log_path.exists():
        log_path.unlink()

    http = requests.Session()
    http.headers.update({"Content-Type": "application/json"})

    total_sent = 0
    abort_signal: AbortSignal | None = None
    tier_results = []

    for tier_num, n_requests in enumerate(tiers, start=1):
        print(f"\n=== Palier {tier_num}/{len(tiers)} : {n_requests} requêtes (cumulé avant : {total_sent}) ===")
        sent, signal = run_tier(http, corpus, n_requests, args.delay_min, args.delay_max, log_path, total_sent)
        total_sent += sent
        tier_results.append({"tier": n_requests, "requests_sent": sent, "aborted": signal is not None})

        if signal:
            print(f"ARRÊT : {signal.reason} à la requête #{signal.request_index} — {signal.details}")
            abort_signal = signal
            break

        print(f"Palier {tier_num} terminé sans signal d'alerte ({sent} requêtes).")
        if tier_num < len(tiers):
            print(f"Pause de {args.pause_between_tiers:.0f}s avant le palier suivant...")
            time.sleep(args.pause_between_tiers)

    # --- recommandation ---
    if abort_signal is None:
        safe_delay_min, safe_delay_max = args.delay_min, args.delay_max
        recommendation = (
            f"Aucun signal de blocage sur {total_sent} requêtes cumulées. "
            f"Débit {args.delay_min}-{args.delay_max}s validé pour l'extraction complète."
        )
    else:
        safe_delay_min, safe_delay_max = args.delay_min * 2, args.delay_max * 2
        recommendation = (
            f"Signal '{abort_signal.reason}' déclenché après {total_sent} requêtes cumulées "
            f"(palier {tier_results[-1]['tier']}). Débit réduit recommandé (x2, marge de sécurité) : "
            f"{safe_delay_min:.1f}-{safe_delay_max:.1f}s entre requêtes."
        )

    avg_delay = (safe_delay_min + safe_delay_max) / 2
    full_corpus_size = len(corpus)
    # ~2 requêtes par mot en moyenne dans query_tuzmo_dictionary.py (1 guess + part
    # proportionnelle d'une création de session tous les ~5 mots)
    estimated_requests = full_corpus_size + full_corpus_size // MAX_VALID_GUESSES_PER_SESSION
    estimated_seconds = estimated_requests * avg_delay

    report = {
        "requests_before_signal": total_sent if abort_signal else None,
        "requests_total_tested": total_sent,
        "abort_signal": {"reason": abort_signal.reason, "at_request": abort_signal.request_index, "details": abort_signal.details}
        if abort_signal
        else None,
        "tier_results": tier_results,
        "current_delay_s": [args.delay_min, args.delay_max],
        "recommended_delay_s": [round(safe_delay_min, 2), round(safe_delay_max, 2)],
        "recommendation": recommendation,
        "full_corpus_size": full_corpus_size,
        "estimated_total_requests_full_extraction": estimated_requests,
        "estimated_total_time_s": round(estimated_seconds, 0),
        "estimated_total_time_h": round(estimated_seconds / 3600, 2),
        "account_note": (
            "Aucun compte utilisé (ni IRAM66 ni compte de test) : le mode /infinite ne "
            "nécessite aucune authentification, chaque partie crée une session anonyme "
            "via un cookie JWT éphémère indépendant de tout compte. Ce test comme "
            "l'extraction complète ne touchent donc à aucun compte."
        ),
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{recommendation}")
    print(f"Estimation extraction complète ({full_corpus_size} mots, ~{estimated_requests} requêtes) : "
          f"{estimated_seconds/3600:.1f}h au débit recommandé.")
    print(f"Rapport : {args.output}")
    print(f"Log détaillé : {log_path}")


if __name__ == "__main__":
    main()
