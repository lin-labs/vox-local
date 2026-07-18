# Agent Instructions — concierge-kb

You are working in the shared knowledge layer for the voice-concierge stack.
This repo holds two datasets, both plain markdown with YAML-ish frontmatter:

- `kb/gems/<city>/<id>.md` — hidden-gem recommendations (see `schema/gem.md`)
- `kb/profiles/<account>.md` — customer profiles (see `schema/profile.md`)

## Rules

0. **Prompt changes ALWAYS sync to Vocal Bridge.** `docs-agent-prompt.txt` is
   deployed config, not documentation: the live phone agent (Koyuki) runs
   whatever Vocal Bridge holds, and the repo file is the source of truth only
   while the two match. ANY edit to `docs-agent-prompt.txt` must be pushed in
   the same change — run `make push-prompt` (wraps `vb prompt set -f
   docs-agent-prompt.txt` and verifies the round-trip). The greeting is a
   separate VB field: sync it with `printf '<greeting>' | vb prompt set
   --greeting`. Never leave the repo and the live agent divergent in either
   direction; if `make push-prompt` reports a diff you didn't make, pull the
   remote version down and reconcile before editing.

0.5. **The VB⇄vox-local protocol doc stays current.** The call-flow contract
   lives in the project vault at `~/agents/obsProjects/vox-local/flow.md`
   (call lifecycle, caller-status contract, `query_backend` op grammar,
   vox-local-vs-web-search split, latency budget, platform facts, ops units).
   ANY change to `docs-agent-prompt.txt`, VB config (greeting, model settings,
   capabilities), the op grammar, the attribution/caller-status behavior, or
   the service/tunnel topology MUST update `flow.md` (including its changelog)
   in the same working session. It is the examination surface Boyan uses to
   debug calls — a stale flow.md is a bug.

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
- `ckb serve` (systemd unit `concierge-kb.service`, 127.0.0.1:7780 on labs)
  exposes the same tools as `POST /call` (CLI-identical JSON) and `POST /mcp`
  (MCP tools/call); `bin/ckb-puffo-bridge` (`concierge-kb-bridge.service`)
  turns `ckb: ...` Puffo messages into `/call` invocations. Keep `/call`
  payload shapes and the `ckb:` trigger grammar backward compatible — see
  `integrations/server.md`.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
