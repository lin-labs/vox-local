import Anthropic from "@anthropic-ai/sdk";
import {
  CLAUDE_STATIC_SYSTEM,
  EDIT_ITINERARY_TOOL,
  SET_CAMERA_TOOL,
  FINALIZE_TOOL,
  WEB_SEARCH_TOOL,
} from "@/lib/agent/anthropic";
import { dynamicSystem } from "@/lib/agent/system";
import { applyOps, createOpsStreamParser, summarize } from "@/lib/agent/ops";
import { mockTurn } from "@/lib/agent/mock";
import type { ChatMsg, Itinerary, ItineraryOp, TurnEvent } from "@/lib/types";

export const maxDuration = 120;

const encoder = new TextEncoder();
const MAX_STEPS = 8;

export async function POST(req: Request) {
  let messages: ChatMsg[] = [];
  let itinerary: Itinerary | null = null;
  try {
    const body = await req.json();
    messages = Array.isArray(body?.messages) ? body.messages : [];
    itinerary = body?.itinerary ?? null;
  } catch {
    return new Response("bad request", { status: 400 });
  }

  const stream = new ReadableStream({
    async start(controller) {
      let emitted = false;
      let closed = false;
      req.signal?.addEventListener?.("abort", () => (closed = true));
      const emit = (e: TurnEvent) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(e)}\n\n`));
          emitted = true;
        } catch {
          closed = true; // client disconnected mid-stream — stop writing
        }
      };
      try {
        const apiKey = process.env.ANTHROPIC_API_KEY;
        if (!apiKey) {
          for (const e of mockTurn(messages, itinerary)) emit(e);
        } else {
          await runAgent(apiKey, messages, itinerary, emit);
        }
      } catch (err) {
        console.error("[concierge] agent failed:", err);
        if (!emitted) {
          try {
            for (const e of mockTurn(messages, itinerary)) emit(e);
          } catch {
            emit({ type: "reply", text: "Forgive me — say that once more?" });
            emit({ type: "done", itinerary, source: "mock" });
          }
        } else {
          emit({
            type: "reply",
            text: "Forgive me — the line to our atlas flickered mid-thought. Say that once more?",
          });
          emit({ type: "done", itinerary, source: "claude" });
        }
      } finally {
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}

/* eslint-disable @typescript-eslint/no-explicit-any */
type BlockMeta = {
  type: string;
  name?: string;
  feedId: string;
  raw: string;
  parser?: ReturnType<typeof createOpsStreamParser>;
  applied: number;
};

async function runAgent(
  apiKey: string,
  history: ChatMsg[],
  startItinerary: Itinerary | null,
  emit: (e: TurnEvent) => void
) {
  const client = new Anthropic({ apiKey });
  const model = process.env.CONCIERGE_MODEL || "claude-sonnet-5";
  let itinerary = startItinerary;
  let msgs: any[] = history.map((m) => ({ role: m.role, content: m.content }));
  let finalized = false;

  for (let step = 0; step < MAX_STEPS && !finalized; step++) {
    if (step > 0) emit({ type: "status", text: "Weighing the next step…" });

    const s = client.messages.stream(
      {
        model,
        max_tokens: 8000,
        system: [
        {
          type: "text",
          text: CLAUDE_STATIC_SYSTEM,
          cache_control: { type: "ephemeral" },
        },
        { type: "text", text: dynamicSystem(itinerary) },
      ] as any,
        messages: msgs,
        tools: [
          WEB_SEARCH_TOOL,
          EDIT_ITINERARY_TOOL,
          SET_CAMERA_TOOL,
          FINALIZE_TOOL,
        ] as any,
      },
      {
        // stream tool-input tokens as generated instead of buffering per block —
        // this is what makes itinerary ops apply live while the model writes
        headers: { "anthropic-beta": "fine-grained-tool-streaming-2025-05-14" },
      }
    );

    const metas = new Map<number, BlockMeta>();
    const editErrors: string[] = [];
    // results keyed by tool_use id, filled as blocks complete
    const resultsById = new Map<string, string>();
    const idByIndex = new Map<number, string>();

    for await (const ev of s as any) {
      if (ev.type === "content_block_start") {
        const b = ev.content_block;
        const meta: BlockMeta = {
          type: b.type,
          name: b.name,
          feedId: `s${step}b${ev.index}`,
          raw: "",
          applied: 0,
        };
        metas.set(ev.index, meta);
        if (b.id) idByIndex.set(ev.index, b.id);

        if (b.type === "server_tool_use" && b.name === "web_search") {
          emit({
            type: "tool",
            item: { id: meta.feedId, name: "web_search", label: "Searching the web…", state: "start" },
          });
          emit({ type: "status", text: "Searching the web…" });
        } else if (b.type === "web_search_tool_result") {
          emit({ type: "status", text: "Reading what I found…" });
        } else if (b.type === "tool_use" && b.name === "edit_itinerary") {
          meta.parser = createOpsStreamParser();
          emit({
            type: "tool",
            item: { id: meta.feedId, name: "edit_itinerary", label: "Editing the itinerary…", state: "start" },
          });
          emit({ type: "status", text: "Reshaping the itinerary…" });
        } else if (b.type === "tool_use" && b.name === "set_camera") {
          emit({
            type: "tool",
            item: { id: meta.feedId, name: "set_camera", label: "Framing the map…", state: "start" },
          });
        } else if (b.type === "tool_use" && b.name === "finalize_turn") {
          emit({
            type: "tool",
            item: { id: meta.feedId, name: "finalize", label: "Composing the reply…", state: "start" },
          });
        }
      } else if (ev.type === "content_block_delta") {
        const meta = metas.get(ev.index);
        if (!meta) continue;
        if (ev.delta?.type === "input_json_delta") {
          meta.raw += ev.delta.partial_json ?? "";
          if (meta.parser) {
            const fresh = meta.parser.push(ev.delta.partial_json ?? "");
            if (fresh.length) {
              const r = applyOps(itinerary, fresh);
              itinerary = r.itinerary;
              editErrors.push(...r.errors);
              meta.applied += fresh.length;
              emit({ type: "ops", ops: fresh });
              emit({
                type: "tool",
                item: {
                  id: meta.feedId,
                  name: "edit_itinerary",
                  label: `Editing the itinerary · ${meta.applied} change${meta.applied === 1 ? "" : "s"}`,
                  state: "start",
                },
              });
            }
          }
        }
      } else if (ev.type === "content_block_stop") {
        const meta = metas.get(ev.index);
        if (!meta) continue;
        let input: any = null;
        try {
          input = meta.raw ? JSON.parse(meta.raw) : null;
        } catch {
          input = null;
        }

        if (meta.type === "server_tool_use" && meta.name === "web_search") {
          const q = typeof input?.query === "string" ? input.query : "the web";
          emit({
            type: "tool",
            item: { id: meta.feedId, name: "web_search", label: `Searched: ${q}`, state: "done" },
          });
          emit({ type: "status", text: `Searching: ${q}` });
        } else if (meta.type === "tool_use" && meta.name === "edit_itinerary") {
          // apply any trailing ops the incremental parser hadn't closed yet
          const all: ItineraryOp[] = Array.isArray(input?.ops) ? input.ops : [];
          if (all.length > meta.applied) {
            const rest = all.slice(meta.applied);
            const r = applyOps(itinerary, rest);
            itinerary = r.itinerary;
            editErrors.push(...r.errors);
            meta.applied = all.length;
            emit({ type: "ops", ops: rest });
          }
          emit({
            type: "tool",
            item: {
              id: meta.feedId,
              name: "edit_itinerary",
              label: `Itinerary updated · ${meta.applied} change${meta.applied === 1 ? "" : "s"}`,
              state: "done",
            },
          });
          const id = idByIndex.get(ev.index);
          if (id)
            resultsById.set(
              id,
              meta.applied === 0
                ? `No ops were parsed — your batch was likely cut off or malformed. Compose GRANULAR ops (set_meta, add_day, add_item …) and continue from the current state:\n${summarize(itinerary)}`
                : `Applied ${meta.applied} op(s).${
                    editErrors.length ? ` ERRORS: ${editErrors.join("; ")}.` : ""
                  }\n${summarize(itinerary)}`
            );
        } else if (meta.type === "tool_use" && meta.name === "set_camera") {
          if (input?.center && typeof input?.zoom === "number") {
            emit({ type: "camera", move: input });
          }
          emit({
            type: "tool",
            item: { id: meta.feedId, name: "set_camera", label: "Camera in motion", state: "done" },
          });
          const id = idByIndex.get(ev.index);
          if (id) resultsById.set(id, "Camera moving.");
        } else if (meta.type === "tool_use" && meta.name === "finalize_turn") {
          const reply =
            typeof input?.reply === "string" && input.reply.trim()
              ? input.reply.trim()
              : "It's arranged.";
          emit({ type: "reply", text: reply });
          const chips = Array.isArray(input?.chips)
            ? input.chips.filter((c: unknown) => typeof c === "string")
            : [];
          if (chips.length) emit({ type: "chips", chips: chips.slice(0, 4) });
          emit({
            type: "tool",
            item: { id: meta.feedId, name: "finalize", label: "Reply delivered", state: "done" },
          });
          const id = idByIndex.get(ev.index);
          if (id) resultsById.set(id, "Delivered.");
          finalized = true;
        }
      }
    }

    const res: any = await s.finalMessage();

    if (finalized) break;

    if (res.stop_reason === "pause_turn") {
      msgs = [...msgs, { role: "assistant", content: res.content }];
      continue;
    }

    const toolUses = (res.content as any[]).filter((b) => b.type === "tool_use");
    if (!toolUses.length) {
      // model answered in plain text without finalize — deliver it
      const text = (res.content as any[])
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join(" ")
        .trim();
      if (text) emit({ type: "reply", text });
      finalized = true;
      break;
    }

    const results = toolUses.map((tu) => ({
      type: "tool_result",
      tool_use_id: tu.id,
      content: resultsById.get(tu.id) ?? "ok",
    }));
    msgs = [
      ...msgs,
      { role: "assistant", content: res.content },
      { role: "user", content: results },
    ];
  }

  if (!finalized) {
    emit({
      type: "reply",
      text: "It's taking shape — anything else while I have the maps out?",
    });
  }
  emit({ type: "done", itinerary, source: "claude" });
}
