#!/usr/bin/env python3
"""Update a puffo-cli identity's server-side profile (display name / avatar /
role) — the Rust CLI only exposes avatar. Signs PATCH /identities/self with the
identity's message subkey straight out of ~/.local/share/puffo-cli/keys, using
the puffo-agent venv's crypto stack.

Usage (run with ~/.venvs/puffo-agent/bin/python):
  puffo_profile.py <slug> [--display-name X] [--avatar-url URL]
                          [--role TEXT] [--role-short TEXT] [--show]
"""
import argparse
import asyncio
import json
from pathlib import Path

import aiohttp
from puffo_agent.crypto.encoding import base64url_decode
from puffo_agent.crypto.http_auth import sign_request
from puffo_agent.crypto.primitives import Ed25519KeyPair

KEYS = Path.home() / ".local/share/puffo-cli/keys"


def _key_and_session(slug: str):
    crypto = json.loads((KEYS / f"{slug}.crypto.json").read_text())
    sess = json.loads((KEYS / f"{slug}.session.json").read_text())
    secret = next(e["secret"]["bytes_b64u"] for e in crypto["entries"]
                  if e["descriptor"]["purpose"] == "message_subkey_signing")
    ident = json.loads((KEYS / f"{slug}.json").read_text())
    return (Ed25519KeyPair.from_secret_bytes(base64url_decode(secret)),
            sess["subkey_id"], ident["server_url"])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--display-name")
    ap.add_argument("--avatar-url")
    ap.add_argument("--role")
    ap.add_argument("--role-short")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    key, subkey_id, base = _key_and_session(args.slug)
    patch = {k: v for k, v in (("display_name", args.display_name),
                               ("avatar_url", args.avatar_url),
                               ("role", args.role),
                               ("role_short", args.role_short)) if v}
    async with aiohttp.ClientSession() as http:
        if patch:
            body = json.dumps(patch).encode()
            auth = sign_request(key, args.slug, subkey_id, "PATCH",
                                "/identities/self", body)
            async with http.patch(base.rstrip("/") + "/identities/self",
                                  data=body, headers=auth.to_dict()) as r:
                print("PATCH", r.status, await r.text())
        if args.show or not patch:
            path = f"/identities/profiles?slugs={args.slug}"
            auth = sign_request(key, args.slug, subkey_id, "GET", path)
            async with http.get(base.rstrip("/") + path,
                                headers=auth.to_dict()) as r:
                print(json.dumps(json.loads(await r.text()), indent=1))


if __name__ == "__main__":
    asyncio.run(main())
