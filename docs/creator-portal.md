# Creator Portal — Design Context and Continuation Guide

## Purpose

The creator portal is the working face of the local guide database. It gives a
guide-maker a small, opinionated space to add places, see the body of work they
have created, and learn whether those places are actually being surfaced during
calls.

The first release deliberately establishes the entire loop instead of trying to
solve every editorial workflow:

```text
Creator adds a place → it is stored in the local guide →
Koyuki resolves it during a call → a safe usage event is recorded →
the creator sees aggregate evidence in the portal.
```

The portal lives at `/creator` on the existing vox-local service. It is a local
single-creator MVP, not yet a multi-tenant publishing system.

## The experience we are building toward

This should feel like a creator's field book, not an admin dashboard. A good
creator should be able to look at the screen and answer three questions fast:

1. What have I made?
2. What should I add next?
3. Which places are travelling through real conversations?

The long-term product is an editorial control room for a distinctive local
guide. The goal is not to maximize database rows; it is to help a creator build
a point of view that callers can feel in a live recommendation.

## Design principles

### 1. Borrow Puffo's warmth and spatial confidence, not its product shell

The visual direction came from `~/dropbox-incoming/puffo-style.png`: a warm
peach-to-lime sidebar, large friendly type, rounded controls, a dense but
calm work surface, and sparing bright pink/lime accents. The portal echoes that
language through its warm gradient navigation rail, oversized editorial
headline, soft panels, and high-contrast creation card.

It intentionally does not imitate Puffo's chat product. The central object is
a place and its story, not a message thread.

### 2. Make the content feel authored

The primary collection is called "Places with a point of view." Each entry
foregrounds the name, city/neighborhood, and the one-breath spoken sell. This
keeps the creator focused on the sentence Koyuki will actually use, rather
than treating a gem as a flat form record.

### 3. Put creation beside feedback

The create form and recommendation pulse sit alongside the collection. A
creator should not need to leave the editorial flow to understand performance.
The form asks only for the minimum useful shape first:

- name, city, and spoken pitch are required;
- neighborhood, tags, price, and insider detail enrich the recommendation;
- details should capture timing, etiquette, or the reason this place matters.

### 4. Analytics should be legible, modest, and privacy-safe

The portal reports "Times served," not a fabricated engagement score. Usage is
evidence for an editor, not an assertion that a caller booked, liked, or even
heard the final spoken sentence. The current event records the gem, timestamp,
source, city, and a small fixed context label. It does **not** store caller ID,
account data, transcript text, or personal trip context in the committed guide
database.

### 5. Keep the first version operationally boring

The portal is one static HTML file served by the existing FastMCP/Starlette app.
It uses vanilla browser JavaScript and SQLite; there is no separate frontend
build, API server, or analytics service. This is intentional: the database is
already the guide's distribution artifact, and creation must work wherever the
existing service works.

## What exists today

### Creator workspace

`GET /creator` serves `src/voice_local/creator_portal.html`.

It provides:

- a live-guide signal and aggregate counts for places, served recommendations,
  and cities;
- client-side search over the creator's collection;
- cards showing each gem's current serve count;
- a local creation flow that inserts a `source: curator` gem and refreshes the
  collection immediately;
- a recent activity panel for the newest served events.

### Creator API

These routes are intentionally small and are consumed only by the portal today:

| Route | Behavior |
| --- | --- |
| `GET /api/creator/dashboard?q=&city=` | returns filtered gems, per-gem counts, aggregate stats, cities, and 12 recent events |
| `POST /api/creator/gems` | validates `name`, `city`, and `pitch`, then upserts a curator gem |
| `GET /api/creator/gems/{gem_id}/events` | returns the aggregate count and up to 20 recent events for one gem |

Existing extension-facing `GET`/`POST /api/gems` behavior is unchanged. That
compatibility matters because the browser extension is already a consumer.

### Tracking path

`CallServices._kb_get` resolves the requested gem and then calls
`db.record_recommendation(...)` before responding to Vocal Bridge. This is the
right MVP hook because `get_gem` is the point at which the assistant asks for
the detailed material it can share—not merely a broad search result.

SQLite creates a `recommendation_events` table and index when the database is
opened. `recommendation_summary` supplies aggregate counts and recent events.
Existing databases migrate naturally through `CREATE TABLE IF NOT EXISTS`.

As of the initial MVP, the data bag also includes seeded historical event counts
with `source: seed`. Treat those as bootstrap analytics, not call-level history.

## Architecture map

```text
creator browser                         live voice call
       │                                      │
       ├─ GET /creator                         ├─ query_backend { op: get_gem }
       ├─ GET /api/creator/dashboard           │
       └─ POST /api/creator/gems               ▼
                    │                    CallServices._kb_get
                    ▼                           │
              FastMCP custom routes             ├─ get_gem
                    │                            └─ record_recommendation
                    └───────────────┬─────────────────────┘
                                    ▼
                          data/gems.db (SQLite)
                         gems + recommendation_events
```

## Important limitations and decisions

### A "served" event is a strong proxy, not a delivery guarantee

An event means the voice agent requested the gem's detail record. It does not
prove the assistant finished speaking it, that the caller heard it, or that the
place was booked. Do not relabel the metric as conversions without a distinct,
auditable event source.

### Context is intentionally fixed today

The stored context is currently `Voice guide detail requested`. This proves the
data path and avoids adding private call material to a git-committed database.
The current `get_gem` payload does not retain the preceding search query, so
the portal cannot yet say "served for a quiet afternoon" truthfully.

To enrich this later, pass a scrubbed, bounded call intent into the event only
after defining a privacy policy. Good candidates are a normalized destination,
time-of-day preference, and a caller-provided category. Do not persist names,
phone numbers, raw transcripts, account notes, booking requests, or freeform
personal details.

### No authentication or creator ownership yet

The portal is intended for a localhost/private operator environment. The
creator endpoints currently have no separate auth gate, and every new portal
entry uses `source: curator`. Do not expose `/creator` through a public tunnel
without adding a creator authentication layer and an authorization boundary.

### No edit/delete flow yet

The underlying `db.add_gem` function is an upsert by city/name-derived ID, so
resubmitting the same place updates selected fields. The portal does not yet
offer an explicit edit, delete, draft, publish, or creator-attribution UI.

### The activity panel is global

The dashboard shows the most recent events across gems. The per-gem events API
exists, but the UI has not yet turned a place card into a detailed analytics
view. That is an intentional next increment, not missing backend capability.

## Key implementation files

| File | Why it matters |
| --- | --- |
| `src/voice_local/creator_portal.html` | Entire portal UI, CSS design system, browser-side fetching, search, and create flow |
| `src/voice_local/mcp_server.py` | `/creator` and `/api/creator/*` FastMCP custom routes |
| `src/voice_local/db.py` | `recommendation_events`, event writer, aggregate summary, gem persistence |
| `src/voice_local/services.py` | Voice `get_gem` instrumentation point |
| `tests/test_mcp_server.py` | Portal route/API coverage |
| `tests/test_services.py` | Proof that a real voice lookup records an event |
| `tests/test_db.py` | Aggregate/context behavior coverage |

## Recommended continuation order

1. **Secure the creator surface before wider exposure.** Add an explicit creator
   auth mechanism and keep it separate from the extension bearer token and MCP
   token. Decide whether it is local-only, single-user password, or account
   backed before implementing UI changes around identity.
2. **Make place cards inspectable and editable.** Add a detail drawer or route
   using `GET /api/creator/gems/{gem_id}/events`, then add explicit update and
   archival semantics. Preserve the existing extension API contract.
3. **Define a safe context taxonomy.** Agree on the small structured intent
   values that may be stored per event, then carry those through the call
   service. Add tests proving disallowed PII cannot reach the table.
4. **Separate editorial and operational metrics.** Keep served count distinct
   from later metrics such as caller save, booking request, booking confirmed,
   or creator share. Each needs its own event and source of truth.
5. **Introduce provenance intentionally.** If multiple creators are needed,
   add a stable creator entity and a `creator_id`/attribution model rather than
   overloading `gems.source`, which currently describes ingestion channel.
6. **Run visual QA after UI changes.** The portal is designed as a deliberately
   composed experience; check desktop and mobile rendering, empty states, long
   names, and large collections instead of treating it as API-only work.

## Verification checklist

```bash
uv run python -m pytest tests -q
uv build --wheel
```

For a manual check on a configured local service, open
`http://127.0.0.1:7780/creator`, create a test place, and confirm it appears
immediately. Trigger a known `get_gem` call through the voice backend, then
refresh the dashboard and confirm the serve count and activity panel change.

