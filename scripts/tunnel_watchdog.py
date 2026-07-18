#!/usr/bin/env python3
"""Public-tunnel watchdog for the Koyuki line (voice-local-zh1).

Checks https://<public>/healthz. On failure: restarts voice-local-ngrok.service,
re-checks, and if the line is still dark posts a Discord alert to #voxcall
(Hermes bot token) — debounced to one alert per hour. Exit 0 always: the timer
must keep firing.

Run by voice-local-watchdog.timer every 2 minutes. The 2026-07-16 incident this
guards against: ngrok exited cleanly on a remote "stop request" and the agent
took a tester's whole call with zero backend tools, silently.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PUBLIC_HEALTHZ = "https://lula-tasteless-businesslike.ngrok-free.dev/healthz"
NGROK_UNIT = "voice-local-ngrok.service"
BACKEND_UNIT = "voice-local.service"
DISCORD_CHANNEL = "1527331190456062143"          # #voxcall
DISCORD_MENTION = "<@827376105639510018>"
STATE = Path.home() / "data/Projects/vox-local/tunnel-watchdog-state.json"
ALERT_COOLDOWN_S = 3600


def healthy() -> bool:
    try:
        with urllib.request.urlopen(PUBLIC_HEALTHZ, timeout=10) as resp:
            return bool(json.loads(resp.read().decode()).get("ok"))
    except Exception:  # noqa: BLE001 - any failure means the line is dark
        return False


def hermes_bot_token() -> str:
    try:
        for line in (Path.home() / ".hermes/.env").read_text().splitlines():
            if line.strip().startswith("DISCORD_BOT_TOKEN") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def alert(text: str) -> None:
    now = time.time()
    try:
        last = json.loads(STATE.read_text()).get("last_alert", 0)
    except (OSError, ValueError):
        last = 0
    if now - last < ALERT_COOLDOWN_S:
        return
    token = hermes_bot_token()
    if not token:
        print("no Discord token; alert suppressed:", text)
        return
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages",
        data=json.dumps({"content": f"{DISCORD_MENTION} {text}"}).encode(),
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (voice-local watchdog, 0.1)"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"last_alert": now}))
    except Exception as exc:  # noqa: BLE001
        print("discord alert failed:", exc)


def main() -> int:
    if healthy():
        return 0
    print("public healthz failing — restarting", NGROK_UNIT)
    subprocess.run(["systemctl", "--user", "restart", NGROK_UNIT], check=False)
    time.sleep(10)
    if healthy():
        alert("voxcall: Koyuki's public tunnel was dark; watchdog restarted ngrok "
              "and the line is healthy again.")
        return 0
    subprocess.run(["systemctl", "--user", "restart", BACKEND_UNIT], check=False)
    time.sleep(10)
    if healthy():
        alert("voxcall: Koyuki's line was dark; watchdog restarted ngrok + backend "
              "and it recovered.")
        return 0
    alert("voxcall: Koyuki's line is DOWN — public healthz still failing after "
          "restarting ngrok and voice-local. Calls are running WITHOUT backend "
          "tools until this is fixed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
