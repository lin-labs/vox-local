"""CallServices op grammar: auth flows, SQLite KB ops, booking round trips.

The fake puffo binary pattern is inherited from voxcall's suite — one-shot
subcommands log argv, `send` prints the envelope line, `listen` tails a file.
"""

from __future__ import annotations

import asyncio
import json
import stat

import pytest

from voice_local import db
from voice_local.accounts import Account, AccountStore
from voice_local.puffo import PuffoClient, PuffoListener
from voice_local.services import CallServices, normalize_start_date

BOYAN = "+16506567722"
FULFILLER = "edwinb-bronze-355b"

FAKE_PUFFO = """#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
def log_argv():
    log = os.environ.get("FAKE_PUFFO_SEND_LOG", "")
    n = 0
    if log:
        n = sum(1 for _ in open(log)) if os.path.exists(log) else 0
        with open(log, "a") as f:
            f.write(json.dumps(args) + "\\n")
    return n
if "channel" in args and "create" in args:
    log_argv(); print("created channel: ch_fake-user-0001")
elif "invitation" in args:
    log_argv(); print("invitation sent")
elif "send" in args:
    n = log_argv(); print(f"sent: msg_fake{n:04d} (2 devices)")
elif "history" in args:
    log_argv(); print("")
elif "listen" in args:
    path = os.environ.get("FAKE_PUFFO_EVENTS", "")
    seen = 0
    deadline = time.time() + 5
    while time.time() < deadline:
        if path and os.path.exists(path):
            lines = open(path).read().splitlines()
            for line in lines[seen:]:
                print(line, flush=True)
            seen = len(lines)
        time.sleep(0.02)
"""


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "bag.db")
    db.add_gem(c, name="Kin no Yu", city="kobe", pitch="Gold water at 8am, 650 yen.",
               tags="onsen,quiet,morning")
    return c


@pytest.fixture
def store(tmp_path):
    s = AccountStore(tmp_path / "accounts")
    s.save(Account(account_number="123456", pin="4242", name="Boyan Lin", phones=[BOYAN]))
    return s


@pytest.fixture
def fake_puffo(tmp_path, monkeypatch):
    bin_path = tmp_path / "puffo"
    bin_path.write_text(FAKE_PUFFO)
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_PUFFO_SEND_LOG", str(tmp_path / "send.log"))
    monkeypatch.setenv("FAKE_PUFFO_EVENTS", str(tmp_path / "events.ndjson"))
    (tmp_path / "events.ndjson").write_text("")
    return {"bin": str(bin_path), "send_log": tmp_path / "send.log"}


def _services(conn, store, *, caller_id=BOYAN, puffo=None):
    pending: list[tuple[str, dict]] = []

    async def send(action, payload):
        pending.append((action, payload))

    svc = CallServices(conn=conn, store=store, puffo=puffo, caller_id=caller_id,
                       destination="", grok=None, fulfiller_slug=FULFILLER,
                       space_id="sp_test", send=send)
    svc._test_pending = pending
    return svc


def _sent(fake_puffo):
    if not fake_puffo["send_log"].exists():
        return []
    return [json.loads(x) for x in fake_puffo["send_log"].read_text().splitlines()]


def q(svc, **kw):
    return asyncio.run(svc.query(json.dumps(kw)))


def test_verify_known_caller_pin_only(conn, store):
    svc = _services(conn, store)
    out = q(svc, op="verify", pin="4242")
    assert "verified — greet Boyan Lin" in out and "Today is" in out
    assert svc.gate.verified.account_number == "123456"


def test_verify_unknown_caller_asks_for_account_number_without_burning(conn, store):
    svc = _services(conn, store, caller_id="+19998887777")
    out = q(svc, op="verify", pin="4242")
    assert "ACCOUNT NUMBER" in out
    assert svc.gate.attempts == 0
    out = q(svc, op="verify", pin="4242", account_number="123456")
    assert "verified" in out


def test_lockout_after_three_strikes(conn, store):
    svc = _services(conn, store)
    for pin in ("0000", "1111", "2222"):
        q(svc, op="verify", pin=pin)
    assert svc.gate.locked
    assert "can't verify" in q(svc, op="verify", pin="4242")


def test_kb_ops_require_verification_then_hit_sqlite(conn, store):
    svc = _services(conn, store)
    assert "isn't verified" in q(svc, op="search_gems", city="kobe", query="onsen")
    q(svc, op="verify", pin="4242")
    out = q(svc, op="search_gems", city="kobe", query="quiet onsen")
    assert "kobe-kin-no-yu" in out and "ground the recommendation" in out
    out = q(svc, op="get_gem", id="kin_no_yu")   # fuzzy voice-model id
    assert "650 yen" in out
    assert "off-book" in q(svc, op="search_gems", city="kobe", query="zzzz qqqq xxxx")


def test_remember_dedupes_and_writes_notes(conn, store):
    svc = _services(conn, store)
    q(svc, op="verify", pin="4242")
    assert "SILENT" in q(svc, op="remember", note="loves quiet mornings")
    assert "SILENT" in q(svc, op="remember", note="loves quiet mornings")  # dupe
    notes = conn.execute("SELECT note FROM notes WHERE account='123456'").fetchall()
    assert len(notes) == 1


def test_add_gem_from_caller(conn, store):
    svc = _services(conn, store)
    q(svc, op="verify", pin="4242")
    out = q(svc, op="add_gem", name="Sky Bar", city="kobe", pitch="Rooftop, no sign.")
    assert "saved" in out
    assert db.get_gem(conn, "kobe-sky-bar")["source"] == "caller"


def test_registration_mints_account_and_profile(conn, store, fake_puffo):
    client = PuffoClient(bin=fake_puffo["bin"], server_url="https://fake/relay",
                         channel_id="ch_test", identity="bot")
    async def send(action, payload):
        pass

    svc = CallServices(conn=conn, store=store, puffo=(client, PuffoListener(client)),
                       caller_id="+15550001111", destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    out = q(svc, op="register", name="Yuki Chen")
    assert "registered — welcome Yuki Chen" in out and "Today is" in out
    accounts = [a for a in store.load_all() if a.name == "Yuki Chen"]
    assert len(accounts) == 1 and accounts[0].phones == ["+15550001111"]
    assert db.profile_brief(conn, accounts[0].account_number)  # profile exists


def test_booking_flow_short_thread_and_date_bump(conn, store, fake_puffo):
    client = PuffoClient(bin=fake_puffo["bin"], server_url="https://fake/relay",
                         channel_id="ch_test", identity="bot")
    async def send(action, payload):
        pass

    svc = CallServices(conn=conn, store=store, puffo=(client, PuffoListener(client)),
                       caller_id=BOYAN, destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    q(svc, op="verify", pin="4242")
    out = q(svc, op="booking_establish", location="Kobe", start_date="2024-12-15",
            days=2, reason="counter dinner")
    assert "[booking] kobe" in out and "2 days" in out
    assert "in the past" in out and "confirm the year" in out   # date guard
    out = q(svc, op="booking_request", kind="explore", details="dinner for two")
    assert "request posted" in out
    sends = _sent(fake_puffo)
    texts = [a[a.index("send") + 1] for a in sends]
    assert texts[0].startswith("[booking] kobe 20") and texts[0].endswith("2 days")
    # the fulfiller is @-tagged in the context post and EVERY request message,
    # so each thread that needs her work notifies her
    assert texts[1] == f"[booking-context] @{FULFILLER} counter dinner"
    assert texts[2] == f"[booking-explore] @{FULFILLER} dinner for two"
    asyncio.run(svc.close())
    itinerary = _sent(fake_puffo)[-1]
    assert itinerary[itinerary.index("send") + 1].startswith("[booking-itinerary]")


def test_normalize_start_date():
    assert normalize_start_date("2099-01-01") == ("2099-01-01", "")
    bumped, note = normalize_start_date("2024-12-15")
    assert bumped >= "2026" and "in the past" in note
    assert normalize_start_date("not-a-date") == ("not-a-date", "")


def test_verify_swapped_fields_do_not_burn(conn, store):
    svc = _services(conn, store, caller_id="+19998887777")
    out = q(svc, op="verify", pin="123456", account_number="4242")  # transposed
    assert "SWAPPED" in out
    assert svc.gate.attempts == 0
    assert "verified" in q(svc, op="verify", pin="4242", account_number="123456")
