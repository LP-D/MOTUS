"""Instrumentation réseau passive des appels API Tuzmo (`/api/game...`) vus par la
page Playwright : pour chaque requête, horodatage d'envoi, code HTTP, latence,
en-têtes de limitation (Retry-After / X-RateLimit-*) et corps de réponse.

Lecture seule : n'envoie aucune requête, ne modifie pas le déroulement du jeu —
sert de source de vérité (ce que le SERVEUR a réellement répondu) face à
l'interprétation DOM du bot, et de détecteur de throttling.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from playwright.sync_api import Page, Request, Response

# Tous les appels API (pas seulement /api/game) : le 429 "guest creation rate
# limited" observé le 23/09/2026 arrivait d'abord sur GET /api/me.
API_MARKER = "/api/"
RATE_LIMIT_HEADER_PREFIXES = ("x-ratelimit", "retry-after")
# Seuils d'arrêt d'urgence (contrainte utilisateur) : tout signal de throttling
# arrête la boucle, distinctement d'un échec de correction.
THROTTLE_STATUS_CODES = {429}
# 5 s depuis le 24/09/2026 (consigne : "latence anormale > 5-10 s") ; latence API
# maximale jamais mesurée : 1,08 s en jeu, 0,49 s sur 2 000 requêtes de validation.
THROTTLE_LATENCY_S = 5.0


@dataclass
class ApiCall:
    kind: str  # "guess" | "create_session" | "other"
    method: str
    url: str
    guess: str | None
    t_sent: float
    t_received: float | None = None
    status: int | None = None
    rate_limit_headers: dict[str, str] = field(default_factory=dict)
    body: dict | None = None
    failure: str | None = None
    _response: Response | None = field(default=None, repr=False)

    @property
    def latency_s(self) -> float | None:
        if self.t_received is None:
            return None
        return round(self.t_received - self.t_sent, 4)

    @property
    def server_error(self) -> str | None:
        return (self.body or {}).get("error")

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "method": self.method,
            "url": self.url,
            "guess": self.guess,
            "t_sent": self.t_sent,
            "t_received": self.t_received,
            "latency_s": self.latency_s,
            "status": self.status,
            "rate_limit_headers": self.rate_limit_headers,
            "server_error": self.server_error,
            "server_result": result_from_body(self.body),
            "failure": self.failure,
        }


def result_from_body(body: dict | None) -> list[str] | None:
    """Extrait la liste "correct"/"present"/"absent" d'une réponse /guess acceptée.

    Formats observés : `result` au premier niveau, ou dernier élément de
    `session.guesses[].result`. None si le coup n'a pas été accepté."""
    if not body or body.get("error"):
        return None
    if isinstance(body.get("result"), list):
        return body["result"]
    guesses = (body.get("session") or {}).get("guesses") or body.get("guesses") or []
    if guesses and isinstance(guesses[-1], dict) and isinstance(guesses[-1].get("result"), list):
        return guesses[-1]["result"]
    return None


def classify_kind(method: str, url: str) -> str:
    path = url.split("?", 1)[0]
    if method == "POST" and path.endswith("/guess"):
        return "guess"
    if method == "POST" and path.endswith("/api/game"):
        return "create_session"
    if method == "POST" and path.endswith("/giveup"):
        return "give_up"
    if method == "POST" and path.endswith("/reset"):
        return "reset"
    return "other"


# Requêtes qui font avancer une partie : comptées dans l'écart minimal entre requêtes.
GAME_ACTION_KINDS = {"guess", "give_up", "reset"}


def session_from_body(body: dict | None) -> dict | None:
    """Objet session d'une réponse /api/game (au premier niveau ou sous `session`)."""
    if not isinstance(body, dict):
        return None
    session = body.get("session", body)
    return session if isinstance(session, dict) and ("id" in session or "firstLetter" in session) else None


def is_throttle_signal(call: ApiCall) -> str | None:
    """Raison lisible si cet appel constitue un signal de throttling, sinon None."""
    if call.status in THROTTLE_STATUS_CODES:
        return f"HTTP {call.status}"
    if any(k.lower() == "retry-after" for k in call.rate_limit_headers):
        return "en-tête Retry-After présent"
    if call.latency_s is not None and call.latency_s > THROTTLE_LATENCY_S:
        return f"latence {call.latency_s:.1f}s > {THROTTLE_LATENCY_S:.0f}s"
    return None


class NetworkMonitor:
    def __init__(self, page: Page):
        self.calls: list[ApiCall] = []
        self._by_request: dict[int, ApiCall] = {}
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    def _on_request(self, request: Request) -> None:
        if API_MARKER not in request.url:
            return
        guess = None
        try:
            payload = json.loads(request.post_data or "null")
            if isinstance(payload, dict):
                guess = payload.get("guess")
        except (json.JSONDecodeError, TypeError):
            pass
        call = ApiCall(
            kind=classify_kind(request.method, request.url),
            method=request.method,
            url=request.url,
            guess=guess,
            t_sent=time.time(),
        )
        self.calls.append(call)
        self._by_request[id(request)] = call

    def _on_response(self, response: Response) -> None:
        call = self._by_request.get(id(response.request))
        if call is None:
            return
        call.t_received = time.time()
        call.status = response.status
        call.rate_limit_headers = {
            k: v for k, v in response.headers.items() if k.lower().startswith(RATE_LIMIT_HEADER_PREFIXES)
        }
        # Le corps est lu plus tard (resolve_bodies), hors du handler d'événement.
        call._response = response

    def _on_request_failed(self, request: Request) -> None:
        call = self._by_request.get(id(request))
        if call is not None:
            call.failure = request.failure or "failed"

    def resolve_bodies(self) -> None:
        for call in self.calls:
            if call._response is not None and call.body is None:
                try:
                    call.body = call._response.json()
                except Exception:  # corps non-JSON ou indisponible
                    call.body = {}

    def guess_calls_since(self, t: float) -> list[ApiCall]:
        self.resolve_bodies()
        return [c for c in self.calls if c.kind == "guess" and c.t_sent >= t]

    def last_guess_sent_at(self) -> float | None:
        """Dernier envoi d'une requête de jeu (coup, abandon, reset)."""
        sent = [c.t_sent for c in self.calls if c.kind in GAME_ACTION_KINDS]
        return max(sent) if sent else None

    def first_throttle_signal(self) -> tuple[ApiCall, str] | None:
        self.resolve_bodies()
        for call in self.calls:
            reason = is_throttle_signal(call)
            if reason:
                return call, reason
        return None
