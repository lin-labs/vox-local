/* Offline concierge — deterministic, keyless safety net.
   Curated destinations with real coordinates and keyword matching. It speaks
   the same TurnEvent protocol as the real agent, and it is HONEST: it never
   claims an edit or research it cannot actually perform. */

import type {
  CameraMove,
  ChatMsg,
  Itinerary,
  ItineraryDay,
  TurnEvent,
} from "@/lib/types";

type MockDest = {
  id: string;
  keywords: string[];
  reply: string;
  mapAction: CameraMove;
  itinerary: Itinerary;
};

const D = (
  day: number,
  location: string,
  center: [number, number],
  summary: string,
  items: ItineraryDay["items"]
): ItineraryDay => ({ day, location, center, summary, items });

const DESTS: MockDest[] = [
  {
    id: "japan",
    keywords: ["japan", "tokyo", "kyoto", "sushi", "ramen", "food", "omakase", "temple"],
    reply:
      "Japan, then — a love letter in neon and cedar. I've sketched three days: Tokyo's markets and midnight alleys, then Kyoto's shrines at first light. It's on the map now. Shall I stretch it to a week, or tune it toward food?",
    mapAction: { center: [139.75, 35.68], zoom: 10.8, pitch: 52, bearing: -18 },
    itinerary: {
      title: "Neon & Cedar",
      subtitle: "Tokyo and Kyoto, at their own pace",
      days: [
        D(1, "Tokyo", [139.7671, 35.6812], "Markets, art, and midnight alleys", [
          { time: "08:00", title: "Tsukiji Outer Market", detail: "Knife-fresh tuna, tamagoyaki, standing sushi", lngLat: [139.7708, 35.6654] },
          { time: "11:30", title: "teamLab Planets", detail: "Barefoot through rooms of water and light", lngLat: [139.7841, 35.6491] },
          { time: "15:00", title: "Meiji Shrine & Omotesandō", detail: "Cedar forest to couture boulevard", lngLat: [139.6993, 35.6764] },
          { time: "19:30", title: "Golden Gai", detail: "Six-seat bars under paper lanterns", lngLat: [139.7046, 35.6938] },
        ]),
        D(2, "Tokyo", [139.7671, 35.6812], "Old Asakusa to the skyline at dusk", [
          { time: "09:00", title: "Sensō-ji at opening", detail: "Incense and empty courtyards", lngLat: [139.7967, 35.7148] },
          { time: "13:00", title: "Nakameguro canal lunch", detail: "Slow riverside izakaya afternoon", lngLat: [139.6987, 35.644] },
          { time: "17:00", title: "Shibuya Sky at dusk", detail: "The crossing from 229 meters", lngLat: [139.7016, 35.658] },
          { time: "20:30", title: "Omakase in Ginza", detail: "Twelve seats, one chef, no menu", lngLat: [139.7671, 35.6717] },
        ]),
        D(3, "Kyoto", [135.7681, 35.0116], "Shrines before the crowds wake", [
          { time: "07:30", title: "Fushimi Inari", detail: "Ten thousand vermilion gates, near-empty", lngLat: [135.7727, 34.9671] },
          { time: "11:00", title: "Arashiyama bamboo grove", detail: "Green light and river boats", lngLat: [135.6725, 35.017] },
          { time: "15:00", title: "Gion tea-house walk", detail: "Lanterns, machiya, a glimpse of geiko", lngLat: [135.7755, 35.0037] },
          { time: "19:00", title: "Kaiseki on Pontochō", detail: "Eleven courses above the Kamo river", lngLat: [135.7708, 35.0044] },
        ]),
      ],
    },
  },
  {
    id: "greece",
    keywords: ["greece", "greek", "santorini", "aegean", "island", "honeymoon", "romantic", "wine"],
    reply:
      "The Aegean it is. Santorini — whitewash over black cliffs, wine the color of straw. Three days: caldera walks, Bronze-Age streets at Akrotiri, and a sunset kept just for you at Ammoudi. Tell me — more languid, or more adventurous?",
    mapAction: { center: [25.42, 36.41], zoom: 11.8, pitch: 55, bearing: 24 },
    itinerary: {
      title: "The Caldera Days",
      subtitle: "Santorini, unhurried",
      days: [
        D(1, "Fira & Imerovigli", [25.4324, 36.4166], "Arrival and the rim at golden hour", [
          { time: "11:00", title: "Cliffside check-in, Imerovigli", detail: "Cave suite over the caldera", lngLat: [25.42, 36.4341] },
          { time: "16:00", title: "Fira → Imerovigli rim walk", detail: "The classic caldera-edge stroll", lngLat: [25.4324, 36.4166] },
          { time: "20:00", title: "Dinner at Skaros Rock", detail: "Sea bream, assyrtiko, candlelight", lngLat: [25.4189, 36.4353] },
        ]),
        D(2, "Akrotiri & the south", [25.4034, 36.3517], "Ruins, red sand, volcanic wine", [
          { time: "09:30", title: "Akrotiri excavations", detail: "A Bronze-Age Pompeii, roofed and quiet", lngLat: [25.4034, 36.3517] },
          { time: "13:00", title: "Red Beach swim", detail: "Crimson cliffs, clear water", lngLat: [25.3946, 36.348] },
          { time: "17:30", title: "Santo Wines tasting", detail: "Assyrtiko flights above the caldera", lngLat: [25.4287, 36.3862] },
        ]),
        D(3, "Oia", [25.3764, 36.4611], "The postcard, before the crowds", [
          { time: "08:00", title: "Oia at first light", detail: "Blue domes to yourself", lngLat: [25.3764, 36.4611] },
          { time: "12:00", title: "Ammoudi Bay", detail: "Swim to the islet, octopus lunch", lngLat: [25.369, 36.4617] },
          { time: "19:45", title: "Sunset at the castle", detail: "The Aegean's most famous goodbye", lngLat: [25.3712, 36.4625] },
        ]),
      ],
    },
  },
  {
    id: "morocco",
    keywords: ["morocco", "marrakech", "desert", "sahara", "souk", "riad", "africa"],
    reply:
      "Marrakech — rose walls, mint tea, drums on the square at dusk. I've drawn three days: the medina's labyrinth, gardens and hammam steam, then a night camped in the Agafay desert. Shall I add the Atlas mountains?",
    mapAction: { center: [-7.99, 31.63], zoom: 12, pitch: 55, bearing: -20 },
    itinerary: {
      title: "Rose City & Desert Stars",
      subtitle: "Marrakech and the Agafay",
      days: [
        D(1, "The Medina", [-7.9892, 31.6258], "Into the labyrinth", [
          { time: "10:00", title: "Souk semmarine", detail: "Brass, cedar, saffron — bargain gently", lngLat: [-7.9886, 31.6287] },
          { time: "13:30", title: "Ben Youssef Madrasa", detail: "Zellige geometry in perfect silence", lngLat: [-7.986, 31.632] },
          { time: "18:00", title: "Jemaa el-Fnaa at dusk", detail: "Drummers, storytellers, smoke and lamps", lngLat: [-7.9892, 31.6258] },
          { time: "20:30", title: "Rooftop dinner, La Mamounia side", detail: "Pastilla under the Koutoubia minaret", lngLat: [-7.9936, 31.6236] },
        ]),
        D(2, "Gardens & Steam", [-7.9892, 31.6258], "Majorelle blue and hammam heat", [
          { time: "09:00", title: "Jardin Majorelle", detail: "Cobalt villa, cactus alleys, YSL", lngLat: [-8.0032, 31.6417] },
          { time: "12:30", title: "Bahia Palace", detail: "Painted ceilings, orange-tree courtyards", lngLat: [-7.9822, 31.6218] },
          { time: "16:00", title: "Traditional hammam", detail: "Black soap, ghassoul, an hour of steam", lngLat: [-7.9878, 31.6295] },
        ]),
        D(3, "Agafay Desert", [-8.15, 31.48], "Stone desert, canvas and stars", [
          { time: "11:00", title: "Drive to Agafay camp", detail: "Lunar hills forty minutes out", lngLat: [-8.15, 31.48] },
          { time: "17:30", title: "Camel ride at golden hour", detail: "Long shadows over the hammada", lngLat: [-8.16, 31.475] },
          { time: "20:00", title: "Dinner under the stars", detail: "Tagine, gnawa music, no light for miles", lngLat: [-8.15, 31.478] },
        ]),
      ],
    },
  },
  {
    id: "iceland",
    keywords: ["iceland", "aurora", "northern lights", "reykjavik", "glacier", "winter", "lights"],
    reply:
      "Iceland — black sand, blue ice, and if the sky cooperates, the aurora. Three days from Reykjavík: lagoon steam, the Golden Circle, and the south coast's waterfalls. Nights are kept free for hunting the lights. Fancy a glacier hike?",
    mapAction: { center: [-20.9, 64.05], zoom: 7.1, pitch: 42, bearing: 0 },
    itinerary: {
      title: "Chasing the Lights",
      subtitle: "Reykjavík and the south",
      days: [
        D(1, "Reykjavík", [-21.8174, 64.1466], "Arrive, thaw, look up", [
          { time: "11:00", title: "Blue Lagoon", detail: "Silica steam in a lava field", lngLat: [-22.4495, 63.8804] },
          { time: "16:00", title: "Old harbor & Harpa", detail: "Glass honeycomb concert hall", lngLat: [-21.9327, 64.1503] },
          { time: "22:00", title: "Aurora watch at Grótta", detail: "Lighthouse dark-sky point", lngLat: [-22.0173, 64.165] },
        ]),
        D(2, "Golden Circle", [-20.7, 64.3], "Rift valley, geysers, thunder", [
          { time: "09:30", title: "Þingvellir rift walk", detail: "Between two tectonic plates", lngLat: [-21.13, 64.2559] },
          { time: "12:30", title: "Geysir & Strokkur", detail: "Eruptions every few minutes", lngLat: [-20.3014, 64.3104] },
          { time: "14:30", title: "Gullfoss", detail: "Two-tier torrent into the canyon", lngLat: [-20.1213, 64.3271] },
        ]),
        D(3, "South Coast", [-19.6, 63.55], "Waterfalls and black sand", [
          { time: "10:00", title: "Seljalandsfoss", detail: "Walk behind the falls", lngLat: [-19.9886, 63.6156] },
          { time: "13:00", title: "Skógafoss", detail: "Sixty meters of spray and rainbows", lngLat: [-19.5114, 63.532] },
          { time: "15:30", title: "Reynisfjara black beach", detail: "Basalt columns, sneaker waves — careful", lngLat: [-19.0448, 63.4041] },
        ]),
      ],
    },
  },
  {
    id: "amalfi",
    keywords: ["italy", "amalfi", "positano", "capri", "ravello", "coast", "italian", "riviera"],
    reply:
      "The Amalfi Coast — lemon terraces stacked over a violet sea. Three days: Positano's beach clubs, the Path of the Gods to Ravello's gardens, then Capri by boat. When are you thinking — May light or September warmth?",
    mapAction: { center: [14.49, 40.63], zoom: 11.4, pitch: 58, bearing: 22 },
    itinerary: {
      title: "Lemons & Cliffs",
      subtitle: "Positano, Ravello, Capri",
      days: [
        D(1, "Positano", [14.4869, 40.628], "Vertical village, horizontal afternoon", [
          { time: "11:00", title: "Spiaggia Grande beach club", detail: "Orange umbrellas, spritz in hand", lngLat: [14.4869, 40.6274] },
          { time: "16:00", title: "Boutique lanes uphill", detail: "Linen, sandals made to measure", lngLat: [14.4853, 40.6289] },
          { time: "19:30", title: "Aperitivo by boat", detail: "The village lights up from the water", lngLat: [14.49, 40.625] },
        ]),
        D(2, "Ravello", [14.6116, 40.6493], "The path above the sea", [
          { time: "08:30", title: "Path of the Gods", detail: "Bomerano to Nocelle, cliffs all the way", lngLat: [14.548, 40.627] },
          { time: "14:30", title: "Villa Cimbrone gardens", detail: "The Terrace of Infinity", lngLat: [14.611, 40.6446] },
          { time: "20:00", title: "Dinner on Ravello's square", detail: "Scialatielli, local falanghina", lngLat: [14.6116, 40.6493] },
        ]),
        D(3, "Capri", [14.243, 40.5532], "Blue grotto, white rock", [
          { time: "09:00", title: "Fast boat to Capri", detail: "Faraglioni close-up on the way", lngLat: [14.2547, 40.5442] },
          { time: "11:00", title: "Blue Grotto", detail: "Row in, glow inside", lngLat: [14.2054, 40.561] },
          { time: "16:00", title: "Gardens of Augustus", detail: "Via Krupp switchbacks below", lngLat: [14.2418, 40.5477] },
        ]),
      ],
    },
  },
  {
    id: "bali",
    keywords: ["bali", "ubud", "indonesia", "surf", "yoga", "tropical", "beach", "jungle"],
    reply:
      "Bali — incense, rice terraces, a warm sea. Three days: Ubud's jungle and water temples, a volcano sunrise, then Uluwatu's cliff temple with fire dance at dusk. Slower and greener, or should I add the islands?",
    mapAction: { center: [115.26, -8.51], zoom: 10.4, pitch: 52, bearing: -15 },
    itinerary: {
      title: "Island of the Gods",
      subtitle: "Ubud to Uluwatu",
      days: [
        D(1, "Ubud", [115.2625, -8.5069], "Jungle, temples, green light", [
          { time: "08:30", title: "Tegallalang rice terraces", detail: "Before the heat, mist on the paddies", lngLat: [115.2779, -8.4312] },
          { time: "11:30", title: "Tirta Empul water temple", detail: "Spring-fed purification pools", lngLat: [115.3151, -8.4156] },
          { time: "19:00", title: "Dinner over the Campuhan ridge", detail: "Frangipani, frog song, candles", lngLat: [115.2561, -8.5065] },
        ]),
        D(2, "Mount Batur & Sidemen", [115.3755, -8.2422], "Sunrise from the crater", [
          { time: "04:00", title: "Batur sunrise trek", detail: "Two hours up, dawn over three lakes", lngLat: [115.3755, -8.2422] },
          { time: "13:00", title: "Sidemen valley drive", detail: "Bali of thirty years ago", lngLat: [115.427, -8.466] },
          { time: "17:00", title: "Massage at the villa", detail: "Earned", lngLat: [115.427, -8.466] },
        ]),
        D(3, "Uluwatu", [115.0849, -8.8291], "Cliffs, surf, fire dance", [
          { time: "10:00", title: "Padang Padang beach", detail: "Swim the cove between the rocks", lngLat: [115.1036, -8.8107] },
          { time: "17:30", title: "Uluwatu Temple", detail: "Seventy-meter cliffs, thieving macaques", lngLat: [115.0849, -8.8291] },
          { time: "18:15", title: "Kecak fire dance", detail: "A hundred voices at sunset", lngLat: [115.0853, -8.8295] },
        ]),
      ],
    },
  },
  {
    id: "patagonia",
    keywords: ["patagonia", "hike", "trek", "glacier", "torres", "fitz roy", "argentina", "chile", "mountains"],
    reply:
      "Patagonia — wind, granite, and ice the color of gin. Three days at the bottom of the world: Perito Moreno's calving wall, Fitz Roy at dawn, and the towers of Paine. It's a bold one. Shall I pencil in the estancia stay too?",
    mapAction: { center: [-72.9, -50.2], zoom: 6.6, pitch: 45, bearing: 10 },
    itinerary: {
      title: "Wind & Granite",
      subtitle: "Southern Patagonia",
      days: [
        D(1, "El Calafate", [-72.2649, -50.3379], "The glacier that growls", [
          { time: "10:00", title: "Perito Moreno catwalks", detail: "Sixty-meter ice wall, thunder included", lngLat: [-73.05, -50.4967] },
          { time: "15:00", title: "Boat to the south face", detail: "Close enough to feel the cold", lngLat: [-72.99, -50.49] },
          { time: "20:00", title: "Lamb asado in town", detail: "Malbec, wood smoke, maps on the table", lngLat: [-72.2649, -50.3379] },
        ]),
        D(2, "El Chaltén", [-72.8863, -49.3315], "Fitz Roy at first light", [
          { time: "06:00", title: "Laguna de los Tres trail", detail: "The classic — 20 km, worth every step", lngLat: [-72.967, -49.293] },
          { time: "17:00", title: "Cerveza artesanal", detail: "Trail-end brewery ritual", lngLat: [-72.8863, -49.3315] },
        ]),
        D(3, "Torres del Paine", [-72.9, -50.95], "Across the border to the towers", [
          { time: "09:00", title: "Drive to Paine", detail: "Guanacos and condors en route", lngLat: [-72.9, -50.95] },
          { time: "13:00", title: "Mirador Las Torres approach", detail: "The three granite teeth", lngLat: [-72.9527, -50.9423] },
          { time: "19:30", title: "Lodge dinner, lake view", detail: "Weather permitting: alpenglow", lngLat: [-72.96, -51.0] },
        ]),
      ],
    },
  },
];

const CHIPS_BY_ID: Record<string, string[]> = {
  japan: ["Make it five days", "Find a kaiseki in Gion", "Tune it toward food"],
  greece: ["Make it five days", "More languid", "Add island hopping"],
  morocco: ["Add the Atlas mountains", "Make it five days", "More souks"],
  iceland: ["Add a glacier hike", "Make it five days", "Best aurora odds"],
  amalfi: ["Make it five days", "Add a boat day", "September or May?"],
  bali: ["Add the islands", "Make it five days", "Slower and greener"],
  patagonia: ["Add the estancia stay", "Make it five days", "How fit must I be?"],
};

function score(text: string, d: MockDest): number {
  let s = 0;
  for (const k of d.keywords) if (text.includes(k)) s += k.length > 5 ? 2 : 1;
  return s;
}

/** One full turn, expressed as the same event stream the real agent emits. */
export function mockTurn(
  messages: ChatMsg[],
  itinerary: Itinerary | null
): TurnEvent[] {
  const lastUser =
    [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const text = lastUser.toLowerCase();

  let best: MockDest | null = null;
  let bestScore = 0;
  for (const d of DESTS) {
    const s = score(text, d);
    if (s > bestScore) {
      best = d;
      bestScore = s;
    }
  }

  if (
    !best &&
    /\b(surprise|anywhere|don'?t know|no idea|you (pick|choose)|dealer'?s choice|inspire)\b/.test(
      text
    )
  ) {
    best = DESTS[messages.length % DESTS.length];
  }

  if (best) {
    return [
      { type: "status", text: "Unfolding the maps…" },
      { type: "tool", item: { id: "m-edit", name: "edit_itinerary", label: "Editing the itinerary…", state: "start" } },
      { type: "ops", ops: [{ op: "replace_trip", itinerary: best.itinerary }] },
      { type: "tool", item: { id: "m-edit", name: "edit_itinerary", label: "Itinerary drafted", state: "done" } },
      { type: "tool", item: { id: "m-cam", name: "set_camera", label: "Camera in motion", state: "done" } },
      { type: "camera", move: best.mapAction },
      { type: "reply", text: best.reply },
      { type: "chips", chips: CHIPS_BY_ID[best.id] ?? ["Make it five days", "Surprise me"] },
      { type: "done", itinerary: best.itinerary, source: "mock" },
    ];
  }

  // Honesty over theater: without the real brain, edits and research are
  // impossible — say so instead of pretending.
  if (itinerary) {
    return [
      {
        type: "reply",
        text:
          "I must be honest — live changes and research need my full brain, and the studio hasn't connected it. Add an xAI key and restart, or name a new destination and I'll chart that instead.",
      },
      { type: "chips", chips: ["Surprise me", "Marrakech & the desert", "Chase the northern lights"] },
      { type: "done", itinerary, source: "mock" },
    ];
  }

  return [
    {
      type: "reply",
      text:
        "I can take you almost anywhere — say Japan, Santorini, Marrakech, Iceland, the Amalfi Coast, Bali, or Patagonia. Or simply tell me a feeling: beach, food, mountains, northern lights — and I'll chart it.",
    },
    { type: "chips", chips: ["A week of food in Japan", "Chase the northern lights", "Surprise me"] },
    { type: "done", itinerary: null, source: "mock" },
  ];
}
