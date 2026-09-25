#!/usr/bin/env python3
"""Lanceur unifié : backend FastAPI du dashboard (uvicorn, local uniquement) +
ouverture du dashboard dans le navigateur, et, sur demande, démarrage du bot dans
le mode choisi. Le navigateur du dashboard (onglet normal) et celui piloté par
Playwright pour jouer (lancé par le bot via `dashboard/bot_runner.py`) sont deux
instances Chromium séparées — aucun conflit.

    python scripts/run_dashboard.py                                  # dashboard seul
    python scripts/run_dashboard.py --mode infinite --games 10 --autostart
    python scripts/run_dashboard.py --mode daily --autostart         # partie du jour

Modes : infinite (défaut) et daily (une partie par jour). Le mode classé n'est pas
automatisé (duels contre de vrais joueurs, cf. bot/modes.py).
`--autostart` passe par la même API que le bouton « Démarrer » (POST /api/start) :
mêmes garde-fous (débit, arrêt d'urgence, une partie quotidienne par jour).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "dashboard"))
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))

from bot.modes import SUPPORTED_MODES  # noqa: E402
from motus_solver.cache import DEFAULT_STRATEGY, STRATEGIES  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765


def wait_until_ready(timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/status", timeout=2):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def autostart(mode: str, games: int, strategy: str) -> None:
    if not wait_until_ready():
        print("Démarrage automatique annulé : le dashboard ne répond pas.")
        return
    payload = json.dumps({"mode": mode, "iterations": games, "strategy": strategy}).encode("utf-8")
    request = urllib.request.Request(f"http://{HOST}:{PORT}/api/start", data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("started"):
        print(f"Bot démarré : mode {body['mode']}, {body['total_iterations']} partie(s), stratégie {body['strategy']}.")
    else:
        print(f"Bot non démarré : {body.get('error') or body.get('status')}")


def open_browser_when_ready() -> None:
    if wait_until_ready():
        webbrowser.open(f"http://{HOST}:{PORT}")


def main() -> None:
    global PORT
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=[m.value for m in SUPPORTED_MODES], default="infinite")
    parser.add_argument("--games", type=int, default=1, help="Parties à enchaîner (quotidien : toujours 1).")
    parser.add_argument("--strategy", choices=STRATEGIES, default=DEFAULT_STRATEGY)
    parser.add_argument("--autostart", action="store_true", help="Démarre le bot dès que le dashboard est prêt.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=PORT, help="Port local (8765 par défaut).")
    args = parser.parse_args()
    PORT = args.port

    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, daemon=True).start()
    if args.autostart:
        threading.Thread(target=autostart, args=(args.mode, max(1, args.games), args.strategy), daemon=True).start()
    print(f"Dashboard : http://{HOST}:{PORT}  (local uniquement, Ctrl+C pour arrêter)")
    uvicorn.run("backend:app", host=HOST, port=PORT, app_dir=str(ROOT_DIR / "dashboard"), log_level="warning")


if __name__ == "__main__":
    main()
