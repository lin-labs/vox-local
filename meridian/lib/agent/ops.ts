/* Deterministic op-applier — the single source of truth for itinerary
   mutations invoked by xAI's client-side custom function calls. */

import type { Itinerary, ItineraryOp } from "@/lib/types";

export type OpsResult = {
  itinerary: Itinerary | null;
  changedIds: string[]; // item ids + "day-N" markers touched this batch
  errors: string[];
};

export function applyOps(
  base: Itinerary | null,
  ops: ItineraryOp[]
): OpsResult {
  let it: Itinerary | null = base ? structuredClone(base) : null;
  const changed = new Set<string>();
  const errors: string[] = [];

  const ensure = (): Itinerary => {
    if (!it) it = { title: "Your journey", days: [] };
    return it;
  };
  const renumber = (x: Itinerary) => {
    x.days.forEach((d, i) => (d.day = i + 1));
  };
  const nextId = (x: Itinerary): string => {
    let max = 0;
    for (const d of x.days)
      for (const item of d.items) {
        const m = /^i(\d+)$/.exec(item.id ?? "");
        if (m) max = Math.max(max, Number(m[1]));
      }
    return `i${max + 1}`;
  };
  const findDay = (x: Itinerary, n: number) => x.days.find((d) => d.day === n);
  const findItem = (x: Itinerary, id: string) => {
    for (const d of x.days) {
      const idx = d.items.findIndex((i) => i.id === id);
      if (idx >= 0) return { d, idx };
    }
    return null;
  };

  for (const op of ops) {
    try {
      switch (op.op) {
        case "replace_trip": {
          it = op.itinerary ? structuredClone(op.itinerary) : null;
          if (it) {
            renumber(it);
            for (const d of it.days) {
              changed.add(`day-${d.day}`);
              for (const item of d.items) {
                if (!item.id) item.id = nextId(it);
                changed.add(item.id);
              }
            }
          }
          break;
        }
        case "set_meta": {
          const x = ensure();
          if (op.title !== undefined) x.title = op.title;
          if (op.subtitle !== undefined) x.subtitle = op.subtitle;
          break;
        }
        case "add_day": {
          const x = ensure();
          const d = {
            day: 0,
            location: op.location,
            center: op.center,
            summary: op.summary,
            items: [],
          };
          const pos =
            op.position && op.position >= 1 && op.position <= x.days.length
              ? op.position - 1
              : x.days.length;
          x.days.splice(pos, 0, d);
          renumber(x);
          changed.add(`day-${d.day}`);
          break;
        }
        case "remove_day": {
          const x = ensure();
          const i = x.days.findIndex((d) => d.day === op.day);
          if (i < 0) {
            errors.push(`remove_day: no day ${op.day}`);
            break;
          }
          x.days.splice(i, 1);
          renumber(x);
          break;
        }
        case "set_day": {
          const x = ensure();
          const d = findDay(x, op.day);
          if (!d) {
            errors.push(`set_day: no day ${op.day}`);
            break;
          }
          if (op.location !== undefined) d.location = op.location;
          if (op.center !== undefined) d.center = op.center;
          if (op.summary !== undefined) d.summary = op.summary;
          changed.add(`day-${d.day}`);
          break;
        }
        case "add_item": {
          const x = ensure();
          const d = findDay(x, op.day);
          if (!d) {
            errors.push(`add_item: no day ${op.day}`);
            break;
          }
          const item = { ...op.item };
          if (!item.id) item.id = nextId(x);
          const pos =
            op.position && op.position >= 1 && op.position <= d.items.length + 1
              ? op.position - 1
              : d.items.length;
          d.items.splice(pos, 0, item);
          changed.add(item.id);
          break;
        }
        case "update_item": {
          const x = ensure();
          const f = findItem(x, op.id);
          if (!f) {
            errors.push(`update_item: no item ${op.id}`);
            break;
          }
          f.d.items[f.idx] = { ...f.d.items[f.idx], ...op.patch, id: op.id };
          changed.add(op.id);
          break;
        }
        case "remove_item": {
          const x = ensure();
          const f = findItem(x, op.id);
          if (!f) {
            errors.push(`remove_item: no item ${op.id}`);
            break;
          }
          f.d.items.splice(f.idx, 1);
          break;
        }
        case "move_item": {
          const x = ensure();
          const f = findItem(x, op.id);
          if (!f) {
            errors.push(`move_item: no item ${op.id}`);
            break;
          }
          const to = findDay(x, op.toDay);
          if (!to) {
            errors.push(`move_item: no day ${op.toDay}`);
            break;
          }
          const [item] = f.d.items.splice(f.idx, 1);
          const pos =
            op.position && op.position >= 1 && op.position <= to.items.length + 1
              ? op.position - 1
              : to.items.length;
          to.items.splice(pos, 0, item);
          changed.add(op.id);
          break;
        }
      }
    } catch (e) {
      errors.push(`${(op as { op: string }).op}: ${String(e)}`);
    }
  }

  return { itinerary: it, changedIds: [...changed], errors };
}

/** Incrementally extracts completed op objects from a streaming
    edit_itinerary tool input (`{"ops":[{...},{...},…]}`) as JSON tokens
    arrive — powers live panel edits mid-generation. */
export function createOpsStreamParser() {
  let buf = "";
  let started = false;
  let idx = 0;
  let depth = 0;
  let inStr = false;
  let esc = false;
  let objStart = -1;
  let total = 0;

  return {
    push(chunk: string): ItineraryOp[] {
      buf += chunk;
      const out: ItineraryOp[] = [];
      if (!started) {
        const m = buf.match(/"ops"\s*:\s*\[/);
        if (!m) return out;
        started = true;
        idx = (m.index ?? 0) + m[0].length;
      }
      for (; idx < buf.length; idx++) {
        const ch = buf[idx];
        if (inStr) {
          if (esc) esc = false;
          else if (ch === "\\") esc = true;
          else if (ch === '"') inStr = false;
          continue;
        }
        if (ch === '"') {
          inStr = true;
        } else if (ch === "{") {
          if (depth === 0) objStart = idx;
          depth++;
        } else if (ch === "}") {
          depth--;
          if (depth === 0 && objStart >= 0) {
            try {
              out.push(JSON.parse(buf.slice(objStart, idx + 1)));
            } catch {
              /* malformed fragment — skip */
            }
            objStart = -1;
          }
        }
      }
      total += out.length;
      return out;
    },
    count(): number {
      return total;
    },
  };
}

/** Compact state readback for tool_results — the model's view after editing. */
export function summarize(it: Itinerary | null): string {
  if (!it || !it.days.length) return "Trip is now empty.";
  const lines = it.days.map(
    (d) =>
      `Day ${d.day} — ${d.location}: ${
        d.items.map((i) => `[${i.id}] ${i.title}`).join("; ") || "(no items)"
      }`
  );
  return `Trip "${it.title}" — ${it.days.length} day(s).\n${lines.join("\n")}`;
}
