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
  name: "edit_itinerary",
  description:
    "Apply structured edits to the traveler's itinerary. Batch every op for the turn into ONE call — each op animates live in the interface the moment it is written, so compose trips as a SEQUENCE of granular ops (set_meta, then add_day + its add_items per day), never as one replace_trip blob. Required fields per op: set_meta{title?,subtitle?} · add_day{location,center,position?} · remove_day{day} · set_day{day,…} · add_item{day,item,position?} · update_item{id,patch} · remove_item{id} · move_item{id,toDay,position?} · replace_trip{itinerary:null} (clear/restart only). Positions are 1-based; days renumber automatically after structural changes.",
  input_schema: {
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
  name: "set_camera",
  description:
    "Fly the 3D map camera. Use whenever the geographic focus of the conversation shifts.",
  input_schema: {
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

export const FINALIZE_TOOL = {
  name: "finalize_turn",
  description:
    "REQUIRED final call of every turn. Delivers your spoken reply and suggestion chips, then ends the turn.",
  input_schema: {
    type: "object" as const,
    required: ["reply"],
    properties: {
      reply: {
        type: "string",
        description:
          "Spoken aloud via TTS. HARD LIMIT 55 words — count them; shorter is better. No markdown, emoji, or lists. At most one question.",
      },
      chips: {
        type: "array",
        items: { type: "string" },
        maxItems: 4,
        description: "2–4 tappable next moves, ≤5 words each",
      },
    },
  },
};

export const WEB_SEARCH_TOOL = {
  type: "web_search_20250305",
  name: "web_search",
  max_uses: 3,
};
