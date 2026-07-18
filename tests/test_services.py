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


def test_kb_reads_are_open_writes_are_gated(conn, store):
    svc = _services(conn, store)
    # exploring is the front door: search/get need NO account
    out = q(svc, op="search_gems", city="kobe", query="quiet onsen")
    assert "kobe-kin-no-yu" in out
    assert "650 yen" in q(svc, op="get_gem", id="kin_no_yu")
    # remember works pre-account (the host's notebook — buffered, flushed later);
    # gem contributions still need a verified account
    assert "SILENT" in q(svc, op="remember", note="loves onsen")
    assert svc._pending_notes == ["loves onsen"]
    assert "needs an account" in q(svc, op="add_gem", name="X", city="kobe", pitch="Y")
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
    assert [n for _, n in store.read_notes("123456")] == ["loves quiet mornings"]


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
    # the agent is told to CONFIRM the linked caller-ID with the caller
    assert "+15550001111" in out and "CONFIRM" in out
    accounts = [a for a in store.load_all() if a.name == "Yuki Chen"]
    assert len(accounts) == 1 and accounts[0].phones == ["+15550001111"]
    store.append_note(accounts[0].account_number, "taste: loves soba")
    assert "Yuki Chen" in store.profile_brief(accounts[0].account_number)


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
    assert "trip noted" in out and "own thread" in out
    assert "in the past" in out and "confirm the year" in out   # date guard
    # a request without a title is bounced with guidance, nothing posted
    out = q(svc, op="booking_request", kind="explore", details="dinner for two")
    assert "title" in out and not _sent(fake_puffo)
    out = q(svc, op="booking_request", kind="explore",
            title="Counter dinner at Sky Bar", details="dinner for two")
    assert "request posted" in out
    out = q(svc, op="booking_request", kind="booked", title="counter dinner at sky bar",
            details="7pm confirmed")
    assert "marked booked" in out
    sends = _sent(fake_puffo)
    texts = [a[a.index("send") + 1] for a in sends]
    assert texts[0] == "[explore] Counter dinner at Sky Bar"       # human thread head
    assert texts[1].startswith(f"[booking-explore] @{FULFILLER} dinner for two")
    assert "(trip: Kobe" in texts[1]                                # context rides inside
    # status echoes the title so the newest message reads at a glance
    assert texts[2].startswith("[booked] Counter dinner at Sky Bar")
    asyncio.run(svc.close())
    itinerary = _sent(fake_puffo)[-1]
    assert itinerary[itinerary.index("send") + 1].startswith("[booking-itinerary]")


def test_attribute_ignores_hallucinated_placeholder_phone(conn, store, tmp_path):
    """The foreground invents caller_phone values (+1234567890 observed live) —
    a placeholder must not match, park, or become an identity."""
    pending_store = AccountStore(tmp_path / "accounts-pending")
    sent: list[tuple[str, dict]] = []

    async def send(action, payload):
        sent.append((action, payload))

    svc = CallServices(conn=conn, store=store, pending_store=pending_store,
                       caller_id="", destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    for fake in ("+1234567890", "+1", "1234567890", "+10000000000", "+16505551234"):
        asyncio.run(svc.attribute(fake))
    assert svc._caller_id == ""
    assert pending_store.lookup_by_phone("+1234567890") is None
    # the no-caller-ID status is what the agent should hear
    briefs = [p["brief"] for a, p in sent if a == "caller_context"]
    assert briefs and "No caller ID" in briefs[0]


def test_caller_name_sets_pending_account_name(conn, store, tmp_path):
    """The name settles at the start of the call — caller_name must land it on
    the pending record so the NEXT call greets them by name (voice-local ask
    2026-07-17: pending 626084 had notes but name='')."""
    pending_store = AccountStore(tmp_path / "accounts-pending")
    sent: list[tuple[str, dict]] = []

    async def send(action, payload):
        sent.append((action, payload))

    svc = CallServices(conn=conn, store=store, pending_store=pending_store,
                       caller_id="+15558889999", destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    out = q(svc, op="caller_name", name="Boyan Lin")
    assert "SILENT" in out
    parked = pending_store.lookup_by_phone("+15558889999")
    assert parked is not None and parked.name == "Boyan Lin"
    # and the brief for a later call now names them
    assert "Boyan Lin" in pending_store.profile_brief(parked.account_number) or \
           pending_store.get(parked.account_number).name == "Boyan Lin"


def test_caller_name_never_overwrites_a_verified_name(conn, store):
    svc = _services(conn, store)                 # caller-ID matched "Boyan Lin"
    q(svc, op="caller_name", name="Someone Else")
    assert store.get("123456").name == "Boyan Lin"


def test_mint_pin_carries_lucky_digit_twice():
    from voice_local.services import _mint_pin
    for _ in range(50):
        pin = _mint_pin("7")
        assert len(pin) == 4 and pin.count("7") >= 2
    assert len(_mint_pin("")) == 4          # no lucky digit -> plain random
    assert len(_mint_pin("x")) == 4         # junk -> plain random
    assert _mint_pin("42").count("4") >= 2  # multi-digit input -> first digit


def test_register_with_lucky_digit_mints_matching_pin(conn, store, fake_puffo):
    client = PuffoClient(bin=fake_puffo["bin"], server_url="https://fake/relay",
                         channel_id="ch_test", identity="bot")
    svc = _services(conn, store, caller_id="+15550009999",
                    puffo=(client, PuffoListener(client)))
    out = q(svc, op="register", name="Mika Tanaka", lucky="3")
    assert "registered — welcome Mika Tanaka" in out
    acct = store.lookup_by_phone("+15550009999")
    assert acct is not None and acct.pin.count("3") >= 2


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


def test_agent_passed_caller_phone_enables_pin_only_match(conn, store):
    # resolver had no caller-ID (solo fallback), but the agent passes the number
    svc = _services(conn, store, caller_id="")
    out = q(svc, op="verify", caller_phone="+1 (650) 656-7722", pin="4242")
    assert "verified — greet Boyan Lin" in out
    assert svc.gate.verified.account_number == "123456"


def test_agent_passed_wrong_phone_still_needs_account_number(conn, store):
    svc = _services(conn, store, caller_id="")
    out = q(svc, op="verify", caller_phone="+19990001111", pin="4242")
    assert "ACCOUNT NUMBER" in out   # no match -> unknown-caller path, no burn
    assert svc.gate.attempts == 0


def test_notebook_notes_flush_into_profile_on_register(conn, store, fake_puffo):
    client = PuffoClient(bin=fake_puffo["bin"], server_url="https://fake/relay",
                         channel_id="ch_test", identity="bot")
    async def send(action, payload):
        pass

    svc = CallServices(conn=conn, store=store, puffo=(client, PuffoListener(client)),
                       caller_id="+15550002222", destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    # notes BEFORE any account: buffered silently, never refused
    assert "SILENT" in q(svc, op="remember", note="wants Tokyo then Hakone in November")
    assert "SILENT" in q(svc, op="remember", note="two people, loves onsen")
    q(svc, op="register", name="Mika Sato")
    acct = next(a for a in store.load_all() if a.name == "Mika Sato")
    brief = store.profile_brief(acct.account_number)
    assert "Tokyo then Hakone" in brief and "loves onsen" in brief  # no re-asking


def _pending_services(conn, store, tmp_path, *, caller_id="+15550001111"):
    pending_store = AccountStore(tmp_path / "accounts-pending")
    pending: list[tuple[str, dict]] = []

    async def send(action, payload):
        pending.append((action, payload))

    svc = CallServices(conn=conn, store=store, pending_store=pending_store,
                       puffo=None, caller_id=caller_id, destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    svc._test_pending = pending
    return svc, pending_store


def test_call_start_attribution_names_known_account_even_with_empty_dossier(conn, store):
    """Ask (2026-07-17): a known caller must be greeted by NAME from the first
    ping, with everything else still behind the PIN — even before any notes
    exist for the account."""
    svc = _services(conn, store)                       # caller-ID = BOYAN, no notes
    asyncio.run(svc.attribute())
    ctx = [p for a, p in svc._test_pending if a == "caller_context"]
    assert len(ctx) == 1
    brief = ctx[0]["brief"]
    assert "Boyan Lin" in brief and "greet them by NAME" in brief
    assert "PIN verification is still required" in brief
    # settled: a second attribute must not resend the dossier
    asyncio.run(svc.attribute())
    assert len([p for a, p in svc._test_pending if a == "caller_context"]) == 1


def test_call_start_attribution_preloads_pending_notes_freely(conn, store, tmp_path):
    """A returning caller with only a pending dossier gets last call's notebook
    right away — no PIN gate on info they themselves gave us."""
    pending_store = AccountStore(tmp_path / "accounts-pending")
    parked = Account(account_number="888888", pin="", name="", phones=["+15552223333"])
    pending_store.save(parked)
    pending_store.append_note("888888", "dreaming of Hakone in November")
    sent: list[tuple[str, dict]] = []

    async def send(action, payload):
        sent.append((action, payload))

    svc = CallServices(conn=conn, store=store, pending_store=pending_store,
                       caller_id="+15552223333", destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    asyncio.run(svc.attribute())
    ctx = [p for a, p in sent if a == "caller_context"]
    assert len(ctx) == 1
    brief = ctx[0]["brief"]
    assert "dreaming of Hakone in November" in brief
    assert "NO account yet" in brief and "never re-ask" in brief


def test_call_start_attribution_flags_returning_caller_with_empty_notebook(conn, store, tmp_path):
    """Called before, nothing noted: the status reply must say so, so Koyuki can
    use the 'didn't get a chance to ask your name last time' opening."""
    pending_store = AccountStore(tmp_path / "accounts-pending")
    pending_store.save(Account(account_number="777001", pin="", name="",
                               phones=["+15554445555"]))
    sent: list[tuple[str, dict]] = []

    async def send(action, payload):
        sent.append((action, payload))

    svc = CallServices(conn=conn, store=store, pending_store=pending_store,
                       caller_id="+15554445555", destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    asyncio.run(svc.attribute())
    (action, payload), = [x for x in sent if x[0] == "caller_context"]
    assert "HAS called before" in payload["brief"]
    assert "didn't get a chance to ask" in payload["brief"]


def test_call_start_attribution_flags_first_time_caller(conn, store, tmp_path):
    """Unknown number: a pending profile is parked within the first seconds AND
    the status reply says first-time so Koyuki asks the name plainly."""
    pending_store = AccountStore(tmp_path / "accounts-pending")
    sent: list[tuple[str, dict]] = []

    async def send(action, payload):
        sent.append((action, payload))

    svc = CallServices(conn=conn, store=store, pending_store=pending_store,
                       caller_id="+15556667777", destination="", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    assert pending_store.lookup_by_phone("+15556667777") is not None  # parked already
    asyncio.run(svc.attribute())
    (action, payload), = [x for x in sent if x[0] == "caller_context"]
    assert "First-time caller" in payload["brief"]
    assert "pleasure to speak with" in payload["brief"]


def test_pre_account_note_parks_a_pending_account(conn, store, tmp_path):
    svc, pending_store = _pending_services(conn, store, tmp_path)
    q(svc, op="remember", note="loves quiet onsen mornings")
    parked = pending_store.lookup_by_phone("+15550001111")
    assert parked is not None and parked.pin == ""
    assert (pending_store.dir / parked.account_number / "persona.md").exists()
    assert [n for _, n in pending_store.read_notes(parked.account_number)] == [
        "loves quiet onsen mornings"]


def test_register_promotes_pending_number_and_clears_parking(conn, store, tmp_path):
    svc, pending_store = _pending_services(conn, store, tmp_path)
    q(svc, op="remember", note="two people, mid-November")
    parked = pending_store.lookup_by_phone("+15550001111")
    out = q(svc, op="register", name="Mika Tanaka")
    assert parked.account_number in out            # same number promoted
    assert store.get(parked.account_number) is not None
    assert pending_store.get(parked.account_number) is None
    assert store.get(parked.account_number).name == "Mika Tanaka"
    assert len(store.read_notes(parked.account_number)) == 1   # notebook moved over


def test_verify_migrates_pending_notes_into_existing_account(conn, store, tmp_path):
    # Same phone as the registered account: earlier call parked notes pre-auth.
    svc, pending_store = _pending_services(conn, store, tmp_path, caller_id=BOYAN)
    parked = Account(account_number="777777", pin="", name="", phones=[BOYAN])
    pending_store.save(parked)
    pending_store.append_note("777777", "wants a Fuji-side seat")
    svc._pending_account = pending_store.lookup_by_phone(BOYAN)
    q(svc, op="verify", pin="4242")
    assert svc.gate.verified.account_number == "123456"
    assert "wants a Fuji-side seat" in [n for _, n in store.read_notes("123456")]
    assert pending_store.read_notes("777777") == []
    assert pending_store.get("777777") is None


def test_pending_notebook_resurfaces_on_next_call(conn, store, tmp_path):
    svc, pending_store = _pending_services(conn, store, tmp_path)
    q(svc, op="remember", note="dreams of Hakone ropeway")
    # New call, same phone, fresh CallServices: the parked notebook rides back in.
    svc2, _ = _pending_services(conn, store, tmp_path)
    svc2._pending_store = pending_store
    svc2._pending_account = pending_store.lookup_by_phone("+15550001111")
    out = q(svc2, op="search_gems", city="hakone", query="ropeway")
    assert any(a == "caller_context" and "dreams of Hakone ropeway" in p.get("brief", "")
               for a, p in svc2._test_pending)


def test_city_guide_rides_first_city_mention_once(conn, store):
    svc = _services(conn, store)
    out = q(svc, op="search_gems", city="kobe", query="onsen")
    assert "[City guide: kobe" in out          # aggressive warm load, same reply
    assert out.index("[City guide") < out.index("ground the recommendation")
    out2 = q(svc, op="search_gems", city="kobe", query="wagyu")
    assert "[City guide" not in out2           # once per city per call


def test_city_guide_op_explicit_then_silent(conn, store):
    svc = _services(conn, store)
    out = q(svc, op="city_guide", city="Kobe city")
    assert "[City guide: kobe" in out and "kobe-kin-no-yu" in out
    assert "SILENT" in q(svc, op="city_guide", city="kobe")
    assert "no notes on paris" in q(svc, op="city_guide", city="paris")


def test_city_guide_follows_get_gem_city(conn, store):
    db.add_gem(conn, name="Zuihoin", city="kyoto", pitch="Raked waves.", tags="temple")
    svc = _services(conn, store)
    out = q(svc, op="get_gem", id="kyoto-zuihoin")
    assert "[City guide: kyoto" in out


def test_city_guide_bypasses_data_truncation(conn, store):
    for i in range(30):
        db.add_gem(conn, name=f"Gem number {i} with a long name", city="osaka",
                   pitch=f"A properly long one-breath pitch sentence number {i}.",
                   tags=f"tag{i % 8},food", details="detail " * 40)
    svc = _services(conn, store)
    out = q(svc, op="city_guide", city="osaka")
    guide_block = out[out.index("[City guide"):]
    assert len(guide_block) > 1500             # would be lost under the data cap
    assert guide_block.count("\n- ") + guide_block.count("\n") >= 30


def test_verify_preloads_destination_guide(conn, store):
    pending: list[tuple[str, dict]] = []

    async def send(action, payload):
        pending.append((action, payload))

    svc = CallServices(conn=conn, store=store, puffo=None, caller_id=BOYAN,
                       destination="kobe", grok=None, fulfiller_slug=FULFILLER,
                       space_id="sp_test", send=send)
    out = q(svc, op="verify", pin="4242")
    assert "verified" in out and "[City guide: kobe" in out


def test_channel_named_after_guest_with_personal_welcome(conn, store, fake_puffo):
    client = PuffoClient(bin=fake_puffo["bin"], server_url="https://fake/relay",
                         channel_id="ch_shared", identity="bot")

    async def send(action, payload):
        pass

    svc = CallServices(conn=conn, store=store, puffo=(client, PuffoListener(client)),
                       caller_id="+15550003333", destination="japan", grok=None,
                       fulfiller_slug=FULFILLER, space_id="sp_test", send=send)
    q(svc, op="remember", note="trip: Hakone onsen, mid-November, two people")
    q(svc, op="remember", note="taste: quiet mornings, hates crowds")
    q(svc, op="register", name="Mika Tanaka")
    acct = next(a for a in store.load_all() if a.name == "Mika Tanaka")
    calls = _sent(fake_puffo)
    create = next(c for c in calls if "channel" in c and "create" in c)
    assert f"{acct.account_number}-mika" in create   # warm name, no japan- prefix
    welcome = next(c for c in calls if "send" in c and "ようこそ" in " ".join(c))
    text = " ".join(welcome)
    assert "Mika" in text and "Hakone onsen" in text and "quiet mornings" in text
    # reuse: second _ensure_channel must NOT send another welcome
    n_sends = sum(1 for c in calls if "send" in c and "ようこそ" in " ".join(c))
    assert n_sends == 1


def test_attribution_ping_parks_pending_and_preloads(conn, store, tmp_path):
    # The check_updates ping alone (no other op) must attribute the caller.
    svc, pending_store = _pending_services(conn, store, tmp_path, caller_id="")
    asyncio.run(svc.attribute("+15550007777"))
    parked = pending_store.lookup_by_phone("+15550007777")
    assert parked is not None                       # unknown number -> parked dossier
    pending_store.append_note(parked.account_number, "taste: loves quiet onsen")
    # next call, same phone: attribution alone preloads the notebook
    svc2, _ = _pending_services(conn, store, tmp_path, caller_id="")
    svc2._pending_store = pending_store
    asyncio.run(svc2.attribute("+15550007777"))
    assert any(a == "caller_context" and "loves quiet onsen" in p.get("brief", "")
               for a, p in svc2._test_pending)
