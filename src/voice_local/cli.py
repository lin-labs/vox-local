"""vox-local CLI.

  vox-local serve                         # the VB MCP backend + HTTP API daemon
  vox-local import-md kb/                 # one-shot legacy markdown -> SQLite
  vox-local gems list [--city kobe]       # inspect the data bag
  vox-local gems add --name ... --city ... --pitch ...
  vox-local account add --account 123456 --pin 4242 --name ... --phone +1650...

Config is environment-driven (~/.env then ./.env, latter wins):
  VOICE_LOCAL_PORT (7780)   VOICE_LOCAL_DB (<repo>/data/gems.db)
  VOICE_LOCAL_STATE (~/data/Projects/vox-local)
  VOICE_LOCAL_GEMS_TOKEN    VB_PUBLIC_URL   VB_AGENT_ID   VB_PHONE_NUMBER
  VOCAL_BRIDGE_API          XAI_API_KEY (trip-context parsing)
  PUFFO_BIN / PUFFO_SERVER_URL / PUFFO_IDENTITY / PUFFO_CHANNEL_ID /
  PUFFO_SPACE_ID / PUFFO_FULFILLER_SLUG   VOICE_LOCAL_DESTINATION (kobe)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from voice_local import db as kbdb

log = logging.getLogger("voice_local.cli")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _bootstrap_env() -> None:
    home_env = Path.home() / ".env"
    if home_env.exists():
        load_dotenv(home_env, override=False)
    local_env = Path(".env")
    if local_env.exists():
        load_dotenv(local_env, override=True)


def _db_path() -> Path:
    return Path(os.environ.get("VOICE_LOCAL_DB", _REPO_ROOT / "data" / "gems.db"))


def _state_dir() -> Path:
    return Path(os.environ.get("VOICE_LOCAL_STATE",
                               Path.home() / "data/Projects/vox-local"))


async def _run_serve(args) -> int:
    import uvicorn

    from voice_local.accounts import AccountStore
    from voice_local.mcp_server import VBBackend, build_app, watchdog_task
    from voice_local.puffo import PuffoClient, PuffoListener, resolve_puffo_bin
    from voice_local.services import CallServices

    api_key = os.environ.get("VOCAL_BRIDGE_API", "")
    if not api_key:
        raise SystemExit("VOCAL_BRIDGE_API not set — needed to resolve in-progress VB "
                         "sessions (caller identity).")
    port = int(os.environ.get("VOICE_LOCAL_PORT", "7780"))
    conn = kbdb.connect(_db_path())
    store = AccountStore(_state_dir() / "accounts")
    destination = os.environ.get("VOICE_LOCAL_DESTINATION", "kobe").lower()

    puffo = None
    listener = None
    puffo_bin = resolve_puffo_bin(os.environ.get("PUFFO_BIN", ""))
    if puffo_bin:
        client = PuffoClient(bin=puffo_bin,
                             server_url=os.environ.get("PUFFO_SERVER_URL", ""),
                             channel_id=os.environ.get("PUFFO_CHANNEL_ID", ""),
                             identity=os.environ.get("PUFFO_IDENTITY", ""),
                             space_id=os.environ.get("PUFFO_SPACE_ID", ""))
        listener = PuffoListener(client)
        listener.start()
        puffo = (client, listener)

    grok = None
    if os.environ.get("XAI_API_KEY"):
        from voice_local.grok import GrokChat

        grok = GrokChat(os.environ["XAI_API_KEY"])

    def services_factory(*, caller_id: str, send):
        return CallServices(conn=conn, store=store, puffo=puffo, caller_id=caller_id,
                            destination=destination, grok=grok,
                            fulfiller_slug=os.environ.get("PUFFO_FULFILLER_SLUG", ""),
                            space_id=os.environ.get("PUFFO_SPACE_ID", ""), send=send)

    backend = VBBackend(services_factory=services_factory, api_key=api_key,
                        agent_id=os.environ.get("VB_AGENT_ID", ""))
    try:
        version = importlib.metadata.version("vox-local")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    public_host = os.environ.get("VB_PUBLIC_URL", "").removeprefix(
        "https://").removeprefix("http://").rstrip("/")
    app = build_app(backend, conn=conn, version=version,
                    vb_phone_number=os.environ.get("VB_PHONE_NUMBER", ""),
                    public_host=public_host,
                    gems_token=os.environ.get("VOICE_LOCAL_GEMS_TOKEN", ""))

    n_gems = conn.execute("SELECT count(*) FROM gems").fetchone()[0]
    print(f"  vox-local: 127.0.0.1:{port}/mcp  (gems: {n_gems}, "
          f"accounts: {len(store.load_all())}, booking: {'ON' if puffo else 'OFF'}, "
          f"destination: {destination})")
    if public_host:
        print(f"  Public: https://{public_host}/mcp")

    wd = asyncio.create_task(watchdog_task())
    reaper = asyncio.create_task(backend.call_end_reaper())
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    try:
        await uvicorn.Server(config).serve()
    finally:
        wd.cancel()
        reaper.cancel()
        await backend.close()
        if listener is not None:
            await listener.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    _bootstrap_env()
    parser = argparse.ArgumentParser(prog="vox-local")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the VB MCP backend + HTTP API daemon")

    p_imp = sub.add_parser("import-md", help="import legacy concierge-kb markdown")
    p_imp.add_argument("kb_dir", nargs="?", default=str(_REPO_ROOT / "kb"))

    p_gems = sub.add_parser("gems", help="inspect or edit the data bag")
    gems_sub = p_gems.add_subparsers(dest="gems_cmd", required=True)
    p_list = gems_sub.add_parser("list")
    p_list.add_argument("--city", default="")
    p_list.add_argument("--query", default="")
    p_add = gems_sub.add_parser("add")
    for f in ("name", "city", "pitch"):
        p_add.add_argument(f"--{f}", required=True)
    for f in ("area", "tags", "price", "booking", "url", "details"):
        p_add.add_argument(f"--{f}", default="")

    p_acct = sub.add_parser("account", help="manage caller accounts")
    acct_sub = p_acct.add_subparsers(dest="account_cmd", required=True)
    p_aadd = acct_sub.add_parser("add")
    p_aadd.add_argument("--account", required=True)
    p_aadd.add_argument("--pin", required=True)
    p_aadd.add_argument("--name", required=True)
    p_aadd.add_argument("--phone", action="append", default=[])

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        return asyncio.run(_run_serve(args))

    conn = kbdb.connect(_db_path())
    if args.cmd == "import-md":
        counts = kbdb.import_markdown(conn, args.kb_dir)
        print(f"imported: {counts['gems']} gems, {counts['profiles']} profiles "
              f"-> {_db_path()}")
        return 0
    if args.cmd == "gems":
        if args.gems_cmd == "list":
            for g in kbdb.search_gems(conn, city=args.city, query=args.query, limit=100):
                print(f"{g['id']:<44} [{','.join(g['tags'])}] {g['pitch'][:70]}")
            return 0
        gem = kbdb.add_gem(conn, name=args.name, city=args.city, pitch=args.pitch,
                           area=args.area, tags=args.tags, price=args.price,
                           booking=args.booking, url=args.url, details=args.details,
                           source="curator")
        print(f"added {gem['id']}")
        return 0
    if args.cmd == "account":
        from voice_local.accounts import Account, AccountStore

        store = AccountStore(_state_dir() / "accounts")
        existing = store.get(args.account)
        account = Account(account_number=args.account, pin=args.pin, name=args.name,
                          phones=list(args.phone),
                          booking_threads=existing.booking_threads if existing else {},
                          channels=existing.channels if existing else {})
        path = store.save(account)
        kbdb.ensure_profile(conn, args.account, name=args.name,
                            phone=args.phone[0] if args.phone else "")
        print(f"saved account {args.account} -> {path}")
        return 0
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
