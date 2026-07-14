"""voice-local's serving surface: the Vocal Bridge MCP backend plus the small
HTTP API the browser extension and ops tooling use.

Endpoints on one port:
  /mcp            — stateless streamable-HTTP MCP, tool `query_backend` (VB's
                    background AI opens a FRESH MCP session per query, so state
                    is keyed by the VB logs API's in_progress session — which is
                    also the ONLY source of caller identity; no metadata arrives
                    in MCP headers).
  /healthz        — Lab Service Protocol liveness (real DB check).
  /twilio-forward — TwiML <Dial> so the legacy Twilio number forwards straight
                    to the agent's own VB number (pure PSTN, no media bridging).
  /api/gems       — POST (token-authed): the web extension adds a hidden gem;
                    GET: list gems (?city=) for quick inspection.

Wire facts inherited from the voxcall probe (2026-07-13): MCP tools execute via
VB's background AI only (background_enabled=true); the MCP python SDK's
DNS-rebinding guard 421s ngrok-preserved Host headers, so the public hostname
must be allowlisted; out-of-band pushes (caller_context / booking_update) have
no channel on the phone path and instead drain into the next tool reply, with
{"op":"check_updates"} as the explicit (usually SILENT) poll.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import httpx

log = logging.getLogger("voice_local.mcp_server")

VB_API_BASE = "https://vocalbridgeai.com"
SESSION_TTL_S = 45 * 60
RESOLVE_CACHE_S = 2.0
RESOLVE_RETRIES = 5
RESOLVE_RETRY_DELAY_S = 0.8


class _CallState:
    """One live phone call: its CallServices plus the pending out-of-band
    payloads (caller_context / booking_update) waiting to ride the next reply."""

    def __init__(self, services, caller_phone: str) -> None:
        self.services = services
        self.caller_phone = caller_phone
        self.pending: list[tuple[str, dict]] = []
        self.last_seen = time.monotonic()


class VBBackend:
    """Session registry + query entry for the MCP tool. `services_factory`
    builds a CallServices given (caller_id, send)."""

    def __init__(self, *, services_factory, api_key: str, agent_id: str) -> None:
        self._services_factory = services_factory
        self._api_key = api_key
        self._agent_id = agent_id
        self._calls: dict[str, _CallState] = {}
        self._resolve_cache: tuple[float, str, str] | None = None
        self._lock = asyncio.Lock()

    @property
    def live_calls(self) -> int:
        return len(self._calls)

    def _vb_headers(self) -> dict:
        headers = {"X-API-Key": self._api_key}
        if self._agent_id:
            headers["X-Agent-Id"] = self._agent_id
        return headers

    async def _fetch_current_session(self) -> tuple[str, str] | None:
        """Newest in_progress VB session -> (session_id, caller_phone)."""
        async with httpx.AsyncClient(timeout=8.0) as http:
            resp = await http.get(f"{VB_API_BASE}/api/v1/logs?limit=5",
                                  headers=self._vb_headers())
            resp.raise_for_status()
            sessions = resp.json().get("sessions", [])
        live = [s for s in sessions if s.get("status") == "in_progress"]
        if not live:
            return None
        if len(live) > 1:
            log.warning("multiple in_progress VB sessions (%d) — using newest", len(live))
        s = max(live, key=lambda s: s.get("started_at") or "")
        return str(s["id"]), str(s.get("caller_phone") or "")

    async def _resolve_call(self) -> _CallState:
        """The _CallState for the call in progress; retries cover logs-API lag on
        a call's first query; a MISS falls back to a shared 'solo' state (account+
        PIN auth still works; only the silent caller-ID match is lost)."""
        now = time.monotonic()
        if self._resolve_cache and now - self._resolve_cache[0] < RESOLVE_CACHE_S:
            _, sid, phone = self._resolve_cache
            return self._state_for(sid, phone)
        for attempt in range(RESOLVE_RETRIES):
            try:
                found = await self._fetch_current_session()
            except Exception as exc:  # noqa: BLE001 - logs API down must not kill the call
                log.warning("VB logs API failed (%r) — attempt %d", exc, attempt + 1)
                found = None
            if found is not None:
                sid, phone = found
                self._resolve_cache = (time.monotonic(), sid, phone)
                return self._state_for(sid, phone)
            await asyncio.sleep(RESOLVE_RETRY_DELAY_S)
        log.warning("no in_progress VB session resolved — using shared 'solo' state")
        return self._state_for("solo", "")

    def _state_for(self, sid: str, phone: str) -> _CallState:
        state = self._calls.get(sid)
        if state is None:
            log.info("new call state: vb_session=%s caller=%s", sid[:12], phone or "?")

            async def send(action: str, payload: dict) -> None:
                self._calls[sid].pending.append((action, payload))

            services = self._services_factory(caller_id=phone, send=send)
            state = _CallState(services, phone)
            self._calls[sid] = state
        state.last_seen = time.monotonic()
        return state

    # ---- the tool -------------------------------------------------------------

    async def query(self, query: str) -> str:
        async with self._lock:   # one call at a time; keeps store/puffo writes serial
            await self._reap()
            state = await self._resolve_call()
            op = _op_of(query)
            if op == "check_updates":
                reply = ""
            else:
                try:
                    reply = await state.services.query(query)
                except Exception:  # noqa: BLE001 - a bad query must never 500 the tool
                    log.exception("backend query failed: %s", query[:200])
                    reply = "backend error — apologize briefly and continue the conversation."
            drained = self._drain(state)
            if op == "check_updates" and not drained:
                return ("SILENT: no booking updates yet. If the caller JUST asked for "
                        "status, say it's still pending with the partner; otherwise say "
                        "absolutely nothing about this and continue naturally.")
            return "\n".join(filter(None, [reply, drained]))

    def _drain(self, state: _CallState) -> str:
        parts = []
        for action, payload in state.pending:
            if action == "caller_context":
                brief = str(payload.get("brief") or "").strip()
                trip = str(payload.get("trip_summary") or "").strip()
                if brief or trip:
                    parts.append("[Caller context — use silently, never re-ask what it "
                                 "answers]\n" + "\n".join(filter(None, [brief, trip])))
            elif action == "booking_update":
                parts.append(f"[Booking update — relay to the caller naturally, once]: "
                             f"{payload.get('text', '')}")
            else:
                hint = payload.get("say_hint") or json.dumps(payload)[:400]
                parts.append(str(hint))
        state.pending.clear()
        return "\n".join(parts)

    # ---- lifecycle ---------------------------------------------------------------

    async def _reap(self) -> None:
        cutoff = time.monotonic() - SESSION_TTL_S
        for sid in [s for s, st in self._calls.items() if st.last_seen < cutoff]:
            await self._close_call(sid)

    async def _close_call(self, sid: str) -> None:
        state = self._calls.pop(sid, None)
        if state is None:
            return
        log.info("closing call state %s (caller=%s)", sid[:12], state.caller_phone or "?")
        try:
            await state.services.close()   # cancels watchers + posts [booking-itinerary]
        except Exception:  # noqa: BLE001 - teardown must not raise
            log.exception("closing call %s failed", sid[:12])

    async def _session_status(self, sid: str) -> str:
        async with httpx.AsyncClient(timeout=8.0) as http:
            resp = await http.get(f"{VB_API_BASE}/api/v1/logs/{sid}",
                                  headers=self._vb_headers())
            resp.raise_for_status()
            return str(resp.json().get("status", ""))

    async def call_end_reaper(self, poll_s: float = 30.0) -> None:
        """Close call state (posting its booking itinerary) promptly after the VB
        session completes — the TTL reaper alone would sit on it for 45 minutes."""
        while True:
            await asyncio.sleep(poll_s)
            for sid in [s for s in self._calls if s != "solo"]:
                try:
                    status = await self._session_status(sid)
                except Exception as exc:  # noqa: BLE001 - poller must survive API blips
                    log.warning("call-end poll failed for %s: %r", sid[:12], exc)
                    continue
                if status and status != "in_progress":
                    async with self._lock:
                        await self._close_call(sid)

    async def close(self) -> None:
        for sid in list(self._calls):
            await self._close_call(sid)


def _op_of(query: str) -> str:
    try:
        return str(json.loads(query).get("op", "")).strip().lower()
    except (ValueError, TypeError, AttributeError):
        return ""


TOOL_DESCRIPTION = (
    "Backend concierge system for account verification, registration, PIN changes, "
    "local hidden-gem knowledge base, memory notes, and bookings. Send ONE JSON object "
    'as a string, e.g. {"op":"verify","pin":"4242"} | {"op":"register","name":"Kenji '
    'Tanaka"} | {"op":"change_pin","new_pin":"5678"} | {"op":"search_gems","city":'
    '"kobe","query":"onsen quiet morning","tags":"onsen"} | {"op":"get_gem","id":'
    '"<gem id>"} | {"op":"remember","note":"..."} | {"op":"add_gem","name":"...",'
    '"city":"kobe","pitch":"..."} | {"op":"booking_establish","location":"...",'
    '"start_date":"YYYY-MM-DD","days":3,"reason":"..."} | {"op":"booking_request",'
    '"kind":"explore|confirmed|update|canceled","details":"..."} | '
    '{"op":"check_updates"}. The response is authoritative guidance to act on '
    "immediately."
)


def build_app(backend: VBBackend, *, conn, version: str, vb_phone_number: str = "",
              public_host: str = "", gems_token: str = ""):
    """Starlette app: /mcp + /healthz + /twilio-forward + /api/gems.

    `public_host` names the tunnel hostname VB connects through (Host allowlist);
    `gems_token` guards the extension's POST /api/gems (Bearer)."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse, Response

    from voice_local import db as kbdb

    hosts = ["localhost", "127.0.0.1", "localhost:*", "127.0.0.1:*"]
    origins = ["http://localhost", "http://127.0.0.1", "https://localhost"]
    if public_host:
        hosts += [public_host, f"{public_host}:*"]
        origins += [f"https://{public_host}"]
    security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)
    mcp = FastMCP("voice-local", stateless_http=True, transport_security=security)

    @mcp.tool(description=TOOL_DESCRIPTION)
    async def query_backend(query: str) -> str:  # noqa: ANN001
        log.info("query_backend <- %s", query[:300])
        reply = await backend.query(query)
        log.info("query_backend -> %s", reply[:300])
        return reply

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):  # noqa: ANN001
        try:
            n_gems = conn.execute("SELECT count(*) FROM gems").fetchone()[0]
        except Exception:  # noqa: BLE001 - a broken DB is exactly what healthz reports
            return JSONResponse({"ok": False, "service": "voice-local",
                                 "version": version, "error": "db"}, status_code=503)
        return JSONResponse({"ok": True, "service": "voice-local", "version": version,
                             "gems": n_gems, "live_calls": backend.live_calls})

    @mcp.custom_route("/twilio-forward", methods=["GET", "POST"])
    async def twilio_forward(request):  # noqa: ANN001
        if not vb_phone_number:
            return Response("forwarding unconfigured", status_code=503)
        twiml = (f'<?xml version="1.0" encoding="UTF-8"?>'
                 f"<Response><Dial>{vb_phone_number}</Dial></Response>")
        return Response(twiml, media_type="text/xml")

    @mcp.custom_route("/api/gems", methods=["GET", "POST", "OPTIONS"])
    async def api_gems(request):  # noqa: ANN001
        cors = {"Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS"}
        if request.method == "OPTIONS":
            return Response("", headers=cors)
        if request.method == "GET":
            gems = kbdb.search_gems(conn, city=request.query_params.get("city", ""),
                                    query=request.query_params.get("q", ""), limit=50)
            return JSONResponse({"count": len(gems), "gems": gems}, headers=cors)
        auth = request.headers.get("authorization", "")
        if not gems_token or auth != f"Bearer {gems_token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=cors)
        try:
            body = json.loads(await request.body())
            gem = kbdb.add_gem(
                conn, name=str(body["name"]), city=str(body["city"]),
                pitch=str(body["pitch"]), area=str(body.get("area", "")),
                tags=str(body.get("tags", "")), price=str(body.get("price", "")),
                booking=str(body.get("booking", "")), url=str(body.get("url", "")),
                details=str(body.get("details", "")), source="web-extension")
        except (KeyError, ValueError, TypeError) as exc:
            return JSONResponse({"error": f"bad gem payload: {exc}"},
                                status_code=400, headers=cors)
        log.info("gem added via extension: %s (%s)", gem["id"], gem.get("url") or "no url")
        return JSONResponse({"ok": True, "gem": gem}, headers=cors)

    return mcp.streamable_http_app()


# ---- systemd notify (Lab Service Protocol: Type=notify + watchdog) ------------------


def sd_notify(message: str) -> None:
    path = os.environ.get("NOTIFY_SOCKET", "")
    if not path:
        return
    import socket

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            if path.startswith("@"):
                path = "\0" + path[1:]
            sock.connect(path)
            sock.send(message.encode())
    except OSError as exc:
        log.warning("sd_notify failed: %r", exc)


async def watchdog_task(interval_s: float = 10.0) -> None:
    sd_notify("READY=1")
    while True:
        await asyncio.sleep(interval_s)
        sd_notify("WATCHDOG=1")
