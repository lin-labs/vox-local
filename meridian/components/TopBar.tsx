"use client";

import { useApp } from "@/lib/store";
import { setHandsFree, setMuted } from "@/lib/orchestrator";
import {
  AudioLinesIcon,
  KeyboardIcon,
  VolumeIcon,
  VolumeOffIcon,
} from "@/components/icons";

function Toggle({
  on,
  onClick,
  title,
  children,
}: {
  on?: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className={`grid size-9 place-items-center rounded-full border transition-all ${
        on
          ? "border-gold-300/50 bg-gold-400/10 text-gold-200"
          : "border-ivory/10 bg-ink-900/40 text-ivory/50 hover:border-ivory/25 hover:text-ivory/80"
      }`}
    >
      {children}
    </button>
  );
}

export default function TopBar() {
  const handsFree = useApp((s) => s.handsFree);
  const muted = useApp((s) => s.muted);
  const textOpen = useApp((s) => s.textOpen);

  return (
    <header className="pointer-events-none absolute inset-x-0 top-0 z-30 flex items-center justify-between px-6 py-4">
      <div className="pointer-events-auto flex items-baseline gap-3">
        <span className="font-display text-[21px] italic tracking-wide text-ivory">
          Meridian
        </span>
        <span className="hidden h-3 w-px bg-ivory/20 sm:block" />
        <span className="hidden font-mono text-[8.5px] uppercase tracking-[0.4em] text-gold-300/55 sm:block">
          Private travel atelier
        </span>
      </div>

      <div className="pointer-events-auto flex items-center gap-2">
        <Toggle
          on={handsFree}
          onClick={() => setHandsFree(!handsFree)}
          title="Hands-free conversation (auto-listen after each reply)"
        >
          <AudioLinesIcon />
        </Toggle>
        <Toggle
          on={!muted}
          onClick={() => setMuted(!muted)}
          title={muted ? "Unmute Meridian's voice" : "Mute Meridian's voice"}
        >
          {muted ? <VolumeOffIcon /> : <VolumeIcon />}
        </Toggle>
        <Toggle
          on={textOpen}
          onClick={() => useApp.setState({ textOpen: !textOpen })}
          title="Type instead of speaking ( / )"
        >
          <KeyboardIcon />
        </Toggle>
      </div>
    </header>
  );
}
