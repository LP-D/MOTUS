"""Backend FastAPI local du dashboard : start/stop/status/logs pour le bot, plus un
WebSocket qui pousse les événements en temps réel (coup joué, feedback, latence par
étape) au frontend. Local uniquement — pas d'exposition réseau, pas d'auth (mono-
utilisateur), lancé via `scripts/run_dashboard.py`.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bot_config import config as bot_config  # noqa: E402
from bot_runner import DEFAULT_AUTH_STATE, DEFAULT_LOG, runner  # noqa: E402
from stats_store import aggregate as aggregate_stats  # noqa: E402
from stats_store import aggregate_solutions_report  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Motus bot dashboard")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


class StartPayload(BaseModel):
    iterations: int = Field(default=1, ge=1, le=100000)


@app.post("/api/start")
def start(payload: StartPayload = StartPayload()) -> JSONResponse:
    """`iterations` > 1 enchaîne N parties consécutives sans intervention manuelle
    (tâche 3) — 1 (défaut) reproduit le comportement "une partie" d'origine."""
    started = runner.start(iterations=payload.iterations)
    return JSONResponse({"started": started, "status": runner.status, "total_iterations": payload.iterations})


@app.post("/api/stop")
def stop() -> JSONResponse:
    stop_requested = runner.stop()
    return JSONResponse({"stop_requested": stop_requested, "status": runner.status})


@app.get("/api/status")
def status() -> JSONResponse:
    return JSONResponse(
        {
            "status": runner.status,
            "running": runner.is_running(),
            "auth_state_present": DEFAULT_AUTH_STATE.exists(),
            "current_iteration": runner.current_iteration,
            "total_iterations": runner.total_iterations,
        }
    )


@app.get("/api/logs")
def logs(limit: int = 100) -> JSONResponse:
    if not DEFAULT_LOG.exists():
        return JSONResponse([])
    lines = DEFAULT_LOG.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines[-limit:] if line.strip()]
    return JSONResponse(records)


class TypingDelayPayload(BaseModel):
    letter_delay_min_ms: float = Field(ge=0)
    letter_delay_max_ms: float = Field(ge=0)


@app.get("/bot/config/typing_delay")
def get_typing_delay() -> JSONResponse:
    return JSONResponse(bot_config.as_dict())


@app.post("/bot/config/typing_delay")
def set_typing_delay(payload: TypingDelayPayload) -> JSONResponse:
    """Applique un nouveau délai anti-détection entre chaque lettre tapée, sans
    redémarrage : le bot en cours (thread séparé) le relit avant le PROCHAIN coup
    (cf. bot_runner.py — jamais en cours de frappe)."""
    try:
        bot_config.set_letter_delay_ms(payload.letter_delay_min_ms, payload.letter_delay_max_ms)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(bot_config.as_dict())


@app.get("/bot/stats")
def stats() -> JSONResponse:
    return JSONResponse(aggregate_stats())


@app.get("/bot/stats/solutions")
def stats_solutions() -> JSONResponse:
    return JSONResponse(aggregate_solutions_report())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            # queue.Queue.get() est bloquant : on le déporte dans un thread pour ne
            # pas geler la boucle asyncio d'uvicorn.
            event = await asyncio.to_thread(runner.events.get)
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
