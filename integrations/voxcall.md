# voxcall integration — five tools, one subprocess

The voice agent gets hidden-gem recommendations and guest memory by exposing
five realtime tools whose handler shells out to `bin/ckb` (JSON in/out, stdlib
only, no venv). Schemas are already in the exact flat format `XaiBrain`
expects (`{"type": "function", "name", "description", "parameters"}`):

```bash
/home/blin/Experiments/voice/concierge-kb/bin/ckb tools schema
```

| Tool | ckb command | When the brain calls it |
|---|---|---|
| `get_caller_brief` | `ckb profile brief <key>` | Once, right after PIN auth — kills the cold start |
| `search_hidden_gems` | `ckb gems search --city X --q "..." [--tag t]` | Caller asks for recommendations |
| `get_hidden_gem` | `ckb gems get <id>` | Before booking / follow-up detail |
| `remember_about_caller` | `ckb profile note <key> "..."` | The moment a durable fact is learned |
| `add_hidden_gem` | `ckb gems add --name ... --city ... --pitch ... [--tags ...]` | Caller shares a place worth keeping |

## Suggested wiring (mirrors `booking/puffo.py` subprocess style)

```python
import asyncio, json

CKB = "/home/blin/Experiments/voice/concierge-kb/bin/ckb"

async def _ckb(*argv: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        CKB, *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    return out.decode() or json.dumps({"error": err.decode()[-300:]})

async def on_kb_tool(name: str, args: dict) -> str | None:
    if name == "get_caller_brief":
        return await _ckb("profile", "brief", args["key"])
    if name == "search_hidden_gems":
        argv = ["gems", "search"]
        if args.get("city"):  argv += ["--city", args["city"]]
        if args.get("query"): argv += ["--q", args["query"]]
        for t in (args.get("tags") or "").split(","):
            if t.strip(): argv += ["--tag", t.strip()]
        return await _ckb(*argv)
    if name == "get_hidden_gem":
        return await _ckb("gems", "get", args["id"])
    if name == "remember_about_caller":
        return await _ckb("profile", "note", args["key"], args["note"])
    if name == "add_hidden_gem":
        argv = ["gems", "add", "--name", args["name"], "--city", args["city"],
                "--pitch", args["pitch"], "--source", "caller"]
        if args.get("tags"): argv += ["--tags", args["tags"]]
        return await _ckb(*argv)
    return None  # not ours

TOOLS = json.loads(__import__("subprocess").run(
    [CKB, "tools", "schema"], capture_output=True, text=True).stdout)
```

Then merge `TOOLS` into the brain's tool list and chain `on_kb_tool` in the
existing `on_tool` dispatcher. Consider adding `remember_about_caller` to
`silent_tools` so a memory write doesn't force a spoken follow-up.

## Flow notes

- Profiles are keyed by the same account number as `memory/accounts.py`; phone
  lookup also works, so after caller-ID + PIN auth, pass either.
- Prompt guidance to add to the concierge instructions: *"Right after
  authenticating, call get_caller_brief. Ground every recommendation in
  search_hidden_gems before improvising. When you learn a durable fact about
  the caller, store it with remember_about_caller immediately."*
- Booking handoff unchanged: once a gem with `booking: phone`/`via-hotel` is
  chosen, file the request into the Puffo thread exactly as today
  (`booking/puffo.py`); the channel agent uses the `hidden-gems` skill in this
  repo to see the same gem/profile data.
