#!/usr/bin/env python3
"""Lance le backend FastAPI du dashboard (uvicorn, local uniquement) et ouvre le
dashboard dans le navigateur par défaut. Le navigateur du dashboard (onglet normal)
et celui piloté par Playwright pour jouer (lancé par le bot via `dashboard/bot_runner.py`
quand on clique "Démarrer") sont deux instances Chromium séparées — aucun conflit,
le second est lancé par Playwright indépendamment du navigateur système.

    python scripts/run_dashboard.py
"""
from __future__ import annotations

import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "dashboard"))

HOST = "127.0.0.1"
PORT = 8765


def open_browser_when_ready() -> None:
    time.sleep(1.0)
    webbrowser.open(f"http://{HOST}:{PORT}")


def main() -> None:
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    print(f"Dashboard : http://{HOST}:{PORT}  (local uniquement, Ctrl+C pour arrêter)")
    uvicorn.run("backend:app", host=HOST, port=PORT, app_dir=str(ROOT_DIR / "dashboard"), log_level="warning")


if __name__ == "__main__":
    main()
