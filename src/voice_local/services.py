"""Per-call backend services: auth, hidden-gem KB, and Puffo booking behind ONE
JSON "op" grammar — the contract the Vocal Bridge agent's background AI speaks
through the `query_backend` MCP tool.

Ported from voxcall's VBCallServices (the LiveKit-era backend) with one
structural change: KB ops hit the SQLite data bag directly (voice_local.db)
instead of shelling out to the legacy `ckb` CLI. Everything else — the AuthGate
security posture (server decides, agent only relays digits, 3-strike lockout),
minted-PIN registration, per-destination Puffo channels, fresh trip-context
parsing, booking dedupe, SILENT no-op replies — carries over verbatim, because
every one of those behaviors was paid for with a live-call bug.

Ops: verify | register | change_pin | search_gems | get_gem | remember |
add_gem | booking_establish | booking_request  (check_updates is handled a
layer up in mcp_server, where the pending-update queue lives).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import random
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable

from voice_local import db as kb
from voice_local.accounts import Account, AccountStore, AuthGate
from voice_local.puffo import BookingSession, ensure_user_channel
from voice_local.trip_context import load_trip_context, trip_context_text

log = logging.getLogger("voice_local.services")

KIND_TO_TOOL = {"explore": "explore_booking", "confirmed": "confirm_booking",
                "update": "update_booking", "canceled": "cancel_booking",
                "booked": "mark_booked"}

_BOOKING_FAIL_PREFIXES = ("could not", "no booking thread")

_LOCKED_HINT = ("verification failed too many times — politely say you can't verify "
                "the account today, say goodbye, and end the call.")


def _today_line() -> str:
    """Date grounding: voice LLMs reliably hallucinate the year ('January' -> a
    PAST January) unless the current date is stated in-band."""
    today = _dt.date.today()
    return f"Today is {today.strftime('%A, %B %d, %Y')} ({today.isoformat()})."


def _mint_pin(lucky: str = "") -> str:
    """Default 4-digit PIN. With a caller's lucky digit, weave it in so it shows
    up AT LEAST twice — a PIN that carries their number is one they'll keep."""
    lucky = lucky[:1] if lucky[:1].isdigit() else ""
    if not lucky:
        return "".join(random.choices("0123456789", k=4))
    digits = [lucky, lucky] + random.choices("0123456789", k=2)
    random.shuffle(digits)
    return "".join(digits)


def normalize_start_date(raw: str) -> tuple[str, str]:
    """(normalized YYYY-MM-DD, note). A past date is bumped to its NEXT future
    occurrence; the note tells the agent to confirm the corrected year."""
    try:
        d = _dt.date.fromisoformat(str(raw).strip())
    except (ValueError, TypeError):
        return str(raw).strip(), ""
    today = _dt.date.today()
    if d >= today:
        return d.isoformat(), ""
    bumped = d
    while bumped < today:
        try:
            bumped = bumped.replace(year=bumped.year + 1)
        except ValueError:  # Feb 29 -> next leap year
            bumped = bumped.replace(year=bumped.year + 1, day=28)
    return bumped.isoformat(), (
        f"NOTE: the {raw} you sent is in the past — {_today_line()} The trip was filed "
        f"for {bumped.isoformat()} instead; confirm the year with the caller in your "
        f"next turn.")


class CallServices:
    """One live call's backend: AuthGate seeded from caller-ID, the verified
    account, the SQLite data bag, and the BookingSession bound to the caller's
    per-destination Puffo channel."""

    def __init__(self, *, conn: sqlite3.Connection, store: AccountStore,
                 pending_store: AccountStore | None = None,
                 puffo: tuple | None = None, caller_id: str = "",
                 destination: str = "", grok=None,
                 fulfiller_slug: str = "", space_id: str = "",
                 channel_invites: list[str] | None = None,
                 send: Callable[[str, dict], Awaitable[None]]) -> None:
        self._conn = conn
        self._store = store
        self._puffo = puffo            # (PuffoClient, PuffoListener) or None
        self._caller_id = caller_id
        self._destination = destination
        self._grok = grok
        self._fulfiller = fulfiller_slug
        self._space_id = space_id
        self._channel_invites = channel_invites or []
        self._send = send              # async (action, payload) -> queue for the reply
        self._seen_notes: set[str] = set()
        # The host's notebook before an account exists: with a pending_store, the
        # first note mints a real account number parked in accounts-pending/ (no PIN,
        # no booking channel) and notes persist under it immediately — surviving
        # hangups. Registration promotes that same number to a full account; verify
        # into a different account migrates the notes. Without a pending_store the
        # notes are buffered in memory for this call only.
        self._pending_store = pending_store
        self._pending_notes: list[str] = []
        self._pending_context_sent = False
        self._pending_created_now = False   # parked THIS call = first-time caller
        self._no_id_notified = False
        self._cities_loaded: set[str] = set()
        matched = store.lookup_by_phone(caller_id) if caller_id else None
        self._pending_account = (pending_store.lookup_by_phone(caller_id)
                                 if pending_store is not None and caller_id else None)
        self.gate = AuthGate(store, matched=matched)
        self.booking: BookingSession | None = None
        # Attribution happens AT CALL START: a known caller is recognized (name +
        # history preloads on the first query); an unknown caller silently gets a
        # pending account parked right away so every note persists — without ever
        # rushing them toward creating a real account.
        if matched is None and self._pending_account is None and caller_id:
            self._ensure_pending_account()

    # ---- op dispatch (query grammar) --------------------------------------------

    _QUERY_OPS = {"verify": "_verify", "register": "_register",
                  "change_pin": "_change_pin", "search_gems": "_kb_search",
                  "get_gem": "_kb_get", "city_guide": "_kb_city_guide",
                  "remember": "_kb_remember", "caller_name": "_caller_name",
                  "add_gem": "_kb_add", "booking_establish": "_booking_establish",
                  "booking_request": "_booking_request"}

    async def attribute(self, agent_phone: str = "") -> None:
        """STEP ONE of every call: put a face on the caller. Match the phone
        against established accounts first, then the pending folder; an unknown
        number silently gets a fresh pending dossier parked so everything
        learned this call persists. A match preloads the whole dossier ONCE as
        silent caller context — greet by name, use the history; PIN still
        gates bookings. Runs on every query (including the check_updates ping)
        and is cheap once attribution is settled."""
        digits = re.sub(r"\D", "", agent_phone or "")
        bare = digits[1:] if digits.startswith("1") and len(digits) == 11 else digits
        if len(bare) < 10 or bare in ("1234567890", "0" * 10) or bare[3:6] == "555":
            # Foreground models INVENT placeholder numbers (+1234567890 and the
            # 555 example +16505551234 observed live 2026-07-17) — a fake number
            # must never match, park, or become an identity.
            agent_phone = ""
        if agent_phone and self.gate.verified is None and self.gate.matched is None:
            matched = self._store.lookup_by_phone(agent_phone)
            if matched is not None:
                log.info("caller-ID match via agent-passed caller_phone")
                self.gate.matched = matched
                if not self._caller_id:
                    self._caller_id = agent_phone
        if (agent_phone and self._pending_store is not None
                and self._pending_account is None and self.gate.matched is None
                and self.gate.verified is None):
            self._pending_account = self._pending_store.lookup_by_phone(agent_phone)
            if not self._caller_id:
                self._caller_id = agent_phone
            if self._pending_account is None:
                # The phone number just became known and matches nothing: park the
                # pending account now — attribution happens when the call happens.
                self._ensure_pending_account()
        # A caller-ID match means this is very likely a returning guest: preload
        # their dossier ONCE, silently, so the conversation is targeted from the
        # first minute. Matched-but-unverified real accounts stay guarded: the
        # dossier informs Koyuki's instincts, but nothing private is recited and
        # bookings still require the PIN. No match = no dossier, and that is fine
        # — never rush toward creating an account.
        if not self._pending_context_sent and self.gate.verified is None:
            # The first reply of every call carries an explicit caller status —
            # known guest / called before / first time — so the agent's opening
            # name move is driven by the system, never guessed.
            if self.gate.matched is not None:
                brief = self._store.profile_brief(self.gate.matched.account_number)
                if not brief:
                    # Empty dossier must still surface the NAME — knowing who is
                    # calling is the whole point of call-start attribution.
                    brief = (f"Caller: {self.gate.matched.name or 'unknown'} "
                             f"(account {self.gate.matched.account_number}; no notes yet)")
                preload = (
                    "Caller-ID matches this returning guest — greet them by NAME "
                    "like an old friend and use their history naturally (never "
                    "re-ask what it answers). PIN verification is still required "
                    "before bookings, account details, or PIN changes; if the "
                    "voice clearly is not them, fall back to neutral hosting:\n"
                    + brief)
            elif self._pending_account is not None and not self._pending_created_now:
                brief = self._pending_store.profile_brief(
                    self._pending_account.account_number)
                if brief:
                    preload = (
                        "Returning caller with NO account yet — your notebook from "
                        "earlier calls (never re-ask these; bookings still require "
                        "creating the account, but never rush that). If the notebook "
                        "does not name them, say you didn't get a chance to ask their "
                        "name last time and ask it now:\n" + brief)
                else:
                    preload = (
                        "Returning caller with NO account yet and an EMPTY notebook — "
                        "this number HAS called before, but you never learned their "
                        "name. Tell them you didn't get a chance to ask last time, "
                        "and ask their name now.")
            elif self._pending_account is not None:
                preload = (
                    "First-time caller — this number has never called before; a fresh "
                    "notebook is already parked for them, so everything you note "
                    "sticks even if the call drops. Ask warmly who you have the "
                    "pleasure to speak with.")
            else:
                # No phone yet — one that arrives later (agent-passed) must still
                # get the real status, so don't burn _pending_context_sent here.
                if not self._no_id_notified:
                    self._no_id_notified = True
                    await self._send("caller_context", {
                        "brief": "No caller ID on this call yet — you don't know "
                                 "who this is. Ask warmly who you have the pleasure "
                                 "to speak with.",
                        "trip_summary": ""})
                return
            self._pending_context_sent = True
            await self._send("caller_context",
                             {"brief": preload, "trip_summary": ""})

    def attribution_phone(self, query: str) -> str:
        """Pull caller_phone out of a raw query string (for ops handled a layer
        up, like check_updates) without dispatching it."""
        try:
            q = json.loads(query)
            return str(q.get("caller_phone", "") or "").strip()
        except (ValueError, TypeError):
            return ""

    async def query(self, query: str) -> str:
        """ONE JSON command string in, spoken-guidance text out — consumed
        synchronously in the agent's querying turn."""
        try:
            q = json.loads(query)
            assert isinstance(q, dict)
        except (ValueError, TypeError, AssertionError):
            return ("could not parse the query — send ONE JSON object like "
                    '{"op":"verify","pin":"1234"} (ops: ' + ", ".join(self._QUERY_OPS) + ")")
        method = self._QUERY_OPS.get(str(q.get("op", "")).strip().lower())
        if method is None:
            return f"unknown op {q.get('op')!r} — valid ops: {', '.join(self._QUERY_OPS)}"
        # Preferred caller-ID path: the agent passes the platform-provided number in
        # the query itself (the MCP wire carries no metadata; the VB logs API is only
        # the fallback). Model-relayed digits are safe to trust for the MATCH because
        # the PIN still gates verification — a wrong number just means no match.
        await self.attribute(str(q.pop("caller_phone", "") or "").strip())
        captured: list[tuple[str, dict]] = []
        outer_send = self._send

        async def capture(name: str, payload: dict) -> None:
            if name in ("caller_context", "booking_update"):
                await outer_send(name, payload)   # out-of-band: rides a later reply
            else:
                captured.append((name, payload))

        self._send = capture
        try:
            await getattr(self, method)({k: v for k, v in q.items() if k != "op"})
        finally:
            self._send = outer_send
        if not captured:
            return ("SILENT: duplicate/no-op — say absolutely nothing about this; "
                    "do not acknowledge it; continue the conversation naturally.")
        parts = []
        for _name, payload in captured:
            hint = str(payload.get("say_hint") or payload.get("detail") or "").strip()
            if hint:
                parts.append(hint)
            data = payload.get("data")
            if data is not None:
                parts.append(json.dumps(data)[:1500] if not isinstance(data, str)
                             else data[:1500])
            # A city guide is the agent's working memory of a place — it must
            # arrive whole (30 one-liners), never squeezed through the 1500-char
            # data cap that keeps ordinary result payloads voice-sized.
            guide = str(payload.get("guide") or "").strip()
            if guide:
                parts.append(guide[:6000])
        return "\n".join(parts) or "ok"

    async def close(self) -> None:
        task = getattr(self, "_context_task", None)
        if task is not None:
            task.cancel()
        if self.booking is not None:
            await self.booking.close()

    # ---- auth ---------------------------------------------------------------------

    async def _verify(self, payload: dict) -> None:
        gate = self.gate
        if gate.verified is not None:
            log.info("verify retry ignored (already verified)")
            return
        if gate.locked:
            await self._send("auth_result", {"ok": False, "locked": True,
                                             "attempts_left": 0, "say_hint": _LOCKED_HINT})
            return
        pin = re.sub(r"\D", "", str(payload.get("pin", "")))
        acct = re.sub(r"\D", "", str(payload.get("account_number", "")))
        if len(pin) < 4:
            await self._send("auth_result", {
                "ok": False, "locked": False,
                "attempts_left": gate.max_attempts - gate.attempts,
                "say_hint": "no PIN digits heard — ask them to say their PIN slowly, one "
                            "digit at a time, then verify with those digits."})
            return
        if gate.matched is None and not acct:
            await self._send("auth_result", {
                "ok": False, "locked": False,
                "attempts_left": gate.max_attempts - gate.attempts,
                "say_hint": "this phone number isn't on any account, so a PIN alone "
                            "can't verify — ask for their ACCOUNT NUMBER too, then send "
                            "both (account_number + pin) in one verify query."})
            return
        if len(pin) == 6 and len(acct) == 4:
            # Transposed fields (accounts are 6 digits, PINs are 4) — a live-call
            # failure mode; point it out instead of burning an attempt.
            await self._send("auth_result", {
                "ok": False, "locked": False,
                "attempts_left": gate.max_attempts - gate.attempts,
                "say_hint": "the account number and PIN look SWAPPED (account numbers "
                            "are 6 digits, PINs are 4) — resend with the fields the "
                            "right way around."})
            return
        account = gate.attempt(pin, acct)
        if account is None:
            left = gate.max_attempts - gate.attempts
            say = _LOCKED_HINT if gate.locked else (
                f"that didn't match — ask them to try again ({left} attempt"
                f"{'s' if left != 1 else ''} left). Never say which part was wrong.")
            await self._send("auth_result", {"ok": False, "locked": gate.locked,
                                             "attempts_left": left, "say_hint": say})
            return
        await self._send("auth_result", {
            "ok": True, "name": account.name,
            "say_hint": f"verified — greet {account.name} by name and start the "
                        "consultation. Silent caller context follows: use it instead of "
                        f"making them re-explain their tastes or bookings. {_today_line()} "
                        "Resolve every relative date ('January', 'next week') against "
                        "TODAY — never into the past."})
        self._flush_notes(account.account_number)
        self._adopt_pending(account.account_number)
        channel_id, channel_warn = await self._ensure_channel(account)
        if channel_warn:
            await self._send("auth_result", {"ok": True, "say_hint": channel_warn.strip()})
        self._start_booking(account, channel_id)
        await self._send_caller_context(account, channel_id)
        await self._load_city_guide(self._destination)

    async def _register(self, payload: dict) -> None:
        if self.gate.verified is not None or self.gate.matched is not None:
            await self._send("registration_result", {
                "ok": False,
                "say_hint": "this caller already has an account — verify with the PIN "
                            "instead of registering."})
            return
        name = str(payload.get("name", "")).strip()
        if not name:
            await self._send("registration_result", {
                "ok": False, "say_hint": "registration needs a name — ask for it."})
            return
        pin = str(payload.get("pin", "")).strip()
        if not pin:
            pin = _mint_pin(str(payload.get("lucky", "")).strip())
        # Promote the parked pending account when one exists: the caller keeps the
        # number their notes already live under; only NOW does the account become
        # real (PIN, file in accounts/, booking channel).
        number = (self._pending_account.account_number
                  if self._pending_account is not None
                  else self._new_account_number())
        account = Account(account_number=number, pin=pin, name=name,
                          phones=[self._caller_id] if self._caller_id else [])
        self._store.save(account)
        self.gate.verified = account
        self._flush_notes(number)
        self._adopt_pending(number)
        channel_id, channel_warn = await self._ensure_channel(account)
        self._start_booking(account, channel_id)
        phone_line = (f"Their account is linked to the number they're calling from "
                      f"({self._caller_id}) — CONFIRM with them that this is their own "
                      f"number to use next time; if it isn't, tell them to note the "
                      f"account number carefully instead."
                      if self._caller_id else
                      "No caller ID came through, so the account is NOT linked to a "
                      "phone — they must note the account number carefully.")
        await self._send("registration_result", {
            "ok": True, "account_number": number, "name": name, "pin": pin,
            "say_hint": f"registered — welcome {name}. Tell them their account number is "
                        f"{number}: read it one digit at a time ({', '.join(number)}). "
                        f"Their PIN is {pin}: read it one digit at a time "
                        f"({', '.join(pin)}), and they can change it right now (or any "
                        f"time) to digits of their own. {phone_line}"
                        f"{channel_warn} {_today_line()}"})
        await self._send_caller_context(account, channel_id)
        await self._load_city_guide(self._destination)

    def _new_account_number(self) -> str:
        while True:
            number = str(random.randrange(100000, 1000000))
            if self._store.get(number) is None and (
                    self._pending_store is None
                    or self._pending_store.get(number) is None):
                return number

    async def _change_pin(self, payload: dict) -> None:
        if self.gate.verified is None:
            await self._send("pin_result", {
                "ok": False, "say_hint": "the caller isn't verified yet — complete "
                                         "verification before changing the PIN."})
            return
        new_pin = str(payload.get("new_pin", "")).strip()
        if not re.fullmatch(r"\d{4,8}", new_pin):
            await self._send("pin_result", {
                "ok": False, "say_hint": "a PIN must be 4 to 8 digits — ask them to "
                                         "pick another."})
            return
        account = self.gate.verified
        account.pin = new_pin
        self._store.save(account)
        await self._send("pin_result", {
            "ok": True,
            "say_hint": f"PIN changed — confirm by reading it back one digit at a time "
                        f"({', '.join(new_pin)}) and remind them to use it next call."})

    # ---- caller context (warm start) -------------------------------------------------

    async def _ensure_channel(self, account: Account) -> tuple[str, str]:
        """(channel_id, warning). A failed per-guest channel creation must be SAID
        to the caller — a silent fallback surfaces as a broken booking much later
        in the conversation, which is worse than honesty now."""
        if self._puffo is None:
            return "", ""
        client, _listener = self._puffo
        if not self._destination:
            return client.channel_id, ""
        newly_created = not account.channels.get(
            re.sub(r"[^a-z0-9]+", "-", self._destination.lower()).strip("-"), "")
        channel_id = await ensure_user_channel(
            client, self._store, account, self._destination,
            space_id=self._space_id, fulfiller_slug=self._fulfiller,
            invite_slugs=self._channel_invites)
        if channel_id and channel_id != client.channel_id:
            if newly_created:
                await self._send_welcome(account, channel_id)
            return channel_id, ""
        return channel_id, (" WARNING: their private booking channel could NOT be set "
                            "up — tell them there's a technical hiccup on the booking "
                            "side right now; recommendations still work fine.")

    async def _send_welcome(self, account: Account, channel_id: str) -> None:
        """First message in a fresh guest channel: a warm, PERSONAL welcome —
        Koyuki already talked with them, so it names what they care about
        (dossier notes), never a generic greeting. Grok writes it when
        available (detached, off the call's critical path); otherwise a warm
        template goes out inline."""
        client, _listener = self._puffo
        number = account.account_number
        first = (account.name or "").split(" ")[0] or "friend"
        trip = [n for _, n in self._store.read_doc(number, "trip.md")]
        persona = [n for _, n in self._store.read_doc(number, "persona.md")]
        companions = self._store.companions(number)

        def template() -> str:
            bits = [f"ようこそ, welcome {first}"
                    + (f" — and {', '.join(c.title() for c in companions)} too" if companions else "")
                    + "! This is your own little corner of Japan planning, with me, Koyuki."]
            if trip:
                bits.append("From our call I am already holding onto this: "
                            + trip[-1].removeprefix("trip:").strip() + ".")
            if persona:
                bits.append("And I have not forgotten — "
                            + persona[-1].removeprefix("personal:").removeprefix(
                                "taste:").removeprefix("constraint:").strip() + ".")
            bits.append("My booking partner sees this channel too, so plans and "
                        "confirmations will land right here. またね!")
            return " ".join(bits)

        if self._grok is None:
            try:
                await client.send(template(), channel=channel_id)
            except Exception:  # noqa: BLE001 - a failed welcome must never break auth
                log.exception("welcome message failed for %s", channel_id)
            return

        notes = "\n".join(f"- {n}" for n in (persona + trip)[-12:])
        send = client.send

        async def compose_and_send() -> None:
            try:
                text = await self._grok.complete([
                    {"role": "system", "content":
                        "You are Koyuki, a warm Japanese-American travel host who has "
                        "lived in Kobe for twenty years. You just finished a phone call "
                        "with a guest and their private Japan-planning channel was "
                        "created. Write the FIRST message in it: 3-4 sentences, plain "
                        "text, no markdown. It must feel personal — weave in one or two "
                        "specific things from your call notes, never a generic welcome. "
                        "Mention that plans and booking confirmations land here and "
                        "your booking partner reads along. A touch of Japanese "
                        "(ようこそ, ね, またね) is welcome."},
                    {"role": "user", "content":
                        f"Guest: {account.name or 'a new friend'}"
                        + (f", traveling with {', '.join(companions)}" if companions else "")
                        + f"\nYour call notes:\n{notes or '- (no notes yet)'}"},
                ], temperature=0.6, max_tokens=250)
            except Exception:  # noqa: BLE001 - fall back to the handwritten warmth
                log.exception("grok welcome failed — sending template")
                text = template()
            try:
                await send(text.strip(), channel=channel_id)
            except Exception:  # noqa: BLE001
                log.exception("welcome message failed for %s", channel_id)

        self._welcome_task = asyncio.create_task(compose_and_send(), name="welcome")

    async def _send_caller_context(self, account: Account, channel_id: str) -> None:
        """The profile brief goes out IMMEDIATELY (same reply as the verify result);
        the trip parse — a whole-channel LLM read that grows with channel history —
        runs detached and rides a LATER reply via the pending queue. Blocking verify
        on it once exceeded VB's tool timeout and deadlocked a live call."""
        brief = self._store.profile_brief(account.account_number)
        await self._send("caller_context", {"brief": brief, "trip_summary": ""})
        if self._grok is None or self._puffo is None or not channel_id:
            return
        client, _listener = self._puffo
        send = self._send   # bind the OUTER send: the per-query capture is long gone
                            # by the time the parse lands

        async def parse() -> None:
            try:
                trip = trip_context_text(
                    await load_trip_context(client, channel_id, self._grok))
            except Exception:  # noqa: BLE001 - context is a bonus, never a failure
                log.exception("trip-context parse failed")
                return
            if trip:
                await send("caller_context", {"brief": "", "trip_summary": trip})

        self._context_task = asyncio.create_task(parse(), name="trip-context")

    def _start_booking(self, account: Account, channel_id: str = "") -> None:
        if self._puffo is None:
            return
        client, listener = self._puffo

        async def notify(text: str) -> None:
            # Fulfiller reply / timeout -> booking_update, coalescing duplicates
            # (one fulfiller message can resolve several watchers at once).
            key = " ".join(text.lower().split())
            last_key, last_ts = getattr(self, "_last_update", ("", 0.0))
            if key == last_key and time.monotonic() - last_ts < 10:
                log.info("coalesced duplicate booking_update")
                return
            self._last_update = (key, time.monotonic())
            ok = not text.startswith("[Booking]:")
            await self._send("booking_update", {
                "ok": ok, "text": text,
                "say_hint": ("relay this booking update to the caller naturally, ONCE." if ok
                             else "the booking request timed out — tell the caller and "
                                  "offer to retry or cancel.")})

        self.booking = BookingSession(
            client=client, listener=listener, account=account, account_store=self._store,
            fulfiller_slug=self._fulfiller, bus=None, inject=notify, channel_id=channel_id)

    # ---- KB (SQLite-native) -----------------------------------------------------------

    def _kb_gate_hint(self) -> dict | None:
        """Write-ish KB ops need a verified account. READS (search/get) don't:
        exploring the city is the front door of the experience — identity only
        enters when the caller wants something arranged."""
        if self.gate.verified is None:
            return {"ok": False, "data": None,
                    "say_hint": "this needs an account — if the caller wants it, offer "
                                "to set one up (or verify a returning caller); otherwise "
                                "continue the conversation naturally."}
        return None

    async def _load_city_guide(self, *raw_cities: str) -> bool:
        """The aggressive warm load: the FIRST time a city enters the
        conversation through ANY op, its full guide (top ~30 gems, one line
        each) rides that same reply — the agent gets a local's mental map
        without having to know to ask. Once per city per call."""
        loaded = False
        for raw in raw_cities:
            city = kb.resolve_city(self._conn, str(raw or ""))
            if not city or city in self._cities_loaded:
                continue
            guide = kb.city_guide(self._conn, city)
            if guide is None:
                continue
            self._cities_loaded.add(city)
            loaded = True
            await self._send("city_guide", {"ok": True, "guide": guide})
        return loaded

    async def _kb_search(self, payload: dict) -> None:
        await self._load_city_guide(str(payload.get("city", "")))
        gems = kb.search_gems(self._conn, city=str(payload.get("city", "")),
                              query=str(payload.get("query", "")),
                              tags=str(payload.get("tags", "")))
        await self._send("kb_result", {
            "ok": bool(gems), "data": {"count": len(gems), "gems": gems},
            "say_hint": ("ground the recommendation in these results — never invent gems."
                         if gems else
                         "no gems matched — say so honestly and offer your own closest "
                         "knowledge, clearly flagged as off-book.")})

    async def _kb_get(self, payload: dict) -> None:
        gem = kb.get_gem(self._conn, str(payload.get("id", "")))
        if gem is not None:
            await self._load_city_guide(gem.get("city", ""))
        await self._send("kb_result", {
            "ok": gem is not None, "data": gem,
            "say_hint": ("share the specifics that matter: when to go, how to get in, "
                         "price feel." if gem else
                         "no such gem — re-run search_gems and use an id from the results.")})

    async def _kb_city_guide(self, payload: dict) -> None:
        raw = str(payload.get("city", "")).strip()
        if await self._load_city_guide(raw):
            return   # the guide itself is the reply
        # Already loaded -> nothing captured -> the SILENT no-op reply; truly
        # unknown cities deserve an honest miss instead.
        if kb.resolve_city(self._conn, raw) or not raw:
            return
        await self._send("kb_result", {
            "ok": False, "data": None,
            "say_hint": f"you have no notes on {raw} — if it comes up, share your own "
                        "closest knowledge honestly (clearly off-book) and remember "
                        "what they were looking for."})

    async def _kb_remember(self, payload: dict) -> None:
        note = str(payload.get("note", "")).strip()
        person = str(payload.get("person", "")).strip()
        note_key = " ".join(note.lower().split())
        if not note or note_key in self._seen_notes:
            log.info("duplicate/empty remember note ignored")
            return
        self._seen_notes.add(note_key)
        if self.gate.verified is None:
            # No account yet: park the note under a pending account number so it
            # survives a hangup; fall back to the in-call buffer without a
            # pending store. Either way it lands on the profile at register/verify
            # — never re-ask the caller.
            pending = self._ensure_pending_account()
            if pending is not None:
                self._pending_store.append_note(pending.account_number, note,
                                                person=person)
            else:
                self._pending_notes.append(note)
        else:
            self._store.append_note(self.gate.verified.account_number, note,
                                    person=person)
        await self._send("kb_result", {
            "ok": True, "data": None,
            "say_hint": "SILENT: noted — say nothing about this; continue naturally."})

    async def _caller_name(self, payload: dict) -> None:
        """File the caller's name on their record the moment it is settled —
        BEFORE any registration. On the pending account it becomes the name the
        next call greets them by; on a matched real account it only fills an
        empty name (a verified name is never overwritten by voice)."""
        name = str(payload.get("name", "")).strip()
        if not name:
            await self._send("kb_result", {
                "ok": False,
                "say_hint": "caller_name needs a name — ask for it (spelled out)."})
            return
        target = None
        if self.gate.verified is not None:
            target = (self._store, self.gate.verified)
        elif self.gate.matched is not None:
            target = (self._store, self.gate.matched) if not self.gate.matched.name else None
        else:
            pending = self._ensure_pending_account()
            if pending is not None:
                target = (self._pending_store, pending)
        if target is not None:
            store, account = target
            account.name = name
            store.save(account)
            log.info("caller name set: %s (account %s)", name, account.account_number)
        await self._send("kb_result", {
            "ok": True, "data": None,
            "say_hint": "SILENT: name noted — say nothing about this; continue naturally."})

    def _flush_notes(self, account_number: str) -> None:
        for note in self._pending_notes:
            self._store.append_note(account_number, note)
        if self._pending_notes:
            log.info("flushed %d pre-account notes to %s",
                     len(self._pending_notes), account_number)
        self._pending_notes.clear()

    def _ensure_pending_account(self) -> Account | None:
        """The parked pre-registration account: a real minted number in
        accounts-pending/ with no PIN and no booking channel."""
        if self._pending_store is None:
            return None
        if self._pending_account is None:
            number = self._new_account_number()
            self._pending_account = Account(
                account_number=number, pin="", name="",
                phones=[self._caller_id] if self._caller_id else [])
            self._pending_store.save(self._pending_account)
            self._pending_created_now = True
            log.info("pending account %s parked (notes persist pre-registration)", number)
        return self._pending_account

    def _adopt_pending(self, account_number: str) -> None:
        """Fold the parked account into a real one: same number at registration
        (file just moves out of accounts-pending/), or migrate the notes when the
        caller verified into a different existing account."""
        if self._pending_account is None or self._pending_store is None:
            return
        old = self._pending_account.account_number
        moved = self._pending_store.move_notes(old, self._store, account_number)
        if moved or old != account_number:
            log.info("adopted pending %s -> %s (%d notes)", old, account_number, moved)
        self._pending_store.remove(old)
        self._pending_account = None

    async def _kb_add(self, payload: dict) -> None:
        if (refuse := self._kb_gate_hint()) is not None:
            await self._send("kb_result", refuse)
            return
        name = str(payload.get("name", "")).strip()
        city = str(payload.get("city", "")).strip()
        pitch = str(payload.get("pitch", "")).strip()
        if not (name and city and pitch):
            await self._send("kb_result", {
                "ok": False, "data": None,
                "say_hint": "add_gem needs name, city, and pitch — collect what's missing."})
            return
        gem = kb.add_gem(self._conn, name=name, city=city, pitch=pitch,
                         tags=str(payload.get("tags", "")), source="caller")
        await self._send("kb_result", {
            "ok": True, "data": {"id": gem["id"]},
            "say_hint": f"gem '{name}' saved to the {city} collection — thank them for "
                        "the local tip."})

    # ---- booking ------------------------------------------------------------------------

    async def _booking_gate(self) -> BookingSession | None:
        if self.gate.verified is None:
            await self._send("booking_result", {
                "ok": False, "detail": "not verified",
                "say_hint": "the caller isn't verified yet — complete verification "
                            "before taking booking requests."})
            return None
        if self.booking is None:
            await self._send("booking_result", {
                "ok": False, "detail": "booking unavailable",
                "say_hint": "booking is not available on this line right now — apologize."})
            return None
        return self.booking

    async def _booking_reply(self, detail: str) -> None:
        await self._send("booking_result", {
            "ok": not detail.startswith(_BOOKING_FAIL_PREFIXES),
            "detail": detail, "say_hint": detail})

    async def _booking_establish(self, payload: dict) -> None:
        booking = await self._booking_gate()
        if booking is None:
            return
        await self._load_city_guide(str(payload.get("location", "")))
        start_date, date_note = normalize_start_date(str(payload.get("start_date", "")))
        detail = await booking.establish_case(
            str(payload.get("location", "")), start_date,
            payload.get("days", ""), str(payload.get("reason", "")))
        if date_note:
            detail = f"{detail}\n{date_note}"
        await self._booking_reply(detail)

    async def _booking_request(self, payload: dict) -> None:
        booking = await self._booking_gate()
        if booking is None:
            return
        raw = str(payload.get("kind") or payload.get("tag") or "").strip().lower()
        kind = raw.removeprefix("[booking-").removesuffix("]").strip("[] -")
        tool = KIND_TO_TOOL.get(kind)
        if tool is None:
            await self._send("booking_result", {
                "ok": False, "detail": f"unknown kind: {payload.get('kind')!r}",
                "say_hint": "booking_request kind must be explore, confirmed, update, "
                            "canceled, or booked."})
            return
        detail = await booking.request(tool, str(payload.get("details", "")),
                                       title=str(payload.get("title", "")))
        if detail.startswith("that request is already filed") or detail.startswith("SILENT"):
            log.info("duplicate booking_request ignored")
            return
        await self._booking_reply(detail)
