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
