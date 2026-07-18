# Bulk outbound-call relay API

`POST /api/outbound/calls` is an operator-only API for starting a small, consented batch of outbound calls through Vocal Bridge. It creates one Puffo thread per recipient, asks the configured outbound agent (`VB_OUTBOUND_AGENT_ID`; falls back to `VB_AGENT_ID`) to use the supplied long-form brief as the call objective, and appends completed transcription turns to the corresponding thread.

Two prober agents exist. **Miyuki** reads the brief over MCP, so one agent serves any case. **Linda** (`docs-linda-agent-prompt.txt`) carries a single fixed case — a family of four, SFO to Tokyo, September 2026 — entirely in her prompt, and has no MCP connection: Vocal Bridge's outbound-call body has no field for a per-call objective, so an agent without the backend never receives the goal block. Point `VB_OUTBOUND_AGENT_ID` at Linda for a repeatable demo run where every call probes the same case; point it at Miyuki when the brief must steer the call. With Linda selected, `description`/`dos`/`donts` still bound the batch and still appear in Puffo, but do not reach the caller.

This endpoint initiates real phone calls. Do not expose it to an untrusted browser or invoke it for a recipient who has not consented to be called.

## Endpoint and authorization

```text
POST /api/outbound/calls
Authorization: Bearer <outbound API token>
Content-Type: application/json
```

The service generates a dedicated bearer token on first startup unless `VOICE_LOCAL_OUTBOUND_TOKEN` supplies one. Its default private location is:

```text
~/data/Projects/vox-local/outbound-bearer-token
```

The token file is mode `0600`, is not committed, and must never be logged or sent to a frontend. The endpoint is intended for trusted operator tooling on the private vox-local network.

## Request body

```json
{
  "phone_numbers": ["+16505550123", "+16505550124"],
  "description": "A full operator brief describing who to call, the purpose of the conversation, their preferences, relevant background, success criteria, and any details the calling agent should know.",
  "agent_fit": "Use Miyuki's brief, courteous evaluator voice. Probe and ask questions; do not answer them.",
  "dos": [
    "Confirm whether now is a good time to talk.",
    "Offer to follow up in the Puffo channel when useful."
  ],
  "donts": [
    "Do not pressure the recipient.",
    "Do not claim the recipient called us."
  ],
  "consent_to_call": true
}
```

| Field | Required | Rules |
| --- | --- | --- |
| `phone_numbers` | Yes | One to five unique E.164 phone numbers, including `+`. |
| `description` | Yes* | A 10–12,000-character, batch-level operator brief. It should be complete enough for the calling agent to understand the caller's intent and desired outcome. |
| `agent_fit` | No | Up to 2,000 characters describing the preferred agent style or role. |
| `dos` | No | Up to 20 non-empty instructions, each at most 500 characters. |
| `donts` | No | Up to 20 non-empty constraints, each at most 500 characters. |
| `consent_to_call` | Yes | Must be the boolean `true`; it is the caller's assertion that every listed recipient may be called. |

`target` remains accepted as a backwards-compatible substitute for `description`. Send `description` for all new integrations. If both are supplied, `description` wins.

The service normalizes the supplied fields into a single brief with `[Outbound call brief]`, optional `[Agent fit]`, `[Do]`, and `[Don't]` sections. All recipients in one request receive the same normalized brief; create separate requests when the objective differs by recipient.

## Success response

The endpoint returns `202 Accepted` once it has created every Puffo root thread and attempted to start every call. `202` means the run was accepted for dialing; it does not mean that a recipient answered or that a call completed successfully.

```json
{
  "ok": true,
  "run_id": "out_1a2b3c4d5e6f...",
  "calls": [
    {
      "recipient": "Recipient ending 0123",
      "thread_root": "msg_...",
      "call_id": "call_...",
      "status": "initiated"
    }
  ]
}
```

Phone numbers are intentionally masked in the response and Puffo thread labels. `thread_root` identifies the Puffo root message for the recipient's call thread.

## Errors

| Status | Meaning |
| --- | --- |
| `400` | Invalid JSON or validation failure, including missing consent, an invalid/duplicate phone number, an empty or oversized brief, or invalid instruction lists. The response is `{ "error": "…" }`. |
| `401` | Missing or incorrect dedicated outbound bearer token. |
| `503` | The outbound relay is not configured: Puffo, the outbound Puffo channel, or the Vocal Bridge call adapter is unavailable. |

An individual provider dial failure is reported in that recipient's Puffo thread as `[System] Dial failed: …`; other recipients in the batch are not cancelled. If any initial Puffo root thread cannot be created, no calls are started, preventing a call with nowhere to relay its transcript.

## What happens after submission

1. vox-local validates the consented batch and builds the shared detailed brief.
2. It creates one root message in `VOICE_LOCAL_OUTBOUND_CHANNEL_ID` for each recipient, containing the brief, a masked recipient label, and a run identifier.
3. It starts the Vocal Bridge calls concurrently.
4. The returned Vocal Bridge room name is associated with the recipient's Puffo thread and with the calling agent's per-call backend context.
5. When the calling agent first checks for backend updates, it receives an `[Outbound call goal]` payload containing the detailed brief. Its live prompt instructs it to use it naturally and never imply that the recipient initiated the call.
6. A background relay polls Vocal Bridge's debug events. Completed `user_transcription` events are posted as `[User] …`; completed `agent_response` events are posted as `[Agent] …`. Status changes appear as `[System] …`.

The debug-event poller deduplicates events and polls roughly every 0.75 seconds while active calls remain. This is a visibility relay, not a second conversation system: Vocal Bridge remains responsible for call audio and the calling agent's live dialogue.

## Operator setup

The serving process must have all of the following before the route is usable:

- `VOCAL_BRIDGE_API` and `VB_AGENT_ID` for the configured Koyuki agent, with Vocal Bridge outbound calling enabled and its Debug Mode API available.
- `VB_OUTBOUND_AGENT_ID` (optional) selects a dedicated outbound agent for these calls — either Miyuki, the brief-driven prober (`docs-miyuki-agent-prompt.txt`, `make push-miyuki-prompt`), or Linda, the fixed-case demo prober (`docs-linda-agent-prompt.txt`, `make push-linda-prompt`). Debug Mode must be enabled on that agent for transcript relay. `VB_EXTRA_AGENT_IDS` lists any additional agents sharing the MCP backend so their sessions resolve for goal delivery and caller attribution; Linda does not use the backend and does not belong in that list.
- A working Puffo configuration (`PUFFO_*`) and `VOICE_LOCAL_OUTBOUND_CHANNEL_ID`, set to the private channel where the per-call threads should appear.
- The dedicated token described above, either generated in the private state directory or injected as `VOICE_LOCAL_OUTBOUND_TOKEN`.

Restart vox-local after changing these settings. `/healthz` only proves the service is alive; a `503` from this endpoint means the outbound relay dependencies were not assembled at startup.

## Safe operator example

The following reads the private token without printing it. Replace the example number and brief only after confirming permission to call every recipient.

```sh
outbound_token="$(<"$HOME/data/Projects/vox-local/outbound-bearer-token")"

curl --fail-with-body \
  -X POST http://127.0.0.1:8000/api/outbound/calls \
  -H "Authorization: Bearer $outbound_token" \
  -H 'Content-Type: application/json' \
  --data '{
    "phone_numbers": ["+16505550123"],
    "description": "Call only with prior consent. Explain that Koyuki is following up about the recipient's Japan trip, learn whether they would like local recommendations, and offer a low-pressure next step.",
    "dos": ["Ask whether this is a good time to talk."],
    "donts": ["Do not pressure them or represent the call as inbound."],
    "consent_to_call": true
  }'
```

## Implementation map

- `src/voice_local/mcp_server.py` authenticates and exposes the HTTP route.
- `src/voice_local/outbound.py` validates requests, creates Puffo threads, starts calls, and relays transcript/status events.
- `src/voice_local/cli.py` assembles the configured relay and creates the private token when needed.
- `docs-miyuki-agent-prompt.txt` defines the outbound prober's behavior (`make push-miyuki-prompt`); `docs-agent-prompt.txt` keeps Koyuki's fallback outbound behavior (`make push-prompt`). Prompt changes must be synchronized to Vocal Bridge in the same change.
- `~/agents/obsProjects/vox-local/flow.md` is the operational call-flow contract and changelog for the Vocal Bridge-to-vox-local integration.
