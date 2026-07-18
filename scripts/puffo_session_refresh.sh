#!/bin/bash
# Refresh Puffo sessions daily (voice-local-puffo-refresh.timer).
#
# Puffo login subkeys expire after ~48h; when koyuki's session lapsed the
# service's `puffo listen` died every 30s for a day (2026-07-17) and no
# fulfiller replies could arrive. `identity use` re-logins non-interactively;
# run it for each identity, ending on voxcallbot to keep it the CLI default.
set -u
P=/home/blin/.local/bin/puffo
URL=https://chat.puffo.ai/relay

for ident in koyuki-concierge-a21d7275 voxcallbot-74dfc575; do
  if out=$("$P" --server-url "$URL" identity use "$ident" 2>&1); then
    echo "refreshed $ident"
  else
    echo "FAILED to refresh $ident: $out" >&2
  fi
done
