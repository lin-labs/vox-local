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
                "update": "update_booking", "canceled": "cancel_booking"}

_BOOKING_FAIL_PREFIXES = ("could not", "no booking thread")

_LOCKED_HINT = ("verification failed too many times — politely say you can't verify "
                "the account today, say goodbye, and end the call.")


def _today_line() -> str:
    """Date grounding: voice LLMs reliably hallucinate the year ('January' -> a
    PAST January) unless the current date is stated in-band."""
    today = _dt.date.today()
    return f"Today is {today.strftime('%A, %B %d, %Y')} ({today.isoformat()})."


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
                 puffo: tuple | None = None, caller_id: str = "",
                 destination: str = "", grok=None,
                 fulfiller_slug: str = "", space_id: str = "",
                 send: Callable[[str, dict], Awaitable[None]]) -> None:
        self._conn = conn
        self._store = store
        self._puffo = puffo            # (PuffoClient, PuffoListener) or None
        self._caller_id = caller_id
        self._destination = destination
        self._grok = grok
        self._fulfiller = fulfiller_slug
        self._space_id = space_id
        self._send = send              # async (action, payload) -> queue for the reply
        self._seen_notes: set[str] = set()
        matched = store.lookup_by_phone(caller_id) if caller_id else None
        self.gate = AuthGate(store, matched=matched)
        self.booking: BookingSession | None = None

    # ---- op dispatch (query grammar) --------------------------------------------

    _QUERY_OPS = {"verify": "_verify", "register": "_register",
                  "change_pin": "_change_pin", "search_gems": "_kb_search",
                  "get_gem": "_kb_get", "remember": "_kb_remember",
                  "add_gem": "_kb_add", "booking_establish": "_booking_establish",
                  "booking_request": "_booking_request"}

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
        return "\n".join(parts) or "ok"

    async def close(self) -> None:
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
        channel_id = await self._ensure_channel(account)
        self._start_booking(account, channel_id)
        await self._send_caller_context(account, channel_id)

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
            pin = "".join(random.choices("0123456789", k=4))
        number = self._new_account_number()
        account = Account(account_number=number, pin=pin, name=name,
                          phones=[self._caller_id] if self._caller_id else [])
        self._store.save(account)
        kb.ensure_profile(self._conn, number, name=name, phone=self._caller_id)
        self.gate.verified = account
        channel_id = await self._ensure_channel(account)
        self._start_booking(account, channel_id)
        await self._send("registration_result", {
            "ok": True, "account_number": number, "name": name, "pin": pin,
            "say_hint": f"registered — welcome {name}. Tell them their account number is "
                        f"{number}: read it one digit at a time ({', '.join(number)}) and "
                        f"ask them to note it down. Their PIN is {pin}: read it one digit "
                        f"at a time ({', '.join(pin)}), and tell them they can ask you to "
                        "change it right now if they'd prefer their own. They'll need the "
                        f"account number and PIN when calling from another phone. "
                        f"{_today_line()}"})
        await self._send_caller_context(account, channel_id)

    def _new_account_number(self) -> str:
        while True:
            number = str(random.randrange(100000, 1000000))
            if self._store.get(number) is None:
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

    async def _ensure_channel(self, account: Account) -> str:
        if self._puffo is None:
            return ""
        client, _listener = self._puffo
        if not self._destination:
            return client.channel_id
        return await ensure_user_channel(
            client, self._store, account, self._destination,
            space_id=self._space_id, fulfiller_slug=self._fulfiller)

    async def _send_caller_context(self, account: Account, channel_id: str) -> None:
        brief = kb.profile_brief(self._conn, account.account_number)
        trip_summary = ""
        if self._grok is not None and self._puffo is not None and channel_id:
            client, _listener = self._puffo
            trip_summary = trip_context_text(
                await load_trip_context(client, channel_id, self._grok))
        await self._send("caller_context", {"brief": brief, "trip_summary": trip_summary})

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
        if self.gate.verified is None:
            return {"ok": False, "data": None,
                    "say_hint": "the caller isn't verified yet — politely complete "
                                "verification before sharing recommendations."}
        return None

    async def _kb_search(self, payload: dict) -> None:
        if (refuse := self._kb_gate_hint()) is not None:
            await self._send("kb_result", refuse)
            return
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
        if (refuse := self._kb_gate_hint()) is not None:
            await self._send("kb_result", refuse)
            return
        gem = kb.get_gem(self._conn, str(payload.get("id", "")))
        await self._send("kb_result", {
            "ok": gem is not None, "data": gem,
            "say_hint": ("share the specifics that matter: when to go, how to get in, "
                         "price feel." if gem else
                         "no such gem — re-run search_gems and use an id from the results.")})

    async def _kb_remember(self, payload: dict) -> None:
        if (refuse := self._kb_gate_hint()) is not None:
            await self._send("kb_result", refuse)
            return
        note = str(payload.get("note", "")).strip()
        note_key = " ".join(note.lower().split())
        if not note or note_key in self._seen_notes:
            log.info("duplicate/empty remember note ignored")
            return
        self._seen_notes.add(note_key)
        kb.add_note(self._conn, self.gate.verified.account_number, note)
        await self._send("kb_result", {
            "ok": True, "data": None,
            "say_hint": "SILENT: noted — say nothing about this; continue naturally."})

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
                            "or canceled."})
            return
        detail = await booking.request(tool, str(payload.get("details", "")))
        if detail.startswith("that request is already filed") or detail.startswith("SILENT"):
            log.info("duplicate booking_request ignored")
            return
        await self._booking_reply(detail)
