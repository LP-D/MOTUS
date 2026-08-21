#!/usr/bin/env python3
"""Vérifie, en jouant des parties réelles sur Tuzmo (mode /infinite, rejouable à
volonté), si le mot mis en cache par `build_root_cache` pour le groupe (lettre,
longueur) tiré est accepté ou rejeté ("Mot inconnu") par le dictionnaire de
validation du jeu.

Constat déclencheur : le corpus (241k mots, Wiktionnaire) est plus large que le
dictionnaire de validation de Tuzmo — certains mots cache (formes rares/conjuguées,
ex. PIAUTE, AROUTINE) sont rejetés en jeu réel. Ce script chiffre l'ampleur du
problème et enregistre les mots concernés, sans rien changer au solveur ni au bot.

    python scripts/validate_root_cache_words.py --games 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motus_solver.cache import cache_key, load_cache  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT_DIR / "data" / "corpus_fr.txt"
DEFAULT_ROOT_CACHE = ROOT_DIR / "data" / "root_cache.json"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "root_cache_word_validation.json"
URL = "https://www.tusmo.xyz/infinite"
ENTER_KEY = "⏎"
UNKNOWN_WORD_TOAST = "Mot inconnu"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS))
    parser.add_argument("--root-cache", default=str(DEFAULT_ROOT_CACHE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    corpus = Corpus.from_file(args.corpus_path)
    root_cache = load_cache(args.root_cache)

    output_path = Path(args.output)
    results: list[dict] = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else []

    def save() -> None:
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for i in range(args.games):
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(URL, wait_until="networkidle", timeout=20000)
                page.locator("button", has_text="C'est parti").first.click()
                page.wait_for_selector(".board.board--stage", timeout=10000)
                page.wait_for_timeout(400)

                first_letter = (
                    page.query_selector(
                        ".board.board--stage .board-row:first-child .cell:first-child .cell__letter"
                    )
                    .text_content()
                    .strip()
                    .upper()
                )
                length = page.eval_on_selector_all(
                    ".board.board--stage .board-row:first-child .cell", "cells => cells.length"
                )
                key = cache_key(first_letter, length)
                entry = root_cache.get(key)
                guess = entry["word"] if entry else corpus.subset(first_letter, length)[0]
                cache_hit = entry is not None

                keyboard = {
                    (b.text_content() or "").strip(): b for b in page.query_selector_all(".keyboard button")
                }
                for letter in guess[1:]:
                    keyboard[letter].click()
                keyboard[ENTER_KEY].click()
                page.wait_for_timeout(1200)

                rejected = UNKNOWN_WORD_TOAST in page.content()
                result = {"group": key, "cache_hit": cache_hit, "guess": guess, "rejected": rejected}
                results.append(result)
                save()  # persiste après chaque partie : une erreur réseau plus loin ne perd pas ce qui précède
                status = "REJETÉ (Mot inconnu)" if rejected else "accepté"
                print(f"[{i + 1}/{args.games}] {key} -> {guess} : {status}")
                page.close()
            except Exception as exc:  # transitoire (réseau, timeout) : on garde ce qui a déjà été mesuré
                print(f"[{i + 1}/{args.games}] échec ({exc.__class__.__name__}: {exc}) — partie ignorée")
        browser.close()

    n_rejected = sum(1 for r in results if r["rejected"])
    print(f"\n{n_rejected}/{len(results)} mots du cache rejetés par le dictionnaire de validation Tuzmo (cumulé).")
    print(f"Détail : {output_path}")


if __name__ == "__main__":
    main()
