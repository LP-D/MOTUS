#!/usr/bin/env python3
"""Boucle d'entraînement automatisée : enchaîne N parties consécutives sur
/infinite sans intervention manuelle (tâche 3), en ligne de commande (sans lancer
le dashboard). Réutilise directement `dashboard.bot_runner.BotRunner` — même
logique, mêmes événements, mêmes stats persistées (`data/dashboard_stats.json`,
partagées avec le dashboard : les deux accumulent dans le même store).

Interruptible proprement (Ctrl+C) : la partie en cours se termine avant l'arrêt (le
`_stop_event` est vérifié entre les coups et entre les parties, jamais en milieu de
frappe), donc jamais de stats corrompues — cf. `BotRunner._play_one_game`.

    python scripts/run_training_loop.py --iterations 100
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "dashboard"))

from bot_runner import BotRunner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iterations", type=int, default=10, help="Nombre de parties à enchaîner.")
    args = parser.parse_args()

    runner = BotRunner()
    runner.start(iterations=args.iterations)
    print(f"Boucle lancée : {args.iterations} partie(s). Ctrl+C pour arrêter proprement.")

    try:
        while runner.is_running() or not runner.events.empty():
            try:
                event = runner.events.get(timeout=0.5)
            except Exception:
                continue
            _print_event(event)
    except KeyboardInterrupt:
        print("\nArrêt demandé (Ctrl+C) — fin de la partie en cours avant arrêt...")
        runner.stop()
        while runner.is_running():
            try:
                event = runner.events.get(timeout=0.5)
                _print_event(event)
            except Exception:
                continue

    print(f"\nTerminé. Statut final : {runner.status}. Stats cumulées : data/dashboard_stats.json")


def _print_event(event: dict) -> None:
    t = time.strftime("%H:%M:%S", time.localtime(event.get("timestamp", time.time())))
    kind = event.get("type")
    if kind == "loop_progress":
        print(f"[{t}] partie {event['current']}/{event['total']}")
    elif kind == "solved":
        print(f"[{t}]   résolu en {event['attempts']} coup(s) : {event['solution']}")
    elif kind in ("not_solved", "candidates_exhausted"):
        print(f"[{t}]   {kind}")
    elif kind == "guess_rejected":
        print(f"[{t}]   rejeté : {event['guess']}")
    elif kind == "error":
        print(f"[{t}]   ERREUR : {event['message']}")
    elif kind == "loop_finished":
        print(f"[{t}] boucle terminée : {event['completed']}/{event['total']} partie(s)")


if __name__ == "__main__":
    main()
