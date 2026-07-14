# concierge-kb as a server — one endpoint, three call styles

`ckb serve` exposes the same five tools the CLI provides over HTTP, so any
consumer — the voice bridge (voxcall), Puffo chat agents, MCP-native agents —
calls into ONE running instance instead of each shelling out to its own copy.
Every network call is translated back into the exact CLI argv and dispatched
through the same code path, so the CLI and the server can never drift.

On labs it runs as a systemd `--user` unit on the lab-service port:

| What | Where |
|---|---|
| Unit | `concierge-kb.service` (`systemctl --user status concierge-kb`) |
| Bind | `127.0.0.1:7780` (registry: `~/.config/ports.yaml`, route `ckb`) |
| Health | `GET http://127.0.0.1:7780/healthz` |
| Logs | `journalctl --user -u concierge-kb -f` |
| Monitoring | `concierge-kb` + `concierge-kb-bridge` rows in `~/.config/lab/services.toml` |

Elsewhere: `CKB_PORT=... CKB_BIND=... bin/ckb serve` (stdlib only, no deps).

## 1. Webhook / tool-call — `POST /call`

The simplest integration: post `{"name": ..., "arguments": {...}}`, get back
exactly the JSON the CLI would print (same keys voxcall already parses).

```bash
curl -s http://127.0.0.1:7780/call -d \
  '{"name": "search_hidden_gems", "arguments": {"city": "kobe", "query": "onsen"}}'
```

`GET /tools/schema` returns the same tool list as `ckb tools schema`, so a
consumer can discover + wire all five tools in two calls (schema, then call).
For voxcall this is a drop-in alternative to the subprocess wiring in
[voxcall.md](voxcall.md): replace `_ckb(*argv)` with one urllib POST.

## 2. MCP — `POST /mcp` (Streamable HTTP)

Minimal MCP server (initialize / tools/list / tools/call / ping) for
MCP-native agents. Register with Claude Code:

```bash
claude mcp add --transport http concierge-kb http://127.0.0.1:7780/mcp
```

Tool results come back as one `text` content block containing the same JSON
payload as `/call`.

## 3. Puffo chat — the `ckb:` trigger

`bin/ckb-puffo-bridge` (unit `concierge-kb-bridge.service`) runs one
long-lived `puffo message listen --json` and turns any channel message
starting with `ckb:` into a `POST /call`, replying in-thread. This is how a
plain chat message invokes the server with no agent in the loop:

```
you:   ckb: search kobe onsen
[ckb]  2 gem(s):
       1. kobe-arima-onsen-kin-no-yu-at-opening — ...
```

Grammar: `ckb: help` | `ckb: search <city|-> <query...>` | `ckb: get <id>` |
`ckb: brief <key>` | `ckb: remember <key> <note...>` | raw JSON
`ckb: {"name": ..., "arguments": {...}}`.

- Replies are prefixed `[ckb]`, which never matches the trigger → no loops.
- **Profile tools are disabled over chat by default** (PII — AGENTS.md rule 5);
  the unit must set `CKB_BRIDGE_ALLOW_PROFILES=1` to enable `brief`/`remember`.
- `CKB_BRIDGE_CHANNELS=ch_a,ch_b` restricts which channels it answers in
  (default: any channel the identity can see). On labs the unit pins this to
  Hackerthon #General (`ch_d91428e5-…`) — the bridge must NOT post into the
  voxcall-booking space (that's live booking traffic).
- Relay sessions expire (~48h); the bridge re-runs `identity use` before every
  listen (re)start, so it heals itself after expiry and relay hiccups.

## Ops notes

- Writes (`add_hidden_gem`, `remember_about_caller`) hit the same markdown
  files under `kb/`; the server serializes tool calls with a lock. Commit KB
  changes as usual — the server needs no restart to see file edits.
- `http://ckb.blin-labs` (Caddy route) activates after the next
  `render-ports.py --apply`; until then use `127.0.0.1:7780`.
- Hackathon-weight unit: `Type=simple`, `Restart=on-failure`, no watchdog. If
  this outlives the hackathon, graduate it to the full Lab Service Protocol
  (`Type=notify`, sd_notify watchdog, conformance check).
