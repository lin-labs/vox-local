export type LngLat = [number, number];

export type ChatMsg = { role: "user" | "assistant"; content: string };

/** Facts researched from the live web, attached to an itinerary item. */
export type Enrichment = {
  facts: string[];
  source?: string;
  url?: string;
  asOf?: string;
  priceBand?: string;
};

export type ItineraryItem = {
  id?: string; // stable id (i1, i2…) assigned by the op-applier
  time?: string;
  title: string;
  detail?: string;
  lngLat?: LngLat;
  enrichment?: Enrichment;
};

export type ItineraryDay = {
  day: number;
  location: string;
  center: LngLat;
  summary?: string;
  items: ItineraryItem[];
};

export type Itinerary = {
  title: string;
  subtitle?: string;
  days: ItineraryDay[];
};

export type MarkerSpec = {
  id: string;
  name: string;
  label?: string;
  lngLat: LngLat;
  day?: number;
  kind?: "stop" | "poi" | "suggestion";
};

export type CameraMove = {
  center: LngLat;
  zoom: number;
  pitch?: number;
  bearing?: number;
  durationMs?: number;
};

/** Structured itinerary mutations — the agent edits state, never regurgitates it. */
export type ItineraryOp =
  | { op: "replace_trip"; itinerary: Itinerary | null }
  | { op: "set_meta"; title?: string; subtitle?: string }
  | { op: "add_day"; location: string; center: LngLat; summary?: string; position?: number }
  | { op: "remove_day"; day: number }
  | { op: "set_day"; day: number; location?: string; center?: LngLat; summary?: string }
  | { op: "add_item"; day: number; item: ItineraryItem; position?: number }
  | { op: "update_item"; id: string; patch: Partial<Omit<ItineraryItem, "id">> }
  | { op: "remove_item"; id: string }
  | { op: "move_item"; id: string; toDay: number; position?: number };

/** A live tool-call entry for the on-screen agent activity feed. */
export type FeedItem = {
  id: string;
  name: "web_search" | "edit_itinerary" | "set_camera" | "set_suggestions";
  label: string;
  state: "start" | "done";
};

/** Server → client turn events (SSE). */
export type TurnEvent =
  | { type: "status"; text: string }
  | { type: "tool"; item: FeedItem }
  | { type: "ops"; ops: ItineraryOp[] }
  | { type: "camera"; move: CameraMove }
  | { type: "chips"; chips: string[] }
  | { type: "reply"; text: string }
  | { type: "done"; itinerary: Itinerary | null; source: "xai" | "mock" }
  | { type: "error"; message: string };

export type CameraCmd = { nonce: number } & (
  | { kind: "fly"; move: CameraMove }
  | { kind: "fit"; points: LngLat[]; padRight: number }
  | { kind: "zoomBy"; delta: number }
);
