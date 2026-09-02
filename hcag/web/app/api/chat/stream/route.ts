import { NextResponse } from "next/server";

export const runtime = "nodejs";
// Buffering here would defeat the point: the browser would receive the whole
// turn at once — a slow synchronous response wearing a stream's clothes.
export const dynamic = "force-dynamic";

const API_URL = process.env.HCAG_API_URL ?? "http://localhost:8000";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  try {
    const upstream = await fetch(`${API_URL}/chat/stream`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });

    // 501 from a non-streaming backend, or any pre-stream failure, is still an
    // ordinary HTTP error — passed through so the client can fall back (§9.5).
    if (!upstream.ok || !upstream.body) {
      const text = await upstream.text().catch(() => "");
      return new NextResponse(text, {
        status: upstream.status,
        headers: {
          "content-type": upstream.headers.get("content-type") ?? "application/json",
        },
      });
    }

    return new NextResponse(upstream.body, {
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
      },
    });
  } catch (e) {
    return NextResponse.json(
      { error: "backend_unreachable", detail: String((e as Error).message ?? e) },
      { status: 502 },
    );
  }
}
