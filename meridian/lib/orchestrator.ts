/* The conversation loop: voice in → streaming agent → live map/itinerary edits → voice out. */

import { useApp } from "@/lib/store";
import {
  cancelSpeak,
  primeTTS,
  speak,
  startRec,
  stopRec,
  sttSupported,
} from "@/lib/speech";
import { fastPath } from "@/lib/fastpath";
import { applyOps } from "@/lib/agent/ops";
import type {
  CameraCmd,
  ChatMsg,
  Itinerary,
  LngLat,
  MarkerSpec,
  TurnEvent,
} from "@/lib/types";

let nonce = 0;
let silenceCount = 0;
let turnAbort: AbortController | null = null;

const THINKING = [
  "Consulting the atlas…",
  "Charting your course…",
  "Calling a few local friends…",
  "Unfolding the maps…",
  "Checking the light this time of year…",
];

const RESEARCH_RE =
  /\b(find|search|look up|research|recommend|best|top.?rated|is (it|there)|what'?s (the|a)|how much|price|open|hours|tell me more|details? (on|about))\b/i;
const ACKS = [
  "Let me look into that for you.",
  "On it — give me a moment.",
  "Allow me a moment to check.",
];

const get = () => useApp.getState();
const set = useApp.setState;
const pick = <T,>(a: T[]): T => a[Math.floor(Math.random() * a.length)];

// ── Derivations ───────────────────────────────────────────────────────────────

function deriveMarkers(it: Itinerary | null): MarkerSpec[] {
  if (!it) return [];
  const out: MarkerSpec[] = [];
  const seen = new Set<string>();
  for (const d of it.days) {
    const key = d.center.map((n) => n.toFixed(3)).join(",");
    if (seen.has(key)) continue; // consecutive days in one city → one pin
    seen.add(key);
    out.push({
      id: `day-${d.day}`,
      name: d.location,
      label: d.location,
      lngLat: d.center,
      day: d.day,
      kind: "stop",
    });
  }
  const poiBudget = Math.max(0, 12 - out.length);
  let used = 0;
  for (const d of it.days) {
    d.items.forEach((item, i) => {
      if (item.lngLat && used < poiBudget) {
        out.push({
          id: item.id ?? `d${d.day}-${i}`,
          name: item.title,
          label: item.title,
          lngLat: item.lngLat,
          day: d.day,
          kind: "poi",
        });
        used++;
      }
    });
  }
  return out;
}

function haversineKm(a: LngLat, b: LngLat): number {
  const R = 6371;
  const dLat = ((b[1] - a[1]) * Math.PI) / 180;
  const dLng = ((b[0] - a[0]) * Math.PI) / 180;
  const la = (a[1] * Math.PI) / 180;
  const lb = (b[1] * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(la) * Math.cos(lb) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function deriveRoute(it: Itinerary | null): LngLat[] | null {
  if (!it) return null;
  const pts: LngLat[] = [];
  for (const d of it.days) {
    const last = pts[pts.length - 1];
    if (!last || last[0] !== d.center[0] || last[1] !== d.center[1]) {
      pts.push(d.center);
    }
  }
  if (pts.length < 2) return null;
  for (let i = 1; i < pts.length; i++) {
    if (haversineKm(pts[i - 1], pts[i]) > 2200) return null; // skip ugly transcontinental chords
  }
  return pts;
}

function refreshDerived(itinerary: Itinerary | null): MarkerSpec[] {
  const markers = deriveMarkers(itinerary);
  set({ itinerary, markers, route: deriveRoute(itinerary) });
  return markers;
}

function markChanged(ids: string[]) {
  if (!ids.length) return;
  set((s) => ({ changed: [...new Set([...s.changed, ...ids])] }));
  setTimeout(() => {
    useApp.setState((s) => ({
      changed: s.changed.filter((id) => !ids.includes(id)),
    }));
  }, 6500);
}

// ── Speaking / listening loop ────────────────────────────────────────────────

let feedClearTimer: ReturnType<typeof setTimeout> | null = null;

function maybeRelisten() {
  // let the finished tool feed linger briefly, then clear
  if (feedClearTimer) clearTimeout(feedClearTimer);
  feedClearTimer = setTimeout(() => set({ feed: [] }), 3000);
  const s = get();
  if (s.handsFree && !s.micBlocked && sttSupported()) {
    startListening();
  } else {
    set({ convo: "idle" });
  }
}

function speakReply(text: string) {
  const s = get();
  if (s.muted) {
    set({ convo: "idle" });
    maybeRelisten();
    return;
  }
  set({ convo: "speaking" });
  speak(text, () => maybeRelisten());
}

export function startListening() {
  if (!sttSupported()) {
    set({
      textOpen: true,
      convo: "idle",
      hint: "Voice needs Chrome — typing works everywhere.",
    });
    return;
  }
  cancelSpeak();
  set({ convo: "listening", interim: "" });
  startRec({
    onInterim: (t) => set({ interim: t }),
    onFinal: (t) => {
      silenceCount = 0;
      stopRec();
      void handleUtterance(t);
    },
    onError: (code) => {
      if (code === "not-allowed" || code === "service-not-allowed") {
        set({
          micBlocked: true,
          handsFree: false,
          textOpen: true,
          convo: "idle",
          hint: "Microphone blocked — type below instead.",
        });
      } else if (code !== "no-speech" && code !== "aborted") {
        set({ convo: "idle" });
      }
    },
    onEnd: () => {
      const s = get();
      if (s.convo !== "listening") return;
      if (s.handsFree && silenceCount < 2) {
        silenceCount++;
        startListening();
      } else {
        silenceCount = 0;
        set({ convo: "idle", hint: "Tap the orb when you're ready." });
      }
    },
  });
}

export function stopListening() {
  stopRec();
  set({ convo: "idle", interim: "" });
}

// ── Entry points ─────────────────────────────────────────────────────────────

export function begin(opts?: { quiet?: boolean }) {
  if (get().started) return;
  primeTTS();
  if (opts?.quiet) set({ muted: true, handsFree: false });
  const h = new Date().getHours();
  const sal =
    h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  const greeting = `${sal} — Meridian here, your private concierge. Name a place, a craving, or a feeling, and I'll chart it while we talk. Where shall we begin?`;
  set({
    started: true,
    messages: [{ role: "assistant", content: greeting }],
    lastReply: greeting,
  });
  speakReply(greeting);
}

export function toggleOrb() {
  const s = get();
  if (!s.started) {
    begin();
    return;
  }
  if (s.convo === "listening") {
    stopListening();
  } else if (s.convo === "speaking") {
    cancelSpeak();
    startListening();
  } else if (s.convo === "idle") {
    startListening();
  }
  // thinking → let it work; Esc interrupts
}

export function interrupt() {
  turnAbort?.abort();
  turnAbort = null;
  cancelSpeak();
  stopRec();
  set({ convo: "idle", interim: "", narration: "" });
}

export function flyToPlace(lngLat: LngLat, zoom = 14.2) {
  set({
    camera: {
      nonce: ++nonce,
      kind: "fly",
      move: { center: lngLat, zoom, pitch: 58, bearing: -20, durationMs: 2600 },
    },
  });
}

function sanitizeForApi(messages: ChatMsg[]): ChatMsg[] {
  const trimmed = messages.slice(-16);
  while (trimmed.length && trimmed[0].role !== "user") trimmed.shift();
  const out: ChatMsg[] = [];
  for (const m of trimmed) {
    const last = out[out.length - 1];
    if (last && last.role === m.role) last.content += "\n" + m.content;
    else out.push({ ...m });
  }
  return out;
}

// ── The streaming turn ───────────────────────────────────────────────────────

export async function handleUtterance(raw: string) {
  const text = raw.trim();
  if (!text) return;
  cancelSpeak();
  turnAbort?.abort();
  set({ interim: "", hint: null });

  const fp = fastPath(text);
  if (fp) {
    if (fp.kind === "hush") {
      set({ convo: "idle" });
      return;
    }
    if (fp.kind === "zoomBy")
      set({ camera: { nonce: ++nonce, kind: "zoomBy", delta: fp.delta } });
    if (fp.kind === "fly")
      set({ camera: { nonce: ++nonce, kind: "fly", move: fp.move } });
    if (fp.kind === "fit") {
      const pts = get().markers.map((m) => m.lngLat);
      if (pts.length >= 2) {
        set({
          camera: {
            nonce: ++nonce,
            kind: "fit",
            points: pts,
            padRight: get().itinerary ? 470 : 130,
          },
        });
      }
    }
    set({ lastReply: fp.ack });
    speakReply(fp.ack);
    return;
  }

  const messages: ChatMsg[] = [
    ...get().messages,
    { role: "user", content: text },
  ];
  if (feedClearTimer) clearTimeout(feedClearTimer);
  set({
    messages,
    convo: "thinking",
    narration: "",
    feed: [],
    thinkingPhrase: pick(THINKING),
  });

  // Research turns run long — acknowledge by voice right away.
  if (RESEARCH_RE.test(text) && !get().muted) speak(pick(ACKS), () => {});

  const ctrl = new AbortController();
  turnAbort = ctrl;
  let sawCamera = false;
  let gotReply = false;
  const markersBefore = get()
    .markers.map((m) => m.id)
    .join(",");

  const onEvent = (ev: TurnEvent) => {
    switch (ev.type) {
      case "status":
        set({ narration: ev.text });
        break;
      case "tool": {
        const feed = [...get().feed];
        const i = feed.findIndex((f) => f.id === ev.item.id);
        if (i >= 0) feed[i] = ev.item;
        else feed.push(ev.item);
        set({ feed: feed.slice(-6) });
        break;
      }
      case "ops": {
        const r = applyOps(get().itinerary, ev.ops);
        refreshDerived(r.itinerary);
        markChanged(r.changedIds);
        break;
      }
      case "camera":
        sawCamera = true;
        set({
          camera: {
            nonce: ++nonce,
            kind: "fly",
            move: { durationMs: 3200, ...ev.move },
          },
        });
        break;
      case "chips":
        set({ chips: ev.chips.slice(0, 4) });
        break;
      case "reply":
        gotReply = true;
        set({
          messages: [
            ...get().messages,
            { role: "assistant", content: ev.text },
          ],
          lastReply: ev.text,
          narration: "",
        });
        speakReply(ev.text);
        break;
      case "done": {
        const markers = refreshDerived(ev.itinerary);
        set({ agentSource: ev.source });
        const after = markers.map((m) => m.id).join(",");
        if (!sawCamera && markers.length && after !== markersBefore) {
          set({
            camera:
              markers.length >= 2
                ? {
                    nonce: ++nonce,
                    kind: "fit",
                    points: markers.map((m) => m.lngLat),
                    padRight: ev.itinerary ? 470 : 130,
                  }
                : {
                    nonce: ++nonce,
                    kind: "fly",
                    move: {
                      center: markers[0].lngLat,
                      zoom: 11.5,
                      pitch: 50,
                      bearing: -12,
                    },
                  },
          });
        }
        break;
      }
      case "error":
        set({ hint: ev.message });
        break;
    }
  };

  const fallback = () => {
    const t =
      "Forgive me — the line to our atlas flickered. Say that once more?";
    set({
      messages: [...get().messages, { role: "assistant", content: t }],
      lastReply: t,
      narration: "",
    });
    speakReply(t);
  };

  try {
    const res = await fetch("/api/concierge", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        messages: sanitizeForApi(messages),
        itinerary: get().itinerary,
      }),
      signal: ctrl.signal,
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const line = frame
          .split("\n")
          .find((l) => l.startsWith("data: "));
        if (!line) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as TurnEvent);
        } catch {
          /* skip malformed frame */
        }
      }
    }
    if (!gotReply) fallback();
  } catch (err) {
    if ((err as Error)?.name === "AbortError") return; // deliberate interrupt
    if (!gotReply) fallback();
  } finally {
    if (turnAbort === ctrl) turnAbort = null;
  }
}

export function setHandsFree(on: boolean) {
  set({ handsFree: on });
  const s = get();
  if (on && s.started && s.convo === "idle" && !s.micBlocked) startListening();
  if (!on && s.convo === "listening") stopListening();
}

export function setMuted(on: boolean) {
  set({ muted: on });
  if (on && get().convo === "speaking") {
    cancelSpeak();
    maybeRelisten();
  }
}
