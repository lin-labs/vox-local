# Discovery Map Website — Product and vox-local Integration Brief

## Why this document exists

This is the build brief for a new website that helps a person discover places
through navigable maps, express what they want in their own words, and move the
result into a dedicated collaboration channel. It is written for a partner who
needs enough product and technical context to design and build the experience
without needing the prior conversations.

The backend name is **vox-local** (not "Vogue's local"). It is the local-guide
and booking-coordination backend behind Koyuki, a Vocal Bridge phone concierge.
It already owns the local place database, phone-number-aware guest accounts,
and per-guest Puffo coordination channels.

## Product thesis

Discovery should start as exploration, not a booking form.

The website should let a person wander through several views of a destination
or interest, collect a few promising possibilities, and then say—in rich,
unstructured language—what would make the experience right for them. When they
are ready, the site turns that exploration into a private handoff for the local
concierge and its human/agent fulfilment network.

The desired outcome is not an on-page itinerary generator. It is a clean bridge
from **"I am curious"** to **"here is a living channel where this request can
be worked through."**

## The user journey

```text
Land on a destination
       ↓
Explore one or more discovery-map modes
       ↓
Open place cards and keep promising discoveries in a short list
       ↓
Describe the real need in freeform language
       ↓
Supply and verify a phone number, with clear consent
       ↓
Submit a discovery handoff to vox-local
       ↓
See a created channel link and continue there for details and coordination
```

The discovery map should not require the user to decide that they are
"booking" before they can browse. The phone number becomes necessary only at
the handoff boundary, where persistent private coordination begins.

## What the website should feel like

This is a discovery tool, not a generic travel-search results page. It should
make local knowledge feel explorable and editorial:

- **Spatial and visual first.** The map is the primary navigation surface;
  lists, filters, and cards support it rather than replace it.
- **Several ways into the same local database.** The user should be able to
  switch map modes without losing their selected places or their written need.
- **Possibility over false certainty.** A discovery marker means "worth looking
  at," not "available," "recommended for everyone," or "booked."
- **A handoff that feels intentional.** The final screen should explain that a
  dedicated channel has been created and why it is the right place for the
  detailed answer, updates, and coordination.

## Discovery-map modes

The first version does not need every mode below, but its data model and UI
state should allow several views over the same set of place records.

| Mode | User question it answers | Presentation |
| --- | --- | --- |
| Neighborhood map | "What is around the part of town I am drawn to?" | geographic markers grouped by area, with walkable clusters |
| Mood map | "What kind of day/night do I want?" | themes such as quiet morning, celebratory dinner, rainy-day refuge, craft, or hidden bar |
| Time map | "What works when I have this time available?" | morning/afternoon/evening and duration-oriented discovery paths |
| Taste map | "What feels like me?" | tag-led paths such as tea, design, bath, seafood, family-friendly, or solo |
| Orbit map | "What is worth a short trip beyond the anchor city?" | nearby towns/day trips, using the existing city-orbit concept where relevant |
| Curator trail | "Show me a coherent local point of view." | editorially grouped places connected by a premise or story |

These are views, not separate content silos. A gem may appear in several modes.
For example, one tea house can be in Kyoto's neighborhood map, the quiet-morning
mood map, and a craft-focused curator trail.

## MVP scope for the website

### Must have

1. Destination selection and at least two useful discovery-map modes (start
   with Neighborhood and Mood if map geography is available; otherwise use a
   styled diagrammatic map plus filters rather than pretending to have precise
   coordinates).
2. Several discovery markers/cards per view, each with a short spoken-style
   pitch and enough metadata to understand the place.
3. A persistent "considering" tray: users can add/remove places while changing
   map mode.
4. A freeform request field that asks what the user actually wants—not just
   dates and party size. It should welcome constraints, energy, occasion,
   accessibility needs, and uncertainty.
5. A phone-number field with consent language.
6. A submit action that calls the new vox-local handoff endpoint.
7. A success state with the returned channel link, a clear next action, and a
   safe retry/error state.

### Explicitly out of scope for the first release

- booking directly inside the map;
- real-time availability, pricing, or inventory claims;
- exposing account dossiers or private notes in the browser;
- a public searchable index of other users' channels;
- treating a place click as a recommendation, booking, or conversion;
- a full CMS for map geometry and curator trails.

## Existing vox-local capabilities

vox-local is an existing Python/FastMCP service, normally listening on
`127.0.0.1:7780` and exposed to Vocal Bridge through a controlled tunnel. Its
important existing capabilities are:

- **Guide data:** `data/gems.db` is SQLite with curated places (`gems`). Gems
  have IDs, name, city, area, tags, price, booking method, source, URL, spoken
  pitch, and longer details.
- **Discovery retrieval:** `search_gems` currently token-matches city, query,
  and tags. `/api/gems` already exposes read access and a token-protected write
  path for the browser extension.
- **Guest accounts:** caller identity and private account dossiers live outside
  the committed guide DB under the project state directory. Accounts include a
  phone list, PIN, name, and per-destination channel IDs.
- **Puffo channels:** the service can create and persist a per-guest,
  per-destination Puffo channel via `ensure_user_channel`. It invites the
  fulfiller and configured collaborators, writes a welcome, and keeps the
  resulting channel ID on the account.
- **Booking thread coordination:** the phone-side `booking_establish` and
  `booking_request` operations create and update structured Puffo threads. This
  is useful precedent for the web handoff, but it is not the web API itself.

## Critical integration fact: the requested endpoint is new

Today, **no public web endpoint accepts a phone number and full discovery
request, creates a channel, and returns a channel URL.** The current channel
creation code is invoked within the authenticated phone call/account flow and
returns a Puffo channel ID internally.

The website requires a purpose-built server endpoint. Do not call the MCP
`query_backend` tool from the browser and do not expose the existing voice
operation grammar to the public web. The new route needs its own authorization,
rate limits, consent handling, validation, and idempotency behavior.

## Proposed handoff API contract

### Route

```http
POST /api/discovery/handoffs
Authorization: Bearer <discovery-web-token>
Content-Type: application/json
Idempotency-Key: <browser-generated UUID>
```

The exact authentication mechanism should be chosen before public deployment.
If the frontend is browser-hosted, a static bearer token is insufficient by
itself; use a backend-for-frontend, short-lived signed request, or another
origin-bound mechanism. CORS should allow only the known discovery-site origin.

### Request

```json
{
  "phone": "+16505550123",
  "destination": "kobe",
  "request": "Two adults, a quiet celebratory dinner after a long train ride. We care more about a warm local feeling than a famous name; vegetarian-friendly would help.",
  "selected_gem_ids": ["kobe-example-place", "kobe-example-bar"],
  "map_context": {
    "mode": "mood",
    "filters": ["quiet", "dinner"],
    "session_id": "opaque-browser-session-id"
  },
  "consent": {
    "phone_contact": true,
    "channel_invitation": true,
    "privacy_version": "2026-07-18"
  }
}
```

### Field rules

| Field | Rule |
| --- | --- |
| `phone` | required E.164-like phone number; normalize server-side; never log it in request logs |
| `destination` | required normalized supported destination/city slug |
| `request` | required rich freeform description; enforce a sensible length range, e.g. 30–2,000 characters |
| `selected_gem_ids` | optional, deduplicated, bounded list; validate every ID against the guide DB but do not fail the whole request for one stale ID without returning a useful error |
| `map_context` | optional structured context for product analytics; do not pass raw browser history or PII |
| `consent` | required explicit booleans/version; reject submission without the needed consent |
| `Idempotency-Key` | required; the same key must return the already-created handoff instead of creating duplicate channels |

### Success response

```json
{
  "ok": true,
  "handoff_id": "dh_01J...",
  "channel": {
    "id": "ch_...",
    "url": "https://chat.puffo.ai/channels/ch_...",
    "label": "Your Kobe discovery thread"
  },
  "status": "created",
  "next_step": "Open your private channel to see the local concierge's detailed follow-up."
}
```

The URL shape must be confirmed with Puffo. vox-local currently receives and
stores a channel ID; it does not prove a stable, user-openable URL convention.
Implement a small `PuffoClient.channel_url(channel_id)` adapter only after
checking the live Puffo route/deep-link contract. Do not hardcode a guessed URL
in the frontend.

### Failure behavior

| Condition | Response behavior |
| --- | --- |
| Invalid phone/request/consent | `400` with field-level, non-sensitive errors |
| Expired or invalid web authorization | `401`/`403` without revealing account existence |
| Duplicate idempotency key | `200` with the original handoff and channel link |
| Existing phone + unresolved account policy | `409` or a neutral verification flow; never disclose whether the phone is registered |
| Puffo channel creation fails | `503` with a retry-safe error; never silently route a private web handoff to a shared channel |
| Rate limit or abuse signal | `429` with generic retry guidance |

## Proposed backend implementation shape

Keep the route in vox-local, but do not put the entire workflow in
`CallServices`: it is designed around one phone call and Vocal Bridge session
resolution. Add a web-specific service that reuses only the stable primitives.

```text
FastMCP custom route: POST /api/discovery/handoffs
        │
        ├─ validate request + consent + rate limit + idempotency
        ├─ normalize phone and destination
        ├─ resolve/create a web-safe guest handoff record
        ├─ create/reuse a private Puffo channel
        ├─ post a structured initial discovery message
        ├─ persist handoff ↔ channel mapping outside gems.db
        └─ return a verified channel URL
```

Recommended new modules/areas:

| Area | Responsibility |
| --- | --- |
| `src/voice_local/mcp_server.py` | thin authenticated HTTP route and request/response mapping |
| New `src/voice_local/discovery.py` | handoff validation, normalization, idempotency, orchestration, and policy boundaries |
| `src/voice_local/puffo.py` | channel URL adapter and any structured initial-message helper |
| State directory, not `data/gems.db` | private handoff records, normalized phone association, idempotency keys, and request text |
| `tests/test_discovery.py` | consent, idempotency, no-account-enumeration, failure, and successful channel-link coverage |

The rich request and phone number are private user data. They must live in the
project state directory or Puffo channel, never in the git-committed
`data/gems.db`.

## Initial channel contents

On successful creation, post one structured but human-readable opening message
to the private channel. It should include:

- destination;
- the user's exact request, clearly labeled as their request;
- selected places, with gem IDs/names and short pitches;
- map mode/filters only when helpful to the fulfiller;
- time submitted and handoff ID;
- a short notice that the user entered through the discovery map.

Do not synthesize a booking request or assert availability. The channel starts
as a discovery conversation; a fulfiller or concierge can turn it into a
booking workflow later.

## Privacy and trust requirements

The phone-number handoff changes the product's risk profile. These are release
requirements, not polish:

1. Explain why a phone number is needed and what channel it creates before the
   submit button.
2. Obtain explicit consent for phone contact and channel participation.
3. Normalize and protect phone numbers; do not echo them in API errors, logs,
   analytics, or URLs.
4. Never reveal whether a submitted phone already has an account/channel.
5. Keep private handoff text out of the repository database and public metrics.
6. Rate-limit the endpoint and add abuse/automation protection appropriate for
   the deployment.
7. Return a channel link only after verifying that it belongs to the newly
   created/reused private handoff context.
8. Define retention/deletion behavior before general availability.

## Open decisions the partner should resolve before building

1. **Map source:** Do we have real coordinates/geometry for gems, or should MVP
   use editorial/diagrammatic maps until geocoding is curated?
2. **Identity policy:** Is phone verification required before creating a channel,
   or is consent plus a one-time SMS/link enough? This affects the user journey
   and how the web flow relates to vox-local's current PIN accounts.
3. **Channel access:** How is the person added to or authenticated into the
   Puffo channel, and what stable URL/deep-link contract does Puffo provide?
4. **Reuse policy:** Should repeated requests from the same phone/destination
   continue in one channel or create a new thread/case? Reuse should be
   intentional and visible to the user.
5. **Fulfilment SLA:** Who sees the new channel, what response is promised, and
   how does the UI set expectations if no human is immediately available?
6. **Selected-place semantics:** Are selections merely inspiration, ranked
   preferences, or an instruction to pursue those exact places?
7. **Destinations:** Which cities are supported at launch, and how should the
   site handle a destination with no curated gems?

## Suggested delivery slices

### Slice 1 — Explore without private handoff

Build the map shell, two map modes, gem cards, filters, selected-place tray,
and a freeform request draft. Drive places from a read-only discovery endpoint.
This validates whether the discovery experience makes sense before collecting
phone numbers.

### Slice 2 — Secure handoff foundation

Implement the server-side handoff service, explicit consent, idempotency,
rate-limiting, private state persistence, and Puffo channel creation. Verify the
live channel URL contract before making it visible in the UI.

### Slice 3 — Connect the final step

Wire the website's submit/success/error states to the endpoint. Test a new
phone, repeat submission, invalid consent, stale selected gem, transient Puffo
failure, and a known existing phone without leaking account existence.

### Slice 4 — Make the map editorially alive

Add curator trails, richer map geometry, place-detail pages, and a deliberate
way to improve gems based on what people ask for. Keep discovery analytics
separate from private request content.

## Definition of done for the first complete loop

A person can pick a destination, explore meaningful place clusters, keep a few
possibilities, write what they really want, explicitly consent to use their
phone number, submit once, and receive a working private channel link. The
channel contains the original request and selected discoveries, a fulfiller can
act on it, repeat submission does not create duplicates, and no private request
or phone data lands in the committed guide database.

