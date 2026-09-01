import AnswerCard from "./AnswerCard";
import Markdown from "./Markdown";
import EscalateCard from "./EscalateCard";
import FeedbackRow from "./FeedbackRow";
import SourcesList from "./SourcesList";
import type { ChatMessage, Feedback } from "./types";

export default function Message({
  msg,
  feedback,
  onThumbUp,
  onThumbDown,
  onEscalate,
}: {
  msg: ChatMessage;
  feedback: Feedback;
  onThumbUp: () => void;
  onThumbDown: () => void;
  onEscalate: () => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {msg.role === "user" && msg.text ? (
        <div
          style={{
            alignSelf: "flex-end",
            maxWidth: "82%",
            background: "var(--primary)",
            color: "#fff",
            padding: "10px 14px",
            borderRadius: "14px 14px 4px 14px",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
          }}
        >
          {/* §10.3 — user input renders literally. Someone typing `2 * 3 * 4`
              or `_maybe_` must see exactly that. Markdown is model output only. */}
          {msg.text}
        </div>
      ) : null}

      {msg.role === "bot" && msg.text ? (
        <div
          style={{
            maxWidth: "92%",
            background: "var(--bg-2)",
            border: "1px solid var(--bg-msg-border)",
            padding: "12px 14px",
            borderRadius: "14px 14px 14px 4px",
            lineHeight: 1.55,
            color: "var(--ink)",
            minWidth: 0,
          }}
        >
          <Markdown text={msg.text} />
        </div>
      ) : null}

      {msg.card ? <AnswerCard card={msg.card} /> : null}
      {msg.sources && msg.sources.length ? <SourcesList sources={msg.sources} /> : null}
      {msg.isEscalate ? <EscalateCard /> : null}
      {msg.hasFeedback ? (
        <FeedbackRow
          feedback={feedback}
          onUp={onThumbUp}
          onDown={onThumbDown}
          onEscalate={onEscalate}
        />
      ) : null}
    </div>
  );
}
