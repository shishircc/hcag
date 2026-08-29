import type { Source } from "./types";

export default function SourcesList({ sources }: { sources: Source[] }) {
  return (
    <div
      style={{
        borderLeft: "2px solid var(--line-2)",
        paddingLeft: 12,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div
        style={{
          fontSize: 12,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--muted-3)",
          fontWeight: 700,
        }}
      >
        From this site
      </div>
      {sources.map((s, i) => (
        <a
          key={i}
          href={s.url ?? "#"}
          style={{ fontSize: 14, textDecoration: "none", color: "var(--ink)" }}
        >
          <span
            style={{
              textDecoration: "underline",
              textUnderlineOffset: 2,
              color: "var(--primary)",
              fontWeight: 600,
            }}
          >
            {s.title}
          </span>
          <span style={{ color: "var(--muted-3)" }}> — {s.section}</span>
        </a>
      ))}
    </div>
  );
}
