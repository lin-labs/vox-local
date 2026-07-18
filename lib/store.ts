import { create } from "zustand";
import type {
  CameraCmd,
  ChatMsg,
  FeedItem,
  Itinerary,
  LngLat,
  MarkerSpec,
} from "@/lib/types";

export type Convo = "idle" | "listening" | "thinking" | "speaking";

export interface AppState {
  started: boolean;
  convo: Convo;
  interim: string;
  lastReply: string;
  hint: string | null;
  thinkingPhrase: string;
  handsFree: boolean;
  muted: boolean;
  textOpen: boolean;
  micBlocked: boolean;
  messages: ChatMsg[];
  itinerary: Itinerary | null;
  markers: MarkerSpec[];
  route: LngLat[] | null;
  camera: CameraCmd | null;
  pulseId: string | null;
  agentSource: "claude" | "mock" | null;
  mapFlavor: "mapbox" | "maplibre" | null;
  mapReady: boolean;
  /** live agent activity, e.g. "Searching: kaiseki near Gion" */
  narration: string;
  /** contextual next-move suggestions from the agent */
  chips: string[];
  /** item/day ids recently changed by ops — drives the diff glow */
  changed: string[];
  /** live tool-call feed shown while the agent works */
  feed: FeedItem[];
}

export const useApp = create<AppState>()(() => ({
  started: false,
  convo: "idle",
  interim: "",
  lastReply: "",
  hint: null,
  thinkingPhrase: "",
  handsFree: true,
  muted: false,
  textOpen: false,
  micBlocked: false,
  messages: [],
  itinerary: null,
  markers: [],
  route: null,
  camera: null,
  pulseId: null,
  agentSource: null,
  mapFlavor: null,
  mapReady: false,
  narration: "",
  chips: [],
  changed: [],
  feed: [],
}));
