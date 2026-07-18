/* One adapter, two engines.
   - NEXT_PUBLIC_MAPBOX_TOKEN present → Mapbox GL v3 Standard (dusk) globe.
   - Otherwise → MapLibre GL v5 globe + Carto dark basemap. Zero keys. */

import type { CameraMove, LngLat, MarkerSpec } from "@/lib/types";

export interface MapHandle {
  flavor: "mapbox" | "maplibre";
  flyTo(m: CameraMove): void;
  fitTo(points: LngLat[], padRight: number): void;
  setMarkers(
    specs: MarkerSpec[],
    onHover: (id: string | null) => void,
    onClick: (s: MarkerSpec) => void
  ): void;
  pulseMarker(id: string | null): void;
  setRoute(coords: LngLat[]): void;
  zoomBy(delta: number): void;
  startIdleSpin(): void;
  stopIdleSpin(): void;
  destroy(): void;
}

const ROUTE_SRC = "meridian-route";

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export async function createMap(
  container: HTMLElement,
  onLoaded: () => void
): Promise<MapHandle> {
  const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
  let lib: any;
  let flavor: "mapbox" | "maplibre";

  const common = {
    container,
    center: [24, 16] as LngLat,
    zoom: 1.55,
    pitch: 0,
    bearing: 0,
    attributionControl: false as const,
  };

  let map: any;
  if (token) {
    const mod: any = await import("mapbox-gl");
    lib = mod.default ?? mod;
    lib.accessToken = token;
    flavor = "mapbox";
    map = new lib.Map({
      ...common,
      style: "mapbox://styles/mapbox/standard",
      projection: "globe",
    });
    map.on("style.load", () => {
      const cfg: Record<string, unknown> = {
        lightPreset: "dusk",
        showRoadLabels: false,
        showTransitLabels: false,
        showPointOfInterestLabels: false,
      };
      for (const [k, v] of Object.entries(cfg)) {
        try {
          map.setConfigProperty("basemap", k, v);
        } catch {
          /* unsupported config on this style version — skip */
        }
      }
      try {
        // local-script labels (Japanese) — the atelier reads like the place itself
        map.setLanguage?.("ja");
      } catch {
        /* noop */
      }
    });
  } else {
    const mod: any = await import("maplibre-gl");
    lib = mod.default ?? mod;
    flavor = "maplibre";
    map = new lib.Map({
      ...common,
      style:
        "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    });
    map.on("style.load", () => {
      try {
        map.setProjection({ type: "globe" });
      } catch {
        /* globe unsupported — flat map still works */
      }
      try {
        map.setSky({
          "sky-color": "#0a1120",
          "horizon-color": "#1d2a44",
          "fog-color": "#0a1120",
          "sky-horizon-blend": 0.6,
          "horizon-fog-blend": 0.6,
        });
      } catch {
        /* noop */
      }
    });
  }

  try {
    map.addControl(new lib.AttributionControl({ compact: true }), "bottom-right");
  } catch {
    /* noop */
  }
  map.once("load", onLoaded);
  map.on("error", (e: any) => {
    console.error("[map]", e?.error?.message ?? e);
    ((window as any).__mapErrs ??= []).push(String(e?.error?.message ?? e));
  });
  (window as any).__meridianMap = map;

  // ── Idle spin (cinematic attract mode) ─────────────────────────────────────
  let spinning = false;
  let raf = 0;
  const spinFrame = () => {
    if (!spinning) return;
    const c = map.getCenter();
    c.lng += 0.03;
    map.jumpTo({ center: c });
    raf = requestAnimationFrame(spinFrame);
  };
  const stopIdleSpin = () => {
    spinning = false;
    cancelAnimationFrame(raf);
  };
  ["mousedown", "wheel", "touchstart"].forEach((ev) =>
    map.on(ev, stopIdleSpin)
  );

  // ── Markers ────────────────────────────────────────────────────────────────
  const markerObjs = new Map<string, { m: any; el: HTMLElement }>();

  // ── Route line ─────────────────────────────────────────────────────────────
  const applyRoute = (coords: LngLat[]) => {
    const data = {
      type: "Feature" as const,
      properties: {},
      geometry: { type: "LineString" as const, coordinates: coords },
    };
    const src = map.getSource(ROUTE_SRC);
    if (src) {
      src.setData(data);
      return;
    }
    map.addSource(ROUTE_SRC, { type: "geojson", data });
    const layout = { "line-cap": "round", "line-join": "round" };
    const glow: any = {
      id: "meridian-route-glow",
      type: "line",
      source: ROUTE_SRC,
      layout,
      paint: {
        "line-color": "#dfc48c",
        "line-width": 5,
        "line-opacity": 0.25,
        "line-blur": 4,
      },
    };
    const core: any = {
      id: "meridian-route-core",
      type: "line",
      source: ROUTE_SRC,
      layout,
      paint: {
        "line-color": "#ead9b0",
        "line-width": 1.6,
        "line-opacity": 0.9,
        "line-dasharray": [0.1, 2.2],
      },
    };
    if (flavor === "mapbox") {
      glow.slot = "top";
      core.slot = "top";
    }
    map.addLayer(glow);
    map.addLayer(core);
  };
  const setRoute = (coords: LngLat[]) => {
    const run = () => {
      try {
        applyRoute(coords);
      } catch {
        /* style not ready yet */
      }
    };
    if (map.isStyleLoaded?.() || map.loaded?.()) run();
    else map.once("load", run);
  };

  return {
    flavor,
    flyTo(m: CameraMove) {
      stopIdleSpin();
      map.flyTo({
        center: m.center,
        zoom: m.zoom,
        pitch: m.pitch ?? 50,
        bearing: m.bearing ?? 0,
        duration: m.durationMs ?? 3200,
        curve: 1.42,
        essential: true,
      });
    },
    fitTo(points: LngLat[], padRight: number) {
      stopIdleSpin();
      if (!points.length) return;
      const bounds = new lib.LngLatBounds(points[0], points[0]);
      points.forEach((p) => bounds.extend(p));
      let cam: any = null;
      try {
        cam = map.cameraForBounds(bounds, {
          padding: { top: 110, bottom: 180, left: 90, right: padRight },
          maxZoom: 12.8,
        });
      } catch {
        /* padding larger than viewport */
      }
      if (!cam) {
        try {
          cam = map.cameraForBounds(bounds, { padding: 60, maxZoom: 12.8 });
        } catch {
          /* noop */
        }
      }
      if (cam) {
        map.flyTo({
          ...cam,
          pitch: 46,
          bearing: -14,
          duration: 3000,
          essential: true,
        });
      } else {
        map.flyTo({
          center: points[0],
          zoom: 9,
          pitch: 46,
          duration: 3000,
          essential: true,
        });
      }
    },
    setMarkers(specs, onHover, onClick) {
      markerObjs.forEach((o) => o.m.remove());
      markerObjs.clear();
      for (const s of specs) {
        const el = document.createElement("div");
        el.className = "m-pin";
        el.innerHTML = `<div class="m-label">${esc(
          s.label || s.name
        )}</div><div class="m-dot"><span>${
          s.day != null ? s.day : "◆"
        }</span></div>`;
        el.addEventListener("mouseenter", () => onHover(s.id));
        el.addEventListener("mouseleave", () => onHover(null));
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          onClick(s);
        });
        const m = new lib.Marker({ element: el, anchor: "center" })
          .setLngLat(s.lngLat)
          .addTo(map);
        markerObjs.set(s.id, { m, el });
      }
    },
    pulseMarker(id) {
      markerObjs.forEach((o, key) => {
        o.el.classList.toggle("pulse", key === id);
      });
    },
    setRoute,
    zoomBy(delta) {
      stopIdleSpin();
      const z = Math.min(17.5, Math.max(1.1, map.getZoom() + delta));
      map.easeTo({ zoom: z, duration: 1100, essential: true });
    },
    startIdleSpin() {
      if (spinning) return;
      spinning = true;
      raf = requestAnimationFrame(spinFrame);
    },
    stopIdleSpin,
    destroy() {
      stopIdleSpin();
      markerObjs.forEach((o) => o.m.remove());
      markerObjs.clear();
      try {
        map.remove();
      } catch {
        /* noop */
      }
    },
  };
}
