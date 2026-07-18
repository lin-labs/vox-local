/* Tool contract between the concierge agent and the interface. */

const lngLat = {
  type: "array",
  items: { type: "number" },
  minItems: 2,
  maxItems: 2,
  description: "[longitude, latitude]",
};

const enrichment = {
  type: "object",
  required: ["facts"],
  properties: {
    facts: {
      type: "array",
      items: { type: "string" },
      maxItems: 3,
      description: "Researched facts, each ≤12 words, drawn from web_search results",
    },
    source: { type: "string", description: "Domain, e.g. tabelog.com" },
    url: { type: "string" },
    asOf: { type: "string", description: "YYYY-MM" },
    priceBand: { type: "string", description: 'e.g. "$$$ · ~$140pp"' },
  },
};

const item = {
  type: "object",
  required: ["title"],
  properties: {
    time: { type: "string", description: "HH:MM" },
    title: { type: "string" },
    detail: { type: "string", description: "≤12 words" },
    lngLat,
    enrichment,
  },
};

export const EDIT_ITINERARY_TOOL = {
  type: "function" as const,
  name: "edit_itinerary",
  description:
    "Apply structured edits to the traveler's itinerary. Batch every op for the turn into ONE call and compose trips as a sequence of granular ops (set_meta, then add_day + its add_items per day), never as one replace_trip blob. Required fields per op: set_meta{title?,subtitle?} · add_day{location,center,position?} · remove_day{day} · set_day{day,…} · add_item{day,item,position?} · update_item{id,patch} · remove_item{id} · move_item{id,toDay,position?} · replace_trip{itinerary:null} (clear/restart only). Positions are 1-based; days renumber automatically after structural changes.",
  parameters: {
    type: "object" as const,
    required: ["ops"],
    properties: {
      ops: {
        type: "array",
        minItems: 1,
        items: {
          type: "object",
          required: ["op"],
          properties: {
            op: {
              type: "string",
              enum: [
                "replace_trip",
                "set_meta",
                "add_day",
                "remove_day",
                "set_day",
                "add_item",
                "update_item",
                "remove_item",
                "move_item",
              ],
            },
            itinerary: {
              type: ["object", "null"],
              description:
                "replace_trip only: full trip {title, subtitle?, days:[{day, location, center, summary?, items:[item]}]}",
            },
            title: { type: "string" },
            subtitle: { type: "string" },
            day: { type: "number", description: "target day number" },
            position: { type: "number", description: "1-based insert position" },
            location: { type: "string" },
            center: lngLat,
            summary: { type: "string" },
            item,
            id: { type: "string", description: "item id, e.g. i3" },
            patch: {
              type: "object",
              description: "update_item only: partial item fields to merge",
              properties: {
                time: { type: "string" },
                title: { type: "string" },
                detail: { type: "string" },
                lngLat,
                enrichment,
              },
            },
            toDay: { type: "number" },
          },
        },
      },
    },
  },
};

export const SET_CAMERA_TOOL = {
  type: "function" as const,
  name: "set_camera",
  description:
    "Fly the 3D map camera. Use whenever the geographic focus of the conversation shifts.",
  parameters: {
    type: "object" as const,
    required: ["center", "zoom"],
    properties: {
      center: lngLat,
      zoom: { type: "number" },
      pitch: { type: "number" },
      bearing: { type: "number" },
    },
  },
};

export const SET_SUGGESTIONS_TOOL = {
  type: "function" as const,
  name: "set_suggestions",
  description:
    "Set two to four short, tappable suggestions for what the traveler might ask next.",
  parameters: {
    type: "object" as const,
    required: ["chips"],
    properties: {
      chips: {
        type: "array",
        minItems: 2,
        items: { type: "string" },
        maxItems: 4,
        description: "2–4 tappable next moves, ≤5 words each",
      },
    },
  },
};

export const WEB_SEARCH_TOOL = {
  type: "web_search" as const,
};

export const XAI_VOICE_TOOLS = [
  WEB_SEARCH_TOOL,
  EDIT_ITINERARY_TOOL,
  SET_CAMERA_TOOL,
  SET_SUGGESTIONS_TOOL,
];
