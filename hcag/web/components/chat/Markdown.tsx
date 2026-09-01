"use client";

/**
 * Renders assistant text as Markdown (DESIGN.md §10.3).
 *
 * Not cosmetic: every layer below this one preserves document structure on
 * purpose. `crawl` repairs tables whose header row lost its GFM delimiter
 * (§4.4.1) — a fix that exists only so the structure survives to a Markdown
 * renderer — `preprocess` concatenates that source Markdown verbatim into
 * `## Content` (§3.4.3), and the packet loader ships it unmodified (§2.6).
 * The model quotes it back as lists, tables and numbered procedures. Rendering
 * that as raw text discards all of it at the final hop.
 *
 * Assistant output ONLY. User input is displayed literally — see Message.tsx.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { Components } from "react-markdown";

/**
 * §10.3.2 — assistant text derives from documents crawled off the public web,
 * so it is never trusted markup. Two independent guards: react-markdown does
 * not enable raw HTML (no rehype-raw here, deliberately), and the tree is then
 * filtered through this allowlist. The parser setting alone is not a security
 * boundary.
 */
const schema = {
  ...defaultSchema,
  tagNames: [
    "p", "br", "strong", "em", "del", "blockquote", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "code", "pre", "a", "img",
  ],
  attributes: {
    ...defaultSchema.attributes,
    a: [["href"], ["title"]],
    img: [["src"], ["alt"], ["title"]],
    th: [["align"]],
    td: [["align"]],
    // No syntax highlighting, so the fence's language class buys nothing and
    // is dropped rather than allowlisted.
    code: [],
  },
  // No raw HTML survives to be re-parsed.
  protocols: { ...defaultSchema.protocols, href: ["http", "https", "mailto"] },
};

const SAFE_SCHEMES = ["http:", "https:", "mailto:"];

/** True for a URL the browser can actually resolve from this origin. */
function isAbsoluteSafe(url: string): boolean {
  try {
    return SAFE_SCHEMES.includes(new URL(url).protocol);
  } catch {
    return false; // relative, or not a URL at all
  }
}

/**
 * §10.3.2 — drop `javascript:`, `data:`, `vbscript:` and friends rather than
 * linkifying them. Relative URLs are kept here and handled per element below,
 * because what to do with them differs for links and images.
 */
function urlTransform(url: string): string {
  const trimmed = url.trim();
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed) && !isAbsoluteSafe(trimmed)) return "";
  return trimmed;
}

const components: Components = {
  /**
   * §10.3.4 — absolute links open in a new tab; relative links point at sibling
   * source documents inside the KB tree, not at web pages, so following one
   * would navigate the host page to a 404. Render those as plain text.
   */
  a({ node, href, children, ...props }) {
    void node; // react-markdown passes the hast node; never spread it onto DOM
    if (!href || !isAbsoluteSafe(href)) return <>{children}</>;
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
        {children}
      </a>
    );
  },

  /**
   * §10.3.4 — `preprocess` rewrites every image reference to `assets/<file>`,
   * relative to the packet folder (§3.4.6). Those paths mean nothing to a
   * browser that is not served from the KB tree, and the assets are not on this
   * origin. Say an image exists rather than emitting a request that will fail.
   */
  img({ src, alt }) {
    const label = alt?.trim() || "image";
    if (!src || !isAbsoluteSafe(String(src))) {
      return <span className="hcag-md-img-placeholder">🖼 {label}</span>;
    }
    return <img src={String(src)} alt={label} />;
  },

  /** §10.3.1 — wide tables scroll inside the bubble; the panel never does. */
  table({ node, children, ...props }) {
    void node;
    return (
      <div className="hcag-md-table-wrap">
        <table {...props}>{children}</table>
      </div>
    );
  },
};

export default function Markdown({ text }: { text: string }) {
  return (
    // §10.3.5 — every Markdown rule is scoped to this class so the widget and
    // its host page cannot restyle each other.
    <div className="hcag-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, schema]]}
        urlTransform={urlTransform}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
