"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useApp } from "@/lib/store";
import { flyToPlace } from "@/lib/orchestrator";
import type { Enrichment } from "@/lib/types";

function EnrichmentBlock({ e }: { e: Enrichment }) {
  const meta = [e.priceBand, e.source, e.asOf && `as of ${e.asOf}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-1.5 rounded-md border border-gold-300/15 bg-gold-400/[0.05] px-2.5 py-2"
    >
      {e.facts.slice(0, 3).map((f) => (
        <div
          key={f}
          className="flex gap-1.5 text-[11px] leading-snug text-gold-100/75"
        >
          <span className="shrink-0 text-gold-300/70">◆</span>
          <span>{f}</span>
        </div>
      ))}
      {meta && (
        <div className="mt-1 font-mono text-[8.5px] uppercase tracking-[0.16em] text-ivory/30">
          {meta}
        </div>
      )}
    </motion.div>
  );
}

export default function ItineraryPanel() {
  const itinerary = useApp((s) => s.itinerary);
  const pulseId = useApp((s) => s.pulseId);
  const changed = useApp((s) => s.changed);

  const setPulse = (id: string | null) => useApp.setState({ pulseId: id });

  return (
    <AnimatePresence>
      {itinerary && (
        <motion.aside
          initial={{ x: 64, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 64, opacity: 0 }}
          transition={{ type: "spring", stiffness: 160, damping: 24 }}
          className="pointer-events-auto absolute right-5 top-[72px] bottom-[136px] z-30 hidden w-[400px] max-w-[40vw] flex-col overflow-hidden rounded-2xl glass shadow-[0_30px_80px_rgba(0,0,0,0.5)] md:flex"
        >
          <header className="border-b hairline px-6 pb-5 pt-6">
            <div className="font-mono text-[9px] uppercase tracking-[0.35em] text-gold-300/60">
              Your itinerary
            </div>
            <h2 className="font-display mt-2 text-[26px] leading-tight text-ivory">
              {itinerary.title}
            </h2>
            {itinerary.subtitle && (
              <p className="mt-1 text-[12.5px] text-ivory/55">
                {itinerary.subtitle}
              </p>
            )}
            <div className="mt-3 font-mono text-[9.5px] uppercase tracking-[0.22em] text-ivory/35">
              {itinerary.days.length} days ·{" "}
              {itinerary.days.reduce((n, d) => n + d.items.length, 0)} moments
            </div>
          </header>

          <div className="scroll-slim flex-1 overflow-y-auto px-4 py-4">
            <AnimatePresence initial={false}>
              {itinerary.days.map((d) => {
                const dayId = `day-${d.day}`;
                const dayGlow = changed.includes(dayId);
                return (
                  <motion.section
                    key={`${d.location}-${d.day}`}
                    layout
                    initial={{ opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, height: 0, overflow: "hidden" }}
                    transition={{ duration: 0.35 }}
                    className="mb-5"
                  >
                    <div
                      className={`cursor-pointer rounded-lg px-2 py-1 transition-colors ${
                        dayGlow ? "bg-gold-300/[0.09]" : ""
                      }`}
                      onMouseEnter={() => setPulse(dayId)}
                      onMouseLeave={() => setPulse(null)}
                      onClick={() => flyToPlace(d.center, 12)}
                    >
                      <div className="flex items-baseline gap-3">
                        <span className="font-mono text-[10px] tracking-[0.25em] text-gold-300/80">
                          DAY {String(d.day).padStart(2, "0")}
                        </span>
                        <span className="font-display text-[17px] italic text-ivory/90">
                          {d.location}
                        </span>
                      </div>
                      {d.summary && (
                        <div className="mt-0.5 text-[11.5px] text-ivory/45">
                          {d.summary}
                        </div>
                      )}
                    </div>

                    <div className="mt-2">
                      <AnimatePresence initial={false}>
                        {d.items.map((item, i) => {
                          const id = item.id ?? `d${d.day}-${i}`;
                          const active = pulseId === id;
                          const glow = changed.includes(id);
                          return (
                            <motion.div
                              key={id}
                              layout
                              initial={{ opacity: 0, y: 8 }}
                              animate={{ opacity: 1, y: 0 }}
                              exit={{ opacity: 0, height: 0, overflow: "hidden" }}
                              transition={{ duration: 0.3, delay: i * 0.03 }}
                              className={`group flex cursor-pointer gap-3 rounded-lg px-2 py-2 transition-colors duration-500 ${
                                glow
                                  ? "bg-gold-300/[0.09] ring-1 ring-gold-300/30"
                                  : active
                                    ? "bg-gold-300/10"
                                    : "hover:bg-white/[0.045]"
                              }`}
                              onMouseEnter={() => setPulse(id)}
                              onMouseLeave={() => setPulse(null)}
                              onClick={() =>
                                flyToPlace(
                                  item.lngLat ?? d.center,
                                  item.lngLat ? 14.8 : 12
                                )
                              }
                            >
                              <div className="w-11 shrink-0 pt-0.5 text-right font-mono text-[10px] text-gold-300/70">
                                {item.time ?? "·"}
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="flex items-baseline gap-1.5 text-[13.5px] leading-snug text-ivory/92">
                                  <span>{item.title}</span>
                                  {item.enrichment && (
                                    <span
                                      className="text-[10px] text-gold-300/80"
                                      title="Researched live"
                                    >
                                      ✦
                                    </span>
                                  )}
                                </div>
                                {item.detail && (
                                  <div className="mt-0.5 text-[11.5px] leading-snug text-ivory/45">
                                    {item.detail}
                                  </div>
                                )}
                                {item.enrichment && (
                                  <EnrichmentBlock e={item.enrichment} />
                                )}
                              </div>
                            </motion.div>
                          );
                        })}
                      </AnimatePresence>
                    </div>
                  </motion.section>
                );
              })}
            </AnimatePresence>
          </div>

          <footer className="border-t hairline px-6 py-3 font-mono text-[8.5px] uppercase tracking-[0.25em] text-ivory/30">
            Composed in conversation — try &ldquo;make it five days&rdquo;
          </footer>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
