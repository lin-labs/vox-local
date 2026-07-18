"use client";

import { motion } from "framer-motion";
import { useApp } from "@/lib/store";
import { toggleOrb } from "@/lib/orchestrator";
import { MicIcon } from "@/components/icons";

export default function VoiceOrb() {
  const convo = useApp((s) => s.convo);
  const thinkingPhrase = useApp((s) => s.thinkingPhrase);
  const narration = useApp((s) => s.narration);

  const status =
    convo === "listening"
      ? "listening"
      : convo === "thinking"
        ? narration || thinkingPhrase || "one moment…"
        : convo === "speaking"
          ? "meridian — tap to interrupt"
          : "tap to speak · or hold space";

  return (
    <div className="pointer-events-auto flex flex-col items-center gap-3">
      <motion.button
        onClick={toggleOrb}
        aria-label="Talk to Meridian"
        className="relative grid size-[86px] place-items-center rounded-full focus:outline-none"
        animate={{
          scale: convo === "listening" ? 1.06 : convo === "speaking" ? 1.02 : 1,
        }}
        whileTap={{ scale: 0.94 }}
        transition={{ type: "spring", stiffness: 300, damping: 20 }}
      >
        {/* outer glow */}
        <div
          className={`absolute inset-[-26px] rounded-full blur-2xl transition-all duration-700 ${
            convo === "listening"
              ? "bg-gold-300/35"
              : convo === "speaking"
                ? "bg-gold-200/25"
                : convo === "thinking"
                  ? "bg-gold-400/20"
                  : "bg-gold-400/15"
          }`}
        />
        {/* sonar rings while listening */}
        {convo === "listening" && (
          <>
            <div className="sonar" />
            <div className="sonar s2" />
          </>
        )}
        {/* rotating conic ring */}
        <div
          className={`orb-ring absolute inset-0 rounded-full ${
            convo === "thinking" ? "fast" : ""
          }`}
          style={{
            WebkitMask:
              "radial-gradient(farthest-side, transparent calc(100% - 2px), #000 calc(100% - 1.5px))",
            mask: "radial-gradient(farthest-side, transparent calc(100% - 2px), #000 calc(100% - 1.5px))",
          }}
        />
        {/* glass disc */}
        <div className="absolute inset-[5px] rounded-full glass shadow-[inset_0_1px_0_rgba(244,234,210,0.15),0_10px_40px_rgba(0,0,0,0.55)]" />
        {/* center state */}
        <div className="relative z-10 text-gold-200">
          {convo === "listening" || convo === "speaking" ? (
            <div className="orb-bars">
              <span />
              <span />
              <span />
              <span />
              <span />
            </div>
          ) : convo === "thinking" ? (
            <div className="orb-dots">
              <span />
              <span />
              <span />
            </div>
          ) : (
            <MicIcon className="opacity-90" />
          )}
        </div>
      </motion.button>

      <div
        className={`max-w-[440px] truncate text-center font-mono text-[9px] uppercase tracking-[0.32em] ${
          convo === "thinking" && narration
            ? "text-gold-200/70"
            : "text-ivory/40"
        }`}
      >
        {status}
      </div>
    </div>
  );
}
