"""Bulk outbound calls with a per-call Puffo transcript thread.

This is an operator-only surface. It keeps the private phone number in memory,
uses the configured shared Puffo channel for operator visibility, and mirrors
only final Vocal Bridge user/agent transcript events into the call's own thread.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
import time
from typing import Awaitable, Callable, Protocol
from uuid import uuid4

import httpx

from voice_local.puffo import PuffoClient

VB_API_BASE = "https://vocalbridgeai.com"
_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class OutboundError(ValueError):
    """A safe, client-visible request or configuration failure."""


class VBCalls(Protocol):
    async def start_call(self, phone: str, brief: str) -> dict: ...
    async def debug_events(self, since: str) -> tuple[list[dict], str]: ...
    async def sessions(self) -> list[dict]: ...


class VocalBridgeCalls:
    """Small, explicit REST adapter for the two VB capabilities we rely on."""

    def __init__(self, *, api_key: str, agent_id: str) -> None:
        self._headers = {"X-API-Key": api_key, "X-Agent-Id": agent_id}

    async def start_call(self, phone: str, brief: str) -> dict:
        # VB currently documents phone_number and participant_name for outbound
        # calls. The target reaches Koyuki via the per-session backend context,
        # not an undocumented outbound-call body field.
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{VB_API_BASE}/api/v1/calls", headers=self._headers,
                json={"phone_number": phone, "participant_name": "Outbound recipient"},
            )
            response.raise_for_status()
            return response.json()

    async def debug_events(self, since: str) -> tuple[list[dict], str]:
        params = {"limit": 100}
        if since:
            params["since"] = since
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{VB_API_BASE}/api/v1/debug/events",
                                        headers=self._headers, params=params)
            response.raise_for_status()
            body = response.json()
        return list(body.get("events") or []), str(body.get("last_timestamp") or since)

    async def sessions(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{VB_API_BASE}/api/v1/logs?limit=100",
                                        headers=self._headers)
            response.raise_for_status()
            return list(response.json().get("sessions") or [])


@dataclass
class OutboundJob:
    phone: str
    target: str
    thread_root: str
    run_id: str
    room_name: str = ""
    call_id: str = ""
    session_id: str = ""
    status: str = "thread_created"

    @property
    def recipient_label(self) -> str:
        return f"Recipient ending {self.phone[-4:]}"

    def public(self) -> dict:
        return {"recipient": self.recipient_label, "thread_root": self.thread_root,
                "call_id": self.call_id, "status": self.status}


class OutboundCallRelay:
    """Starts a small, consented batch and mirrors VB transcript turns to Puffo."""

    def __init__(self, *, puffo: PuffoClient | None, vb: VBCalls | None) -> None:
        self._puffo = puffo
        self._vb = vb
        self._jobs: dict[str, OutboundJob] = {}
        self._session_jobs: dict[str, OutboundJob] = {}
        self._last_timestamp = ""
        self._seen_events: set[str] = set()
        self._poller: asyncio.Task | None = None
        self._register_target: Callable[[str, str], None] | None = None

    @property
    def configured(self) -> bool:
        return self._puffo is not None and self._vb is not None and bool(self._puffo.channel_id)

    def set_target_registrar(self, register: Callable[[str, str], None]) -> None:
        self._register_target = register

    @staticmethod
    def validate(*, phones: object, target: object, description: object,
                 dos: object, donts: object, agent_fit: object,
                 consent_to_call: object) -> tuple[list[str], str]:
        if consent_to_call is not True:
            raise OutboundError("consent_to_call must be true")
        if not isinstance(phones, list) or not 1 <= len(phones) <= 5:
            raise OutboundError("phone_numbers must contain between 1 and 5 numbers")
        normalized = [str(phone).strip() for phone in phones]
        if any(not _PHONE_RE.fullmatch(phone) for phone in normalized):
            raise OutboundError("phone_numbers must use E.164 format")
        if len(set(normalized)) != len(normalized):
            raise OutboundError("phone_numbers must be unique")
        primary = str(description or target or "").strip()
        if not 10 <= len(primary) <= 12_000:
            raise OutboundError("description (or target) must be between 10 and 12000 characters")

        def list_items(value: object, field: str) -> list[str]:
            if value is None:
                return []
            if not isinstance(value, list) or len(value) > 20:
                raise OutboundError(f"{field} must be a list of at most 20 items")
            items = [str(item).strip() for item in value]
            if any(not item or len(item) > 500 for item in items):
                raise OutboundError(f"{field} items must be non-empty and at most 500 characters")
            return items

        clean_fit = str(agent_fit or "").strip()
        if len(clean_fit) > 2_000:
            raise OutboundError("agent_fit must be at most 2000 characters")
        sections = ["[Outbound call brief]", primary]
        if clean_fit:
            sections += ["\n[Agent fit]", clean_fit]
        for heading, items in (("Do", list_items(dos, "dos")),
                               ("Don't", list_items(donts, "donts"))):
            if items:
                sections += [f"\n[{heading}]", *[f"- {item}" for item in items]]
        return normalized, "\n".join(sections)

    async def start(self, *, phones: object, target: object = "", description: object = "",
                    dos: object = None, donts: object = None, agent_fit: object = "",
                    consent_to_call: object) -> dict:
        if not self.configured:
            raise OutboundError("outbound relay is not configured")
        normalized, clean_target = self.validate(
            phones=phones, target=target, description=description, dos=dos, donts=donts,
            agent_fit=agent_fit, consent_to_call=consent_to_call)
        run_id = f"out_{uuid4().hex}"
        # Roots are created before any dial starts: every result has a durable
        # operator thread, including a provider failure before pickup.
        roots = await asyncio.gather(*[
            self._puffo.send(
                f"[Outbound call] {clean_target}\n{self._masked(phone)} · run {run_id[:12]}",
                channel=self._puffo.channel_id)
            for phone in normalized
        ])
        if any(not root for root in roots):
            raise OutboundError("could not create every Puffo call thread; no calls were started")
        jobs = [OutboundJob(phone=phone, target=clean_target, thread_root=root, run_id=run_id)
                for phone, root in zip(normalized, roots, strict=True)]
        await asyncio.gather(*(self._start_job(job) for job in jobs))
        self._ensure_poller()
        return {"ok": True, "run_id": run_id, "calls": [job.public() for job in jobs]}

    async def _start_job(self, job: OutboundJob) -> None:
        try:
            result = await self._vb.start_call(job.phone, job.target)
            job.call_id = str(result.get("call_id") or "")
            job.room_name = str(result.get("room_name") or "")
            job.status = str(result.get("status") or "initiated")
            if not job.room_name:
                raise OutboundError("Vocal Bridge did not return a room name")
            self._jobs[job.room_name] = job
            if self._register_target is not None:
                self._register_target(job.room_name, job.target)
            await self._puffo.send("[System] Dial initiated.", thread=job.thread_root,
                                   channel=self._puffo.channel_id)
        except Exception as exc:  # noqa: BLE001 - each recipient gets a visible failure
            job.status = "failed"
            await self._puffo.send(f"[System] Dial failed: {self._safe_error(exc)}",
                                   thread=job.thread_root, channel=self._puffo.channel_id)

    @staticmethod
    def _masked(phone: str) -> str:
        return f"Recipient ending {phone[-4:]}"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return re.sub(r"\+\d{7,15}", "[redacted]", str(exc))[:180] or "provider error"

    def _ensure_poller(self) -> None:
        if self._poller is None or self._poller.done():
            self._poller = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while self._jobs:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - a transient debug API failure must not kill relay
                await asyncio.sleep(2.0)
            else:
                await asyncio.sleep(0.75)

    async def poll_once(self) -> None:
        if not self._jobs:
            return
        sessions = await self._vb.sessions()
        for session in sessions:
            job = self._jobs.get(str(session.get("room_name") or ""))
            if job is None:
                continue
            job.session_id = str(session.get("id") or job.session_id)
            if job.session_id:
                self._session_jobs[job.session_id] = job
            status = str(session.get("status") or "")
            if status and status != "in_progress" and job.status not in {"completed", "failed"}:
                job.status = status
                await self._puffo.send(f"[System] Call {status}.", thread=job.thread_root,
                                       channel=self._puffo.channel_id)
                self._jobs.pop(job.room_name, None)
        events, self._last_timestamp = await self._vb.debug_events(self._last_timestamp)
        for event in events:
            await self._relay_event(event)

    async def _relay_event(self, event: dict) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type not in {"user_transcription", "agent_response"}:
            return
        session_id = str(event.get("session_id") or "")
        job = self._session_jobs.get(session_id)
        if job is None:
            return
        data = event.get("data") or {}
        text = str(data.get("transcript") or data.get("text") or "").strip()
        if not text:
            return
        fingerprint = json.dumps([session_id, event_type, event.get("timestamp", ""), text],
                                 ensure_ascii=False)
        if fingerprint in self._seen_events:
            return
        self._seen_events.add(fingerprint)
        # Keep the dedupe cache bounded for a long-lived service.
        if len(self._seen_events) > 2_000:
            self._seen_events = set(list(self._seen_events)[-1_000:])
        prefix = "[User]" if event_type == "user_transcription" else "[Agent]"
        await self._puffo.send(f"{prefix} {text}", thread=job.thread_root,
                               channel=self._puffo.channel_id)
