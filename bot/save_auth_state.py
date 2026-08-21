#!/usr/bin/env python3
"""Script one-shot : ouvre un navigateur visible sur Tuzmo, laisse l'utilisateur se
connecter manuellement (compte requis pour le duel classé / les stats), puis
sauvegarde la session (cookies + storage) via `storage_state` de Playwright dans un
fichier JSON réutilisable par le bot et le dashboard — évite de se reconnecter à
chaque lancement.

À relancer quand la session expire (le bot/dashboard le signale alors : les appels
API échouent ou reviennent en état anonyme) ou après une déconnexion volontaire.

    python bot/save_auth_state.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT_DIR / "data" / "tuzmo_auth_state.json"
URL = "https://www.tusmo.xyz/"


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(URL, wait_until="networkidle")

        print("Connecte-toi manuellement dans la fenêtre du navigateur (bouton")
        print('"Compte" en haut à droite), puis reviens ici et appuie sur Entrée.')
        input("Appuie sur Entrée une fois connecté... ")

        context.storage_state(path=str(output_path))
        print(f"Session sauvegardée : {output_path}")
        browser.close()


if __name__ == "__main__":
    main()
