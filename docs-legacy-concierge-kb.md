# concierge-kb

Shared knowledge layer for the voice-concierge hackathon stack: a **hidden-gem
knowledge base** (travel/local recommendations the guidebooks miss) and
**customer profiles** (preferences, constraints, emergency info, trip history —
so no interaction ever starts cold).

Very different consumers read and write the same files:

| Consumer | How it accesses |
|---|---|
| **voxcall** (inbound voice agent) | Realtime tool calls → subprocess `bin/ckb ... ` (JSON out), or HTTP `POST /call` against `ckb serve`. Tool schemas: `ckb tools schema` / `GET /tools/schema` |
| **Puffo channel agents** (claude/codex booking agents) | The [`skills/local-guide`](skills/local-guide/SKILL.md) skill (renamed from hidden-gems) wraps the same CLI; MCP-native agents use `POST /mcp`; they may also edit the markdown directly |
| **Puffo chat messages** (any human/bot in a channel) | `ckb: search kobe onsen` → [`bin/ckb-puffo-bridge`](bin/ckb-puffo-bridge) → `POST /call` → threaded `[ckb]` reply |

The served surface (webhook `/call`, MCP `/mcp`, chat bridge) is one daemon —
see [integrations/server.md](integrations/server.md). On labs it runs as
`concierge-kb.service` on `127.0.0.1:7780` with the bridge alongside.

The markdown files under `kb/` are the **source of truth**. The CLI is a thin
view/edit layer — humans and agents can edit files directly and commit.

## Layout

```
kb/
  gems/<city>/<id>.md      # one hidden gem per file (frontmatter + pitch + details)
  profiles/<account>.md    # one customer per file, keyed by voxcall account number
bin/ckb                    # stdlib-only Python CLI + `ckb serve` (HTTP /call + MCP /mcp)
bin/ckb-puffo-bridge       # Puffo listener: `ckb: ...` chat messages -> POST /call
schema/                    # field documentation for gems and profiles
skills/local-guide/        # skill for Puffo channel agents
integrations/voxcall.md    # how to wire the five tools into the voxcall brain
integrations/server.md     # the served surface: webhook, MCP, chat bridge, ops
```

## Quickstart

```bash
CKB="$(git rev-parse --show-toplevel)/bin/ckb"   # from inside the checkout

$CKB gems search --city kobe --q "wagyu dinner"      # ranked JSON results
$CKB gems get kobe-yazawa-teppan                     # full detail incl. insider notes
$CKB gems add --name "..." --city kyoto --pitch "..." --tags food,quiet

$CKB profile brief 123456          # compact call-start brief (no cold start)
$CKB profile get +16506567722      # phone-number lookup works too
$CKB profile note 123456 "Prefers aisle seats on shinkansen."
$CKB profile upsert 300100 --set name="New Guest" --set 'phones=[+1415...]'

$CKB tools schema                  # xai-realtime tool JSON for voxcall

$CKB serve                         # HTTP server on 127.0.0.1:7780 (CKB_PORT/CKB_BIND)
curl -s localhost:7780/call -d '{"name":"search_hidden_gems","arguments":{"city":"kobe"}}'
```

No dependencies — plain `python3`. Set `CKB_ROOT` to point at an alternate
`kb/` directory (tests, staging); consumers outside the checkout set
`CKB_REPO` to wherever they cloned this repo.

## Conventions

- Gem ids: `<city>-<slug>` (e.g. `kobe-yazawa-teppan`); city dirs are lowercase slugs.
- Profiles are keyed by **voxcall account number** (`~/data/Projects/voxcall/accounts/`),
  with `phones` enabling caller-ID lookup.
- Profile `## Notes` is append-only with PT timestamps — the accumulating memory.
- Anything an agent learns that future agents should know goes in a gem or a
  profile note, then gets committed. Commit early, commit often — this repo IS
  the shared memory.
