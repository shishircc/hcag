import type { AnswerCard, ChatMessage, Source } from "@/components/chat/types";

export type { ChatMessage };

export type ChatReply = {
  text?: string;
  card?: AnswerCard;
  sources?: Source[];
  isEscalate?: boolean;
  hasFeedback?: boolean;
};

const SCRIPTED_SOURCES: Source[] = [
  { title: "Employment Pass eligibility", section: "Work passes / Professionals" },
  { title: "S Pass eligibility", section: "Work passes / Skilled workers" },
  { title: "Which pass is suitable for my candidate?", section: "FAQ" },
];

// Faithful to the prototype's four scripted turns.
export function scriptedReply(step: number): ChatReply {
  if (step === 0) {
    return {
      text: "Happy to help with that. Tell me a little about the person applying and I will work out which passes they qualify for.",
      sources: SCRIPTED_SOURCES,
    };
  }
  if (step === 1) {
    return {
      text: "Thanks. Who is the pass for — a professional or manager, a skilled tradesperson, a domestic worker, or a student or trainee?",
    };
  }
  if (step === 2) {
    return {
      text: "Understood. What fixed monthly salary do you expect to pay?",
    };
  }
  return {
    text: "Based on your answers, the Employment Pass is the closest fit.",
    card: {
      title: "Employment Pass",
      subtitle: "For foreign professionals, managers and executives",
      bullets: [
        { text: "Fixed monthly salary of at least $5,600, rising with age and sector benchmarks." },
        { text: "Candidate must pass the points-based assessment framework." },
        { text: "No quota or levy applies. The employer submits the application." },
        { text: "Valid up to 2 years for a first application, 3 years on renewal." },
      ],
    },
    sources: [SCRIPTED_SOURCES[0], SCRIPTED_SOURCES[2]],
    hasFeedback: true,
  };
}

// Real chat call — hits the Next.js API route which proxies to the FastAPI server.
export async function sendChat(
  sessionId: string,
  history: ChatMessage[],
  message: string,
): Promise<ChatReply> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      history: history.map((m) => ({ role: m.role, text: m.text ?? "" })),
      message,
    }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`chat request failed: ${res.status} ${body}`);
  }
  return (await res.json()) as ChatReply;
}

export function isApiEnabled(): boolean {
  // Client-side toggle. The Next.js API route decides how to answer; the widget
  // just needs to know whether to try the network at all. Default: off so the
  // prototype flow works without a backend.
  const v = process.env.NEXT_PUBLIC_USE_API;
  return v === "1" || v === "true";
}

/**
 * Turn events, per DESIGN.md §2.14.1.
 *
 * Deliberately the same vocabulary the voice transcription channel publishes
 * (§5.7): one schema, two transports. The widget reduces both with the same
 * code, so a delta-handling bug is one bug rather than two.
 */
export type TurnEvent = {
  seq: number;
  kind:
    | "assistant.start"
    | "assistant.delta"
    | "assistant.final"
    | "tool.start"
    | "tool.end"
    | "error";
  turn_id: string;
  text?: string;
  tool?: string;
  requested?: string[];
  loaded?: string[];
  active_after?: string[];
  detail?: string;
};

export class StreamUnsupported extends Error {}

/**
 * Stream a turn, invoking `onEvent` as each event arrives.
 *
 * Resolves with the final answer. Throws `StreamUnsupported` when the backend
 * does not stream (501 — the RAG baseline, §9.5) so the caller can fall back
 * to `sendChat`, and throws on any other failure.
 */
export async function streamChat(
  sessionId: string,
  history: ChatMessage[],
  message: string,
  onEvent: (e: TurnEvent) => void,
): Promise<string> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      history: history.map((m) => ({ role: m.role, text: m.text ?? "" })),
      message,
    }),
  });

  if (res.status === 501) throw new StreamUnsupported("backend does not stream");
  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => "");
    throw new Error(`chat stream failed: ${res.status} ${body}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer: string | null = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line. A chunk can split a frame, so
    // only whole frames are consumed and the remainder is carried forward.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let event: TurnEvent;
      try {
        event = JSON.parse(line.slice(6)) as TurnEvent;
      } catch {
        continue;
      }
      onEvent(event);
      // The 200 was committed before the failure, so an error arrives in-band
      // and cannot be inferred from the status line (§2.14.3).
      if (event.kind === "error") throw new Error(event.detail ?? "turn failed");
      if (event.kind === "assistant.final") answer = event.text ?? "";
    }
  }

  // A stream that ends without assistant.final is a FAILED turn, not a short
  // answer. Rendering a truncated answer as a complete one is the worst
  // outcome available (§2.14.3).
  if (answer === null) throw new Error("stream ended before the answer was complete");
  return answer;
}

/** Last dotted segment of a packet id — `…employment-pass.eligibility` → `eligibility`. */
export function shortPacketName(id: string): string {
  const seg = id.split(".").filter(Boolean).pop() ?? id;
  return seg.replace(/-/g, " ");
}
