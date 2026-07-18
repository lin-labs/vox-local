/* Tool schema forcing the concierge into renderable, structured turns. */

const lngLat = {
  type: "array",
  items: { type: "number" },
  minItems: 2,
  maxItems: 2,
  description: "[longitude, latitude]",
};

export const CONCIERGE_TOOL = {
  name: "render_concierge_turn",
  description:
    "Render the concierge's spoken reply plus the map and itinerary state for this turn.",
  input_schema: {
    type: "object" as const,
    required: ["reply"],
    properties: {
      reply: {
        type: "string",
        description:
          "Spoken aloud via TTS. Under 55 words. No markdown, lists, or emoji.",
      },
      mapAction: {
        type: "object",
        required: ["center", "zoom"],
        properties: {
          center: lngLat,
          zoom: { type: "number" },
          pitch: { type: "number" },
          bearing: { type: "number" },
        },
      },
      markers: {
        type: "array",
        maxItems: 12,
        items: {
          type: "object",
          required: ["id", "name", "lngLat"],
          properties: {
            id: { type: "string" },
            name: { type: "string" },
            label: { type: "string" },
            lngLat,
            day: { type: "number" },
          },
        },
      },
      itinerary: {
        type: ["object", "null"],
        required: ["title", "days"],
        properties: {
          title: { type: "string" },
          subtitle: { type: "string" },
          days: {
            type: "array",
            items: {
              type: "object",
              required: ["day", "location", "center", "items"],
              properties: {
                day: { type: "number" },
                location: { type: "string" },
                center: lngLat,
                summary: { type: "string" },
                items: {
                  type: "array",
                  items: {
                    type: "object",
                    required: ["title"],
                    properties: {
                      time: { type: "string" },
                      title: { type: "string" },
                      detail: { type: "string" },
                      lngLat,
                    },
                  },
                },
              },
            },
          },
        },
      },
    },
  },
};
