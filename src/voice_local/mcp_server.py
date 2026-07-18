"""vox-local's serving surface: the Vocal Bridge MCP backend plus the small
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
        self._refresh_task: asyncio.Task | None = None
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
            # Overlapping calls (e.g. a test call during a real one): prefer the
            # session we ALREADY hold state for — continuity beats recency; a
            # conversation must not flap to whichever call started last.
            log.warning("multiple in_progress VB sessions (%d)", len(live))
            for s in live:
                if str(s["id"]) in self._calls:
                    return str(s["id"]), str(s.get("caller_phone") or "")
        s = max(live, key=lambda s: s.get("started_at") or "")
        return str(s["id"]), str(s.get("caller_phone") or "")

    async def _resolve_call(self) -> _CallState:
        """The _CallState for the call in progress; retries cover logs-API lag on
        a call's first query; a MISS falls back to a shared 'solo' state (account+
        PIN auth still works; only the silent caller-ID match is lost).

        Once a REAL session is known, the cache is served stale-while-revalidate:
        the logs API costs ~2.5s a round trip, and paying it inline put that tax
        on every spoken turn (measured 2026-07-17: 9ms of query behind 14.7s of
        resolving). A stale hit answers now and refreshes in the background; the
        only queries that still block are the first of a call (cache empty or
        'solo', where serving stale would split one call's auth across two
        states). Back-to-back calls can land a first query or two on the old
        call's state until a refresh lands — attribution still routes by the
        caller_phone inside the query, so notes and dossiers stay correct."""
        now = time.monotonic()
        if self._resolve_cache and self._resolve_cache[1] != "solo":
            ts, sid, phone = self._resolve_cache
            if now - ts >= RESOLVE_CACHE_S:
                self._spawn_refresh()
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

    def _spawn_refresh(self) -> None:
        """Refresh the resolve cache off the query path; at most one in flight."""
        if self._refresh_task is not None and not self._refresh_task.done():
            return

        async def refresh() -> None:
            try:
                found = await self._fetch_current_session()
            except Exception as exc:  # noqa: BLE001 - background refresh must never raise
                log.warning("background session refresh failed: %r", exc)
                return
            if found is not None:
                sid, phone = found
                self._resolve_cache = (time.monotonic(), sid, phone)

        self._refresh_task = asyncio.get_running_loop().create_task(refresh())

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
                # The poll doubles as the ATTRIBUTION PING: even though the op is
                # handled at this layer, the caller still gets matched (accounts,
                # then pending) and their dossier preloaded into this very reply.
                try:
                    await state.services.attribute(
                        state.services.attribution_phone(query))
                except Exception:  # noqa: BLE001 - attribution must never break the poll
                    log.exception("attribution ping failed")
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
        if self._resolve_cache and self._resolve_cache[1] == sid:
            # The call is over: the next query must re-resolve synchronously
            # instead of riding a stale pointer to this closed state.
            self._resolve_cache = None
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
    'Tanaka","lucky":"7"} (lucky digit optional — the minted PIN then carries it at '
    'least twice) | {"op":"change_pin","new_pin":"5678"} | {"op":"search_gems","city":'
    '"kobe","query":"onsen quiet morning","tags":"onsen"} | {"op":"city_guide","city":'
    '"<city>"} (the full mental map of a city — fires automatically the first time a '
    "city appears in any query; call it explicitly the moment a city enters the "
    'conversation) | {"op":"get_gem","id":'
    '"<gem id>"} | {"op":"remember","note":"..."} | {"op":"add_gem","name":"...",'
    '"city":"kobe","pitch":"..."} | {"op":"booking_establish","location":"...",'
    '"start_date":"YYYY-MM-DD","days":3,"reason":"..."} (trip context — no thread) | '
    '{"op":"booking_request","kind":"explore|confirmed|update|canceled|booked",'
    '"title":"Shinkansen from Nagoya to Osaka","details":"..."} (ONE booking item = '
    "ONE thread named by its short human title; update/canceled/booked post the "
    'status back onto that item) | '
    '{"op":"check_updates"}. The response is authoritative guidance to act on '
    "immediately."
)


def build_app(backend: VBBackend, *, conn, version: str, vb_phone_number: str = "",
              public_host: str = "", gems_token: str = "", mcp_token: str = ""):
    """Starlette app: /mcp + /healthz + /twilio-forward + /api/gems.

    `public_host` names the tunnel hostname VB connects through (Host allowlist);
    `gems_token` guards the extension's POST /api/gems (Bearer); `mcp_token`
    guards /mcp itself — accepted as `Authorization: Bearer`, `X-API-Key`, or a
    `?key=` query param (for MCP clients that can only carry a URL)."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import HTMLResponse, JSONResponse, Response

    from voice_local import db as kbdb

    hosts = ["localhost", "127.0.0.1", "localhost:*", "127.0.0.1:*"]
    origins = ["http://localhost", "http://127.0.0.1", "https://localhost"]
    if public_host:
        hosts += [public_host, f"{public_host}:*"]
        origins += [f"https://{public_host}"]
    security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)
    mcp = FastMCP("vox-local", stateless_http=True, transport_security=security)

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
            return JSONResponse({"ok": False, "service": "vox-local",
                                 "version": version, "error": "db"}, status_code=503)
        return JSONResponse({"ok": True, "service": "vox-local", "version": version,
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

    @mcp.custom_route("/creator", methods=["GET"])
    async def creator_portal(request):  # noqa: ANN001
        """The local, single-creator workspace. Authentication is intentionally
        out of scope for this first localhost-oriented MVP."""
        from importlib.resources import files

        page = files("voice_local").joinpath("creator_portal.html").read_text()
        return HTMLResponse(page)

    @mcp.custom_route("/api/creator/dashboard", methods=["GET"])
    async def creator_dashboard(request):  # noqa: ANN001
        query = request.query_params.get("q", "")
        city = request.query_params.get("city", "")
        gems = kbdb.search_gems(conn, city=city, query=query, limit=100)
        summary = kbdb.recommendation_summary(conn, limit=12)
        for gem in gems:
            gem["recommendations"] = summary["by_gem"].get(gem["id"], 0)
        cities = [row[0] for row in conn.execute(
            "SELECT DISTINCT city FROM gems WHERE city != '' ORDER BY city")]
        return JSONResponse({
            "gems": gems,
            "stats": {"gems": conn.execute("SELECT count(*) FROM gems").fetchone()[0],
                      "recommendations": summary["total"], "cities": len(cities)},
            "cities": cities,
            "recent_events": summary["events"],
        })

    @mcp.custom_route("/api/creator/gems", methods=["POST"])
    async def creator_gems(request):  # noqa: ANN001
        try:
            body = json.loads(await request.body())
            required = {key: str(body.get(key, "")).strip()
                        for key in ("name", "city", "pitch")}
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ValueError("missing " + ", ".join(missing))
            gem = kbdb.add_gem(
                conn, name=required["name"], city=required["city"],
                pitch=required["pitch"], area=str(body.get("area", "")),
                tags=str(body.get("tags", "")), price=str(body.get("price", "")),
                booking=str(body.get("booking", "")), url=str(body.get("url", "")),
                details=str(body.get("details", "")), source="curator")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "gem": gem})

    @mcp.custom_route("/api/creator/gems/{gem_id}/events", methods=["GET"])
    async def creator_gem_events(request):  # noqa: ANN001
        gem_id = request.path_params["gem_id"]
        if kbdb.get_gem(conn, gem_id) is None:
            return JSONResponse({"error": "gem not found"}, status_code=404)
        return JSONResponse(kbdb.recommendation_summary(conn, gem_id=gem_id, limit=20))

    app = mcp.streamable_http_app()
    if not mcp_token:
        return app

    import hmac
    from urllib.parse import parse_qs

    async def token_gate(scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http" and scope.get("path", "").rstrip("/") == "/mcp":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            supplied = (auth.split(" ", 1)[1].strip()
                        if auth.lower().startswith("bearer ") else
                        headers.get("x-api-key", "").strip())
            if not supplied:
                qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
                supplied = (qs.get("key") or [""])[0]
            if not hmac.compare_digest(supplied, mcp_token):
                log.warning("mcp auth rejected (client %s)", scope.get("client"))
                await JSONResponse({"error": "unauthorized"}, status_code=401)(
                    scope, receive, send)
                return
        await app(scope, receive, send)

    return token_gate


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
