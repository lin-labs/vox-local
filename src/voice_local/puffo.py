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
``<account_number>-<first-name>`` inside the bot-owned space (see
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


def booking_thread_title(tool: str, title: str) -> str:
    """Human-relatable thread head: `[booking] Shinkansen from Nagoya to Osaka`
    (or `[explore] ...` when the thread exists purely to research something).
    ONE thread = ONE booking item; status changes are posted back into the
    thread echoing this title with a new tag ([booked], [booking-canceled]) so
    the latest message shows where things stand at a glance."""
    prefix = "[explore]" if tool == "explore_booking" else "[booking]"
    return f"{prefix} {' '.join(str(title).split())}"


def _norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


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

    Channels are named ``<account_number>-<first-name>`` (e.g. 470400-mika) —
    the whole space is about Japan already, so the guest's name is the warm,
    non-redundant label — inside the bot-owned space; the standing membership
    (`invite_slugs`, always including the fulfiller) is invited so the humans
    and agents who coordinate bookings see every new guest channel. The mapping
    is persisted on the account keyed by destination slug. On creation failure
    the client's default (shared) channel is returned — booking degrades to the
    old single-channel behavior rather than breaking the call."""
    slug = _slug(destination)
    existing = account.channels.get(slug, "")
    if existing:
        return existing
    first = _slug((account.name or "").split(" ")[0]) if account.name else ""
    name = f"{account.account_number}-{first}" if first else account.account_number
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
        "mark_booked": "[booked]",
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
        self.thread_root: str = ""   # most recently touched ITEM thread this call
        self.thread_name: str = ""
        self.trip_context: str = ""  # "Nagoya, 2026-12-15, 2 days, family trip"
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
        """Record the trip's shape for this call. No thread is opened here —
        every booking ITEM gets its own human-titled thread at request time;
        the trip context rides inside each item's first message instead."""
        bits = [p for p in (str(location).strip(), str(start_date).strip(),
                            f"{days} days" if str(days).strip() else "",
                            str(reason).strip()) if p]
        self.trip_context = ", ".join(bits)
        await self._publish("booking_trip", {"context": self.trip_context})
        return ("trip noted — now file EACH booking or exploration as its own thread: "
                "booking_request with kind and a short human title naming the single "
                "thing (e.g. 'Shinkansen from Nagoya to Osaka', 'Ryokan night in "
                "Hakone'); keep chatting meanwhile.")

    def _find_thread(self, title: str) -> tuple[str, str]:
        """(name, root) of the existing item thread best matching `title`."""
        want = _norm_title(title)
        if not want:
            return "", ""
        for name, root in reversed(list(self.account.booking_threads.items())):
            have = _norm_title(re.sub(r"^\[[a-z-]+\]\s*", "", name))
            if have and (have == want or want in have or have in want):
                return name, root
        return "", ""

    async def request(self, tool: str, details: str, title: str = "") -> str:
        """One booking item = one human-titled thread. explore/confirmed open the
        thread (`[explore]/[booking] <title>`) and post the details inside;
        update/canceled/booked post back into the matching thread echoing the
        title with the new tag, so the latest message reads as the item's status.
        A watcher injects the fulfiller's first reply without blocking the call."""
        tag = self.TAGS[tool]
        title = " ".join(str(title).split())
        # Voice models retry tool calls while awaiting slow replies; an identical
        # request within a minute is a retry, not a new ask — don't double-post.
        key = (tag, title, " ".join(details.split())[:200])
        if key == self._last_request[0] and time.monotonic() - self._last_request[1] < 60:
            return ("SILENT: this exact request is already filed — say absolutely nothing "
                    "about it; do not re-announce; continue the conversation naturally.")
        self._last_request = (key, time.monotonic())
        name, root = self._find_thread(title)
        if not root and tool in ("update_booking", "cancel_booking", "mark_booked"):
            name, root = self.thread_name, self.thread_root   # fall back to active item
        if not root:
            if not title:
                return ("this booking needs a short human 'title' naming the single "
                        "thing (e.g. 'Shinkansen from Nagoya to Osaka') — resend "
                        "booking_request with a title.")
            name = booking_thread_title(tool, title)
            root = await self._client.send(name, channel=self.channel_id)
            if not root:
                return "could not reach the booking channel — apologize and offer to try again later."
            self.account.booking_threads[name] = root
            self._store.save(self.account)
            await self._publish("booking_thread", {"name": name, "root": root, "reused": False})
        self.thread_root, self.thread_name = root, name
        # The canonical thread head wins over the model's re-typed casing.
        display = re.sub(r"^\[[a-z-]+\]\s*", "", name).strip() or title
        mention = f"@{self._fulfiller} " if self._fulfiller else ""
        trip = f" (trip: {self.trip_context})" if self.trip_context else ""
        if tool in ("explore_booking", "confirm_booking"):
            text = f"{tag} {mention}{details}{trip}"
        else:
            # Status change: echo the title with the new tag so the thread's
            # newest message shows where the item stands at a glance.
            text = f"{tag} {display}" + (f" — {mention}{details}" if details.strip() else "")
        env_id = await self._client.send(text, thread=root, channel=self.channel_id)
        if not env_id:
            return "could not post the request — apologize and offer to try again."
        await self._publish("booking_request", {"tag": tag, "details": details,
                                                "thread": name, "envelope_id": env_id})
        if tool == "mark_booked":
            return f"marked booked: {display}. Keep chatting naturally."
        entry = {"tag": tag, "title": display, "details": details,
                 "status": "pending", "reply": ""}
        self._activity.append(entry)
        task = asyncio.create_task(self._watch(tag, env_id, entry, thread_root=root),
                                   name=f"booking-watch-{env_id}")
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)
        return ("request posted to the human fulfiller; keep chatting naturally — you'll get "
                "a [Booking update] message when they respond (usually a few minutes).")

    async def _watch(self, tag: str, request_id: str, entry: dict | None = None, *,
                     thread_root: str = "") -> None:
        """Wait for the FIRST fulfiller message in the item's thread newer than the
        request; inject it. On timeout, inject the retry/cancel notice. Never raises."""
        thread_root = thread_root or self.thread_root
        q = self._listener.subscribe()
        try:
            deadline = time.monotonic() + self._timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                event = await asyncio.wait_for(q.get(), timeout=remaining)
                if is_resolution(event, thread_root=thread_root, fulfiller=self._fulfiller,
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
        if not self._activity:
            return
        status_word = {"pending": "awaiting partner reply", "timeout": "no reply (timed out)"}
        lines = [f"[booking-itinerary] call summary ({len(self._activity)} request(s)):"]
        for i, e in enumerate(self._activity, 1):
            line = f"{i}. {e['tag']} {e.get('title', '')}: {e['details'][:200]}"
            if e["status"] == "replied":
                line += f" -> partner: {e['reply'][:200]}"
            else:
                line += f" -> {status_word.get(e['status'], e['status'])}"
            lines.append(line)
        try:
            # Channel-level post: the summary spans every item thread this call.
            await self._client.send("\n".join(lines), channel=self.channel_id)
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
