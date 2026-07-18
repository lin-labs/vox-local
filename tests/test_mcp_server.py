"""VBBackend session keying + drain semantics, and the HTTP surface
(/healthz, /twilio-forward, /api/gems auth)."""

from __future__ import annotations

import asyncio
import json

from voice_local import db, mcp_server
from voice_local.mcp_server import VBBackend, _op_of


class FakeServices:
    def __init__(self, caller_id, send):
        self.caller_id = caller_id
        self.send = send
        self.queries = []
        self.attributed = []
        self.closed = False

    async def query(self, query):
        self.queries.append(query)
        return "ok-reply"

    def attribution_phone(self, query):
        import json as _json
        try:
            return str(_json.loads(query).get("caller_phone", "") or "")
        except ValueError:
            return ""

    async def attribute(self, phone=""):
        self.attributed.append(phone)

    async def close(self):
        self.closed = True


def make_backend(session):
    made = []

    def factory(*, caller_id, send):
        s = FakeServices(caller_id, send)
        made.append(s)
        return s

    backend = VBBackend(services_factory=factory, api_key="k", agent_id="a")

    async def fetch():
        return session

    backend._fetch_current_session = fetch
    return backend, made


def test_op_of():
    assert _op_of('{"op":"check_updates"}') == "check_updates"
    assert _op_of("junk") == ""


def test_session_keying_and_caller_id():
    backend, made = make_backend(("vb1", "+16506567722"))
    assert asyncio.run(backend.query('{"op":"verify","pin":"4242"}')) == "ok-reply"
    asyncio.run(backend.query('{"op":"search_gems"}'))
    assert len(made) == 1 and made[0].caller_id == "+16506567722"


def test_solo_fallback(monkeypatch):
    monkeypatch.setattr(mcp_server, "RESOLVE_RETRIES", 1)
    monkeypatch.setattr(mcp_server, "RESOLVE_RETRY_DELAY_S", 0.0)
    backend, made = make_backend(None)
    asyncio.run(backend.query('{"op":"verify","pin":"1"}'))
    assert "solo" in backend._calls and made[0].caller_id == ""


def test_drain_and_check_updates():
    backend, made = make_backend(("s1", ""))

    async def run():
        await backend.query('{"op":"verify","pin":"4242"}')
        empty = await backend.query('{"op":"check_updates"}')
        await made[0].send("booking_update", {"ok": True, "text": "table booked 7pm"})
        got = await backend.query('{"op":"check_updates"}')
        return empty, got

    empty, got = asyncio.run(run())
    assert "SILENT" in empty
    assert "table booked 7pm" in got and "[Booking update" in got
    assert made[0].queries == ['{"op":"verify","pin":"4242"}']


def test_stale_cache_serves_now_and_refreshes_in_background(monkeypatch):
    """After the cache goes stale, a query must NOT block on the logs API: it
    answers from the stale (sid, phone) and a background refresh updates the
    cache for later queries."""
    monkeypatch.setattr(mcp_server, "RESOLVE_CACHE_S", 0.0)  # instantly stale
    backend, made = make_backend(("vb1", "+16506567722"))
    fetches = []
    real_fetch = backend._fetch_current_session

    async def counting_fetch():
        fetches.append(1)
        return ("vb2", "+15550001111") if len(fetches) > 1 else await real_fetch()

    backend._fetch_current_session = counting_fetch

    async def run():
        await backend.query('{"op":"verify","pin":"1"}')      # cold: blocks, fetch #1
        await backend.query('{"op":"search_gems"}')           # stale: serves vb1 now
        assert "vb1" in backend._calls and "vb2" not in backend._calls
        await backend._refresh_task                            # let the refresh land
        assert backend._resolve_cache[1] == "vb2"
        await backend.query('{"op":"get_gem","id":"g1"}')     # rides refreshed cache
        assert "vb2" in backend._calls

    asyncio.run(run())
    assert len(fetches) >= 2


def test_close_call_invalidates_resolve_cache():
    backend, made = make_backend(("vb1", "+16506567722"))

    async def run():
        await backend.query('{"op":"verify","pin":"1"}')
        assert backend._resolve_cache[1] == "vb1"
        await backend._close_call("vb1")
        assert backend._resolve_cache is None and made[0].closed

    asyncio.run(run())


def test_http_surface(tmp_path):
    from starlette.testclient import TestClient

    conn = db.connect(tmp_path / "bag.db")
    db.add_gem(conn, name="Kin no Yu", city="kobe", pitch="Gold water.")
    backend, _ = make_backend(None)
    app = mcp_server.build_app(backend, conn=conn, version="test",
                               vb_phone_number="+14849905902",
                               public_host="example.ngrok-free.dev",
                               gems_token="tok123")
    with TestClient(app) as client:
        health = client.get("/healthz").json()
        assert health["ok"] is True and health["gems"] == 1

        twiml = client.post("/twilio-forward")
        assert "<Dial>+14849905902</Dial>" in twiml.text

        # extension endpoint: auth required for POST, open for GET
        assert client.post("/api/gems", json={}).status_code == 401
        r = client.post("/api/gems", headers={"Authorization": "Bearer tok123"},
                        content=json.dumps({"name": "Sky Bar", "city": "kobe",
                                            "pitch": "Rooftop.", "url": "https://x.com"}))
        assert r.status_code == 200 and r.json()["gem"]["source"] == "web-extension"
        got = client.get("/api/gems?city=kobe").json()
        assert got["count"] == 2


def test_creator_portal_and_api(tmp_path):
    from starlette.testclient import TestClient

    conn = db.connect(tmp_path / "bag.db")
    seed = db.add_gem(conn, name="Kin no Yu", city="kobe", pitch="Gold water.")
    db.record_recommendation(conn, gem_id=seed["id"], city="kobe",
                             context="Voice guide detail requested")
    backend, _ = make_backend(None)
    app = mcp_server.build_app(backend, conn=conn, version="test")
    with TestClient(app) as client:
        page = client.get("/creator")
        assert page.status_code == 200 and "Field Notes" in page.text
        dashboard = client.get("/api/creator/dashboard").json()
        assert dashboard["stats"] == {"gems": 1, "recommendations": 1, "cities": 1}
        assert dashboard["gems"][0]["recommendations"] == 1
        created = client.post("/api/creator/gems", json={
            "name": "Moon Bar", "city": "Kobe", "pitch": "A small nightcap.",
            "tags": "bar,night",
        })
        assert created.status_code == 200 and created.json()["gem"]["source"] == "curator"
        assert client.get(f"/api/creator/gems/{seed['id']}/events").json()["total"] == 1


def test_mcp_token_gate(tmp_path):
    from starlette.testclient import TestClient

    conn = db.connect(tmp_path / "bag.db")
    backend, _ = make_backend(None)
    app = mcp_server.build_app(backend, conn=conn, version="test",
                               public_host="example.ngrok-free.dev",
                               mcp_token="sekret42")
    with TestClient(app) as client:
        # healthz stays open
        assert client.get("/healthz").json()["ok"] is True
        # /mcp: no credential -> 401; wrong credential -> 401
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer nope"},
                           json={}).status_code == 401
        # right credential in any of the three shapes -> passes the gate
        # (the MCP transport then rejects the bad payload, but NOT with a 401)
        for kwargs in ({"headers": {"Authorization": "Bearer sekret42"}},
                       {"headers": {"X-API-Key": "sekret42"}},
                       {"params": {"key": "sekret42"}}):
            assert client.post("/mcp", json={}, **kwargs).status_code != 401
