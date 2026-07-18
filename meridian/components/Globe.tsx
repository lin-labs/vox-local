"use client";

import { useEffect, useRef, useState } from "react";
import { createMap, type MapHandle } from "@/lib/mapAdapter";
import { useApp } from "@/lib/store";
import { flyToPlace } from "@/lib/orchestrator";
import type { MarkerSpec } from "@/lib/types";

export default function Globe() {
  const ref = useRef<HTMLDivElement>(null);
  const handleRef = useRef<MapHandle | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let dead = false;
    if (!ref.current) return;
    // Reveal even if the tile CDN is slow — tiles stream in progressively.
    const failsafe = setTimeout(() => setVisible(true), 3000);
    createMap(ref.current, () => {
      clearTimeout(failsafe);
      setVisible(true);
    }).then((h) => {
      if (dead) {
        h.destroy();
        return;
      }
      handleRef.current = h;
      h.startIdleSpin();
      useApp.setState({ mapReady: true, mapFlavor: h.flavor });
    });
    return () => {
      dead = true;
      clearTimeout(failsafe);
      handleRef.current?.destroy();
      handleRef.current = null;
    };
  }, []);

  const camera = useApp((s) => s.camera);
  useEffect(() => {
    const h = handleRef.current;
    if (!h || !camera) return;
    h.stopIdleSpin();
    if (camera.kind === "fly") h.flyTo(camera.move);
    else if (camera.kind === "fit") h.fitTo(camera.points, camera.padRight);
    else if (camera.kind === "zoomBy") h.zoomBy(camera.delta);
  }, [camera]);

  const markers = useApp((s) => s.markers);
  useEffect(() => {
    const h = handleRef.current;
    if (!h) return;
    h.setMarkers(
      markers,
      (id) => useApp.setState({ pulseId: id }),
      (s: MarkerSpec) => flyToPlace(s.lngLat)
    );
  }, [markers]);

  const route = useApp((s) => s.route);
  useEffect(() => {
    handleRef.current?.setRoute(route ?? []);
  }, [route]);

  const pulseId = useApp((s) => s.pulseId);
  useEffect(() => {
    handleRef.current?.pulseMarker(pulseId);
  }, [pulseId]);

  return (
    <div
      className={`absolute inset-0 transition-opacity duration-[1400ms] ${
        visible ? "opacity-100" : "opacity-0"
      }`}
    >
      {/* map container: inline styles so neither React re-renders nor the
          map library's own stylesheet (`.maplibregl-map{position:relative}`,
          whose cascade order vs Tailwind is build-dependent) can collapse it */}
      <div
        ref={ref}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
        }}
      />
    </div>
  );
}
