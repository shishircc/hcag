import type { Feedback } from "./types";

function chipStyle(on: boolean): React.CSSProperties {
  return {
    background: on ? "var(--primary)" : "#fff",
    color: on ? "#fff" : "var(--ink-3)",
    border: on ? "1px solid transparent" : "1px solid var(--line-3)",
    borderRadius: 999,
    padding: "5px 12px",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  };
}

export default function FeedbackRow({
  feedback,
  onUp,
  onDown,
  onEscalate,
}: {
  feedback: Feedback;
  onUp: () => void;
  onDown: () => void;
  onEscalate: () => void;
}) {
  const label = feedback ? "Thanks for the feedback." : "Did this answer help?";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
        paddingTop: 2,
      }}
    >
      <span style={{ fontSize: 13, color: "var(--muted-3)", marginRight: 4 }}>{label}</span>
      <button
        type="button"
        aria-label="Helpful"
        onClick={onUp}
        style={chipStyle(feedback === "up")}
      >
        ▲ Yes
      </button>
      <button
        type="button"
        aria-label="Not helpful"
        onClick={onDown}
        style={chipStyle(feedback === "down")}
      >
        ▼ No
      </button>
      <button
        type="button"
        onClick={onEscalate}
        style={{
          background: "transparent",
          border: 0,
          color: "var(--primary)",
          fontSize: 13,
          fontWeight: 600,
          cursor: "pointer",
          textDecoration: "underline",
        }}
      >
        Talk to an officer
      </button>
    </div>
  );
}
