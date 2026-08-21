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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bot_runner import DEFAULT_AUTH_STATE, DEFAULT_LOG, runner  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Motus bot dashboard")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/start")
def start() -> JSONResponse:
    started = runner.start()
    return JSONResponse({"started": started, "status": runner.status})


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
        }
    )


@app.get("/api/logs")
def logs(limit: int = 100) -> JSONResponse:
    if not DEFAULT_LOG.exists():
        return JSONResponse([])
    lines = DEFAULT_LOG.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines[-limit:] if line.strip()]
    return JSONResponse(records)


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
