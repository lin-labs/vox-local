# Meridian — xAI Voice-First Travel Concierge

Talk to a private travel concierge over a cinematic 3D globe. Meridian uses
xAI's realtime Voice Agent API for one low-latency speech-to-speech session;
the same Grok voice model researches the web and drives itinerary and camera
tools while it talks.

Built for the Sabre hackathon. Next.js 15 · TypeScript · Tailwind v4 ·
Mapbox/MapLibre GL · xAI Voice Agent API.

## Setup

Prerequisites: Node 20+, a modern browser, and an xAI API key.

```bash
npm ci
cp .env.local.example .env.local
# Set XAI_API_KEY in .env.local
npm run dev                 # http://localhost:3000
```

From the repository root, the equivalent shortcuts are `make meridian-setup`,
`make meridian-dev`, and `make meridian-check`. The root `make meridian-dev`
target sources `~/.zshrc` before starting Next, so Boyan's local
`XAI_API_KEY` export is used directly without copying the secret into
`.env.local`.

| Environment variable | Required? | Purpose |
|---|---|---|
| `XAI_API_KEY` | For live voice | Stays server-side and mints five-minute browser client secrets. |
| `XAI_VOICE_MODEL` | Optional | Defaults to `grok-voice-think-fast-1.0`. |
| `XAI_VOICE` | Optional | Defaults to `eve`. |
| `ANTHROPIC_API_KEY` | For the typed brain | Claude agent loop (SSE + web search) when the voice connection is unavailable. Stays server-side. |
| `CONCIERGE_MODEL` | Optional | Claude model for the typed brain; defaults to `claude-sonnet-5`. |
| `NEXT_PUBLIC_MAPBOX_TOKEN` | Recommended | Cinematic Mapbox dusk globe; without it, the app uses the keyless MapLibre fallback. |

Without xAI configuration, typed prompts stream from the Claude concierge when
`ANTHROPIC_API_KEY` is set, and otherwise fall back to an honest scripted demo
for the included destinations. The app never exposes `XAI_API_KEY` or
`ANTHROPIC_API_KEY` to browser code.

## Try it

Click **Begin the conversation**, then say:

1. “A week of food in Japan, late October.”
2. “Make it a five day trip.”
3. “Find a top-rated kaiseki near Gion and add it.”
4. “Show me the whole world.”

Keys: **Space** talk · **/** type · **Esc** interrupt. `?quiet=1` mutes audio
and disables hands-free mode for silent demos and browser automation.

## Architecture

```text
Browser microphone (24 kHz PCM)
  └─ wss://api.x.ai/v1/realtime
       ├─ server VAD + grok-transcribe captions
       ├─ streamed Grok voice audio
       ├─ xAI web_search
       └─ custom functions
            edit_itinerary → structured live UI mutations
            set_camera     → cinematic map moves
            set_suggestions → next-turn chips

POST /api/realtime-token
  └─ server-side XAI_API_KEY → five-minute xAI client secret
```

| File | Role |
|---|---|
| `app/api/realtime-token/route.ts` | Secure ephemeral-token minting. |
| `lib/xai-realtime.ts` | WebSocket, microphone PCM capture, and streamed PCM playback. |
| `lib/orchestrator.ts` | Realtime events, custom tool execution, UI state, and offline fallback. |
| `lib/agent/system.ts` / `tools.ts` | Concierge protocol and xAI tool schemas. |
| `lib/agent/ops.ts` | Deterministic itinerary op applier. |
| `app/api/concierge/route.ts` | Claude SSE agent loop for typed turns (fine-grained tool streaming). |
| `lib/agent/anthropic.ts` | Claude protocol and Anthropic tool schemas. |
| `lib/agent/mock.ts` | Keyless typed demo brain. |
| `lib/mapAdapter.ts` | Mapbox / MapLibre adapter. |

## Contributor path

See [`CONTRIBUTING.md`](CONTRIBUTING.md). New work starts from
`origin/meridian-dev` or `origin/main` and edits this folder directly. Do not
continue the original unrelated `origin/meridian` branch.

## Gotchas

- Never run `next build` while `next dev` is serving; both write `.next`.
- Browser WebSockets authenticate with the short-lived client secret from the
  server route. Never add `NEXT_PUBLIC_XAI_API_KEY` or otherwise expose the
  long-lived key.
- Treat every xAI `session.update` as replacement-safe: include VAD,
  transcription, audio format, tools, and voice whenever refreshing dynamic
  instructions, or microphone turns can stream audio without producing
  transcripts/function calls.
- xAI input and output are 24 kHz mono PCM. Changing the session format also
  requires changing capture/playback conversion in `lib/xai-realtime.ts`.
- Map container sizing is inline-styled intentionally; map-library CSS order can
  otherwise collapse it.
- Background Chrome tabs may pause WebGL painting. Verify map state through
  `window.__meridianMap` when automating a hidden tab.
