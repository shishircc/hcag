export default function VoiceOverlay({
  voiceState,
  caption,
  onExit,
  onEnd,
}: {
  voiceState: string;
  caption: string;
  onExit: () => void;
  onEnd: () => void;
}) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "var(--dark-2)",
        color: "#fff",
        zIndex: 5,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "28px 24px 24px",
        borderRadius: 14,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          alignSelf: "stretch",
          fontSize: 13,
          color: "var(--dark-text-2)",
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: 999,
            background: "var(--success-mid)",
          }}
        />
        <span>Voice mode · {voiceState}</span>
        <button
          type="button"
          onClick={onExit}
          aria-label="Close voice mode"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: 0,
            color: "var(--dark-text-2)",
            cursor: "pointer",
            fontSize: 15,
          }}
        >
          ✕
        </button>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 24,
          textAlign: "center",
        }}
      >
        <div
          style={{
            position: "relative",
            width: 108,
            height: 108,
            display: "grid",
            placeItems: "center",
          }}
        >
          <span
            style={{
              position: "absolute",
              inset: 0,
              borderRadius: 999,
              border: "2px solid var(--primary-mid)",
              animation: "hcag-ring 2s infinite",
            }}
          />
          <span
            style={{
              position: "absolute",
              inset: 0,
              borderRadius: 999,
              border: "2px solid var(--primary-mid)",
              animation: "hcag-ring 2s 1s infinite",
            }}
          />
          <span
            style={{
              width: 84,
              height: 84,
              borderRadius: 999,
              background: "var(--primary-fill)",
              display: "grid",
              placeItems: "center",
              fontSize: 30,
            }}
          >
            ◍
          </span>
        </div>
        <div
          style={{
            fontSize: 19,
            lineHeight: 1.5,
            maxWidth: "30ch",
            textWrap: "pretty" as React.CSSProperties["textWrap"],
            minHeight: "3em",
          }}
        >
          {caption}
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button
          type="button"
          style={{
            background: "var(--dark-3)",
            border: "1px solid var(--dark-4)",
            color: "var(--dark-text-4)",
            borderRadius: 999,
            padding: "11px 18px",
            cursor: "pointer",
          }}
        >
          Mute
        </button>
        <button
          type="button"
          onClick={onExit}
          style={{
            background: "#fff",
            border: 0,
            color: "var(--dark-2)",
            borderRadius: 999,
            padding: "11px 18px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Switch to typing
        </button>
        <button
          type="button"
          onClick={onEnd}
          style={{
            background: "var(--danger)",
            border: 0,
            color: "#fff",
            borderRadius: 999,
            padding: "11px 18px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          End
        </button>
      </div>
    </div>
  );
}
