"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useApp } from "@/lib/store";
import type { FeedItem } from "@/lib/types";

const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

function Icon({ name }: { name: FeedItem["name"] }) {
  if (name === "web_search")
    return (
      <svg viewBox="0 0 24 24" width="12" height="12" {...base}>
        <circle cx="11" cy="11" r="6.5" />
        <path d="m20 20-4.2-4.2" />
      </svg>
    );
  if (name === "edit_itinerary")
    return (
      <svg viewBox="0 0 24 24" width="12" height="12" {...base}>
        <path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3Z" />
        <path d="m13.5 6.5 3 3" />
      </svg>
    );
  if (name === "set_camera")
    return (
      <svg viewBox="0 0 24 24" width="12" height="12" {...base}>
        <circle cx="12" cy="12" r="8.5" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    );
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" {...base}>
      <path d="M4 12c5-1 7-3 8-8 1 5 3 7 8 8-5 1-7 3-8 8-1-5-3-7-8-8Z" />
    </svg>
  );
}

export default function AgentFeed() {
  const feed = useApp((s) => s.feed);

  return (
    <div className="pointer-events-none absolute left-5 top-16 z-30 hidden w-[300px] flex-col gap-1.5 md:flex">
      <AnimatePresence initial={false}>
        {feed.map((f) => (
          <motion.div
            key={f.id}
            layout
            initial={{ opacity: 0, x: -14 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -10 }}
            transition={{ duration: 0.25 }}
            className={`flex items-center gap-2.5 rounded-lg border px-3 py-2 backdrop-blur-md ${
              f.state === "done"
                ? "border-ivory/8 bg-ink-950/50 text-ivory/45"
                : "border-gold-300/25 bg-ink-900/65 text-gold-200/90"
            }`}
          >
            <span className={f.state === "done" ? "text-ivory/35" : "text-gold-300"}>
              <Icon name={f.name} />
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-[10px] uppercase tracking-[0.14em]">
              {f.label}
            </span>
            {f.state === "start" ? (
              <span className="relative flex size-1.5 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-gold-300/70" />
                <span className="relative inline-flex size-1.5 rounded-full bg-gold-300" />
              </span>
            ) : (
              <svg viewBox="0 0 24 24" width="11" height="11" {...base} className="shrink-0 text-gold-300/70">
                <path d="m5 13 4.5 4.5L19 7" />
              </svg>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
