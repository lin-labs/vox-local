# Validate profile — vox-local

## Smoke commands

- Python service: `make test`
- Meridian dev server: `make meridian-dev` (normally port 3000)
- Meridian production artifact: `make meridian-check`

## E2E entry points

- Open `http://127.0.0.1:<port>/?quiet=1&auto=1` in Chromium.
- Confirm `POST /api/realtime-token` returns 200 and the browser opens
  `wss://api.x.ai/v1/realtime`.
- Type a trip request, wait for `Your itinerary`, and verify at least one day,
  a spoken-response transcript, and no page errors.
- With a fake or real microphone, click `Talk to Meridian` and verify the UI
  enters `listening`, then stops cleanly on the second click.

## Test entry points

- Python regression: `uv run python -m pytest tests -q`
- Meridian static + build: `make meridian-check`
- Security seam: no `NEXT_PUBLIC_XAI_API_KEY`; only
  `meridian/app/api/realtime-token/route.ts` reads `XAI_API_KEY`.

## Dev environment

- Meridian requires Node 20+ and `npm --prefix meridian ci`.
- Live voice requires server-side `XAI_API_KEY`; the MapLibre fallback needs no
  map key.
- If port 3000 is occupied, run `npm --prefix meridian run dev -- -p 3010` and
  use that origin for browser microphone permissions.

## Known quirks

- Never run `next build` while `next dev` is serving; both write `.next`.
- Map tiles and the realtime WebSocket can prevent Playwright `networkidle`;
  wait for the rendered `main` element and user-visible assertions instead.
- EC2 deploy verification must check both
  `systemctl --user is-active vox-local.service` and `/healthz`. A stale
  unmanaged `vox-up.sh` / `vox-local serve` process can keep port 7780 healthy
  while systemd restart is failing with bind errno 98.

## Highest fidelity rung available

- [x] Static / typecheck
- [x] Python unit + integration suite
- [x] Real xAI dependency E2E
- [x] Chromium user flow with fake microphone
