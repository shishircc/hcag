export default function EscalateCard() {
  return (
    <div
      style={{
        border: "1px solid var(--line-2)",
        borderRadius: 12,
        padding: "14px 16px",
        background: "#fff",
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: 4 }}>Connecting you to an officer</div>
      <div style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.55 }}>
        Wait time is about 4 minutes. Your conversation and the pages you viewed will be shared
        with the officer so you do not need to repeat yourself.
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
        <button
          type="button"
          style={{
            background: "var(--primary)",
            color: "#fff",
            border: 0,
            borderRadius: 8,
            padding: "9px 14px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Wait in queue
        </button>
        <button
          type="button"
          style={{
            background: "#fff",
            border: "1px solid var(--line-3)",
            borderRadius: 8,
            padding: "9px 14px",
            cursor: "pointer",
          }}
        >
          Email me instead
        </button>
      </div>
    </div>
  );
}
