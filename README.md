# HCAG — Hierarchical Context Augmented Generation

An LLM agent backed by a hierarchical knowledge base. Instead of flat-index RAG over chunks, HCAG navigates a taxonomy, retrieves whole leaf documents, classifies the problem's branch once, and reuses that retrieval across the reasoning steps of a task.

**See [DESIGN.md](./DESIGN.md) for the complete design — approach, decisions, sequence diagrams, class diagram, tech stack, and CLI semantics.**

## Why HCAG

Flat RAG loses on knowledge-heavy tasks in three ways ([DESIGN.md §1.2](./DESIGN.md#12-what-hcag-solves)):

1. **Knowledge isolation** — every chunk in the corpus competes on every query. HCAG's taxonomy gates 90%+ of the KB out of scope before retrieval.
2. **Complex reasoning** — Flat RAG assembles an answer from partial chunks that have noise making reasoning weaker, while HCAG retrieves high quality leaf documents of relevant taxonomy branch so the model reasons over complete and higher quality input. Retrieval of relevant material for reasoning is different: HCAG uses an LLM to reason over the taxonomy to retrieve compared to RAG's embedding model's similarity search. Reasoning over taxonomy using LLM model for retrieval provides stronger handling of multiple simultaneous constraints and more control over the result. 
3. **Speed and cost** — HCAG classifies the task branch **once** and reuses the same active set across many reasoning steps, keeping the prompt prefix byte-stable for cache hits (90%+ token savings on repeated calls).

## When to Use It

- **Use HCAG** for autonomous customer support over large/complex knowledge, root cause analysis, and autonomous operation workflows — knowledge-heavy tasks with strong reasoning inside a bounded branch.
- **Use Agentic Search** for open-ended research or report generation.
- **Use flat RAG** for small KBs, FAQ-style questions, and MVPs where a 70–80% accuracy ceiling is acceptable.

See [DESIGN.md §1.3](./DESIGN.md#13-when-to-use-hcag-vs-alternatives) for the full comparison.

**Prerequisite:** HCAG's quality is bounded by the quality of the taxonomy. Building a good one is a one-time upfront investment.

## Repo Layout

```
hcag/
├── DESIGN.md              # Full design document
├── hcag/                  # Python package
│   ├── models.py          # Domain DTOs (Catalog, Packet, Delta, ...)
│   ├── config.py          # Pydantic v2 config models
│   ├── logger.py          # JSON-lines file logger
│   ├── tracing.py         # Optional OTEL setup
│   ├── memory/            # Sole KB accessor (D4a)
│   │   ├── storage.py     # KBStorage protocol + LocalFsStorage
│   │   ├── eviction.py    # TokenBudget + LRUEvictionPolicy
│   │   ├── module.py      # FileSystemMemoryModule
│   │   └── packet_loader.py
│   ├── compiled_io.py     # `compiled.md` schema (front-matter + Sub-topics + Content)
│   ├── runtime/           # Agent runtime
│   │   ├── llm.py         # LLM protocol + LiteLLM adapter
│   │   └── agent.py       # AgentRuntime — bootstrap + tool loop
│   ├── cli/               # `hcag` build tool
│   │   ├── preprocess.py  # Single DFS pass: emit compiled.md at every folder
│   │   ├── metadata_llm.py # LLM-generated folder title/short/long
│   │   └── main.py        # Typer entry point
│   ├── crawl/             # `crawl` CLI — mirror sites into a raw KB
│   │   ├── urls.py        # Normalize, prefix-scope, URL → ./kb path
│   │   ├── fetch.py       # httpx wrapper with retries + redirect cap
│   │   ├── html_conv.py   # DOM pre-pass + trafilatura main-content → Markdown
│   │   ├── pdf_conv.py    # PDF → Markdown, extract embedded images
│   │   ├── core.py        # Single-pass BFS + convert + write
│   │   └── main.py        # Typer entry point
│   ├── evalgen/           # `evalgen` CLI — generate eval Q/A from a KB
│   │   ├── kb_scan.py     # Scan packets, paragraphs, image assets
│   │   ├── generators.py  # Per-kind LLM generators + validators
│   │   ├── csv_writer.py  # Fixed 7-column CSV output
│   │   ├── runner.py      # Orchestrate scan → generate → write
│   │   └── main.py        # Typer entry point
│   ├── voice/             # `hcag-voice` — LiveKit voice worker
│   │   ├── config.py      # voice.toml schema (LiveKit + STT + TTS)
│   │   ├── worker.py      # LiveKit room worker + livekit-agents bridge
│   │   ├── session.py     # Per-room VoiceSession orchestrator
│   │   ├── adapters.py    # STT/TTS provider adapters
│   │   ├── startup.py     # Preload initial packets + cache warm-up
│   │   ├── transcription.py
│   │   └── main.py        # Typer entry point
│   ├── server/            # `hcag-server` — FastAPI backend for the web widget
│   │   ├── app.py         # POST /chat, POST /livekit/token, GET /health
│   │   └── main.py        # Typer + uvicorn entry point
│   └── web/               # Next.js chat + voice web widget
│       ├── app/           # App Router pages + /api/{chat,livekit/token} proxies
│       ├── components/    # Host page + chat widget (launcher, panel, voice overlay)
│       ├── lib/           # chat-client, voice-client (LiveKit Room hook)
│       └── README.md      # Frontend + backend run instructions
└── tests/
```

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # add [otel] for tracing, [image] for MIME detection,
                                 # [voice] for the LiveKit voice worker, [web] for the FastAPI backend
```

Requires Python 3.11+.

## LLM Provider Configuration

HCAG uses **[LiteLLM](https://litellm.ai)** under the hood, so it never imports vendor SDKs directly ([DESIGN.md §2.13.8](./DESIGN.md#2138-deliberate-non-dependencies)). Switching providers is config-only.

| Provider | `provider` | Credentials |
|---|---|---|
| Anthropic direct (default) | `anthropic` | `ANTHROPIC_API_KEY` env var |
| AWS Bedrock | `bedrock` | Standard AWS credential chain |
| Ollama (local) | `ollama` | `OLLAMA_API_BASE` (defaults to `http://localhost:11434`) |
| OpenAI-compatible | `openai` | `OPENAI_API_KEY` |

## Building a KB with the `hcag` CLI

Given a raw KB tree of `.md` files and images organized by taxonomy, one command normalizes the whole tree in a single depth-first pass:

```bash
hcag preprocess ./my-kb
```

`preprocess` walks the tree DFS post-order and emits exactly one `compiled.md` per folder — leaf, taxonomy node, mixed, or root. Each file carries the folder's own summary metadata in front-matter, an optional `## Sub-topics` section, and an optional `## Content` section with the folder's own source markdown ([DESIGN.md §3.4](./DESIGN.md#34-hcag-preprocess--detailed-semantics), [§3.7](./DESIGN.md#37-generated-file-format--summary)).

`preprocess` **fails closed**. Every folder needs an LLM call, so the command probes the configured provider before scanning the tree — if the key is unset, the model id is wrong, or the endpoint is unreachable, it exits non-zero having written nothing, naming which of those it was. Once the walk is running, a call that fails after retries aborts the run rather than writing a placeholder summary: because a parent summarizes from its children's descriptions, a placeholder feeds every ancestor above it, and the resulting KB looks complete while its prose is quietly degraded. Aborts are resumable — a plain re-run skips the folders that already succeeded. Pass `--allow-partial` to accept the degraded build instead ([DESIGN.md §3.4.9](./DESIGN.md#349-llm-preflight-and-failure-policy)).

**The `## Sub-topics` section indexes the folder's entire subtree, not just its immediate children.** The DFS return channel carries each folder's summary *and its already-assembled subtree index* up to its parent, which re-parents those records (depth +1, path prefixed) and splices them in — so the index grows as the recursion unwinds and is complete at the root. That is what lets the agent locate any document at any depth from the bootstrap catalog alone, instead of walking the tree one `check_and_load_kb` at a time ([DESIGN.md D3a](./DESIGN.md#d3a-catalogs-roll-up-the-whole-subtree-not-one-level)). Summarization still looks only one level down, so build cost stays at one LLM call per folder. Because aggregation happens on that same return path, there is no separate `hcag aggregate` step — the root's `compiled.md` is the final write of the pass.

Iterate on one branch:

```bash
hcag preprocess ./my-kb --only ./my-kb/billing/refunds --force
# → regenerates that subtree, then re-emits its ancestors up to the root so
#   their ## Sub-topics indexes pick up the change. Mandatory, not an
#   optimization: the root catalog names every folder, so any leaf edit
#   invalidates it.
```

Config is read from `./my-kb/hcag.toml` (optional). Example:

```toml
[llm]
provider = "bedrock"
model    = "bedrock/anthropic.claude-3-5-haiku-20241022-v1:0"
preflight = true           # probe the LLM before the walk; see DESIGN.md §3.4.9
max_retries = 2            # retries per call, exponential backoff, before aborting

[tokenizer]
kind = "tiktoken"

[compiled]
root_id = "_root"          # id used for the root folder if a non-empty one is needed

[catalog]                  # subtree roll-up — see DESIGN.md §3.4.4
max_depth   = 0            # cap roll-up depth; 0 = index the whole subtree
long_depth  = 1            # `long` on entries this deep or shallower
include_tree = true        # compact `#### Tree` outline atop each section
warn_tokens = 40000        # WARN if the ROOT catalog outgrows this

[log]
file_path = "./hcag-build.log"
level     = "INFO"
```

Details: [DESIGN.md §3](./DESIGN.md#part-3--the-hcag-cli-tool).

## Building a Raw KB with the `crawl` CLI

If your source content lives on the web rather than in a local folder, `crawl` mirrors one or more sites into a local Markdown tree that `hcag preprocess` can then consume.

```bash
# Fetch the seed and everything within its prefix, up to 3 hops
crawl --depth 3 https://docs.example.com/api/

# Multiple seeds define a union of allowed prefixes
crawl --depth 2 https://a.example.com/ https://b.example.com/docs/
```

What it does ([DESIGN.md §4](./DESIGN.md#part-4--the-crawl-cli-tool)):

- **Prefix-scoped BFS.** Each seed URL doubles as the site boundary — links are followed only if they begin with a seed's URL, so the crawl never wanders off to unrelated domains or parent paths.
- **Reading-mode main-content extraction.** Every HTML page is reduced to the body an author actually wrote using [trafilatura](https://trafilatura.readthedocs.io/) — the same class of extractor behind browser reading modes ([§4.4.1](./DESIGN.md#441-html--main-content-extraction)). Headings, **bold**/*italic*, lists, tables, code blocks, in-body links, and content images are all preserved; navigation, breadcrumbs, sidebars, cookie banners, comment threads, and footers are dropped. Tune the bias with `--extract-favor {balanced,precision,recall}`, or turn extraction off with `--no-extract`.
- **Links still come from the whole page.** Nav is chrome in the output but is how a site exposes its own structure, so link discovery reads the full DOM even though only the main content is written to disk.
- **HTML and PDF.** Both are fetched and converted to Markdown; embedded images are extracted and rewritten to local paths so pages render offline.
- **Depth-limited.** `--depth N` caps hops from any seed (seed is depth 0). Cycles are broken by a visited-URL set — every in-scope URL is fetched at most once.
- **Mirrored layout.** Output lands under `./kb/<domain>/<url-path>/…`. A page's Markdown goes at the deepest level of its own URL path: a page at `…/topic/subtopic` that has crawled sub-pages is written as `…/topic/subtopic/index.md`, *inside* that folder alongside its children — not as `subtopic.md` next to it. Pages with no crawled sub-pages stay flat as `<segment>.md` in their parent, so a `--depth 4` crawl puts its level-4 pages at level 3 of the tree. This keeps a topic's overview in the same folder — and therefore the same packet — as its detail pages ([DESIGN.md §4.5](./DESIGN.md#45-output-layout)). Extracted images sit beside the Markdown that references them, prefixed with its basename so identically-named images from different pages don't collide.
- **One decision per page, no corpus-level state.** Extraction is the only content decision — no second stripping pass, no cross-page comparison. Pages the extractor can't handle (JS shells, pure link indexes) are written whole-DOM with a `crawl.extract.fallback` warning: a dirty page beats a missing one, and the log says which pages need attention.
- **Small images filtered.** Images below `--min-image-bytes` (default `10240` = 10 KB) are skipped and their Markdown references removed, so inline glyphs and rating stars that survive extraction don't bloat the KB or the downstream index ([§4.4.3](./DESIGN.md#443-images)). Set `--min-image-bytes 0` to keep every image.
- **Structured logging.** JSON-lines log at `./crawl.log` records every fetch, extraction decision (including `retained_pct` per page), write, image extraction, and skip decision (out-of-scope / already-visited / depth-cap / undersized-image). Levels: DEBUG · INFO · WARN · ERROR. Any ERROR exits non-zero.

End-to-end with `hcag`:

```bash
crawl --depth 3 https://docs.example.com/api/
hcag preprocess ./kb
```

Details: [DESIGN.md §4](./DESIGN.md#part-4--the-crawl-cli-tool).

## Indexing a KB for Flat Hybrid Search with the `rag` CLI

`rag` is a peer to `hcag preprocess` — an **alternative** way to make a KB queryable. Where `hcag` normalizes a taxonomy for the runtime agent to navigate, `rag` builds a flat [LanceDB](https://lancedb.github.io/lancedb/) index over the same source content, supporting **hybrid retrieval** (dense vector + BM25 keyword, fused with a reranker).

```bash
rag --kb ./kb --index ./local_lancedb
```

Use it as a **Flat-RAG fallback** the caller composes on top of HCAG ([§1.3.5 combining approaches](./DESIGN.md#135-combining-approaches)), as a **baseline** in `eval` runs, or for ad-hoc grep over the corpus a notebook or retriever service opens directly.

**What gets indexed** ([DESIGN.md §8.2](./DESIGN.md#82-kb-input-model)):

- Every `.md` / `.txt` / `.html` / `.pdf` under `--kb` — chunked with a Markdown-aware windowing strategy that respects heading and paragraph boundaries.
- Every image that lives **outside** an HCAG `assets/` folder — indirectly: `rag` passes each image to a multimodal LLM, embeds the returned text description, and stores a reference back to the original `image_path` so consumers can dereference on hit.

**What's skipped** — to avoid double-counting HCAG's own artifacts:

- `compiled.md` at every folder — it concatenates source content the raw files already carry ([§8.2](./DESIGN.md#82-kb-input-model)).
- Anything under an `assets/` folder that sits alongside a `compiled.md` — the folder's compiled body has already indirectly referenced it.

**Query pattern** ([DESIGN.md §8.6](./DESIGN.md#86-hybrid-search-semantics)) — the CLI produces the index; downstream code queries it:

```python
import lancedb
db  = lancedb.connect("./local_lancedb")
tbl = db.open_table("kb")
hits = (
    tbl.search(query="how do partial refunds work?", query_type="hybrid")
       .rerank(reranker=lancedb.rerankers.RRFReranker())
       .limit(10)
       .to_list()
)
# Each hit exposes: id, kb_path, chunk_index, text, headings, image_path.
```

**Idempotent re-indexing** — `rag` upserts by content-hash-derived `id`s and keeps a `manifest` table of per-file hashes, so a second run against a stable KB is a no-op and a partial edit re-embeds only the touched files. Pass `--recreate` to rebuild from scratch (needed when changing embedding model or chunk parameters).

Config is read from `./rag.toml` (optional) or `--config <path>`. Minimal example:

```toml
[embedding]
provider    = "openai"
model       = "text-embedding-3-small"
api_key_env = "OPENAI_API_KEY"
batch_size  = 32

[image]                                # multimodal LLM used to describe images
provider    = "anthropic"
model       = "claude-haiku-4-5-20251001"
api_key_env = "ANTHROPIC_API_KEY"

[chunking]
target_tokens    = 500
overlap_tokens   = 60
respect_headings = true

[index]
table = "kb"
```

End-to-end from a crawl:

```bash
crawl --depth 3 https://docs.example.com/api/
rag --kb ./kb --index ./local_lancedb
# … or, alongside the HCAG pipeline for the same KB:
hcag preprocess ./kb
```

The two indexes are independent — `hcag preprocess` writes one `compiled.md` per folder alongside the source files; `rag` writes only into `./local_lancedb/`. `rag` deliberately skips every `compiled.md` when it re-scans the tree, so the two can coexist.

Details: [DESIGN.md §8](./DESIGN.md#part-8--the-rag-cli-tool).

## Generating Eval Sets with the `evalgen` CLI

Once a KB is normalized by `hcag preprocess`, `evalgen` produces a CSV of question / expected-answer pairs grounded in that KB — for measuring retrieval and answer quality against the runtime agent.

```bash
# 100 questions total, 20 of each kind
evalgen ./my-kb --out my-kb-eval.csv --total 100 --seed 42

# Or specify per-kind counts explicitly
evalgen ./my-kb --out my-kb-eval.csv \
    --simple 20 --medium 20 --complex 20 --hard-1 20 --hard-2 20 --seed 42
```

Five question kinds ([DESIGN.md §6.4](./DESIGN.md#64-question-types)):

- **`simple`** — FAQ-style, answer appears verbatim in a packet. Measures retrieval.
- **`medium`** — reasoning grounded in a single paragraph of a single packet.
- **`complex`** — deduction across ≥3 distinct paragraphs within one packet.
- **`hard-1`** — cross-packet: needs 2 packets, ≥3 paragraphs total. Measures whether the agent loads a second packet when needed.
- **`hard-2`** — multimodal: needs an image from `assets/` read alongside the packet markdown. Measures the multimodal loading path.

Output is a fixed 7-column CSV: `question_id, kind, question, expected_answer, actual_answer, score, remark`. `evalgen` always leaves the last three columns empty — they are populated by a downstream evaluation harness that runs the agent and scores its responses (0–3).

Config is read from `./my-kb/evalgen.toml` (optional) or `--config <path>`. A strong, multimodal-capable model is recommended:

```toml
[llm]
provider    = "anthropic"
model       = "claude-opus-4-7"
api_key_env = "ANTHROPIC_API_KEY"

[generation]
cross_packet_bias    = "taxonomy"  # bias hard-1 pairs toward taxonomy siblings
paragraph_min_chars  = 120
max_retries_per_item = 2

[log]
file_path = "./evalgen.log"
level     = "INFO"
```

Shortfalls (e.g., requesting more `hard-2` than image-bearing packets available) are logged as `WARN` and reflected in the end-of-run summary; only genuine errors (empty KB, unwritable output, mutually-exclusive flags) cause a non-zero exit.

Details: [DESIGN.md §6](./DESIGN.md#part-6--the-evalgen-cli-tool).

## Scoring the Agent with the `eval` CLI

`eval` closes the loop `evalgen` opens: it runs the CSV question set against a **live** chatbot backend and scores each answer with an LLM-as-judge. It's built on [promptfoo](https://www.promptfoo.dev/) — you get concurrent execution, retries, and an HTML report for free — and speaks to the chatbot over `POST /chat` (the same endpoint `hcag-server` from the [Web Chat and Voice Widget](#web-chat-and-voice-widget) exposes).

```bash
# 1. Bring up the backend under test
hcag-server serve --agent-config ./agent.toml --port 8000

# 2. Score the eval set — writes a completed CSV + an HTML report
eval kb-eval.csv \
    --backend-url http://localhost:8000 \
    --out kb-eval-scored.csv \
    --report kb-eval-report.html \
    --max-turns 5 --concurrency 4 --seed 42
```

For each question:

1. `eval` opens a session and calls `POST /chat` with the question.
2. If the chatbot answers, capture it into `actual_answer`.
3. If the chatbot asks a **clarifying question**, the LLM judge plays the user role, supplies a clarification grounded in `expected_answer` (without leaking it verbatim), and the conversation continues on the same `session_id` until an answer arrives or `--max-turns` is hit ([DESIGN.md §7.4](./DESIGN.md#74-execution-loop)).
4. The full multi-turn transcript is retained for scoring and for the HTML report.

Scoring is a fixed rubric ([DESIGN.md §7.5](./DESIGN.md#75-llm-as-judge-scoring)):

| Score | Meaning |
|---|---|
| `0` | Wrong and misleading answer. |
| `1` | Partially correct, but missing key points. |
| `2` | Partially correct, and includes the key points. |
| `3` | Accurate and comprehensive answer. |

The judge writes a one-sentence justification into `remark`. The judge is stateless per row, so scoring is order-independent and `--concurrency` can fan out safely.

**Two outputs, always written together on completion:**

- **Completed CSV** — same 7-column schema as `evalgen` (`question_id, kind, question, expected_answer, actual_answer, score, remark`), with the last three columns populated. Row order preserved so `diff` between runs is meaningful ([DESIGN.md §7.7](./DESIGN.md#77-output--completed-csv)).
- **HTML report** — run summary, per-kind panels (`simple / medium / complex / hard-1 / hard-2` — each with count, mean score, histogram, pass rate), score distribution across all kinds, row-level table with expandable transcripts, and an optional `--baseline <prior.csv>` comparison bar for regression detection ([DESIGN.md §7.8](./DESIGN.md#78-output--html-report)).

Config lives in `eval.toml` (optional) or via CLI flags. A strong judge model is recommended:

```toml
[backend]
url             = "http://localhost:8000"
request_timeout = 60
session_scope   = "per-question"    # per-question | per-run

[loop]
max_turns = 5

[judge]
provider    = "anthropic"
model       = "claude-opus-4-7"
api_key_env = "ANTHROPIC_API_KEY"

[run]
concurrency = 4
seed        = 42

[report]
baseline = ""                       # optional path to a prior --out CSV
```

End-to-end regression workflow:

```bash
crawl --depth 3 https://docs.example.com/api/
hcag preprocess ./kb
evalgen ./kb --out kb-eval.csv --total 100 --seed 42     # once per KB revision
hcag-server serve --agent-config ./agent.toml --port 8000 &
eval kb-eval.csv --backend-url http://localhost:8000 \
     --out kb-eval-scored.csv --report kb-eval-report.html
```

Commit `kb-eval.csv` alongside the KB revision it was generated from, and re-run `eval` after each agent, prompt, or KB change to detect quality drift. Pass `--baseline kb-eval-scored.prev.csv` to render side-by-side per-kind pass rates and deltas.

Details: [DESIGN.md §7](./DESIGN.md#part-7--the-eval-cli-tool).

## Running the Agent

```python
from hcag.config import AgentConfig
from hcag.runtime import AgentRuntime

cfg = AgentConfig(
    kb_root="./my-kb",
    max_active_tokens=32000,
)
agent = AgentRuntime(cfg=cfg)

reply = agent.run_turn("How do partial refunds work?")
print(reply)
```

Turns come two ways ([DESIGN.md §2.14](./DESIGN.md#214-turn-api--synchronous-and-streaming)): `run_turn()` returns the finished answer, and `run_turn_stream()` yields events as they happen — text deltas plus `tool.start`/`tool.end` naming the packets being loaded. Streaming is the primitive and `run_turn` drains it, so the two cannot drift. Over HTTP that is `POST /chat` (one JSON object) and `POST /chat/stream` (SSE); the chat widget and the voice session both consume the streaming form, and share one event vocabulary across their two transports.

At bootstrap the agent reads the root `compiled.md` and injects its `## Sub-topics` section — the complete index of every folder in the KB, at every depth — into the system prompt. Because the whole hierarchy is visible from turn one, the agent does not navigate the taxonomy: it finds the matching leaf entry in the catalog and requests that id directly via `check_and_load_kb`, however deep it sits. Loading a packet delivers its `## Content` plus any images from `assets/`; a non-root packet's own `## Sub-topics` section is elided on the way out, since it is a verbatim subset of the catalog already in the system prompt. See [DESIGN.md §2.10](./DESIGN.md#210-sequence-diagrams) for the turn-by-turn sequence diagrams.

## Web Chat and Voice Widget

A drop-in web widget that exposes the HCAG agent as a self-service support chatbot with an optional voice mode. Ported from a Claude Design handoff and shipped as a demo Next.js app plus a thin FastAPI backend that wraps `AgentRuntime` and mints LiveKit tokens.

**Layout:**

- `hcag/web/`     — Next.js 14 + React + TypeScript frontend (App Router).
- `hcag/server/`  — FastAPI backend (`hcag-server` CLI).
- Voice ties into the existing `hcag-voice` LiveKit worker — no fork.

**What's in the widget:**

- Minimised **launcher** with an optional "need help?" nudge popover.
- **Docked panel** (400×620) — bot/user bubbles, "Best match" answer card with source citations, escalate-to-officer card, thumbs-up/down feedback, transcript download.
- **Focus mode** — expands to near-fullscreen with a dimmed backdrop.
- **Mobile mode** (412×844 phone frame) with the same panel adapted.
- **Voice overlay** — animated pulse rings, live listening/speaking captions, mute / switch-to-typing / end. Joins a LiveKit room the `hcag-voice` worker is publishing on.

**Run — mock mode (no backend):**

```bash
cd hcag/web
npm install
npm run dev          # http://localhost:3000
```

The chat runs the prototype's scripted 4-turn flow so the UI is immediately explorable. No API keys, no KB, no LiveKit.

**Run — with the real HCAG agent:**

```bash
# Install the backend extras
pip install -e ".[web,voice,dev]"

# Terminal 1 — FastAPI backend
ANTHROPIC_API_KEY=... hcag-server serve \
    --agent-config ./examples/agent.toml \
    --port 8000

# Terminal 2 — Next.js frontend
cd hcag/web
echo 'NEXT_PUBLIC_USE_API=1' >> .env.local
echo 'HCAG_API_URL=http://localhost:8000' >> .env.local
npm run dev
```

`hcag-server` reuses one `AgentRuntime` per `session_id` so the KB catalog and active packet set stay warm across turns — no re-bootstrap per HTTP call.

**Run — with voice enabled (adds LiveKit):**

```bash
# Terminal 3 — LiveKit voice worker (from a previous commit)
export LIVEKIT_URL=wss://your-livekit.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
hcag-voice serve --config ./voice.toml
```

When the user opens voice mode the browser calls `POST /api/livekit/token`, connects to the returned LiveKit URL + room (by convention `hcag-<sessionId>`), publishes its microphone, and subscribes to the agent's audio. The `hcag-voice` worker joins the same room and drives the STT → HCAG → TTS loop; the overlay reflects `ActiveSpeakersChanged` and shows transcriptions if the worker publishes them.

**Backend endpoints (`hcag-server`):**

| Endpoint | Purpose |
|---|---|
| `POST /chat` | `{ session_id, message, history[] }` → `{ text, session_id }`. Runs one `AgentRuntime.run_turn`. |
| `POST /livekit/token` | `{ identity, room? }` → `{ url, token, room }`. Mints a LiveKit access token via `livekit-api`. |
| `GET /health` | Sanity probe — returns `kb_root` and live session count. |

**Wheel packaging:** `hcag/web/{node_modules,.next,out,build,dist}` are excluded from the Python wheel in `pyproject.toml`. The frontend source lives under `hcag/web/` to keep the repo cohesive; the Python backend is a proper submodule at `hcag/server/`.

Full setup + env vars: [`hcag/web/README.md`](./hcag/web/README.md).

## Observability

Two independent layers ([DESIGN.md §2.11](./DESIGN.md#211-observability)):

1. **JSON-lines file log** — always on. Captures every key decision.
2. **OpenTelemetry traces** — off unless a destination is configured. Emits GenAI-semantic-convention spans plus HCAG-specific ones (`tool.*`, `kb.*`, `hcag.*`).

There are two ways to configure that one exporter; set **at most one** ([DESIGN.md §2.11.1](./DESIGN.md#2111-configuration)).

**Direct Langfuse** — the short form. HCAG derives the OTLP endpoint, pins `http/protobuf`, and builds the Basic auth header from your key pair, so there is no URL path or base64 to assemble by hand:

```toml
[observability.langfuse]
host           = "https://cloud.langfuse.com"   # or a regional / self-hosted base URL
public_key_env = "LANGFUSE_PUBLIC_KEY"
secret_key_env = "LANGFUSE_SECRET_KEY"
```

```bash
export LANGFUSE_PUBLIC_KEY=pk-...   # keys come from the environment,
export LANGFUSE_SECRET_KEY=sk-...   # never from the config file
```

**Generic OTLP** — set `otel.endpoint` for AWS CloudWatch (via ADOT), Grafana Tempo, Honeycomb, or any OTLP receiver. Langfuse works here too; the block above is the same thing with the path and header filled in.

Three behaviors worth knowing: configuring **neither** means nothing leaves the process; configuring **both** is a startup error (to send to more than one backend, point `otel.endpoint` at a collector and let it fan out); and a Langfuse block whose key env var is unset is *also* a startup error, rather than silently exporting nothing — writing a key inline in the TOML is rejected outright so it cannot be committed by accident.

Every CLI (`hcag`, `crawl`, `evalgen`, `eval`, `rag`, `hcag-voice`, `hcag-server`) accepts `--verbose` / `-v`, which mirrors the file log to stderr in the same JSON-lines shape. The file sink is unchanged — it stays at whatever level `--log-level` / config specifies — so `--verbose` is purely additive and safe to leave on during development.

## Testing

```bash
pytest -q
```

The suite covers the LRU eviction algorithm, memory module end-to-end, the agent tool loop (with a `FakeLLM` — no network), the crawl core (BFS, dedup, main-content extraction, image size filter), `evalgen`, and the voice startup + transcription paths.

## Design Deep-Dive

Full contents in [DESIGN.md](./DESIGN.md):

- [The HCAG Approach](./DESIGN.md#11-the-hcag-approach)
- [What HCAG Solves](./DESIGN.md#12-what-hcag-solves)
- [When to Use HCAG (vs Alternatives)](./DESIGN.md#13-when-to-use-hcag-vs-alternatives)
- [Key Design Decisions (D1–D10)](./DESIGN.md#18-key-design-decisions)
- [Component Class Diagram](./DESIGN.md#29-component-class-diagram)
- [Sequence Diagrams](./DESIGN.md#210-sequence-diagrams)
- [Observability](./DESIGN.md#211-observability)
- [Tech Stack](./DESIGN.md#213-tech-stack)
- [The `hcag` CLI Tool](./DESIGN.md#part-3--the-hcag-cli-tool)
- [The `crawl` CLI Tool](./DESIGN.md#part-4--the-crawl-cli-tool)
- [The `evalgen` CLI Tool](./DESIGN.md#part-6--the-evalgen-cli-tool)
- [The `eval` CLI Tool](./DESIGN.md#part-7--the-eval-cli-tool)
- [The `rag` CLI Tool](./DESIGN.md#part-8--the-rag-cli-tool)

## License

MIT
