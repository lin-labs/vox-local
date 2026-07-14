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
        self.closed = False

    async def query(self, query):
        self.queries.append(query)
        return "ok-reply"

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
