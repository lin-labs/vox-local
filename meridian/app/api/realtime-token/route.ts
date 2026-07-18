import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const XAI_CLIENT_SECRETS_URL =
  "https://api.x.ai/v1/realtime/client_secrets";

export async function POST() {
  const apiKey = process.env.XAI_API_KEY?.trim();
  if (!apiKey) {
    return NextResponse.json(
      { error: "XAI_API_KEY is not configured" },
      { status: 503, headers: { "cache-control": "no-store" } }
    );
  }

  try {
    const upstream = await fetch(XAI_CLIENT_SECRETS_URL, {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ expires_after: { seconds: 300 } }),
      cache: "no-store",
    });

    if (!upstream.ok) {
      console.error(
        `[realtime-token] xAI rejected client-secret request: ${upstream.status}`
      );
      return NextResponse.json(
        { error: "xAI voice authentication is temporarily unavailable" },
        { status: 502, headers: { "cache-control": "no-store" } }
      );
    }

    const secret = (await upstream.json()) as {
      value?: unknown;
      expires_at?: unknown;
    };
    if (typeof secret.value !== "string" || !secret.value) {
      console.error("[realtime-token] xAI returned no client secret");
      return NextResponse.json(
        { error: "xAI voice authentication returned an invalid response" },
        { status: 502, headers: { "cache-control": "no-store" } }
      );
    }

    return NextResponse.json(
      {
        value: secret.value,
        expires_at: secret.expires_at,
        model: process.env.XAI_VOICE_MODEL || "grok-voice-think-fast-1.0",
        voice: process.env.XAI_VOICE || "eve",
      },
      { headers: { "cache-control": "no-store" } }
    );
  } catch (error) {
    console.error("[realtime-token] xAI request failed", error);
    return NextResponse.json(
      { error: "Could not reach xAI voice authentication" },
      { status: 502, headers: { "cache-control": "no-store" } }
    );
  }
}
