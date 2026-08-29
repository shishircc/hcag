import type { CSSProperties } from "react";

type Row = { title: string; blurb: string };

const professionals: Row[] = [
  {
    title: "Employment Pass",
    blurb:
      "For foreign professionals, managers and executives earning at least $5,600 a month who pass the assessment framework.",
  },
  {
    title: "EntrePass",
    blurb:
      "For foreign entrepreneurs starting a venture-backed business or one with innovative technologies.",
  },
  {
    title: "Personalised Employment Pass",
    blurb:
      "For high-earning Employment Pass holders or overseas professionals who need greater flexibility.",
  },
];

const skilled: Row[] = [
  {
    title: "S Pass",
    blurb:
      "For skilled workers earning at least $3,300 a month, subject to sector quota and levy.",
  },
  {
    title: "Work Permit for migrant worker",
    blurb:
      "For skilled and semi-skilled workers in construction, manufacturing, marine shipyard, process or services.",
  },
  {
    title: "Work Permit for domestic worker",
    blurb:
      "For migrant domestic workers, including confinement nannies employed for up to 16 weeks.",
  },
];

const family: Row[] = [
  {
    title: "Dependant's Pass",
    blurb: "For spouses and children of eligible Employment Pass or S Pass holders.",
  },
  {
    title: "Long-Term Visit Pass",
    blurb: "For parents, common-law spouses or step-children of eligible pass holders.",
  },
];

const related = [
  "Foreign workforce framework",
  "How do I submit work pass-related requests?",
  "Employment agencies",
];

const shellStyle: CSSProperties = {
  position: "relative",
  width: "100%",
  minHeight: "100vh",
  background: "#fff",
};

const rowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "240px 1fr",
  gap: 24,
  padding: "16px 0",
  borderBottom: "1px solid var(--line)",
};

function Section({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <>
      <h2 style={{ fontSize: 22, margin: "0 0 12px", fontWeight: 700 }}>{title}</h2>
      <div style={{ borderTop: "2px solid var(--ink)", marginBottom: 40 }}>
        {rows.map((r) => (
          <div key={r.title} style={rowStyle}>
            <a href="#" style={{ fontWeight: 600 }}>
              {r.title}
            </a>
            <p style={{ margin: 0, color: "var(--ink-2)", lineHeight: 1.55 }}>{r.blurb}</p>
          </div>
        ))}
      </div>
    </>
  );
}

export default function HostPage() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        background: "var(--bg)",
      }}
    >
      <div style={shellStyle}>
        {/* Top bar */}
        <div style={{ background: "#fff" }}>
          <div
            style={{
              background: "var(--dark)",
              color: "var(--dark-text)",
              fontSize: 13,
              padding: "7px 24px",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span>A government agency website</span>
            <span>Last updated 01 June 2026</span>
          </div>
          <div
            style={{
              padding: "16px 24px",
              borderBottom: "1px solid var(--line)",
              display: "flex",
              alignItems: "center",
              gap: 16,
              flexWrap: "wrap",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: 6,
                  background: "var(--primary)",
                  color: "#fff",
                  display: "grid",
                  placeItems: "center",
                  fontWeight: 700,
                  fontSize: 14,
                }}
              >
                WP
              </div>
              <div style={{ lineHeight: 1.15 }}>
                <div style={{ fontWeight: 700, fontSize: 16 }}>Work Pass Authority</div>
                <div style={{ fontSize: 12, color: "var(--muted-2)" }}>Passes and permits</div>
              </div>
            </div>
            <div
              style={{
                display: "flex",
                gap: 20,
                marginLeft: "auto",
                fontSize: 15,
                flexWrap: "wrap",
              }}
            >
              <a
                href="#"
                style={{
                  color: "var(--ink)",
                  textDecoration: "none",
                  borderBottom: "3px solid var(--primary)",
                  paddingBottom: 2,
                  fontWeight: 600,
                }}
              >
                Work passes
              </a>
              <a href="#" style={{ color: "var(--ink-3)", textDecoration: "none" }}>
                Employment practices
              </a>
              <a href="#" style={{ color: "var(--ink-3)", textDecoration: "none" }}>
                Safety and health
              </a>
              <a href="#" style={{ color: "var(--ink-3)", textDecoration: "none" }}>
                eServices
              </a>
            </div>
          </div>
        </div>

        {/* Breadcrumb */}
        <div
          style={{
            background: "var(--bg-3)",
            padding: "10px 24px",
            fontSize: 13,
            color: "var(--muted-2)",
            borderBottom: "1px solid var(--line)",
          }}
        >
          <a href="#" style={{ color: "var(--muted-2)" }}>
            Home
          </a>{" "}
          <span style={{ padding: "0 6px" }}>/</span> Work passes
        </div>

        {/* Content */}
        <div style={{ background: "#fff", padding: "40px 24px 56px" }}>
          <div style={{ maxWidth: 860, margin: "0 auto" }}>
            <h1
              style={{
                fontSize: 40,
                lineHeight: 1.1,
                margin: "0 0 16px",
                fontWeight: 700,
                letterSpacing: "-0.01em",
              }}
            >
              Work passes
            </h1>
            <p
              style={{
                fontSize: 18,
                lineHeight: 1.6,
                color: "var(--ink-2)",
                margin: "0 0 40px",
                maxWidth: "68ch",
                textWrap: "pretty" as CSSProperties["textWrap"],
              }}
            >
              All foreigners who intend to work here must hold a valid pass before they start
              work. If you are engaging foreigners to work, you must ensure they hold a valid
              pass. Find out which pass is suitable, whether they are eligible, and how to apply.
            </p>

            <Section title="Professionals" rows={professionals} />
            <Section title="Skilled and semi-skilled workers" rows={skilled} />
            <Section title="Family members" rows={family} />

            <div
              style={{
                background: "var(--bg-3)",
                border: "1px solid var(--line)",
                padding: "20px 24px",
              }}
            >
              <h2 style={{ fontSize: 16, margin: "0 0 12px", fontWeight: 700 }}>Related</h2>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {related.map((label) => (
                  <a key={label} href="#">
                    {label}
                  </a>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            background: "var(--dark)",
            color: "var(--dark-text-2)",
            padding: "32px 24px",
            fontSize: 13,
            display: "flex",
            gap: 20,
            flexWrap: "wrap",
          }}
        >
          <span>© 2026 Work Pass Authority</span>
          <a href="#" style={{ color: "var(--dark-text-2)" }}>
            Privacy
          </a>
          <a href="#" style={{ color: "var(--dark-text-2)" }}>
            Terms of use
          </a>
          <a href="#" style={{ color: "var(--dark-text-2)" }}>
            Contact us
          </a>
        </div>
      </div>
    </div>
  );
}
