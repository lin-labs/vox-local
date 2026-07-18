"use client";

import { useApp } from "@/lib/store";

/* Quiet, honest capability badges — bottom-left. */
export default function Badges() {
  const mapFlavor = useApp((s) => s.mapFlavor);
  const agentSource = useApp((s) => s.agentSource);

  const notes: string[] = [];
  if (mapFlavor === "maplibre")
    notes.push("demo basemap — add NEXT_PUBLIC_MAPBOX_TOKEN for the cinematic globe");
  if (agentSource === "mock")
    notes.push("offline concierge — add XAI_API_KEY for realtime voice");

  if (!notes.length) return null;

  return (
    <div className="pointer-events-none absolute bottom-3 left-3 z-30 flex flex-col gap-1.5">
      {notes.map((n) => (
        <div
          key={n}
          className="rounded-full border border-ivory/8 bg-ink-950/60 px-3 py-1 font-mono text-[8.5px] uppercase tracking-[0.14em] text-ivory/35 backdrop-blur-sm"
        >
          ◈ {n}
        </div>
      ))}
    </div>
  );
}
