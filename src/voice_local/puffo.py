"""Puffo booking layer — file booking requests into a Puffo chat thread where a
human fulfiller responds, without ever blocking the live phone conversation.

Wraps the `puffo` CLI binary via asyncio subprocesses:
- send:   one short-lived `puffo message send ...` per request; the envelope id is
  parsed from its stdout (`sent: msg_... (N devices)` — see puffo-cli send.rs). The
  parse is isolated in ``parse_send_envelope_id`` because it's the one seam that may
  need adjusting after live testing.
- receive: ONE long-lived `puffo message listen --json` per daemon (NDJSON events),
  fanned out to per-request watchers through subscriber queues (same drop-nothing
  pattern as EventBus, but bounded). Auto-restarts with backoff if it dies.

Threading model: each caller gets a channel PER DESTINATION, named
``<destination-slug>-<account_number>`` inside the bot-owned space (see
``ensure_user_channel``; ids persist in the account's ``channels``). Inside that
channel each trip gets a ROOT message named
``<location>-<startDate>-<days>-<reason-slug>`` (no account id — the thread name is
visible to the whole channel); the root's envelope id is the thread handle, persisted
in the account's ``booking_threads`` so a later call about the same trip reuses it.
Request tools return immediately; a per-request asyncio task watches for the FIRST
fulfiller reply in that thread and injects it into the live voice session, or injects
a timeout notice after 5 minutes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger("voice_local.puffo")

RESOLUTION_TIMEOUT_S = 300.0  # 5 minutes: a human fulfiller should ack within this
_SEND_ID_RE = re.compile(r"\b(msg_[A-Za-z0-9][\w-]*)\b")
_CHANNEL_ID_RE = re.compile(r"\b(ch_[A-Za-z0-9][\w-]*)\b")

_FALLBACK_BINS = (
    Path.home() / ".cargo/bin/puffo",
    Path("/home/blin/Experiments/voice/puffo-cli/target/release/puffo"),
)


def resolve_puffo_bin(configured: str = "") -> str:
    """Locate the puffo binary: explicit setting > PATH > known local builds."""
    if configured:
        return configured
    found = shutil.which("puffo")
    if found:
        return found
    for p in _FALLBACK_BINS:
        if p.exists():
            return str(p)
    return ""


def parse_send_envelope_id(output: str) -> str:
    """Extract the sent envelope id from `puffo message send` output.

    Current CLI prints `sent: msg_... (N devices)` on stdout; we accept any line
    carrying a msg_ token so minor format drift doesn't break capture. Returns ""
    when no id is present (caller decides how to degrade)."""
    m = _SEND_ID_RE.search(output or "")
    return m.group(1) if m else ""


def parse_created_channel_id(output: str) -> str:
    """Extract the new channel id from `puffo channel create` output.

    Current CLI prints `created channel: ch_...`; like the send parse, any line
    carrying a ch_ token is accepted. Returns "" when no id is present."""
    m = _CHANNEL_ID_RE.search(output or "")
    return m.group(1) if m else ""


_HISTORY_LINE_RE = re.compile(r"^\d+\s+msg_[0-9a-f-]+\s+(?P<who>\S+): (?P<text>.*)$")


def format_thread_history(raw: str, *, max_lines: int = 20) -> str:
    """Condense `message history` output to `sender: text` lines (newest last) for
    prompt injection — timestamps/envelope ids/thread markers are noise to the model."""
    lines = []
    for line in (raw or "").splitlines():
        m = _HISTORY_LINE_RE.match(line.strip())
        if not m:
            continue
        text = re.sub(r"\s*\[thread:msg_[0-9a-f-]+\]$", "", m.group("text"))
        lines.append(f"{m.group('who')}: {text}")
    return "\n".join(lines[-max_lines:])


def _slug(text: str) -> str:
    """Lowercase kebab: 'Food & Sake Tour!' -> 'food-sake-tour'."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", str(text).lower())).strip("-")


def booking_thread_name(location: str, start_date: str, days: object, reason: str = "") -> str:
    """Short coordination title: `[booking] <location> <date> <N> days`. The reason is
    NOT part of the name (it was producing unreadable multi-clause slugs) — it's posted
    as context inside the thread instead. One trip (location+date+days) = one thread."""
    return f"[booking] {_slug(location)} {_slug(start_date)} {_slug(str(days))} days"


def is_resolution(event: dict, *, thread_root: str, fulfiller: str, request_id: str = "",
                  channel_id: str = "") -> bool:
    """Does this listen event resolve a request in `thread_root`? Only a real chat
    message, from the fulfiller, threaded under our root (or the root itself, if the
    fulfiller somehow answers top-level with our root id), and not our own request."""
    if event.get("type") != "message":
        return False
    if event.get("sender_slug") != fulfiller:
        return False
    if channel_id and event.get("channel_id") not in (None, channel_id):
        return False
    if request_id and event.get("envelope_id") == request_id:
        return False
    return event.get("thread_root_id") == thread_root


class PuffoClient:
    """Thin async wrapper over one-shot `puffo message send` invocations."""

    def __init__(self, *, bin: str, server_url: str, channel_id: str,
                 identity: str = "", timeout: float = 30.0, space_id: str = "") -> None:
        self.bin = bin
        self.server_url = server_url
        self.channel_id = channel_id
        self.identity = identity
        self.timeout = timeout
        # The CLI resolves --channel through a local directory cache that goes stale
        # for channels created/renamed by others; an explicit --space always works.
        self.space_id = space_id

    def _space_argv(self) -> list[str]:
        return ["--space", self.space_id] if self.space_id else []

    def _base_argv(self) -> list[str]:
        argv = [self.bin, "--server-url", self.server_url]
        if self.identity:
            argv += ["--identity", self.identity]
        return argv

    async def _run(self, argv: list[str], *, label: str) -> str | None:
        """One short-lived puffo invocation -> stdout text, or None on timeout /
        nonzero exit (already logged). All one-shot subcommands funnel through here."""
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            log.warning("puffo %s timed out after %.0fs", label, self.timeout)
            return None
        if proc.returncode != 0:
            log.warning("puffo %s failed rc=%s: %s", label, proc.returncode,
                        (err or out).decode(errors="replace").strip()[:300])
            return None
        return out.decode(errors="replace")

    async def send(self, text: str, *, thread: str = "", channel: str = "") -> str:
        """Post `text` to the channel (optionally as a thread reply); returns the
        envelope id ("" if the send failed or the id couldn't be parsed). `channel`
        overrides the client's default channel (per-user destination channels)."""
        argv = self._base_argv() + ["message", "send", text,
                                    "--channel", channel or self.channel_id, *self._space_argv()]
        if thread:
            argv += ["--thread", thread]
        out = await self._run(argv, label="send")
        if out is None:
            return ""
        env_id = parse_send_envelope_id(out)
        if not env_id:
            log.warning("puffo send: could not parse envelope id from %r", out[:200])
        return env_id

    async def history(self, thread: str, *, limit: int = 30, channel: str = "") -> str:
        """Raw `message history` output for one thread ("" on failure). Used to warm a
        new call with the caller's existing booking state instead of cold-starting."""
        argv = self._base_argv() + ["message", "history",
                                    "--channel", channel or self.channel_id, *self._space_argv(),
                                    "--thread", thread, "--limit", str(limit)]
        return await self._run(argv, label="history") or ""

    async def channel_history(self, channel: str = "", *, limit: int = 150) -> str:
        """Raw `message history` for a WHOLE channel (no --thread; "" on failure) —
        the fresh-parse source for the caller's per-destination trip context."""
        argv = self._base_argv() + ["message", "history",
                                    "--channel", channel or self.channel_id, *self._space_argv(),
                                    "--limit", str(limit)]
        return await self._run(argv, label="channel-history") or ""

    async def create_channel(self, name: str, space_id: str) -> str:
        """`puffo channel create <name> --space <space_id>` -> the new channel id
        ("" on failure or when no ch_ id could be parsed from stdout)."""
        argv = self._base_argv() + ["channel", "create", name, "--space", space_id]
        out = await self._run(argv, label="channel create")
        if out is None:
            return ""
        ch_id = parse_created_channel_id(out)
        if not ch_id:
            log.warning("puffo channel create: could not parse channel id from %r", out[:200])
        return ch_id

    async def invite(self, slug: str, channel_id: str) -> bool:
        """Invite `slug` into a channel (so the human fulfiller sees it). True on
        success; failures are logged and non-fatal — booking still works, unseen.
        --space is passed explicitly: the CLI's local channel cache goes stale for
        channels created by others and then refuses the id outright."""
        argv = self._base_argv() + ["invitation", "send", slug, "--channel", channel_id,
                                    *self._space_argv()]
        return await self._run(argv, label="invite") is not None

    def listen_argv(self) -> list[str]:
        return self._base_argv() + ["message", "listen", "--json"]


async def ensure_user_channel(client: PuffoClient, store, account, destination: str, *,
                              space_id: str, fulfiller_slug: str,
                              invite_slugs: list[str] | None = None) -> str:
    """The caller's per-destination channel id, creating it on first use.

    Channels are named ``<destination-slug>-<account_number>`` (e.g. japan-470400)
    inside the bot-owned space; the standing membership (`invite_slugs`, always
    including the fulfiller) is invited so the humans and agents who coordinate
    bookings see every new guest channel. The mapping is persisted on the account
    keyed by destination slug. On creation failure the client's default (shared)
    channel is returned — booking degrades to the old single-channel behavior
    rather than breaking the call."""
    slug = _slug(destination)
    existing = account.channels.get(slug, "")
    if existing:
        return existing
    name = f"{slug}-{account.account_number}"
    channel_id = await client.create_channel(name, space_id)
    if not channel_id:
        log.warning("could not create user channel %r — falling back to the shared "
                    "channel %s", name, client.channel_id)
        return client.channel_id
    invitees = list(dict.fromkeys([*(invite_slugs or []), fulfiller_slug]))
    for member in filter(None, invitees):
        if not await client.invite(member, channel_id):
            log.warning("could not invite %s into %s (%s)", member, name, channel_id)
    account.channels[slug] = channel_id
    store.save(account)
    return channel_id


class PuffoListener:
    """One long-lived `puffo message listen --json` per daemon. Parses NDJSON events
    and fans `type=="message"` dicts out to subscriber queues. Restarts the subprocess
    with exponential backoff (reset after a healthy run) — booking watchers must keep
    working across relay hiccups for the daemon's whole lifetime."""

    def __init__(self, client: PuffoClient, *, max_backoff: float = 30.0) -> None:
        self._client = client
        self._max_backoff = max_backoff
        self._subs: set[asyncio.Queue[dict]] = set()
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subs.discard(q)

    def _dispatch(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # drop oldest, keep live (EventBus pattern)
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="puffo-listen")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            started = time.monotonic()
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._client.listen_argv(),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                assert self._proc.stdout is not None
                async for raw in self._proc.stdout:
                    line = raw.decode(errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type") == "message":
                        self._dispatch(event)
                await self._proc.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the daemon's ear alive
                log.warning("puffo listen error: %r", exc)
            if time.monotonic() - started > 60:
                backoff = 1.0  # it ran healthily for a while; restart eagerly
            log.info("puffo listen exited; restarting in %.0fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)


class BookingSession:
    """Per-call booking orchestration for ONE verified account: the five voice-brain
    tools land here. Every request posts immediately and answers the model right away
    ("keep chatting"); a watcher task per request injects the fulfiller's first reply
    (or a 5-minute timeout notice) into the live session via `inject`."""

    TAGS = {
        "explore_booking": "[booking-explore]",
        "confirm_booking": "[booking-confirmed]",
        "update_booking": "[booking-update]",
        "cancel_booking": "[booking-canceled]",
    }

    def __init__(self, *, client: PuffoClient, listener: PuffoListener, account,
                 account_store, fulfiller_slug: str, bus=None, inject=None,
                 timeout_s: float = RESOLUTION_TIMEOUT_S, channel_id: str = "") -> None:
        self._client = client
        self._listener = listener
        # the channel this call's booking traffic lives in — the caller's
        # per-destination channel when given, else the client's shared default
        self.channel_id = channel_id or client.channel_id
        self.account = account
        self._store = account_store
        self._fulfiller = fulfiller_slug
        self._bus = bus
        self._inject = inject  # async (text: str) -> None into the live session
        self._timeout_s = timeout_s
        self.thread_root: str = ""   # active trip thread for this call
        self.thread_name: str = ""
        self._watchers: set[asyncio.Task] = set()
        self._last_request: tuple = (None, 0.0)  # (tag+details key, monotonic ts)
        # Everything filed this call, for the end-of-call [booking-itinerary] post:
        # {"tag", "details", "status": pending|replied|timeout, "reply": str}
        self._activity: list[dict] = []

    async def _publish(self, name: str, args: dict) -> None:
        """Observability seam: `bus` is any object with `async publish(name, args)`
        (vox-local has no event bus of its own; None is the norm)."""
        if self._bus is not None:
            await self._bus.publish(name, args)

    async def establish_case(self, location: str, start_date: str, days, reason: str) -> str:
        """Open (or reuse) the trip thread: post the ROOT message named exactly
        `<location>-<startDate>-<days>-<reason-slug>` and persist its envelope id in
        the account's booking_threads keyed by that name."""
        name = booking_thread_name(location, start_date, days, reason)
        existing = self.account.booking_threads.get(name, "")
        if existing:
            self.thread_root, self.thread_name = existing, name
            await self._publish("booking_thread", {"name": name, "root": existing, "reused": True})
            return (f"existing booking thread '{name}' reopened — use the booking tools to "
                    f"explore, confirm, update, or cancel; keep chatting meanwhile.")
        env_id = await self._client.send(name, channel=self.channel_id)
        if not env_id:
            return "could not reach the booking channel — apologize and offer to try again later."
        self.account.booking_threads[name] = env_id
        self._store.save(self.account)
        self.thread_root, self.thread_name = env_id, name
        if reason.strip() or self._fulfiller:
            # The reason rides INSIDE the thread (the title stays short by design),
            # and the fulfiller is @-tagged so every thread that needs her work
            # notifies her from message one.
            mention = f"@{self._fulfiller} " if self._fulfiller else ""
            await self._client.send(f"[booking-context] {mention}{reason.strip()}".strip(),
                                    thread=env_id, channel=self.channel_id)
        await self._publish("booking_thread", {"name": name, "root": env_id, "reused": False})
        return (f"booking thread '{name}' opened — use explore/confirm/update/cancel tools "
                f"for requests; keep chatting meanwhile.")

    async def request(self, tool: str, details: str) -> str:
        """explore/confirm/update/cancel — post `[tag] details` into the trip thread and
        watch for the fulfiller's reply without blocking the conversation."""
        tag = self.TAGS[tool]
        if not self.thread_root:
            return "no booking thread yet — call establish_case with the trip details first."
        # Voice models retry tool calls while awaiting slow replies; an identical
        # request within a minute is a retry, not a new ask — don't double-post.
        key = (tag, " ".join(details.split())[:200])
        if key == self._last_request[0] and time.monotonic() - self._last_request[1] < 60:
            return ("SILENT: this exact request is already filed — say absolutely nothing "
                    "about it; do not re-announce; continue the conversation naturally.")
        self._last_request = (key, time.monotonic())
        mention = f"@{self._fulfiller} " if self._fulfiller else ""
        env_id = await self._client.send(f"{tag} {mention}{details}", thread=self.thread_root,
                                         channel=self.channel_id)
        if not env_id:
            return "could not post the request — apologize and offer to try again."
        await self._publish("booking_request", {"tag": tag, "details": details,
                                                "thread": self.thread_name, "envelope_id": env_id})
        entry = {"tag": tag, "details": details, "status": "pending", "reply": ""}
        self._activity.append(entry)
        task = asyncio.create_task(self._watch(tag, env_id, entry),
                                   name=f"booking-watch-{env_id}")
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)
        return ("request posted to the human fulfiller; keep chatting naturally — you'll get "
                "a [Booking update] message when they respond (usually a few minutes).")

    async def _watch(self, tag: str, request_id: str, entry: dict | None = None) -> None:
        """Wait for the FIRST fulfiller message in our thread newer than the request;
        inject it. On timeout, inject the retry/cancel notice. Never raises."""
        q = self._listener.subscribe()
        try:
            deadline = time.monotonic() + self._timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                event = await asyncio.wait_for(q.get(), timeout=remaining)
                if is_resolution(event, thread_root=self.thread_root, fulfiller=self._fulfiller,
                                 request_id=request_id, channel_id=self.channel_id):
                    text = str(event.get("content", "")).strip()
                    if entry is not None:
                        entry["status"], entry["reply"] = "replied", text
                    await self._publish("booking_resolution",
                                        {"tag": tag, "text": text, "thread": self.thread_name})
                    if self._inject is not None:
                        await self._inject(f"[Booking update]: {text}")
                    return
        except asyncio.TimeoutError:
            if entry is not None:
                entry["status"] = "timeout"
            await self._publish("booking_timeout", {"tag": tag, "thread": self.thread_name})
            if self._inject is not None:
                await self._inject("[Booking]: request timed out after 5 minutes — tell the "
                                   "caller and offer to retry or cancel.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a watcher must never take the call down
            log.warning("booking watcher error: %r", exc)
        finally:
            self._listener.unsubscribe(q)

    async def close(self) -> None:
        for t in list(self._watchers):
            t.cancel()
        await self._post_itinerary()

    async def _post_itinerary(self) -> None:
        """End-of-call consolidation: one [booking-itinerary] message stating every
        request filed this call and where it stands, so the thread reads as a single
        coordinated plan instead of scattered asks. Best-effort — never raises."""
        if not self._activity or not self.thread_root:
            return
        status_word = {"pending": "awaiting partner reply", "timeout": "no reply (timed out)"}
        lines = [f"[booking-itinerary] {self.thread_name} — call summary "
                 f"({len(self._activity)} request(s)):"]
        for i, e in enumerate(self._activity, 1):
            line = f"{i}. {e['tag']} {e['details'][:200]}"
            if e["status"] == "replied":
                line += f" -> partner: {e['reply'][:200]}"
            else:
                line += f" -> {status_word.get(e['status'], e['status'])}"
            lines.append(line)
        try:
            await self._client.send("\n".join(lines), thread=self.thread_root,
                                    channel=self.channel_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not raise
            log.warning("itinerary post failed: %r", exc)


# ---- voice-brain tool schemas (post-auth only; the factory gates them) --------------

ESTABLISH_CASE_TOOL = {
    "type": "function",
    "name": "establish_case",
    "description": (
        "Open (or reopen) the booking-request thread for ONE trip. Call this once before "
        "any explore/confirm/update/cancel booking tool. Returns immediately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "trip location, e.g. 'Kobe'"},
            "start_date": {"type": "string", "description": "start date, e.g. '2026-08-01'"},
            "days": {"type": "integer", "description": "trip length in days"},
            "reason": {"type": "string", "description": "short reason, e.g. 'sake tastings'"},
        },
        "required": ["location", "start_date", "days", "reason"],
    },
}


def _booking_tool(name: str, description: str) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description + " Returns immediately; the human fulfiller replies "
        "asynchronously as a [Booking update] — keep the conversation going meanwhile.",
        "parameters": {
            "type": "object",
            "properties": {"details": {"type": "string",
                                        "description": "full request details in one message"}},
            "required": ["details"],
        },
    }


EXPLORE_BOOKING_TOOL = _booking_tool(
    "explore_booking", "Ask the human fulfiller for options/availability/prices for this trip.")
CONFIRM_BOOKING_TOOL = _booking_tool(
    "confirm_booking", "Ask the human fulfiller to lock in a specific booking.")
UPDATE_BOOKING_TOOL = _booking_tool(
    "update_booking", "Ask the human fulfiller to change an existing booking request.")
CANCEL_BOOKING_TOOL = _booking_tool(
    "cancel_booking", "Cancel a booking or a pending request — ALWAYS allowed, even while "
    "other requests are still pending.")

BOOKING_TOOLS = [ESTABLISH_CASE_TOOL, EXPLORE_BOOKING_TOOL, CONFIRM_BOOKING_TOOL,
                 UPDATE_BOOKING_TOOL, CANCEL_BOOKING_TOOL]
