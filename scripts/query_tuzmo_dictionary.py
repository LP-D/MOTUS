#!/usr/bin/env python3
"""Interroge le dictionnaire de validation RÉEL de Tuzmo, côté serveur, pour
construire une liste de référence (mot -> valide/invalide), sans lancer de partie
au sens du bot de jeu — script séparé, autonome (`requests`, pas de Playwright).

Découverte (inspection de l'onglet Network sur /infinite, cf. session) : la
validation d'un mot n'est PAS côté client (aucun dictionnaire n'est téléchargé dans
le bundle JS) — chaque coup déclenche un appel serveur :

    POST https://www.tusmo.xyz/api/game/{session_id}/guess
    Content-Type: application/json
    body: {"guess": "<MOT>"}

    -> 200 OK, mot valide  : {"session": {...guesses mis à jour...}, "result": [...]}
    -> 200 OK, mot invalide: {"session": {...inchangé...}, "error": "INVALID_WORD"}

La session est créée via `POST /api/game` (body {"lang":"fr","mode":"infinite"}),
qui pose un cookie `tusmo_token` (JWT, HttpOnly) nécessaire aux appels suivants —
géré ici via une `requests.Session()` classique, aucune authentification par compte
n'est requise (le flux /infinite est jouable anonymement).

Contrainte structurelle : chaque session impose une (lettre, longueur) choisie par
le SERVEUR (pas nous) — un mot ne peut être testé QUE contre une session dont
firstLetter/wordLength correspondent. On ne peut donc pas interroger un mot précis à
la demande ; on teste, pour chaque session obtenue, les mots du corpus local encore
non testés qui correspondent à son (lettre, longueur). Une session accepte un nombre
limité de vraies réussites (le jeu réel plafonne à 6 coups) ; dès qu'une réponse
renvoie une erreur autre que INVALID_WORD (session terminée), on en recrée une.

Limitation de débit : délai aléatoire (par défaut 1.5-2.5s) entre CHAQUE requête
HTTP (guess ou création de session) — volontairement prudent (même ordre de
grandeur que les délais de frappe déjà utilisés ailleurs dans ce dépôt) pour un
petit jeu indépendant, à ajuster via --delay-min/--delay-max si besoin.

Résultats persistés en continu (data/tuzmo_dictionary_validation.json), résumable :
un mot déjà dans le fichier n'est pas retesté.

    python scripts/query_tuzmo_dictionary.py --max-words 200
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.corpus import Corpus  # noqa: E402

BASE_URL = "https://www.tusmo.xyz"
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "tuzmo_dictionary_validation.json"


def create_session(http: requests.Session) -> dict:
    resp = http.post(f"{BASE_URL}/api/game", json={"lang": "fr", "mode": "infinite"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def submit_guess(http: requests.Session, session_id: str, word: str) -> dict:
    resp = http.post(f"{BASE_URL}/api/game/{session_id}/guess", json={"guess": word}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def load_results(path: Path) -> dict[str, bool]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_results(results: dict[str, bool], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-words", type=int, default=200, help="Budget de mots testés sur ce run.")
    parser.add_argument(
        "--max-valid-guesses-per-session", type=int, default=5,
        help="Nb max de mots VALIDES soumis par session avant d'en recréer une "
        "(le jeu réel plafonne à 6 coups ; on s'arrête à 5 par prudence, une "
        "session pourrait se terminer plus tôt selon l'état serveur).",
    )
    parser.add_argument("--delay-min", type=float, default=1.5, help="Délai mini (s) entre requêtes HTTP.")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Délai maxi (s) entre requêtes HTTP.")
    args = parser.parse_args()

    corpus = Corpus.from_file(args.corpus_path)
    output_path = Path(args.output)
    results = load_results(output_path)
    print(f"{len(results)} mot(s) déjà validé(s) dans {output_path} (repris tel quel).")

    http = requests.Session()
    http.headers.update({"Content-Type": "application/json"})

    tested_this_run = 0
    while tested_this_run < args.max_words:
        session = create_session(http)
        time.sleep(random.uniform(args.delay_min, args.delay_max))

        session_id = session["id"]
        letter, length = session["firstLetter"], session["wordLength"]
        candidates = [
            w for w in corpus.subset(letter, length) if w not in results
        ]
        if not candidates:
            print(f"session {letter},{length} : aucun mot restant à tester, session ignorée.")
            continue

        valid_guesses_used = 0
        for word in candidates:
            if tested_this_run >= args.max_words or valid_guesses_used >= args.max_valid_guesses_per_session:
                break

            response = submit_guess(http, session_id, word)
            error = response.get("error")
            if error == "INVALID_WORD":
                results[word] = False
            elif error is None:
                results[word] = True
                valid_guesses_used += 1
            else:
                # session terminée/épuisée pour une autre raison que le mot lui-même
                print(f"  session {letter},{length} terminée ({error}), rotation.")
                break

            tested_this_run += 1
            save_results(results, output_path)
            status = "valide" if results[word] else "INVALIDE"
            print(f"[{tested_this_run}/{args.max_words}] {letter},{length} {word} -> {status}")

            time.sleep(random.uniform(args.delay_min, args.delay_max))

    n_valid = sum(1 for v in results.values() if v)
    print(f"\n{len(results)} mot(s) testé(s) au total ({n_valid} valides, {len(results) - n_valid} invalides).")
    print(f"Résultats : {output_path}")


if __name__ == "__main__":
    main()
