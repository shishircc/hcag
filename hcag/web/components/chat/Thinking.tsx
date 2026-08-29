export default function Thinking({ label = "Checking work pass pages" }: { label?: string }) {
  const dot = {
    width: 7,
    height: 7,
    borderRadius: 999,
    background: "var(--muted-3)",
    animation: "hcag-dot 1.1s infinite",
  } as const;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        background: "var(--bg-2)",
        border: "1px solid var(--bg-msg-border)",
        padding: "12px 14px",
        borderRadius: "14px 14px 14px 4px",
        alignSelf: "flex-start",
      }}
    >
      <span style={dot} />
      <span style={{ ...dot, animationDelay: "0.15s" }} />
      <span style={{ ...dot, animationDelay: "0.3s" }} />
      <span style={{ fontSize: 13, color: "var(--muted-2)", marginLeft: 4 }}>{label}</span>
    </div>
  );
}
