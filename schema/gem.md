# Gem schema — `kb/gems/<city>/<id>.md`

## Frontmatter (flat, one line per key)

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `<city>-<slug>`, unique across the KB, matches filename |
| `name` | yes | Display name |
| `city` | yes | Lowercase city slug (`kobe`, `arima`, `kyoto`, `osaka`, ...) — matches parent dir |
| `area` | no | Neighborhood slug |
| `tags` | yes | `[a, b, c]` — search facets: `food`, `coffee`, `onsen`, `bar`, `temple`, `dinner`, `quiet`, ... |
| `price` | no | `$` … `$$$$` |
| `phone` | no | E.164-ish; needed if `booking: phone` |
| `booking` | no | `walk-in` \| `phone` \| `online` \| `via-hotel` — how the concierge secures it |
| `source` | no | `curator` \| `agent` \| `caller` \| URL — provenance |
| `updated` | yes | `YYYY-MM-DD` |

## Body

- **First paragraph = the voice pitch.** One sentence, speakable, no markdown.
  This is what the voice agent reads to the caller — write it for the ear.
- `## Details` — hours, prices, what to order, closures.
- `## Insider notes` — the actual hidden-gem knowledge: timing tricks, what to
  ask for, follow-on suggestions, booking hints for the concierge itself.
