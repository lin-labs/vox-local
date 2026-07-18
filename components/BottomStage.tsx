"use client";

import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useApp } from "@/lib/store";
import { handleUtterance } from "@/lib/orchestrator";
import VoiceOrb from "@/components/VoiceOrb";
import { SendIcon } from "@/components/icons";

const DEFAULT_CHIPS = [
  "A week of food in Japan",
  "Honeymoon in the Greek isles",
  "Chase the northern lights",
  "Marrakech & the desert",
  "Surprise me",
];

function Subtitles() {
  const convo = useApp((s) => s.convo);
  const interim = useApp((s) => s.interim);
  const lastReply = useApp((s) => s.lastReply);

  return (
    <div className="flex min-h-[58px] w-full max-w-2xl items-end justify-center px-6 text-center">
      <AnimatePresence mode="wait">
        {convo === "listening" && interim ? (
          <motion.p
            key="interim"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="font-mono text-[13px] tracking-wide text-gold-200/90"
          >
            &ldquo;{interim}&rdquo;
          </motion.p>
        ) : lastReply ? (
          <motion.p
            key={lastReply}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.5 }}
            className="font-display text-[18.5px] leading-snug text-ivory/90 [text-shadow:0_2px_18px_rgba(0,0,0,0.7)]"
          >
            {lastReply}
          </motion.p>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function Chips() {
  const itinerary = useApp((s) => s.itinerary);
  const started = useApp((s) => s.started);
  const dynamic = useApp((s) => s.chips);
  const convo = useApp((s) => s.convo);

  if (!started || convo === "thinking") return null;
  const chips = dynamic.length
    ? dynamic
    : itinerary
      ? []
      : DEFAULT_CHIPS;
  if (!chips.length) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ delay: 0.2 }}
      className="pointer-events-auto flex max-w-3xl flex-wrap items-center justify-center gap-2 px-6"
    >
      {chips.map((c, i) => (
        <button
          key={c}
          data-chip={i}
          onClick={() => void handleUtterance(c)}
          className="rounded-full border border-ivory/12 bg-ink-900/50 px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-ivory/60 backdrop-blur-md transition-all hover:border-gold-300/40 hover:bg-gold-400/10 hover:text-gold-200"
        >
          {c}
        </button>
      ))}
    </motion.div>
  );
}

function TextDock() {
  const textOpen = useApp((s) => s.textOpen);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (textOpen) inputRef.current?.focus();
  }, [textOpen]);

  if (!textOpen) return null;

  const submit = () => {
    const v = inputRef.current?.value.trim();
    if (!v) return;
    inputRef.current!.value = "";
    void handleUtterance(v);
  };

  return (
    <motion.form
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      data-textdock
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="pointer-events-auto flex w-[400px] max-w-[86vw] items-center gap-2 rounded-full glass py-1.5 pl-5 pr-1.5"
    >
      <input
        ref={inputRef}
        data-textinput
        placeholder="Tell Meridian where the two of you are going…"
        className="w-full bg-transparent text-[13px] text-ivory placeholder:text-ivory/30 focus:outline-none"
        onKeyDown={(e) => {
          if (e.key === "Escape") useApp.setState({ textOpen: false });
          e.stopPropagation();
        }}
      />
      <button
        type="submit"
        aria-label="Send"
        className="grid size-8 shrink-0 place-items-center rounded-full bg-gold-300/90 text-ink-950 transition hover:bg-gold-200"
      >
        <SendIcon />
      </button>
    </motion.form>
  );
}

function Hint() {
  const hint = useApp((s) => s.hint);
  useEffect(() => {
    if (!hint) return;
    const t = setTimeout(() => useApp.setState({ hint: null }), 4200);
    return () => clearTimeout(t);
  }, [hint]);

  return (
    <AnimatePresence>
      {hint && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          className="rounded-full border border-gold-300/25 bg-ink-900/70 px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-gold-200/80 backdrop-blur-md"
        >
          {hint}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export default function BottomStage() {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-7 z-30 flex flex-col items-center gap-4">
      <Hint />
      <Subtitles />
      <Chips />
      <TextDock />
      <VoiceOrb />
    </div>
  );
}
