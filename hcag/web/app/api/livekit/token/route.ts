import { NextResponse } from "next/server";

export const runtime = "nodejs";

const API_URL = process.env.HCAG_API_URL ?? "http://localhost:8000";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  try {
    const upstream = await fetch(`${API_URL}/livekit/token`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch (e) {
    return NextResponse.json(
      { error: "backend_unreachable", detail: String((e as Error).message ?? e) },
      { status: 502 },
    );
  }
}
