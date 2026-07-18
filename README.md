# vox-local

**Local knowledge, spoken.** vox-local is the backend brain for a
[Vocal Bridge](https://vocalbridgeai.com) phone concierge: callers dial the
agent's number, Vocal Bridge owns the audio, and its background AI reaches this
service over **MCP** for everything that has to be real — identity, memory,
local knowledge, and human-coordinated bookings.

Pivoted from `concierge-kb` (2026-07-14): same knowledge, better body.
The legacy markdown-era docs live in
[docs-legacy-concierge-kb.md](docs-legacy-concierge-kb.md).

## What it does

1. **Hidden-gem data bag** — a SQLite database (`data/gems.db`, committed to
   git: the repo is the distribution channel) of curated local spots with
   voice-ready pitches. Token-overlap search grounds every recommendation the
   agent makes; callers can contribute gems mid-call (`add_gem`).
2. **Guest accounts & memory** — caller-ID matched accounts, PIN verification
   with a 3-strike gate (server decides; the agent only relays digits),
   registration that mints account+PIN on the phone, and a fresh per-call trip
   summary parsed from the guest's booking channel. **User data never lives in
   gems.db**: each account is a dossier folder under the state dir —

   ```
   ~/data/Projects/vox-local/accounts/200773/
   ├── account.json   # identity, phones, PIN, booking channels
   ├── persona.md     # the caller: family, style, tastes, constraints
   ├── mark_kim.md    # one page per named travel companion
   └── trip.md        # exploration + clear-cut plans (trip:/reaction: notes)
   ```

   Attribution happens when the call happens: a known caller-ID preloads the
   whole dossier as silent context (greet by name, PIN still gates bookings);
   an unknown caller silently gets the same folder parked in
   `<state>/accounts-pending/` (no PIN, no channel) so every note persists
   across hangups — without rushing anyone toward an account. Registration
   promotes the parked number to a full account (only then is the booking
   channel created); verifying into an existing account merges the dossier in.
3. **Puffo booking coordination** — per-guest destination channels named after
   the guest (`<account>-<first-name>`, e.g. `470400-mika` — the space is
   already about Japan), opened with a personal welcome message drawn from the
   call dossier (Grok-written when available), one short thread per trip
   (`[booking] kobe 2026-12-15 2 days`), tagged requests
   (`[booking-explore|confirmed|update|canceled]`), async fulfiller replies
   relayed into the live call, and a consolidated `[booking-itinerary]` posted
   at hang-up.
4. **Web extension** (`extension/`) — clip any place you find while browsing
   into the data bag; your concierge recommends it on the next call.

## Architecture

```
Caller ──PSTN──> VB phone number ──> Vocal Bridge (STT/TTS/turn-taking)
                                          │ background AI, MCP tools/call
                                          ▼
                     vox-local  /mcp  (streamable HTTP, stateless)
                     ├── query_backend: ONE JSON op per query
                     │     verify · register · change_pin · search_gems ·
                     │     get_gem · remember · add_gem · booking_establish ·
                     │     booking_request · check_updates
                     ├── /healthz  /twilio-forward  /api/gems
                     ├── SQLite data bag (gems · profiles · notes)
                     ├── accounts (JSON, state dir) + AuthGate
                     └── Puffo (channels · threads · fulfiller watch)
```

Wire facts the design rests on (probed live): VB opens a **fresh MCP session
per query** and sends **no caller metadata**, so per-call state is keyed by the
VB logs API's `in_progress` session — which also supplies `caller_phone` for
the silent caller-ID match. Out-of-band pushes (caller context, booking
updates) drain into the next tool reply; `{"op":"check_updates"}` is the
explicit poll the agent runs while requests are pending.

## Run

```bash
uv sync
uv run vox-local import-md kb/       # one-shot legacy markdown -> SQLite
uv run vox-local serve               # 127.0.0.1:$VOICE_LOCAL_PORT (7780)
uv run vox-local gems list --city kobe
uv run python -m pytest tests -q
```

## Meridian web app

The cinematic xAI voice concierge contributed by Vincent lives as an ordinary
app under [`meridian/`](meridian/). It does not share dependencies with the
Python service:

```bash
make meridian-setup
make meridian-dev       # http://localhost:3000
make meridian-check     # type-check + production build
```

New web work branches from `origin/meridian-dev` (or `origin/main`) and edits
the folder directly. The old unrelated `origin/meridian` branch is retained
only as the provenance of the initial subtree import.

Config via env (`~/.env` then `./.env`): `VOICE_LOCAL_PORT`, `VOICE_LOCAL_DB`,
`VOICE_LOCAL_STATE`, `VOICE_LOCAL_GEMS_TOKEN`, `VOICE_LOCAL_DESTINATION`,
`VOCAL_BRIDGE_API`, `VB_AGENT_ID`, `VB_PHONE_NUMBER`, `VB_PUBLIC_URL`,
`XAI_API_KEY`, `PUFFO_*`.

`/mcp` is bearer-gated: the token lives at
`$VOICE_LOCAL_STATE/mcp-bearer-token` (or env `VOICE_LOCAL_MCP_TOKEN`) and
Vocal Bridge presents it as `Authorization: Bearer`, `X-API-Key`, or a `?key=`
query param on the MCP URL (the shape its config reliably carries). Missing
token file = auth off. `/healthz` stays open.

On labs it runs per the Lab Service Protocol: `vox-local.service`
(Type=notify + watchdog) on `127.0.0.1:7780`, publicly exposed for Vocal Bridge
through the `vox-local-ngrok.service` static tunnel. `make deploy` from the
Mac, `make release` on labs.

## The data bag is a git artifact

New gems (CLI, extension, or callers) land in `data/gems.db` on the serving
box. Publish them with a commit:

```bash
make push-gems     # commit data/gems.db + push
```

## Legacy

`bin/ckb` and the markdown `kb/` tree remain from concierge-kb for the
voxcall Grok-path integration; `vox-local import-md` migrates their content.
New consumers should use the MCP surface or `voice_local.db`.
