import type { AnswerCard as AnswerCardT } from "./types";

export default function AnswerCard({ card }: { card: AnswerCardT }) {
  return (
    <div
      style={{
        border: "1px solid var(--line-2)",
        borderRadius: 12,
        overflow: "hidden",
        background: "#fff",
      }}
    >
      <div
        style={{
          padding: "14px 16px",
          background: "var(--primary-tint)",
          borderBottom: "1px solid var(--line-2)",
        }}
      >
        <div
          style={{
            fontSize: 12,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: "var(--primary)",
            fontWeight: 700,
          }}
        >
          Best match
        </div>
        <div style={{ fontSize: 19, fontWeight: 700, marginTop: 2 }}>{card.title}</div>
        <div style={{ fontSize: 14, color: "var(--muted)", marginTop: 2 }}>{card.subtitle}</div>
      </div>
      <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        {card.bullets.map((b, i) => (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: "18px 1fr",
              gap: 8,
              fontSize: 14,
              lineHeight: 1.5,
              color: "var(--ink-2)",
            }}
          >
            <span style={{ color: "var(--success-dark)", fontWeight: 700 }}>✓</span>
            <span>{b.text}</span>
          </div>
        ))}
      </div>
      <div style={{ padding: "0 16px 16px", display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          style={{
            background: "var(--primary)",
            color: "#fff",
            border: 0,
            borderRadius: 8,
            padding: "10px 16px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Check eligibility in full
        </button>
        <button
          type="button"
          style={{
            background: "#fff",
            border: "1px solid var(--line-3)",
            borderRadius: 8,
            padding: "10px 16px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Compare with S Pass
        </button>
      </div>
    </div>
  );
}
