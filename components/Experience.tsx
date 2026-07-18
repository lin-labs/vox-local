"use client";

import { useEffect } from "react";
import dynamic from "next/dynamic";
import { useApp } from "@/lib/store";
import { begin, interrupt, toggleOrb } from "@/lib/orchestrator";
import TopBar from "@/components/TopBar";
import ItineraryPanel from "@/components/ItineraryPanel";
import BottomStage from "@/components/BottomStage";
import IntroOverlay from "@/components/IntroOverlay";
import Badges from "@/components/Badges";
import AgentFeed from "@/components/AgentFeed";

const Globe = dynamic(() => import("@/components/Globe"), { ssr: false });

export default function Experience() {
  // Keyboard: space = talk, "/" = type, esc = interrupt.
  useEffect(() => {
    const isTyping = () => {
      const el = document.activeElement;
      return el?.tagName === "INPUT" || el?.tagName === "TEXTAREA";
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat) return;
      if (e.code === "Space" && !isTyping()) {
        e.preventDefault();
        toggleOrb();
      } else if (e.key === "/" && !isTyping()) {
        e.preventDefault();
        useApp.setState({ textOpen: true });
      } else if (e.key === "Escape") {
        interrupt();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ?quiet=1 → muted + no auto-listen (silent demos, automated checks).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("quiet") === "1") {
      useApp.setState({ muted: true, handsFree: false });
      if (params.get("auto") === "1") begin({ quiet: true });
    }
  }, []);

  return (
    <main className="relative h-dvh w-screen overflow-hidden bg-ink-950 text-ivory">
      {/* deep space behind the globe */}
      <div className="starfield absolute inset-0" />

      <Globe />

      {/* cinematic finish */}
      <div className="vignette pointer-events-none absolute inset-0 z-20" />
      <div className="edge-fade-top pointer-events-none absolute inset-x-0 top-0 z-20 h-24" />
      <div className="edge-fade-bottom pointer-events-none absolute inset-x-0 bottom-0 z-20 h-44" />
      <div className="grain pointer-events-none absolute inset-0 z-[21]" />

      <TopBar />
      <AgentFeed />
      <ItineraryPanel />
      <BottomStage />
      <Badges />
      <IntroOverlay />
    </main>
  );
}
