# concierge-kb

Shared knowledge layer for the voice-concierge hackathon stack: a **hidden-gem
knowledge base** (travel/local recommendations the guidebooks miss) and
**customer profiles** (preferences, constraints, emergency info, trip history —
so no interaction ever starts cold).

Two very different consumers read and write the same files:

| Consumer | How it accesses |
|---|---|
| **voxcall** (inbound voice agent) | Realtime tool calls → subprocess `bin/ckb ... ` (JSON out). Tool schemas: `ckb tools schema` |
| **Puffo channel agents** (claude/codex booking agents) | The [`skills/hidden-gems`](skills/hidden-gems/SKILL.md) skill wraps the same CLI; they may also edit the markdown directly |

The markdown files under `kb/` are the **source of truth**. The CLI is a thin
view/edit layer — humans and agents can edit files directly and commit.

## Layout

```
kb/
  gems/<city>/<id>.md      # one hidden gem per file (frontmatter + pitch + details)
  profiles/<account>.md    # one customer per file, keyed by voxcall account number
bin/ckb                    # stdlib-only Python CLI, JSON output
schema/                    # field documentation for gems and profiles
skills/hidden-gems/        # skill for Puffo channel agents
integrations/voxcall.md    # how to wire the five tools into the voxcall brain
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
