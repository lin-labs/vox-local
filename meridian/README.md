# Meridian — Voice-First Travel Concierge

Talk to a private travel concierge over a cinematic 3D globe. The agent plans
in real time: the itinerary **composes itself item-by-item on screen while the
model writes it**, live web research lands inside itinerary cards (facts,
price band, source), a tool-call feed shows every search/edit/camera action as
it happens, and the camera flies wherever the conversation goes.

Built for the Sabre hackathon. Next.js 15 · TypeScript · Tailwind v4 ·
Mapbox/MapLibre GL · Web Speech API · Claude (agentic tool loop over SSE).

## Setup

Prereqs: **Node 20+** and **Chrome** (voice uses Chrome's Web Speech API).

```bash
npm install
cp .env.local.example .env.local   # then fill in keys, see below
npm run dev                        # → http://localhost:3000 in Chrome, sound on
```

| Env var (`.env.local`) | Required? | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | For the real brain | Agentic concierge (Claude Sonnet 5 + web search). Without it, a scripted offline demo brain runs — honest but limited. |
| `NEXT_PUBLIC_MAPBOX_TOKEN` | Recommended | Cinematic Mapbox dusk globe, 3D buildings, Japanese labels. Free at account.mapbox.com. Without it: keyless MapLibre/Carto fallback. |
| `CONCIERGE_MODEL` | Optional | Default `claude-sonnet-5`; `claude-haiku-4-5-20251001` for snappier, simpler replies. |

`NEXT_PUBLIC_*` vars are inlined at compile time — restart `npm run dev` after
changing them. Allow the mic when Chrome asks.

## Try it (the 90-second demo)

Click **Begin the conversation**, then say (or press `/` and type):

1. **"A week of food in Japan, late October"** — watch the trip build live,
   day by day, while the tool feed narrates.
2. **"Make it a five day trip"** — days are surgically edited; changes glow gold.
3. **"Find a top-rated kaiseki near Gion and add it"** — live web search →
   researched facts, price band, and source land in the itinerary card; camera
   dives into Kyoto.
4. **"Zoom out" / "show me the whole world"** — instant, no model round-trip.

Keys: **Space** talk · **/** type · **Esc** interrupt. Hover itinerary rows ↔
map pins pulse; click a row to fly there. `?quiet=1` on the URL = muted, no
auto-listen (for silent demos / automated testing).

## How it works

```
voice (Web Speech STT)
 ├─ fast-path regex → instant camera commands
 └─ POST /api/concierge  ······ SSE stream, agentic loop (≤8 steps)
      tools: web_search (Anthropic server tool)
             edit_itinerary  → granular ops (add_day, add_item, update_item…)
             set_camera      → cinematic camera moves
             finalize_turn   → spoken reply + suggestion chips
      Fine-grained tool streaming: ops are parsed out of the token stream and
      applied the moment the model writes them (client + server share one
      isomorphic op-applier, so state converges). Falls back to an offline
      mock brain (same event protocol) on missing key or errors.
```

| File | Role |
|---|---|
| `app/api/concierge/route.ts` | Streaming agent loop, tool dispatch, SSE events |
| `lib/agent/system.ts` / `tools.ts` | Concierge persona/protocol · tool schemas |
| `lib/agent/ops.ts` | Op-applier + incremental ops-from-token-stream parser |
| `lib/agent/mock.ts` | Keyless demo brain (7 destinations, never lies) |
| `lib/orchestrator.ts` | Client: voice loop, SSE consumption, state application |
| `lib/mapAdapter.ts` | One interface, two engines (Mapbox ⇄ keyless MapLibre) |
| `components/` | Globe, VoiceOrb, ItineraryPanel (diff glow), AgentFeed, BottomStage |

## Gotchas (read me, agents)

- **Never run `next build` while `next dev` is serving** — they share `.next`
  and it corrupts dev chunks ("Cannot read properties of undefined (reading
  'call')"). Type-check with `npx tsc --noEmit`; if it happens: kill dev,
  `rm -rf .next`, restart.
- `claude-sonnet-5` **rejects the `temperature` param** (400). Don't add it back.
- Live op streaming requires the `anthropic-beta: fine-grained-tool-streaming-2025-05-14`
  header (already set in the route) — without it tool args arrive in one burst.
- The map container's sizing is **inline-styled on purpose** — Tailwind vs
  map-library CSS cascade order is build-dependent; classes here once collapsed
  the map to 300px. Same story for `.m-pin` (never set `position` on it).
- Map labels are Japanese by design (`map.setLanguage("ja")` in
  `lib/mapAdapter.ts`) — change or remove to taste. Road/transit/POI labels are
  disabled via Mapbox Standard config.
- Background/occluded Chrome windows pause WebGL painting — screenshots of a
  hidden tab show a black map while the app is fine. Verify via state
  (`window.__meridianMap`), not pixels.

## Roadmap

Sabre APIs as concierge tools (flight/hotel inventory → bookable lines),
trip persistence + share links, sentence-streamed TTS.
