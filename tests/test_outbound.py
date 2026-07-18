import asyncio
import json
import stat

import pytest

from voice_local.outbound import OutboundCallRelay, OutboundError
from voice_local.puffo import PuffoClient


_FAKE_PUFFO = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if "send" in args:
    path = os.environ["OUTBOUND_PUFFO_LOG"]
    n = sum(1 for _ in open(path)) if os.path.exists(path) else 0
    with open(path, "a") as f: f.write(json.dumps(args) + "\\n")
    print(f"sent: msg_fake{n:04d}")
'''


@pytest.fixture
def fake_puffo(tmp_path, monkeypatch):
    binary = tmp_path / "puffo"
    binary.write_text(_FAKE_PUFFO)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "puffo.log"
    monkeypatch.setenv("OUTBOUND_PUFFO_LOG", str(log))
    return {"bin": str(binary), "send_log": log}


class FakeVB:
    def __init__(self):
        self.calls = []
        self._events = []
        self._sessions = []

    async def start_call(self, phone, target):
        self.calls.append((phone, target))
        suffix = phone[-4:]
        return {"call_id": f"call-{suffix}", "room_name": f"room-{suffix}",
                "status": "initiated"}

    async def debug_events(self, since):
        return self._events, "cursor-1"

    async def sessions(self):
        return self._sessions


def _relay(fake_puffo):
    puffo = PuffoClient(bin=fake_puffo["bin"], server_url="https://fake/relay",
                        channel_id="ch_target", identity="bot")
    return OutboundCallRelay(puffo=puffo, vb=FakeVB())


def _sent(fake_puffo):
    p = fake_puffo["send_log"]
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_rejects_unsafe_or_invalid_batches(fake_puffo):
    relay = _relay(fake_puffo)
    with pytest.raises(OutboundError, match="consent"):
        asyncio.run(relay.start(phones=["+16505550123"], target="Call about Japan", consent_to_call=False))
    with pytest.raises(OutboundError, match="between 1 and 5"):
        asyncio.run(relay.start(phones=[], target="Call about Japan", consent_to_call=True))
    with pytest.raises(OutboundError, match="E.164"):
        asyncio.run(relay.start(phones=["6505550123"], target="Call about Japan", consent_to_call=True))


def test_starts_parallel_threads_and_relays_each_final_turn(fake_puffo):
    relay = _relay(fake_puffo)
    registered = {}
    relay.set_target_registrar(lambda room, target: registered.setdefault(room, target))
    result = asyncio.run(relay.start(
        phones=["+16505550123", "+16505550456"],
        target="Invite them to discuss a quiet Japan trip.", consent_to_call=True))
    assert len(result["calls"]) == 2
    assert all("+1650" not in str(call) for call in result["calls"])
    assert len(relay._vb.calls) == 2
    assert registered == {
        "room-0123": "[Outbound call brief]\nInvite them to discuss a quiet Japan trip.",
        "room-0456": "[Outbound call brief]\nInvite them to discuss a quiet Japan trip.",
    }
    relay._vb._sessions = [
        {"id": "session-0123", "room_name": "room-0123", "status": "in_progress"},
        {"id": "session-0456", "room_name": "room-0456", "status": "in_progress"},
    ]
    relay._vb._events = [
        {"session_id": "session-0123", "event_type": "user_transcription",
         "timestamp": "1", "data": {"transcript": "Hello there"}},
        {"session_id": "session-0123", "event_type": "agent_response",
         "timestamp": "2", "data": {"text": "Hi, I am Koyuki."}},
        {"session_id": "session-0456", "event_type": "user_transcription",
         "timestamp": "3", "data": {"text": "Who is this?"}},
    ]
    asyncio.run(relay.poll_once())
    asyncio.run(relay.poll_once())  # cursor replay must not duplicate the messages
    sent = _sent(fake_puffo)
    all_text = "\n".join(call[call.index("send") + 1] for call in sent if "send" in call)
    assert "[User] Hello there" in all_text
    assert "[Agent] Hi, I am Koyuki." in all_text
    assert "[User] Who is this?" in all_text
    assert all_text.count("[User] Hello there") == 1


def test_long_form_brief_with_guidance_is_posted_in_full(fake_puffo):
    relay = _relay(fake_puffo)
    description = "A" * 11_500
    asyncio.run(relay.start(
        phones=["+16505550123"], description=description,
        dos=["Ask open questions", "Offer practical next steps"],
        donts=["Do not pressure the recipient"],
        agent_fit="A patient travel concierge is the best fit.", consent_to_call=True))
    sent = _sent(fake_puffo)
    root_text = next(call[call.index("send") + 1] for call in sent if "send" in call)
    assert description in root_text
    assert "[Agent fit]" in root_text
    assert "[Do]" in root_text and "[Don't]" in root_text
