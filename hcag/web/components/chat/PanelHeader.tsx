import BotAvatar from "./BotAvatar";

const iconBtn: React.CSSProperties = {
  width: 32,
  height: 32,
  borderRadius: 8,
  border: "1px solid var(--line)",
  background: "#fff",
  color: "var(--ink-3)",
  cursor: "pointer",
  display: "grid",
  placeItems: "center",
  fontSize: 13,
  flex: "none",
};

export default function PanelHeader({
  botName,
  statusLine,
  focus,
  onEnterVoice,
  onToggleFocus,
  onMinimize,
  onDismiss,
}: {
  botName: string;
  statusLine: string;
  focus: boolean;
  onEnterVoice: () => void;
  onToggleFocus: () => void;
  onMinimize: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "14px 16px",
        borderBottom: "1px solid var(--line)",
        background: "#fff",
        flex: "none",
      }}
    >
      <div style={{ position: "relative", flex: "none" }}>
        <BotAvatar size={34} />
        <span
          style={{
            position: "absolute",
            right: -1,
            bottom: -1,
            width: 11,
            height: 11,
            borderRadius: 999,
            background: "var(--success)",
            border: "2px solid #fff",
          }}
        />
      </div>
      <div style={{ lineHeight: 1.2, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>{botName}</div>
        <div style={{ fontSize: 12, color: "var(--muted-2)" }}>{statusLine}</div>
      </div>
      <div style={{ display: "flex", gap: 4, marginLeft: "auto", alignItems: "center" }}>
        <button
          type="button"
          title="Switch to voice"
          aria-label="Switch to voice"
          onClick={onEnterVoice}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            border: "1px solid var(--primary-border)",
            background: "var(--primary-tint)",
            color: "var(--primary-text)",
            borderRadius: 999,
            padding: "6px 12px 6px 8px",
            fontSize: 13,
            fontWeight: 700,
            cursor: "pointer",
            flex: "none",
          }}
        >
          <span
            style={{
              width: 20,
              height: 20,
              borderRadius: 999,
              background: "var(--primary)",
              color: "#fff",
              display: "grid",
              placeItems: "center",
              fontSize: 11,
            }}
          >
            ◍
          </span>
          <span>Voice</span>
        </button>
        <button
          type="button"
          title={focus ? "Shrink back to corner" : "Expand to full screen"}
          aria-label={focus ? "Shrink back to corner" : "Expand to full screen"}
          onClick={onToggleFocus}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            border: "1px solid var(--line-3)",
            background: "#fff",
            color: "var(--ink)",
            borderRadius: 999,
            padding: "6px 12px 6px 9px",
            fontSize: 13,
            fontWeight: 700,
            cursor: "pointer",
            flex: "none",
          }}
        >
          <span style={{ fontSize: 13, lineHeight: 1 }}>{focus ? "⤡" : "⤢"}</span>
          <span>{focus ? "Shrink" : "Expand"}</span>
        </button>
        <button type="button" title="Minimise" aria-label="Minimise" onClick={onMinimize} style={iconBtn}>
          —
        </button>
        <button type="button" title="Close" aria-label="Close" onClick={onDismiss} style={iconBtn}>
          ✕
        </button>
      </div>
    </div>
  );
}
