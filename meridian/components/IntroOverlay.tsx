"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useApp } from "@/lib/store";
import { begin } from "@/lib/orchestrator";

export default function IntroOverlay() {
  const started = useApp((s) => s.started);

  return (
    <AnimatePresence>
      {!started && (
        <motion.div
          key="intro"
          exit={{ opacity: 0, scale: 1.02, filter: "blur(6px)" }}
          transition={{ duration: 0.9, ease: "easeInOut" }}
          className="absolute inset-0 z-50 flex flex-col items-center justify-center px-6 text-center"
          style={{
            background:
              "radial-gradient(90% 70% at 50% 45%, rgba(4,7,13,0.42) 0%, rgba(4,7,13,0.82) 100%)",
          }}
        >
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1.1, delay: 0.15, ease: "easeOut" }}
            className="flex flex-col items-center"
          >
            <div className="font-mono text-[9.5px] uppercase tracking-[0.5em] text-gold-300/70">
              Meridian · Private Travel Atelier
            </div>

            <h1 className="font-display mt-6 max-w-3xl text-[clamp(40px,7vw,76px)] leading-[1.04] text-ivory">
              The world, arranged{" "}
              <em className="italic text-gold-200">around you.</em>
            </h1>

            <p className="mt-6 max-w-md text-[14.5px] leading-relaxed text-ivory/60">
              A private concierge that listens. Speak naturally — the globe
              moves with your words, and your itinerary composes itself in
              real time.
            </p>

            <button
              data-begin
              onClick={() => begin()}
              className="mt-10 rounded-full bg-gold-300 px-8 py-3.5 text-[13px] font-medium tracking-[0.08em] text-ink-950 shadow-[0_0_50px_rgba(223,196,140,0.35)] transition-all hover:bg-gold-200 hover:shadow-[0_0_70px_rgba(223,196,140,0.5)]"
            >
              Begin the conversation
            </button>

            <div className="mt-5 font-mono text-[9px] uppercase tracking-[0.3em] text-ivory/35">
              or press space · best in Chrome, sound on
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
