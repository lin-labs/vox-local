# Agent Instructions — concierge-kb

You are working in the shared knowledge layer for the voice-concierge stack.
This repo holds two datasets, both plain markdown with YAML-ish frontmatter:

- `kb/gems/<city>/<id>.md` — hidden-gem recommendations (see `schema/gem.md`)
- `kb/profiles/<account>.md` — customer profiles (see `schema/profile.md`)

## Rules

1. **Prefer the CLI for reads and small writes** — `bin/ckb` (stdlib python3,
   JSON out). It handles phone-number lookup, ranking, and timestamped notes:
   `ckb gems search`, `ckb gems get`, `ckb profile brief`, `ckb profile note`.
2. **Direct file edits are fine for curation** (rewriting a gem's details,
   restructuring a profile section) — keep frontmatter flat (`key: value` or
   `key: [a, b]`, one line each); the parser is deliberately tiny.
3. **Profiles: `## Notes` is append-only.** Never rewrite or delete existing
   notes; add new timestamped bullets (use `ckb profile note`).
4. **Never invent gems.** Only add places that came from the caller, the
   fulfiller, or a verifiable source; set `source:` accordingly.
5. **Privacy:** profiles contain PII. Don't copy profile contents into public
   channels; Puffo booking threads get only the minimum needed for the booking.
6. **Commit after meaningful changes** with a short message; this repo is the
   shared memory across voxcall and the Puffo channel agents.

## Consumers you must not break

- voxcall subprocesses `bin/ckb` with `--city/--q/--tag` flags and parses the
  JSON keys `gems[].id/name/pitch`, `brief`, `ok`. Don't rename them.
- The tool schemas in `bin/ckb` (`ckb tools schema`) are loaded by the voxcall
  brain at call setup; keep names/parameters backward compatible.
