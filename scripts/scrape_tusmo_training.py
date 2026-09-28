#!/usr/bin/env python3
"""Scrape du mode Entraînement de Tusmo (/entrainement, compte Pro « lettre » requis)
via son API JSON, sans navigateur : le solveur joue chaque partie, Tusmo note chaque
coup (`percent`, 100 = meilleur coup possible selon Tusmo) et renvoie en fin de partie
le rapport « ce qu'il fallait jouer » (meilleur mot par coup). Chaque partie est
ajoutée à `data/tusmo_training_log.jsonl` (réponses brutes incluses).

Endpoints (relevés dans le bundle TrainingView du site le 28/09/2026) :
- POST /api/training {lang, wordLen, maxTries, showCandidates, preview, initials}
- POST /api/training/<id>/guess {word}   -> session (guesses[].result, percent)
- POST /api/training/<id>/preview {word} -> {percent} (option `preview` : la note
  d'un mot avant de le jouer ; nombre limité, TOO_MANY_PREVIEWS)
- GET  /api/training/<id>/report          -> rapport de fin de partie

Session : `data/tuzmo_auth_state.json` (cf. bot/save_auth_state.py), authentification
par cookie. Même débit que scripts/validate_root_candidates.py : 1,5 à 2,5 s entre
chaque requête, arrêt immédiat sur HTTP 429 / Retry-After / latence > 5 s.

    python scripts/scrape_tusmo_training.py --games 5
    python scripts/scrape_tusmo_training.py --games 10 --length 7 --letter R --preview
    python scripts/scrape_tusmo_training.py --summary
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

from motus_solver.blocklist import load_blocklist  # noqa: E402
from motus_solver.cache import DEFAULT_STRATEGY, ROOT_CACHE_FILES, load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.solver import Solver  # noqa: E402

BASE_URL = "https://www.tusmo.xyz"
DATA = ROOT_DIR / "data"
AUTH_STATE = DATA / "tuzmo_auth_state.json"
DEFAULT_LOG = DATA / "tusmo_training_log.jsonl"
GAP_S = (1.5, 2.5)
THROTTLE_LATENCY_S = 5.0
RESULT_CODES = {"correct": "2", "present": "1", "absent": "0"}
MAX_REJECTS_PER_MOVE = 20
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Throttled(RuntimeError):
    pass


class TrainingAPI:
    def __init__(self, auth_state: Path = AUTH_STATE):
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Origin": BASE_URL,
                                  "Referer": BASE_URL + "/entrainement"})
        state = json.loads(auth_state.read_text(encoding="utf-8"))
        for c in state["cookies"]:
            if "tusmo" in c["domain"]:
                self.http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        self.last_t = 0.0
        self.sent = 0

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
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
        limited = any(k.lower() == "retry-after" for k in resp.headers)
        if resp.status_code == 429 or limited or latency > THROTTLE_LATENCY_S:
            raise Throttled(f"HTTP {resp.status_code}, {latency:.1f}s sur {method} {path}")
        try:
            return resp.json()
        except ValueError:
            return {"error": f"HTTP {resp.status_code}"}

    def me(self) -> dict:
        return self.call("GET", "/api/me")

    def start(self, config: dict) -> dict:
        return self.call("POST", "/api/training", config)

    def guess(self, session_id: str, word: str) -> dict:
        return self.call("POST", f"/api/training/{session_id}/guess", {"word": word})

    def preview(self, session_id: str, word: str) -> dict:
        return self.call("POST", f"/api/training/{session_id}/preview", {"word": word})

    def report(self, session_id: str) -> dict:
        return self.call("GET", f"/api/training/{session_id}/report")


def result_to_pattern(result: list[str]) -> str:
    """["correct", "present", "absent", ...] -> "210..." (codage de motus_solver)."""
    return "".join(RESULT_CODES[r] for r in result)


def play_game(api, solver: Solver, session: dict, use_preview: bool = False) -> dict:
    """Joue la session jusqu'au bout avec le solveur ; renvoie l'enregistrement de la
    partie (coups notés par Tusmo, mots refusés, rapport brut)."""
    moves, rejected = [], []
    preview_on = use_preview and session.get("preview", False)
    while session.get("status") == "playing":
        if not solver.candidates:
            break  # solution hors corpus du solveur : rien de cohérent à proposer
        suggestions = solver.suggest(top_n=3)
        word = suggestions[0][0] if suggestions else None
        rejects = 0
        while word is not None:
            preview = None
            if preview_on:
                p = api.preview(session["id"], word)
                if p.get("error") == "TOO_MANY_PREVIEWS":
                    preview_on = False
                preview = p.get("percent")
            resp = api.guess(session["id"], word)
            if resp.get("error") != "INVALID_WORD":
                break
            rejected.append(word)
            solver.discard(word)
            rejects += 1
            suggestions = solver.suggest(top_n=3) if solver.candidates and rejects < MAX_REJECTS_PER_MOVE else []
            word = suggestions[0][0] if suggestions else None
        if word is None:
            break
        if "error" in resp:
            moves.append({"word": word, "error": resp["error"]})
            session = resp.get("session", session)
            break
        session = resp
        last = session["guesses"][-1]
        pattern = result_to_pattern(last["result"])
        solver.play(word)
        solver.update(pattern)
        moves.append({"word": word, "pattern": pattern, "percent": last.get("percent"),
                      "preview_percent": preview, "winning": last.get("winning"),
                      "solver_top": [[w, round(float(s), 4)] for w, s, _ in suggestions],
                      "candidates_left_tusmo": session.get("candidatesLeft"),
                      "candidates_left_solver": len(solver.candidates)})
    report = api.report(session["id"]) if session.get("status") != "playing" else None
    return {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "session_id": session["id"],
            "first_letter": session.get("firstLetter"), "word_len": session.get("wordLen"),
            "max_tries": session.get("maxTries"), "status": session.get("status"),
            "strategy": solver.strategy, "moves": moves, "rejected": rejected,
            "session": session, "report": report}


def summarize(records: list[dict]) -> str:
    """Synthèse à partir des rapports de fin de partie de Tusmo (`report.moves` :
    word, percent, bestWord, candidatesBefore)."""
    done = [r for r in records if (r.get("report") or {}).get("moves")]
    if not done:
        return "aucune partie terminée."
    moves = [m for r in done for m in r["report"]["moves"]]
    firsts = [r["report"]["moves"][0] for r in done]
    won = [r for r in done if r["report"].get("won")]
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    lines = [
        f"parties terminées : {len(done)} (gagnées : {len(won)}, {len(won) / len(done):.0%})",
        "essais moyens (parties gagnées) : " + (f"{mean([r['report']['tries'] for r in won]):.2f}" if won else "-"),
        f"note Tusmo moyenne des coups du solveur : {mean([m['percent'] for m in moves]):.1f} % "
        f"({len(moves)} coups, {mean([m['percent'] >= 100 for m in moves]):.0%} à 100 %)",
        f"coup du solveur = meilleur mot selon Tusmo : {mean([m['word'] == m['bestWord'] for m in moves]):.0%}",
        f"coup 1 : note moyenne {mean([m['percent'] for m in firsts]):.1f} %, "
        f"liste de solutions Tusmo du groupe : {mean([m['candidatesBefore'] for m in firsts]):.0f} mots en moyenne",
    ]
    return "\n".join(lines)


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--length", type=int, choices=range(5, 10), help="Longueur fixe (sinon tirée au hasard).")
    parser.add_argument("--max-tries", type=int, choices=range(4, 8), default=6)
    parser.add_argument("--letter", action="append", default=[], help="Initiale(s) imposée(s), répétable.")
    parser.add_argument("--preview", action="store_true",
                        help="Note de chaque mot avant de le jouer (partie hors progression, nombre limité).")
    parser.add_argument("--strategy", choices=sorted(ROOT_CACHE_FILES), default=DEFAULT_STRATEGY)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--summary", action="store_true", help="Synthèse du journal existant, sans jouer.")
    args = parser.parse_args()

    if args.summary:
        print(summarize(load_records(args.log)))
        return
    if not AUTH_STATE.exists():
        sys.exit("pas de session : lance d'abord python bot/save_auth_state.py (compte Pro)")

    api = TrainingAPI()
    me = api.me()
    user = me.get("user", me)
    if user.get("tier") != "lettre":
        sys.exit(f"compte sans accès Entraînement (tier={user.get('tier')!r}) : Pro requis")

    corpus = Corpus.from_file(DATA / "corpus_fr.txt")
    root_cache = load_cache(DATA / ROOT_CACHE_FILES[args.strategy])
    blocklist = load_blocklist(DATA / "known_invalid_words.json")
    known_valid = load_blocklist(DATA / "known_valid_words.json")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        for i in range(1, args.games + 1):
            config = {"lang": "fr", "wordLen": args.length or random.randint(5, 9), "maxTries": args.max_tries,
                      "showCandidates": True, "preview": args.preview,
                      "initials": [x.upper() for x in args.letter]}
            session = api.start(config)
            if "error" in session:
                print(f"partie {i} : démarrage refusé ({session['error']} {session.get('letter') or ''})")
                break
            solver = Solver(letter=session["firstLetter"], length=session["wordLen"], corpus=corpus,
                            root_cache=root_cache, blocklist=blocklist, known_valid=known_valid,
                            strategy=args.strategy, max_attempts=session["maxTries"])
            record = play_game(api, solver, session, use_preview=args.preview)
            record["config"] = config
            with args.log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
            answer = (record["report"] or {}).get("answer", "?")
            notes = " ".join(f"{m['word']}:{m.get('percent')}%" for m in record["moves"])
            print(f"partie {i}/{args.games} {session['firstLetter']}-{session['wordLen']} "
                  f"{record['status']} (réponse {answer}) | {notes}")
    except Throttled as exc:
        print(f"arrêt d'urgence (limitation serveur) : {exc}")
    print(f"\n{api.sent} requêtes envoyées. Cette session :\n{summarize(records)}")


if __name__ == "__main__":
    main()
