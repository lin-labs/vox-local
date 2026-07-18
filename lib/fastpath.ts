/* Instant voice → map commands, no round-trip to the model. */

import type { CameraMove } from "@/lib/types";

export type FastResult =
  | { kind: "zoomBy"; delta: number; ack: string }
  | { kind: "fly"; move: CameraMove; ack: string }
  | { kind: "fit"; ack: string }
  | { kind: "hush" };

export function fastPath(raw: string): FastResult | null {
  const t = raw.toLowerCase().trim().replace(/[.!,]+$/, "");

  if (/^(stop|quiet|hush|pause|shut up|silence)$/.test(t)) {
    return { kind: "hush" };
  }
  // Short utterances only — longer phrasings deserve the concierge.
  if (t.length <= 26) {
    if (/\b(zoom in|closer|get closer|come in|go in)\b/.test(t))
      return { kind: "zoomBy", delta: 1.8, ack: "Closer." };
    if (/\b(zoom out|back out|pull back|go out|farther|further)\b/.test(t))
      return { kind: "zoomBy", delta: -1.8, ack: "Pulling back." };
  }
  if (
    /\b(whole world|the world|the globe|whole planet|from space|all the way out|see the earth)\b/.test(
      t
    ) &&
    t.length <= 34
  ) {
    return {
      kind: "fly",
      move: { center: [20, 18], zoom: 1.7, pitch: 0, bearing: 0 },
      ack: "The world.",
    };
  }
  if (
    /\b(whole trip|entire trip|full trip|whole route|my route|whole itinerary|show the itinerary|show my trip)\b/.test(
      t
    )
  ) {
    return { kind: "fit", ack: "Your route, end to end." };
  }
  return null;
}
