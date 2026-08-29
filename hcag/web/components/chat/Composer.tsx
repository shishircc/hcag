import type { KeyboardEvent } from "react";

export default function Composer({
  draft,
  onDraft,
  onSend,
  onEnterVoice,
  focus,
  mobile,
  onDownload,
}: {
  draft: string;
  onDraft: (v: string) => void;
  onSend: () => void;
  onEnterVoice: () => void;
  focus: boolean;
  mobile: boolean;
  onDownload: () => void;
}) {
  const expanded = focus && !mobile;
  const voiceBtnStyle: React.CSSProperties = expanded
    ? {
        display: "flex",
        alignItems: "center",
        gap: 9,
        border: "1px solid var(--primary-border)",
        background: "var(--primary-tint)",
        color: "var(--primary-text)",
        borderRadius: 999,
        padding: "8px 16px 8px 9px",
        fontSize: 15,
        fontWeight: 700,
        cursor: "pointer",
        flex: "none",
      }
    : {
        display: "grid",
        placeItems: "center",
        width: 36,
        height: 36,
        borderRadius: 999,
        border: "1px solid var(--line-2)",
        background: "var(--bg-3)",
        cursor: "pointer",
        flex: "none",
        padding: 0,
      };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && draft.trim()) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div
      style={{
        borderTop: "1px solid var(--line)",
        padding: "12px 14px",
        background: "#fff",
        flex: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 8,
          border: "1px solid var(--line-3)",
          borderRadius: 12,
          padding: "8px 8px 8px 14px",
          background: "#fff",
        }}
      >
        <input
          value={draft}
          onChange={(e) => onDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder="Type your question"
          aria-label="Type your question"
          style={{
            flex: 1,
            border: 0,
            outline: "none",
            fontSize: 15,
            padding: "7px 0",
            minWidth: 0,
          }}
        />
        <button
          type="button"
          aria-label="Talk to us in voice mode"
          title="Talk instead of typing"
          onClick={onEnterVoice}
          style={voiceBtnStyle}
        >
          <span
            style={{
              width: 22,
              height: 22,
              borderRadius: 999,
              background: "var(--primary)",
              color: "#fff",
              display: "grid",
              placeItems: "center",
              fontSize: 12,
              flex: "none",
            }}
          >
            ◍
          </span>
          <span style={{ whiteSpace: "nowrap", display: expanded ? "inline" : "none" }}>
            Talk instead of typing
          </span>
        </button>
        <button
          type="button"
          aria-label="Send"
          onClick={onSend}
          style={{
            width: 36,
            height: 36,
            borderRadius: 999,
            border: 0,
            background: "var(--primary)",
            color: "#fff",
            cursor: "pointer",
            flex: "none",
          }}
        >
          ↑
        </button>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginTop: 8,
          flexWrap: "wrap",
        }}
      >
        <div
          style={{
            fontSize: 12,
            color: "var(--muted-4)",
            lineHeight: 1.4,
            flex: 1,
            minWidth: 140,
          }}
        >
          Answers are drawn from this website. Verify against the linked pages before you apply.
        </div>
        <button
          type="button"
          onClick={onDownload}
          style={{
            background: "transparent",
            border: 0,
            padding: 0,
            color: "var(--primary)",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
            textDecoration: "underline",
            textUnderlineOffset: 2,
            flex: "none",
          }}
        >
          Download full transcript
        </button>
      </div>
    </div>
  );
}
