import type { Device } from "./types";

function chip(on: boolean): React.CSSProperties {
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

export default function DevBar({
  device,
  onDesktop,
  onMobile,
  onReset,
}: {
  device: Device;
  onDesktop: () => void;
  onMobile: () => void;
  onReset: () => void;
}) {
  const mobile = device === "mobile";
  return (
    <div
      style={{
        position: "fixed",
        top: 12,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 60,
        display: "flex",
        gap: 6,
        alignItems: "center",
        background: "var(--dark)",
        color: "#fff",
        padding: "6px 8px",
        borderRadius: 999,
        boxShadow: "0 6px 24px rgba(0,0,0,.28)",
        fontSize: 13,
      }}
    >
      <span
        style={{
          padding: "0 6px",
          opacity: 0.6,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          fontSize: 11,
        }}
      >
        Prototype
      </span>
      <button type="button" onClick={onDesktop} style={chip(!mobile)}>
        Desktop
      </button>
      <button type="button" onClick={onMobile} style={chip(mobile)}>
        Mobile
      </button>
      <button
        type="button"
        onClick={onReset}
        style={{
          background: "transparent",
          color: "var(--dark-text-3)",
          border: "1px solid var(--dark-4)",
          padding: "5px 12px",
          borderRadius: 999,
          cursor: "pointer",
        }}
      >
        Reset
      </button>
    </div>
  );
}
