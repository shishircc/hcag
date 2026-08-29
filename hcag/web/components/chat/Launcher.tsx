import type { KeyboardEvent } from "react";
import BotAvatar from "./BotAvatar";

export default function Launcher({
  mobile,
  showNudge,
  hasBadge,
  draft,
  onDraft,
  onSubmit,
  onOpenDocked,
  onEnterVoice,
  onHideNudge,
  onStartTriage,
}: {
  mobile: boolean;
  showNudge: boolean;
  hasBadge: boolean;
  draft: string;
  onDraft: (v: string) => void;
  onSubmit: (text: string) => void;
  onOpenDocked: () => void;
  onEnterVoice: () => void;
  onHideNudge: () => void;
  onStartTriage: () => void;
}) {
  const wrap: React.CSSProperties = mobile
    ? {
        position: "fixed",
        bottom: 24,
        left: "50%",
        transform: "translateX(-50%)",
        width: 380,
        maxWidth: "calc(100vw - 32px)",
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-end",
        gap: 12,
      }
    : {
        position: "fixed",
        right: 24,
        bottom: 24,
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-end",
        gap: 12,
        maxWidth: "calc(100vw - 48px)",
      };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const v = (e.target as HTMLInputElement).value.trim();
    if (e.key === "Enter" && v) {
      e.preventDefault();
      onSubmit(v);
    }
  };

  return (
    <div style={wrap}>
      {showNudge ? (
        <div
          style={{
            background: "#fff",
            border: "1px solid var(--line-2)",
            borderRadius: 12,
            padding: "14px 16px",
            boxShadow: "0 12px 32px rgba(16,20,24,.14)",
            maxWidth: 300,
            animation: "hcag-rise 0.3s ease both",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Need help with work passes?</div>
          <div style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.5 }}>
            Support is online now. Chat or talk it through with us.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button
              type="button"
              onClick={onStartTriage}
              style={{
                background: "var(--primary)",
                color: "#fff",
                border: 0,
                borderRadius: 8,
                padding: "8px 14px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Start chat
            </button>
            <button
              type="button"
              onClick={onHideNudge}
              style={{
                background: "transparent",
                border: 0,
                color: "var(--muted-2)",
                padding: "8px 10px",
                cursor: "pointer",
              }}
            >
              No thanks
            </button>
          </div>
        </div>
      ) : null}

      <div
        style={{
          background: "#fff",
          border: "1px solid var(--line-2)",
          borderRadius: 18,
          boxShadow: "0 16px 44px rgba(16,20,24,.22)",
          width: "min(400px, 100%)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "14px 16px",
            background: "var(--primary)",
            color: "#fff",
          }}
        >
          <div style={{ position: "relative", flex: "none" }}>
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 999,
                background: "#fff",
                overflow: "hidden",
                position: "relative",
              }}
            >
              <span
                style={{
                  position: "absolute",
                  left: "50%",
                  top: 8,
                  transform: "translateX(-50%)",
                  width: 15,
                  height: 15,
                  borderRadius: 999,
                  background: "var(--primary)",
                }}
              />
              <span
                style={{
                  position: "absolute",
                  left: "50%",
                  bottom: -12,
                  transform: "translateX(-50%)",
                  width: 30,
                  height: 24,
                  borderRadius: "999px 999px 0 0",
                  background: "var(--primary)",
                }}
              />
            </div>
            <span
              style={{
                position: "absolute",
                right: -1,
                bottom: -1,
                width: 13,
                height: 13,
                borderRadius: 999,
                background: "var(--success)",
                border: "2px solid var(--primary)",
              }}
            />
            {hasBadge ? (
              <span
                style={{
                  position: "absolute",
                  top: -6,
                  right: -8,
                  minWidth: 20,
                  height: 20,
                  borderRadius: 999,
                  background: "var(--danger)",
                  color: "#fff",
                  fontSize: 12,
                  fontWeight: 700,
                  display: "grid",
                  placeItems: "center",
                  padding: "0 5px",
                  border: "2px solid #fff",
                }}
              >
                1
              </span>
            ) : null}
          </div>
          <div style={{ lineHeight: 1.25, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 16 }}>Work pass support</div>
            <div style={{ fontSize: 13, opacity: 0.85 }}>
              Chat or talk to us — replies in seconds
            </div>
          </div>
        </div>
        <div
          style={{
            padding: "12px 12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              border: "1px solid var(--line-3)",
              borderRadius: 999,
              padding: "6px 6px 6px 16px",
            }}
          >
            <input
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              onKeyDown={onKey}
              onFocus={onOpenDocked}
              placeholder="Type your question"
              aria-label="Type your question"
              style={{
                flex: 1,
                border: 0,
                outline: "none",
                padding: "6px 0",
                fontSize: 15,
                background: "transparent",
                minWidth: 0,
              }}
            />
            <button
              type="button"
              onClick={onOpenDocked}
              aria-label="Start chat"
              style={{
                width: 38,
                height: 38,
                borderRadius: 999,
                border: 0,
                background: "var(--primary)",
                color: "#fff",
                cursor: "pointer",
                flex: "none",
                fontSize: 16,
              }}
            >
              ↑
            </button>
          </div>
          <button
            type="button"
            onClick={onEnterVoice}
            aria-label="Talk to us in voice mode"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 10,
              width: "100%",
              border: "1px solid var(--primary-border)",
              background: "var(--primary-tint)",
              color: "var(--primary-text)",
              borderRadius: 999,
              padding: "11px 16px",
              fontSize: 15,
              fontWeight: 700,
              cursor: "pointer",
            }}
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
              }}
            >
              ◍
            </span>
            <span>Talk instead of typing</span>
          </button>
        </div>
      </div>
    </div>
  );
}
