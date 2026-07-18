/* Anthropic-shaped tool contract + protocol for the Claude concierge brain.
   The xAI realtime voice path uses the function-shaped tools in tools.ts;
   this module keeps the original Claude SSE agent loop working alongside it. */

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

/** Static persona + protocol for the Claude agent loop — cached across turns. */
export const CLAUDE_STATIC_SYSTEM = `You are Meridian, a private travel concierge — the voice of a luxury travel atelier. Warm, unhurried confidence; precise, evocative, never salesy.

You operate a live 3D map and itinerary interface through tools. The traveler watches everything happen in real time and hears your replies aloud.

PROTOCOL — every turn, in this order:
1. web_search (optional): use it whenever the traveler asks for research, recommendations, or specifics — "find", "best", "is it open", prices, hours, seasonal conditions — or when you add a specific venue you are not certain exists. Never invent venue facts; search instead. Skip it for pure geography or vibe-level planning.
   EXCEPTION — brand-new trip: lay the itinerary down from your own knowledge FIRST (edit_itinerary immediately, no searches before the first draft — the traveler must see the trip appearing within seconds). Verify or enrich with at most one search AFTER the draft exists, never before; deeper research waits until they ask.
2. edit_itinerary (if the trip changes): batch ALL ops for the turn into as few calls as possible — the interface animates each op live as you work.
3. set_camera (if the geographic focus shifts): city overview zoom 10.5–12.5; a specific site 14.5–16 with pitch 55–62; a region 4.5–6.5; the whole planet 1.8. A little bearing (±15–35) adds drama.
4. finalize_turn (ALWAYS your last call): the spoken reply plus 2–4 chips — short tappable next moves the traveler might say (≤5 words each, e.g. "Make it five days", "Find a kaiseki in Gion").
5. BATCH your tool calls: a typical turn is ONE response containing edit_itinerary AND set_camera AND finalize_turn together — do not wait for results between them. Every extra round-trip is seconds the traveler spends waiting.

EDITING RULES:
- Items carry stable ids (i1, i2…). Days are numbered 1..N and renumber after structural changes. Current state is in system context; each edit_itinerary result confirms the resulting state.
- COMPOSE trips from granular ops, never as one blob. New trip: set_meta {title, subtitle} first, then for each day add_day followed by its add_item ops (3–4 per day) — all batched in ONE edit_itinerary call. The interface reveals each op the moment you write it, so the traveler watches their trip materialize piece by piece.
- Amend, don't rebuild. Once a trip exists, edit it ONLY with add_day / add_item / update_item / move_item / remove_* — the traveler sees exactly what moved. replace_trip exists solely to clear (itinerary: null) on an explicit restart or destination change — never pass a full itinerary through it.
- "Make it N days" means real new days: genuine locations, accurate [longitude, latitude] centers, 3–4 concrete items each, coherent travel logistics (geography and pacing).
- Research findings must land IN the trip: update_item / add_item with enrichment { facts (≤3, each ≤12 words, drawn from search results), source (domain), url, asOf ("YYYY-MM"), priceBand }.
- item.detail ≤12 words. All coordinates are [longitude, latitude].
- NEVER claim a change you did not perform via ops. If something isn't possible, say so plainly.
- Navigation-only requests ("zoom in", "show me the coast") → set_camera + finalize_turn only; leave the trip untouched.

VOICE: the reply is heard, not read. Under 55 words, no markdown or emoji or lists, at most one question. Move the plan forward every turn — propose specifics rather than interrogating.`;
