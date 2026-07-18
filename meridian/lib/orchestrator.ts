/* Conversation loop: xAI realtime voice → live tools → streamed voice reply. */

import { useApp } from "@/lib/store";
import { fastPath } from "@/lib/fastpath";
import { applyOps, summarize } from "@/lib/agent/ops";
import { mockTurn } from "@/lib/agent/mock";
import { STATIC_SYSTEM, dynamicSystem } from "@/lib/agent/system";
import { XAI_VOICE_TOOLS } from "@/lib/agent/tools";
import {
  XaiRealtimeVoice,
  type RealtimeEvent,
} from "@/lib/xai-realtime";
import type {
  CameraMove,
  ChatMsg,
  FeedItem,
  Itinerary,
  ItineraryOp,
  LngLat,
  MarkerSpec,
  TurnEvent,
} from "@/lib/types";

let nonce = 0;
let realtime: XaiRealtimeVoice | null = null;
let connecting: Promise<void> | null = null;
let assistantTranscript = "";
let awaitingToolContinuation = false;
let responseDone = false;
let sawCamera = false;
let markersBefore = "";
let feedClearTimer: number | null = null;

const THINKING = [
  "Consulting the atlas…",
  "Charting your course…",
  "Calling a few local friends…",
  "Unfolding the maps…",
  "Checking the light this time of year…",
];

const get = () => useApp.getState();
const set = useApp.setState;
const pick = <T,>(items: T[]): T =>
  items[Math.floor(Math.random() * items.length)];

// ── Itinerary derivations ────────────────────────────────────────────────────

function deriveMarkers(itinerary: Itinerary | null): MarkerSpec[] {
  if (!itinerary) return [];
  const markers: MarkerSpec[] = [];
  const seen = new Set<string>();
  for (const day of itinerary.days) {
    const key = day.center.map((n) => n.toFixed(3)).join(",");
    if (seen.has(key)) continue;
    seen.add(key);
    markers.push({
      id: `day-${day.day}`,
      name: day.location,
      label: day.location,
      lngLat: day.center,
      day: day.day,
      kind: "stop",
    });
  }
  const poiBudget = Math.max(0, 12 - markers.length);
  let used = 0;
  for (const day of itinerary.days) {
    day.items.forEach((item, index) => {
      if (item.lngLat && used < poiBudget) {
        markers.push({
          id: item.id ?? `d${day.day}-${index}`,
          name: item.title,
          label: item.title,
          lngLat: item.lngLat,
          day: day.day,
          kind: "poi",
        });
        used++;
      }
    });
  }
  return markers;
}

function haversineKm(a: LngLat, b: LngLat): number {
  const radius = 6371;
  const dLat = ((b[1] - a[1]) * Math.PI) / 180;
  const dLng = ((b[0] - a[0]) * Math.PI) / 180;
  const latA = (a[1] * Math.PI) / 180;
  const latB = (b[1] * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(latA) * Math.cos(latB) * Math.sin(dLng / 2) ** 2;
  return 2 * radius * Math.asin(Math.sqrt(h));
}

function deriveRoute(itinerary: Itinerary | null): LngLat[] | null {
  if (!itinerary) return null;
  const points: LngLat[] = [];
  for (const day of itinerary.days) {
    const last = points[points.length - 1];
    if (!last || last[0] !== day.center[0] || last[1] !== day.center[1]) {
      points.push(day.center);
    }
  }
  if (points.length < 2) return null;
  for (let i = 1; i < points.length; i++) {
    if (haversineKm(points[i - 1], points[i]) > 2200) return null;
  }
  return points;
}

function refreshDerived(itinerary: Itinerary | null): MarkerSpec[] {
  const markers = deriveMarkers(itinerary);
  set({ itinerary, markers, route: deriveRoute(itinerary) });
  return markers;
}

function markChanged(ids: string[]) {
  if (!ids.length) return;
  set((state) => ({ changed: [...new Set([...state.changed, ...ids])] }));
  window.setTimeout(() => {
    set((state) => ({
      changed: state.changed.filter((id) => !ids.includes(id)),
    }));
  }, 6500);
}

function beginTurn() {
  if (feedClearTimer) window.clearTimeout(feedClearTimer);
  assistantTranscript = "";
  awaitingToolContinuation = false;
  responseDone = false;
  sawCamera = false;
  markersBefore = get()
    .markers.map((marker) => marker.id)
    .join(",");
  set({
    feed: [],
    narration: "",
    thinkingPhrase: pick(THINKING),
  });
}

function maybeMoveCameraToChanges() {
  const markers = refreshDerived(get().itinerary);
  const after = markers.map((marker) => marker.id).join(",");
  if (sawCamera || !markers.length || after === markersBefore) return;
  set({
    camera:
      markers.length >= 2
        ? {
            nonce: ++nonce,
            kind: "fit",
            points: markers.map((marker) => marker.lngLat),
            padRight: get().itinerary ? 470 : 130,
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

// ── xAI session ──────────────────────────────────────────────────────────────

function currentInstructions(): string {
  return `${STATIC_SYSTEM}\n\n${dynamicSystem(get().itinerary)}`;
}

function initialSession() {
  return {
    instructions: currentInstructions(),
    reasoning: { effort: "high" },
    turn_detection: {
      type: "server_vad",
      threshold: 0.55,
      silence_duration_ms: 700,
      prefix_padding_ms: 350,
    },
    audio: {
      input: {
        format: { type: "audio/pcm", rate: 24_000 },
        transcription: {
          model: "grok-transcribe",
          language_hint: "en",
          keyterms: [
            "Meridian",
            "kaiseki",
            "omakase",
            "Gion",
            "Santorini",
            "Reykjavik",
          ],
        },
      },
      output: { format: { type: "audio/pcm", rate: 24_000 } },
    },
    tools: XAI_VOICE_TOOLS,
  };
}

function getRealtime(): XaiRealtimeVoice {
  if (!realtime) {
    realtime = new XaiRealtimeVoice(handleRealtimeEvent);
    realtime.setMuted(get().muted);
  }
  return realtime;
}

async function ensureRealtime(): Promise<XaiRealtimeVoice> {
  const client = getRealtime();
  if (!connecting) {
    connecting = client
      .connect(initialSession())
      .then(() => set({ agentSource: "xai" }))
      .catch((error) => {
        client.close();
        realtime = null;
        throw error;
      })
      .finally(() => {
        connecting = null;
      });
  }
  await connecting;
  return client;
}

function eventText(event: RealtimeEvent, key: string): string {
  const value = event[key];
  return typeof value === "string" ? value : "";
}

function addMessage(message: ChatMsg) {
  const messages = get().messages;
  const last = messages[messages.length - 1];
  if (last?.role === message.role && last.content === message.content) return;
  set({ messages: [...messages, message] });
}

function upsertFeed(item: FeedItem) {
  const feed = [...get().feed];
  const index = feed.findIndex((entry) => entry.id === item.id);
  if (index >= 0) feed[index] = item;
  else feed.push(item);
  set({ feed: feed.slice(-6) });
}

function handleBuiltInToolEvent(event: RealtimeEvent) {
  if (
    event.type !== "response.output_item.added" &&
    event.type !== "response.output_item.done"
  ) {
    return;
  }
  const item = event.item as Record<string, unknown> | undefined;
  const itemType = typeof item?.type === "string" ? item.type : "";
  if (!itemType.includes("web_search")) return;
  const id = typeof item?.id === "string" ? item.id : "web-search";
  const done = event.type.endsWith(".done");
  upsertFeed({
    id,
    name: "web_search",
    label: done ? "Research complete" : "Searching the live web…",
    state: done ? "done" : "start",
  });
  set({ narration: done ? "" : "Checking current details…" });
}

function handleFunctionCall(event: RealtimeEvent) {
  const name = eventText(event, "name");
  const callId = eventText(event, "call_id");
  if (!name || !callId) return;
  awaitingToolContinuation = true;

  let args: Record<string, unknown> = {};
  try {
    args = JSON.parse(eventText(event, "arguments") || "{}") as Record<
      string,
      unknown
    >;
  } catch {
    realtime?.sendFunctionOutput(callId, { ok: false, error: "Invalid JSON arguments" });
    return;
  }

  const feedName = name as FeedItem["name"];
  const labels: Record<string, [string, string]> = {
    edit_itinerary: ["Editing the itinerary…", "Itinerary updated"],
    set_camera: ["Moving across the globe…", "Camera in position"],
    set_suggestions: ["Preparing next moves…", "Suggestions ready"],
  };
  const label = labels[name] || [`Running ${name}…`, `${name} complete`];
  if (name in labels) {
    upsertFeed({ id: callId, name: feedName, label: label[0], state: "start" });
  }

  let output: unknown;
  try {
    if (name === "edit_itinerary") {
      const ops = Array.isArray(args.ops) ? (args.ops as ItineraryOp[]) : [];
      const result = applyOps(get().itinerary, ops);
      refreshDerived(result.itinerary);
      markChanged(result.changedIds);
      output = {
        ok: result.errors.length === 0,
        errors: result.errors,
        state: summarize(result.itinerary),
      };
    } else if (name === "set_camera") {
      const center = args.center as LngLat;
      const zoom = Number(args.zoom);
      if (!Array.isArray(center) || center.length !== 2 || !Number.isFinite(zoom)) {
        throw new Error("center and zoom are required");
      }
      sawCamera = true;
      set({
        camera: {
          nonce: ++nonce,
          kind: "fly",
          move: {
            center,
            zoom,
            pitch: Number(args.pitch) || 50,
            bearing: Number(args.bearing) || 0,
            durationMs: 3200,
          },
        },
      });
      output = { ok: true };
    } else if (name === "set_suggestions") {
      const chips = Array.isArray(args.chips)
        ? args.chips.map(String).filter(Boolean).slice(0, 4)
        : [];
      set({ chips });
      output = { ok: true, count: chips.length };
    } else {
      output = { ok: false, error: `Unknown function: ${name}` };
    }
  } catch (error) {
    output = { ok: false, error: String(error) };
  }

  if (name in labels) {
    upsertFeed({ id: callId, name: feedName, label: label[1], state: "done" });
  }
  realtime?.sendFunctionOutput(callId, output);
}

function finishTurn() {
  responseDone = false;
  maybeMoveCameraToChanges();
  set({ narration: "", interim: "" });
  if (feedClearTimer) window.clearTimeout(feedClearTimer);
  feedClearTimer = window.setTimeout(() => set({ feed: [] }), 3000);

  const state = get();
  if (state.handsFree && !state.micBlocked) {
    if (realtime?.microphoneActive) set({ convo: "listening" });
    else void startListening();
  } else {
    realtime?.stopMicrophone();
    set({ convo: "idle" });
  }
}

function completeAssistantTranscript(event?: RealtimeEvent) {
  const completed = event ? eventText(event, "transcript") : "";
  if (completed) assistantTranscript = completed;
  const text = assistantTranscript.trim();
  if (!text) return;
  set({ lastReply: text });
  addMessage({ role: "assistant", content: text });
}

function handleRealtimeEvent(event: RealtimeEvent) {
  handleBuiltInToolEvent(event);
  switch (event.type) {
    case "input_audio_buffer.speech_started":
      beginTurn();
      realtime?.updateSession({ instructions: currentInstructions() });
      set({ convo: "listening", interim: "", hint: null });
      break;
    case "conversation.item.input_audio_transcription.updated": {
      const transcript = eventText(event, "transcript");
      if (transcript) set({ interim: transcript });
      break;
    }
    case "conversation.item.input_audio_transcription.completed": {
      const transcript = eventText(event, "transcript").trim();
      if (transcript) {
        set({ interim: transcript });
        addMessage({ role: "user", content: transcript });
      }
      break;
    }
    case "input_audio_buffer.speech_stopped":
      if (!get().handsFree) realtime?.stopMicrophone();
      set({ convo: "thinking", thinkingPhrase: pick(THINKING) });
      break;
    case "response.created":
      assistantTranscript = "";
      responseDone = false;
      set({ convo: "thinking" });
      break;
    case "response.output_audio_transcript.delta": {
      assistantTranscript += eventText(event, "delta");
      if (assistantTranscript) set({ lastReply: assistantTranscript });
      break;
    }
    case "response.output_audio_transcript.done":
      completeAssistantTranscript(event);
      break;
    case "response.output_audio.delta":
      if (!get().muted) set({ convo: "speaking" });
      break;
    case "response.function_call_arguments.done":
      handleFunctionCall(event);
      break;
    case "response.done":
      if (awaitingToolContinuation) {
        awaitingToolContinuation = false;
        realtime?.requestResponse();
        break;
      }
      completeAssistantTranscript();
      responseDone = true;
      set({ agentSource: "xai", narration: "" });
      if (!realtime?.isPlaying || get().muted) finishTurn();
      break;
    case "playback.idle":
      if (responseDone) finishTurn();
      break;
    case "error": {
      const error = event.error as Record<string, unknown> | undefined;
      const message =
        (typeof error?.message === "string" && error.message) ||
        eventText(event, "message") ||
        "xAI voice reported a recoverable error";
      set({ hint: message.slice(0, 180) });
      break;
    }
    case "transport.error":
      set({ hint: "The xAI voice connection flickered. Typed demo mode still works." });
      break;
    case "transport.closed":
      realtime = null;
      connecting = null;
      if (get().started) {
        set({
          convo: "idle",
          agentSource: "mock",
          hint: "Voice disconnected — type to continue in offline demo mode.",
        });
      }
      break;
  }
}

// ── Offline typed fallback ───────────────────────────────────────────────────

function applyMockEvent(event: TurnEvent) {
  switch (event.type) {
    case "status":
      set({ narration: event.text });
      break;
    case "tool":
      upsertFeed(event.item);
      break;
    case "ops": {
      const result = applyOps(get().itinerary, event.ops);
      refreshDerived(result.itinerary);
      markChanged(result.changedIds);
      break;
    }
    case "camera":
      sawCamera = true;
      set({
        camera: {
          nonce: ++nonce,
          kind: "fly",
          move: { durationMs: 3200, ...event.move },
        },
      });
      break;
    case "chips":
      set({ chips: event.chips.slice(0, 4) });
      break;
    case "reply":
      set({ lastReply: event.text });
      addMessage({ role: "assistant", content: event.text });
      break;
    case "done":
      refreshDerived(event.itinerary);
      set({ agentSource: "mock" });
      finishTurn();
      break;
    case "error":
      set({ hint: event.message });
      break;
  }
}

function runMockTurn() {
  for (const event of mockTurn(get().messages, get().itinerary)) {
    applyMockEvent(event);
  }
}

// ── Public UI actions ────────────────────────────────────────────────────────

export function begin(opts?: { quiet?: boolean }) {
  if (get().started) return;
  const client = getRealtime();
  client.primeAudio();
  if (opts?.quiet) set({ muted: true, handsFree: false });
  client.setMuted(Boolean(opts?.quiet));

  const hour = new Date().getHours();
  const salutation =
    hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const greeting = `${salutation} — Meridian here, your private concierge. Name a place, a craving, or a feeling, and I'll chart it while we talk. Where shall we begin?`;
  set({
    started: true,
    messages: [{ role: "assistant", content: greeting }],
    lastReply: greeting,
    convo: opts?.quiet ? "idle" : "thinking",
  });

  void ensureRealtime()
    .then((connected) => {
      if (!opts?.quiet) connected.forceMessage(greeting);
    })
    .catch((error) => {
      set({
        agentSource: "mock",
        convo: "idle",
        textOpen: true,
        hint: `${String(error)} — typed demo mode is available.`.slice(0, 180),
      });
    });
}

export async function startListening() {
  getRealtime().primeAudio();
  set({ convo: "thinking", interim: "", hint: null });
  try {
    const client = await ensureRealtime();
    client.updateSession({ instructions: currentInstructions() });
    await client.startMicrophone();
    set({ convo: "listening" });
  } catch (error) {
    const denied =
      error instanceof DOMException &&
      (error.name === "NotAllowedError" || error.name === "SecurityError");
    set({
      micBlocked: denied,
      handsFree: false,
      textOpen: true,
      convo: "idle",
      agentSource: "mock",
      hint: denied
        ? "Microphone blocked — type below instead."
        : `${String(error)} — type to use the offline demo.`.slice(0, 180),
    });
  }
}

export function stopListening() {
  realtime?.stopMicrophone();
  set({ convo: "idle", interim: "" });
}

export function toggleOrb() {
  const state = get();
  if (!state.started) {
    begin();
    return;
  }
  if (state.convo === "listening") stopListening();
  else if (state.convo === "speaking") {
    interrupt();
    void startListening();
  } else if (state.convo === "idle") void startListening();
}

export function interrupt() {
  responseDone = false;
  awaitingToolContinuation = false;
  realtime?.cancelResponse();
  if (!get().handsFree) realtime?.stopMicrophone();
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

async function speakFastPath(text: string) {
  set({ lastReply: text, convo: get().muted ? "idle" : "thinking" });
  if (get().muted) return;
  try {
    const client = await ensureRealtime();
    client.forceMessage(text);
  } catch {
    set({ convo: "idle" });
  }
}

export async function handleUtterance(raw: string) {
  const text = raw.trim();
  if (!text) return;
  set({ interim: "", hint: null });

  const shortcut = fastPath(text);
  if (shortcut) {
    if (shortcut.kind === "hush") {
      interrupt();
      return;
    }
    if (shortcut.kind === "zoomBy") {
      set({ camera: { nonce: ++nonce, kind: "zoomBy", delta: shortcut.delta } });
    } else if (shortcut.kind === "fly") {
      set({ camera: { nonce: ++nonce, kind: "fly", move: shortcut.move } });
    } else if (shortcut.kind === "fit") {
      const points = get().markers.map((marker) => marker.lngLat);
      if (points.length >= 2) {
        set({
          camera: {
            nonce: ++nonce,
            kind: "fit",
            points,
            padRight: get().itinerary ? 470 : 130,
          },
        });
      }
    }
    await speakFastPath(shortcut.ack);
    return;
  }

  beginTurn();
  addMessage({ role: "user", content: text });
  set({ convo: "thinking" });
  try {
    const client = await ensureRealtime();
    client.updateSession({ instructions: currentInstructions() });
    client.sendText(text);
  } catch (error) {
    set({
      hint: `${String(error)} — using the offline demo.`.slice(0, 180),
      agentSource: "mock",
    });
    runMockTurn();
  }
}

export function setHandsFree(on: boolean) {
  set({ handsFree: on });
  const state = get();
  if (on && state.started && state.convo === "idle" && !state.micBlocked) {
    void startListening();
  }
  if (!on && state.convo === "listening") stopListening();
}

export function setMuted(on: boolean) {
  set({ muted: on });
  realtime?.setMuted(on);
  if (on && get().convo === "speaking") {
    if (responseDone) finishTurn();
    else set({ convo: "thinking" });
  }
}
