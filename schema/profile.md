# Profile schema — `kb/profiles/<account>.md`

Keyed by **voxcall account number** (the same key as the voxcall deployment's
account store, `$VOXCALL_STATE_DIR/accounts/<account>.json`). `phones` lets
`ckb` resolve a caller-ID to the profile.

## Frontmatter

| Field | Required | Meaning |
|---|---|---|
| `account` | yes | voxcall account number; matches filename |
| `name` | yes | Full name |
| `phones` | yes | `[+1650..., ...]` — caller-ID lookup keys |
| `home_city` | no | Slug |
| `languages` | no | `[en, zh]` |
| `tier` | no | `standard` \| `gold` — service level |
| `emergency_contact` | no | Free text, quoted |
| `updated` | yes | `YYYY-MM-DD` (maintained by `ckb`) |

## Body sections (headings are load-bearing — `ckb profile brief` extracts them)

- `## Preferences` — tastes, pace, communication preferences.
- `## Constraints & Emergency` — allergies, medical, mobility, escalation path.
  This is the "no cold start" section: whatever an agent must know in an
  emergency lives here, not buried in notes.
- `## Trip History` — one bullet per trip, newest last.
- `## Notes` — **append-only** timestamped bullets (`- YYYY-MM-DD HH:MM PT: ...`),
  written via `ckb profile note` / the `remember_about_caller` voice tool.
  Durable facts discovered here should periodically be promoted up into
  Preferences/Constraints by a curating agent.
