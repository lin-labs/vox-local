"""Trip context — a FRESH per-call understanding of the caller's booking channel.

On every call the caller's per-destination Puffo channel (see
``ensure_user_channel``) is re-read end to end: the channel history plus each
thread's history is condensed to ``sender: text`` lines and handed to ONE text-LLM
completion (GrokChat) with a strict-JSON extraction prompt. The result — trips,
booked/pending items, and suggested next categories — is what the voice agent
receives as silent context, so it never re-asks about a trip the channel already
settled. Nothing is cached and nothing is auto-written back to the profile (the
agent has remember_about_caller for durable facts); any failure anywhere returns
``{}`` because context parsing must never break a live call.
"""

from __future__ import annotations

import json
import logging
import re

from voice_local.puffo import format_thread_history

log = logging.getLogger("voice_local.trip_context")

_THREAD_ROOT_RE = re.compile(r"\[thread:(msg_[0-9a-f-]+)\]")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_EXTRACTION_SYSTEM = """\
You extract a traveler's booking state from a concierge chat channel transcript.
Messages tagged [booking-explore]/[booking-confirmed]/[booking-update]/[booking-canceled]
are requests filed by the concierge bot; replies from other senders are the human
fulfiller resolving them. Thread root messages are named
<location>-<startDate>-<days>-<reason>.

Reply with ONLY a JSON object (no prose, no markdown fences) of this exact shape:
{"trips": [{"destination": str, "start_date": str, "days": int,
            "status": "confirmed"|"tentative"|"ambiguous", "notes": str}],
 "items": [{"what": str, "status": "booked"|"pending"|"canceled"|"declined",
            "detail": str}],
 "suggested_next": [str, ...]}

"suggested_next" lists categories the traveler has NOT arranged yet that a concierge
would naturally offer (e.g. "dinner reservation", "airport transfer", "onsen day").
Use empty lists when the transcript shows nothing for a section."""


def _extract_json(reply: str) -> dict:
    """Parse the model reply defensively: bare JSON, fenced JSON, or JSON embedded in
    prose all work; anything unparseable (or non-object) is {}."""
    text = (reply or "").strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def load_trip_context(client, channel_id: str, grok) -> dict:
    """Fetch + condense the channel's full history (and each thread's), then one
    GrokChat completion into the trips/items/suggested_next dict. {} on ANY failure
    (no grok, empty channel, subprocess/API/parse error) — never raises."""
    if grok is None or not channel_id:
        return {}
    try:
        raw = await client.channel_history(channel_id)
        sections = []
        channel_lines = format_thread_history(raw, max_lines=60)
        if channel_lines:
            sections.append(f"[channel]\n{channel_lines}")
        # every thread root referenced in the channel gets its own condensed history
        for root in dict.fromkeys(_THREAD_ROOT_RE.findall(raw)):
            thread_lines = format_thread_history(
                await client.history(root, channel=channel_id))
            if thread_lines:
                sections.append(f"[thread {root}]\n{thread_lines}")
        if not sections:
            return {}
        reply = await grok.complete(
            [{"role": "system", "content": _EXTRACTION_SYSTEM},
             {"role": "user", "content": "\n\n".join(sections)}],
            temperature=0.0, max_tokens=900)
        return _extract_json(reply)
    except Exception as exc:  # noqa: BLE001 - context parsing must never break the call
        log.warning("trip context load failed (%r) — continuing without it", exc)
        return {}


def trip_context_text(ctx: dict) -> str:
    """The trip-context dict as a compact human-readable block for prompt injection
    ("" when there's nothing to say). Malformed entries are skipped, not fatal."""
    if not isinstance(ctx, dict):
        return ""
    lines: list[str] = []
    trips = [t for t in (ctx.get("trips") or []) if isinstance(t, dict)]
    if trips:
        lines.append("Trips:")
        for t in trips:
            head = (f"- {t.get('destination', '?')}: {t.get('start_date', '?')}, "
                    f"{t.get('days', '?')} day(s) [{t.get('status', 'ambiguous')}]")
            notes = str(t.get("notes", "")).strip()
            lines.append(f"{head} — {notes}" if notes else head)
    items = [i for i in (ctx.get("items") or []) if isinstance(i, dict)]
    if items:
        lines.append("Arrangements:")
        for i in items:
            head = f"- {i.get('what', '?')} [{i.get('status', 'pending')}]"
            detail = str(i.get("detail", "")).strip()
            lines.append(f"{head} — {detail}" if detail else head)
    suggested = [str(s).strip() for s in (ctx.get("suggested_next") or []) if str(s).strip()]
    if suggested:
        lines.append("Not yet arranged (offer if it fits naturally): " + ", ".join(suggested))
    return "\n".join(lines)
