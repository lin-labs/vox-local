# Project Retrospective Notes

## Reliable patterns

- Import standalone application history with a two-parent subtree-style merge so the original commit remains traceable while the files live under their owned folder.
- Keep partner development on a main-based integration branch; retain the original unrelated branch as provenance rather than using it for new work.
- Validate browser voice changes against the real provider with a short-lived credential and a focused end-to-end flow, in addition to typechecking and production builds.

## Local gotchas

- Port 3000 may already belong to another application. Identify the listening process before testing and use the next free port without stopping unrelated work.
- Do not run a production build while the Meridian development server is using the same `.next` directory.
- Preserve unrelated dirty files when syncing or rebasing; stage only the files owned by the current change.
- For EC2 deploys, `/healthz` alone is insufficient. Check `systemctl --user is-active vox-local.service` too; a stale unmanaged launcher can own port 7780 while the unit is failing to restart.

## Completion checklist

- Confirm `make meridian-check` passes.
- Exercise token minting, the xAI realtime socket, a tool-driven itinerary update, and microphone start/stop in a browser.
- Confirm the merge commit still has the imported Meridian commit as its second parent.
- Confirm `origin/meridian-dev` points at the current main-based integration tip.
