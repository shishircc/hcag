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

## Getting Started

A complete run of the whole pipeline against two real seed URLs — Singapore MOM's Employment Pass and Overseas Networks & Expertise Pass sections. Crawl → build → generate an eval set → score it → explore in the browser. Every command below is meant to be run in order from an empty directory.

### 0. Prerequisites

| Need | Why | Required for |
|---|---|---|
| **Python 3.11+** | Everything. | all steps |
| **An Anthropic API key** | `hcag`, `evalgen`, `evalrun` and the agent all call an LLM. | steps 2–8 |
| **Node 18+** | `evalrun` drives [promptfoo](https://www.promptfoo.dev/) through `npx`; the web widget is Next.js. | steps 7, 8 |
| **Docker** | Only to run a local Langfuse for traces. Langfuse Cloud, another OTLP backend, or no tracing at all all work instead. | step 5a (optional) |
| **A LiveKit account** | Voice mode only. Skip it and everything else still works. | step 9 (optional) |

```bash
git clone <this-repo> hcag && cd hcag
python -m venv .venv && source .venv/bin/activate
pip install -e ".[web,dev]"          # add ,voice for step 9; ,otel for tracing
export ANTHROPIC_API_KEY=sk-ant-...  # every LLM step reads this from the environment
```

The provider is config, not code — `provider = "bedrock"` or `"ollama"` in the files below runs the same pipeline without an Anthropic key ([LLM Provider Configuration](#llm-provider-configuration)).

### 1. Crawl the two seed sections

Both URLs live on one host, so they are one crawl. Each seed also defines a prefix scope: pages are followed only under a seed's path, while **PDFs and images are fetched wherever they live** ([DESIGN.md §4.3.4](./DESIGN.md#434-asset-scope)) — on this site the COMPASS PDFs sit under `/-/media/`, entirely outside both seeds.

```bash
crawl --depth 3 --output ./kb \
    https://www.mom.gov.sg/passes-and-permits/employment-pass \
    https://www.mom.gov.sg/passes-and-permits/overseas-networks-expertise-pass
```

`crawl` prints each URL as it fetches and ends with a report of what was included and what was skipped, grouped by reason. Expect roughly 35 pages, ~20 PDFs and a handful of images; the counts drift as MOM edits the site. Skipped URLs are not failures — `out-of-scope` and `already-visited` dominate a healthy run.

The domain becomes the first folder under `--output`, so you now have:

```
kb/
└── www.mom.gov.sg/
    └── passes-and-permits/
        ├── employment-pass/
        │   ├── index.md
        │   ├── eligibility/
        │   └── ...
        └── overseas-networks-expertise-pass/
```

`./kb` is the **KB root** from here on. Every step below points at it.

<details>
<summary>Useful crawl flags when adapting this to your own site</summary>

- `--depth N` — link-following depth from each seed. `3` reaches this site's third-level pages; higher pulls in more of the site and costs more in step 3.
- `--min-image-bytes 10240` — skip logos and icons. Lower it if your images are small but meaningful.
- `--asset-hosts cdn.example.com` — allow assets from a media subdomain.
- `--no-extract` — keep the whole DOM instead of just the article body, when extraction gets a page wrong.
</details>

### 2. Configure the build

`hcag` reads `hcag.toml` from the KB root. Start from the sample:

```bash
cp examples/kb-example/hcag.toml ./kb/hcag.toml
```

Defaults are fine for this walkthrough. The one field worth a look is the build model — it runs once per folder, so it is the cost lever:

```toml
[llm]
provider    = "anthropic"
model       = "claude-opus-4-6"
api_key_env = "ANTHROPIC_API_KEY"
preflight   = true      # prove the LLM works before writing anything (§3.4.9)
```

### 3. Build the KB

```bash
hcag ./kb
```

One DFS pass writes a `compiled.md` into every folder — this KB has about 23 of them, one LLM call each. Each file carries that folder's own content plus a catalog of its **entire subtree**, so `kb/compiled.md` ends up a complete index of the KB and the agent can reach any document in one hop ([DESIGN.md §3.4](./DESIGN.md#34-hcag--detailed-semantics)).

The build **fails closed**: if the LLM is unreachable it aborts at startup, before scanning the tree or writing a byte. If a single folder cannot be summarized mid-walk it aborts there rather than filling the rest of the tree with placeholders, because a placeholder feeds every ancestor's summary and the result is indistinguishable from a good build by inspection. Pass `--allow-partial` only when you want that trade.

```bash
head -40 kb/compiled.md    # the root catalog the agent will hold in its system prompt
```

Re-running is incremental. Use `hcag ./kb --force` to regenerate everything after changing prompts or the model.

### 4. Generate a 100-question eval set

```bash
cp examples/evalgen.toml ./kb/evalgen.toml
evalgen ./kb --out kb-eval.csv --total 100 --seed 42
```

`--total 100` splits evenly across the five kinds — 20 each of `simple`, `medium`, `complex`, `hard-1` (cross-packet) and `hard-2` (multimodal, reading an image or PDF page). `hard-2` needs a multimodal model; the sample config's default is one.

This is the slowest and most expensive step in the walkthrough — one generation per question, with retries on validation failure. `--seed 42` makes it reproducible. Start with `--total 25` if you just want to see the shape of the output.

The result is an 8-column CSV: `question_id, kind, question, expected_answer, source, actual_answer, score, remark`. The last three are empty — step 7 fills them. `source` carries the URLs of the packets and images each question was built from, so you can check any expected answer against the page it came from.

Expected answers are held to a completeness standard ([DESIGN.md §6.4.0](./DESIGN.md#640-expected-answers-must-be-complete)) — an answer that is true but omits a condition is scored as wrong, not merely terse, because a chatbot reproducing only the first clause would otherwise grade as correct.

### 5. Configure and start the agent backend

```bash
cp examples/agent.toml ./agent.toml
```

The only field you must check is `kb_root` — it defaults to `./kb`, already correct if you followed step 1.

```bash
hcag-server --agent-config ./agent.toml --port 8000
```

The sample ships with **no trace destination configured**, so this starts with nothing to set up. Tracing is entirely optional and off by default; see [step 5a](#5a-observability-optional-but-worth-it) if you want it.

Check it:

```bash
curl -s localhost:8000/health        # -> kb_root and live session count
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id":"t1","message":"What is the minimum monthly salary for an Employment Pass?","history":[]}'
```

Leave it running — steps 7 and 8 both talk to it.

### 5a. Observability (optional, but worth it)

Everything above works with no tracing at all. It is worth ten minutes anyway: HCAG's whole premise is *which packets did the agent load, and did the prompt actually contain the answer* — questions a trace answers directly and a log file answers awkwardly. Spans carry the full prompt, the loaded packet set, tool calls and token counts ([DESIGN.md §2.11](./DESIGN.md#211-observability)).

**Option A — Langfuse, the quickest path.** Best fit for this project: it renders a turn as a generation with input and output side by side, which is exactly the view you want when an eval row scored 0 and you need to know whether the agent had the right packet.

```bash
# A local instance, if you don't already have one — not mandatory
git clone https://github.com/langfuse/langfuse && cd langfuse && docker compose up -d
# -> http://localhost:3000, create a project, copy the key pair
```

Then uncomment `[observability.langfuse]` in `agent.toml` and export the keys (Langfuse Cloud is the same with `host = "https://cloud.langfuse.com"`):

```toml
[observability.langfuse]
host           = "http://localhost:3000"
public_key_env = "LANGFUSE_PUBLIC_KEY"
secret_key_env = "LANGFUSE_SECRET_KEY"
```

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

HCAG derives the OTLP endpoint, pins `http/protobuf`, and builds the Basic auth header itself — no URL path or base64 to assemble by hand.

**Option B — any OTLP endpoint.** Use this for AWS (an [ADOT](https://aws-otel.github.io/) collector forwarding to CloudWatch, which is also how you'd ship traces alongside a Bedrock-hosted model), Grafana Tempo, Honeycomb, or a collector of your own. Set `otel.endpoint` to the **base** URL; HCAG appends `/v1/traces`:

```toml
[observability.otel]
endpoint     = "http://localhost:4318"
protocol     = "http/protobuf"
service_name = "hcag-agent"
```

Both forms drive the same single exporter. Configure **at most one** — configuring both is a startup error, on purpose, because traces would otherwise go somewhere you did not choose; to fan out to two backends, point `otel.endpoint` at a collector and let it do that.

```bash
pip install -e ".[otel]"       # the exporter is an optional extra
```

**If tracing is misconfigured, the agent still runs.** A missing key or an unreachable exporter prints a loud `WARNING: tracing disabled: ...` on stderr, logs it at `ERROR`, and serves turns with tracing off. Answering questions does not depend on being able to report on it — but you are told immediately, because traces you think you are collecting and aren't is the failure worth being noisy about.

### 6. Configure the scorer

In a second terminal (same virtualenv):

```bash
source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-ant-...
cp examples/evalrun.toml ./evalrun.toml
```

`evalrun.toml` is read from the **current working directory**, not the KB — `evalrun` never sees a KB, only a CSV and a backend URL. Run it from elsewhere and it silently falls back to defaults, so it prints both model names on stderr when no config is found.

Two models are configured here, and they are commonly keyed separately: a cheap `[classifier.llm]` that decides whether a reply is an answer, a clarifying question or a refusal, and a strong `[judge.llm]` that scores. Both preflight at startup.

### 7. Score the agent

```bash
evalrun kb-eval.csv \
    --backend-url http://localhost:8000 \
    --out kb-eval-scored.csv \
    --report kb-eval-report.html \
    --max-turns 5 --concurrency 4 --seed 42
```

The first run downloads promptfoo through `npx`, so give it a minute. Before dispatching a single row `evalrun` probes `/health`, loads its prompts, and preflights both models — a bad judge key otherwise surfaces only after the entire run has been paid for against the backend, and fails every row identically.

```bash
open kb-eval-report.html    # per-kind panels, score histogram, expandable transcripts
```

Keep `kb-eval-scored.csv`. On the next run, `--baseline kb-eval-scored.csv` renders per-kind deltas so you can see whether a prompt or KB change helped.

### 8. Explore in the browser

```bash
cd hcag/web
npm install
cat > .env.local <<'EOF'
NEXT_PUBLIC_USE_API=1
HCAG_API_URL=http://localhost:8000
EOF
npm run dev            # http://localhost:3000
```

The widget answers from the running backend, renders Markdown (the KB is full of tables, and this is the hop where that structure would otherwise be thrown away), and streams tokens over SSE. Omit `NEXT_PUBLIC_USE_API=1` and it runs a scripted mock flow with no backend, no keys and no KB — useful for looking at the UI alone.

Ask it something the eval set got wrong and watch the tool calls in `hcag-agent.log` to see which packets it loaded.

### 9. Voice mode (optional)

There is no sample `voice.toml` in `examples/`, so write a minimal one. `kb_root` is the only required field; everything else has a default:

```bash
pip install -e ".[voice]"
cat > voice.toml <<'EOF'
kb_root = "./kb"

[livekit]
url = "wss://your-project.livekit.cloud"

[llm]
provider    = "anthropic"
model       = "claude-haiku-4-5-20251001"
api_key_env = "ANTHROPIC_API_KEY"
EOF
```

Credentials come from the environment. The LiveKit **URL** is config (or `--livekit-url`), not an env var — only the key pair is:

```bash
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
export DEEPGRAM_API_KEY=...          # STT
export ELEVENLABS_API_KEY=...        # TTS

hcag-voice dry-run --config ./voice.toml    # bootstrap + warm-up, no room joined
hcag-voice serve --config ./voice.toml
```

`dry-run` prints the resolved plan without joining a room — the fast way to check credentials and preloaded packets before starting the worker.

Add `NEXT_PUBLIC_LIVEKIT_URL=wss://your-project.livekit.cloud` to `hcag/web/.env.local` and the widget's voice button becomes live.

### What you should have

| File | From | What it is |
|---|---|---|
| `kb/` | step 1 | Crawled Markdown mirror, images and PDFs included |
| `kb/**/compiled.md` | step 3 | One packet per folder; the root is the whole-KB index |
| `kb-eval.csv` | step 4 | 100 questions with complete expected answers and provenance |
| `kb-eval-scored.csv` | step 7 | The same rows with the agent's answers and 0–3 scores |
| `kb-eval-report.html` | step 7 | Per-kind pass rates and transcripts |

Commit `kb-eval.csv` next to the KB revision it came from, and re-run steps 5–7 after any agent, prompt or KB change to catch quality drift.

## Repo Layout

```
hcag/
├── DESIGN.md              # Full design document
├── hcag/                  # Python package
│   ├── models.py          # Domain DTOs (Catalog, Packet, Delta, ...)
│   ├── config.py          # Pydantic v2 config models
│   ├── logger.py          # JSON-lines file logger
│   ├── tracing.py         # Optional OTEL setup (generic OTLP or direct Langfuse)
│   ├── prompting.py       # Prompt loader — names in code, text in files (D11)
│   ├── prompts/           # The prompt files themselves; overridable per prompt
│   ├── memory/            # Sole KB accessor (D4a)
│   │   ├── storage.py     # KBStorage protocol + LocalFsStorage
│   │   ├── eviction.py    # TokenBudget + LRUEvictionPolicy
│   │   ├── module.py      # FileSystemMemoryModule
│   │   └── packet_loader.py
│   ├── compiled_io.py     # `compiled.md` schema (front-matter + Sub-topics + Content)
│   ├── runtime/           # Agent runtime
│   │   ├── llm.py         # LLM protocol + LiteLLM adapter
│   │   └── agent.py       # AgentRuntime — bootstrap + tool loop
│   ├── cli/               # `hcag` build tool (no subcommand)
│   │   ├── preprocess.py  # Single DFS pass: emit compiled.md at every folder
│   │   ├── metadata_llm.py # LLM-generated folder title/short/long
│   │   └── main.py        # Typer entry point
│   ├── crawl/             # `crawl` CLI — mirror sites into a raw KB
│   │   ├── urls.py        # Normalize, scope, URL → ./kb path, sidecar, leaf collapse
│   │   ├── fetch.py       # httpx wrapper with retries + redirect cap
│   │   ├── html_conv.py   # DOM pre-pass + trafilatura main-content → Markdown
│   │   ├── pdf_conv.py    # PDF → Markdown via PyMuPDF4LLM (GFM tables), images deduped
│   │   ├── core.py        # Single-pass BFS + convert + write
│   │   └── main.py        # Typer entry point
│   ├── evalgen/           # `evalgen` CLI — generate eval Q/A from a KB
│   │   ├── kb_scan.py     # Scan packets, paragraphs, image assets
│   │   ├── generators.py  # Per-kind LLM generators + validators
│   │   ├── csv_writer.py  # Fixed 8-column CSV output (incl. `source`)
│   │   ├── runner.py      # Orchestrate scan → generate → write
│   │   └── main.py        # Typer entry point
│   ├── eval/              # `evalrun` CLI — score an eval set against a live backend
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
hcag ./my-kb
```

There is no subcommand — building a KB is the only thing this CLI does, so there is no verb to choose. Flags scope a run instead: `--only`, `--force`, `--allow-partial`.

`hcag` walks the tree DFS post-order and emits exactly one `compiled.md` per folder — leaf, taxonomy node, mixed, or root. Each file carries the folder's own summary metadata in front-matter, an optional `## Sub-topics` section, and an optional `## Content` section with the folder's own source markdown ([DESIGN.md §3.4](./DESIGN.md#34-hcag--detailed-semantics), [§3.7](./DESIGN.md#37-generated-file-format--summary)).

`hcag` **fails closed**. Every folder needs an LLM call, so the command probes the configured provider before scanning the tree — if the key is unset, the model id is wrong, or the endpoint is unreachable, it exits non-zero having written nothing, naming which of those it was. Once the walk is running, a call that fails after retries aborts the run rather than writing a placeholder summary: because a parent summarizes from its children's descriptions, a placeholder feeds every ancestor above it, and the resulting KB looks complete while its prose is quietly degraded. Aborts are resumable — a plain re-run skips the folders that already succeeded. Pass `--allow-partial` to accept the degraded build instead ([DESIGN.md §3.4.9](./DESIGN.md#349-llm-preflight-and-failure-policy)).

**The `## Sub-topics` section indexes the folder's entire subtree, not just its immediate children.** The DFS return channel carries each folder's summary *and its already-assembled subtree index* up to its parent, which re-parents those records (depth +1, path prefixed) and splices them in — so the index grows as the recursion unwinds and is complete at the root. That is what lets the agent locate any document at any depth from the bootstrap catalog alone, instead of walking the tree one `check_and_load_kb` at a time ([DESIGN.md D3a](./DESIGN.md#d3a-catalogs-roll-up-the-whole-subtree-not-one-level)). Summarization still looks only one level down, so build cost stays at one LLM call per folder. Because aggregation happens on that same return path, there is no separate `hcag aggregate` step — the root's `compiled.md` is the final write of the pass.

**Two things decide the quality of the catalog it builds**, and both were arrived at by watching it go wrong:

- **A parent summarizes from its children's *long* descriptions, never their one-line shorts** ([§3.4.4](./DESIGN.md#344-catalog-section-content-subtree-roll-up)). Summarization here is iterated — the root's description is a summary of summaries — so feeding one-line labels upward compounds the loss at every level, and a branch genuinely about "SAML assertion mapping, certificate rotation, IdP metadata exchange" arrives at the root as "authentication settings". Cost is unchanged: still one LLM call per folder.
- **A folder's description describes *that folder*, not its children.** Before the subtree roll-up a parent had to advertise what lay below it; now every descendant has its own catalog entry, so borrowing their specifics only creates false matches — and the child's description is often the *stronger* keyword match, because particulars are what queries contain.

**Sources are concatenated in reading order** ([§3.4.3](./DESIGN.md#343-compiledmd-assembly)): the folder's own `index.md` leads, then the order its index page linked the rest (from the crawl sidecar, which sees the full DOM and so survives extraction dropping a hub's link list), then anything unmentioned alphabetically. A packet is one document an LLM reads top to bottom, so concatenation order *is* reading order — alphabetical opens a work-pass topic on `appeal-against-a-rejected-application`.

Iterate on one branch:

```bash
hcag ./my-kb --only ./my-kb/billing/refunds --force
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

If your source content lives on the web rather than in a local folder, `crawl` mirrors one or more sites into a local Markdown tree that `hcag` can then consume.

```bash
# Fetch the seed and everything within its prefix, up to 3 hops
crawl --depth 3 https://docs.example.com/api/

# Multiple seeds define a union of allowed prefixes
crawl --depth 2 https://a.example.com/ https://b.example.com/docs/
```

What it does ([DESIGN.md §4](./DESIGN.md#part-4--the-crawl-cli-tool)):

- **Prefix-scoped BFS — for pages.** Each seed URL doubles as the site boundary; links are followed only if they begin with a seed's URL, so the crawl never wanders off to unrelated domains or parent paths.
- **Assets ignore the prefix.** A PDF or image *referenced by* an in-scope page is fetched whatever its path, and the depth limit does not apply to it either ([§4.3.4](./DESIGN.md#434-asset-scope)). Sites file assets where the CMS puts them: on `mom.gov.sg` **all 17** linked PDFs live under a `/-/media/…` root and **none** is inside the seed prefix, so scoping them by path drops every cited primary source rather than filtering any. Assets are terminal — nothing is discovered *through* one — so this cannot expand the crawl. The host bound still applies: same host as the citing page, widened with `--asset-hosts`.
- **Reading-mode main-content extraction.** Every HTML page is reduced to the body an author actually wrote using [trafilatura](https://trafilatura.readthedocs.io/) — the same class of extractor behind browser reading modes ([§4.4.1](./DESIGN.md#441-html--main-content-extraction)). Headings, **bold**/*italic*, lists, tables, code blocks, in-body links, and content images are all preserved; navigation, breadcrumbs, sidebars, cookie banners, comment threads, and footers are dropped. Tune the bias with `--extract-favor {balanced,precision,recall}`, or turn extraction off with `--no-extract`.
- **Links still come from the whole page.** Nav is chrome in the output but is how a site exposes its own structure, so link discovery reads the full DOM even though only the main content is written to disk.
- **HTML and PDF.** Both are fetched and converted to Markdown; embedded images are extracted and rewritten to local paths so pages render offline. PDFs go through [PyMuPDF4LLM](https://pymupdf.readthedocs.io/), which **reconstructs tables as GFM** ([§4.4.2](./DESIGN.md#442-pdf)) — on a 24-document sample that recovers 684 tables where the previous text-layer extractor produced none. It matters for correctness, not looks: a flattened table with vertically merged cells silently drops the qualifiers that apply to its rows. *(PyMuPDF is AGPL-3.0 or an Artifex commercial licence — the one non-permissive dependency; see [§2.13.6](./DESIGN.md#2136-dependency-summary).)*
- **A page wrapped in one `<form>` still extracts.** ASP.NET WebForms puts the whole body inside `<form runat="server">`, and reading-mode extractors discard form subtrees as chrome — taking the article with them. Such a wrapper is unwrapped before extraction ([§4.4.1](./DESIGN.md#441-html--main-content-extraction)); on the page that motivated it, extraction went from 10.5k characters to 24k and from 5 tables to 14.
- **Depth-limited.** `--depth N` caps hops from any seed (seed is depth 0). Cycles are broken by a visited-URL set — every in-scope URL is fetched at most once.
- **Mirrored layout.** Output lands under `./kb/<domain>/<url-path>/…`. A page's Markdown goes at the deepest level of its own URL path: a page at `…/topic/subtopic` that has crawled sub-pages is written as `…/topic/subtopic/index.md`, *inside* that folder alongside its children — not as `subtopic.md` next to it. Pages with no crawled sub-pages stay flat as `<segment>.md` in their parent, so a `--depth 4` crawl puts its level-4 pages at level 3 of the tree. This keeps a topic's overview in the same folder — and therefore the same packet — as its detail pages ([DESIGN.md §4.5](./DESIGN.md#45-output-layout)). Extracted images sit beside the Markdown that references them, prefixed with its basename so identically-named images from different pages don't collide.
- **One decision per page, no corpus-level state.** Extraction is the only content decision — no second stripping pass, no cross-page comparison. Pages the extractor can't handle (JS shells, pure link indexes) are written whole-DOM with a `crawl.extract.fallback` warning: a dirty page beats a missing one, and the log says which pages need attention.
- **Small images filtered.** Images below `--min-image-bytes` (default `10240` = 10 KB) are skipped and their Markdown references removed, so inline glyphs and rating stars that survive extraction don't bloat the KB or the downstream index ([§4.4.3](./DESIGN.md#443-images)). Set `--min-image-bytes 0` to keep every image.
- **Provenance sidecar.** Every folder holding documents gets a `.hcag-crawl.json` recording each file's and image's origin URL, plus the order the folder's index page linked its children ([§4.5.3](./DESIGN.md#453-link-order-sidecar)). A filename is a sanitized, collapsed, extension-stripped derivative of its URL and cannot be inverted, so without this a mirrored tree has no way back to its sources. `hcag` carries it into `compiled.md`, and `evalgen` emits it as the eval CSV's `source` column.
- **Console + structured logging.** Each URL prints as it is fetched — *when the fetch starts*, so a hang leaves the culprit on screen — and a report at the end lists what was included by kind and what was skipped by reason ([§4.7.1](./DESIGN.md#471-console-output)). `--quiet` drops the progress lines, `--report-limit N` bounds the examples per skip group. Separately, a JSON-lines log at `./crawl.log` records every fetch, extraction decision (including `retained_pct`), write, image extraction and skip. Levels: DEBUG · INFO · WARN · ERROR. Any ERROR exits non-zero.
- **A partial extraction is flagged.** Extraction can "succeed" and still lose half a page, which `--min-extract-chars` cannot catch. A `crawl.extract.low_retention` WARN fires below 25% retention — a threshold calibrated against hand-checked pages rather than chosen, because the obvious 55% flagged 15 pages of which 11 had lost nothing.

End-to-end with `hcag`:

```bash
crawl --depth 3 https://docs.example.com/api/
hcag ./kb
```

Details: [DESIGN.md §4](./DESIGN.md#part-4--the-crawl-cli-tool).

## Indexing a KB for Flat Hybrid Search with the `rag` CLI

`rag` is a peer to `hcag` — an **alternative** way to make a KB queryable. Where `hcag` normalizes a taxonomy for the runtime agent to navigate, `rag` builds a flat [LanceDB](https://lancedb.github.io/lancedb/) index over the same source content, supporting **hybrid retrieval** (dense vector + BM25 keyword, fused with a reranker).

```bash
rag --kb ./kb --index ./local_lancedb
```

Use it as a **Flat-RAG fallback** the caller composes on top of HCAG ([§1.3.5 combining approaches](./DESIGN.md#135-combining-approaches)), as a **baseline** in `evalrun` runs, or for ad-hoc grep over the corpus a notebook or retriever service opens directly.

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
hcag ./kb
```

The two indexes are independent — `hcag` writes one `compiled.md` per folder alongside the source files; `rag` writes only into `./local_lancedb/`. `rag` deliberately skips every `compiled.md` when it re-scans the tree, so the two can coexist.

Details: [DESIGN.md §8](./DESIGN.md#part-8--the-rag-cli-tool).

## Generating Eval Sets with the `evalgen` CLI

Once a KB is normalized by `hcag`, `evalgen` produces a CSV of question / expected-answer pairs grounded in that KB — for measuring retrieval and answer quality against the runtime agent.

```bash
# 100 questions total, 20 of each kind
evalgen ./my-kb --out my-kb-eval.csv --total 100 --seed 42

# Or specify per-kind counts explicitly
evalgen ./my-kb --out my-kb-eval.csv \
    --simple 20 --medium 20 --complex 20 --hard-1 20 --hard-2 20 --seed 42
```

Five question kinds ([DESIGN.md §6.4](./DESIGN.md#64-question-types)):

- **`simple`** — FAQ-style, requiring no reasoning: the reader looks the answer up. Measures retrieval. The *question* is simple, not the answer — a looked-up fact is routinely conditional, and the expected answer must state every condition the source attaches to it.
- **`medium`** — reasoning grounded in a single paragraph of a single packet.
- **`complex`** — deduction across ≥3 distinct paragraphs within one packet.
- **`hard-1`** — cross-packet: needs 2 packets, ≥3 paragraphs total. Measures whether the agent loads a second packet when needed.
- **`hard-2`** — multimodal: needs an image from `assets/` read alongside the packet markdown. Measures the multimodal loading path.

**Expected answers are complete, not short** ([§6.4.0](./DESIGN.md#640-expected-answers-must-be-complete)). Every kind shares one completeness standard, because the expected answer is what an agent is scored against: anything it omits is something the agent may omit and still be marked fully correct. Asked *"what is the minimum salary an EP candidate needs?"*, an answer of *"at least $5,600 a month"* is **wrong, not merely terse** — in the source that figure applies only below age 24, only outside financial services, and only before a stated date, rising to $10,700 by age 45. So an answer states the fact with every condition attached: what it varies by, the full range, when a different value applies, and worked examples where the source gives them. Length follows completeness.

Output is a fixed 8-column CSV: `question_id, kind, question, expected_answer, source, actual_answer, score, remark`. `source` holds the origin URLs the question was grounded in — packets first, then any image ([§6.7.1](./DESIGN.md#671-the-source-column)) — so a reviewer can check a generated answer against the authoritative page, and a stale eval set is detectable without re-crawling. `evalgen` always leaves the last three columns empty; they are populated by `evalrun`.

**Startup is loud and fail-closed** ([§6.2.2](./DESIGN.md#622-startup--config-visibility-and-llm-preflight)). `evalgen` makes one LLM call per question and writes the CSV at the end, so a bad key found on question 40 costs the run. A probe runs first — a real generation-shaped request, checked for a parseable reply — and a failure exits non-zero having written nothing. If `evalgen.toml` is missing it says so on stderr, naming the model it fell back to: the default is small and cheap, which produces weak questions and no `hard-2` at all.

Config is read from `./my-kb/evalgen.toml` (optional) or `--config <path>`. A strong, multimodal-capable model is recommended:

```toml
[llm]
provider    = "anthropic"
model       = "claude-opus-4-7"
api_key_env = "ANTHROPIC_API_KEY"

preflight   = true                 # probe before generating; §6.2.2
max_retries = 2

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

## Scoring the Agent with the `evalrun` CLI

`evalrun` closes the loop `evalgen` opens: it runs the CSV question set against a **live** chatbot backend and scores each answer with an LLM-as-judge. It's built on [promptfoo](https://www.promptfoo.dev/) — you get concurrent execution, retries, and an HTML report for free — and speaks to the chatbot over `POST /chat` (the same endpoint `hcag-server` from the [Web Chat and Voice Widget](#web-chat-and-voice-widget) exposes).

```bash
# 1. Bring up the backend under test
hcag-server --agent-config ./agent.toml --port 8000

# 2. Score the eval set — writes a completed CSV + an HTML report
evalrun kb-eval.csv \
    --backend-url http://localhost:8000 \
    --out kb-eval-scored.csv \
    --report kb-eval-report.html \
    --max-turns 5 --concurrency 4 --seed 42
```

For each question:

1. `evalrun` opens a session and calls `POST /chat` with the question.
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

The rubric, the clarifier, and the answer/clarify/refusal classifier are ordinary registry prompts (`eval.score`, `eval.clarify`, `eval.classify`) — Markdown files overridable through `prompts_dir` like every other prompt. What separates a `1` from a `2` on your KB is a domain call, and tightening it shouldn't need a release.

**Startup checks** ([DESIGN.md §7.3.1](./DESIGN.md#731-startup--config-visibility-and-llm-preflight)) — the backend `/health` probe, the prompt load, and a preflight of *both* eval models, all before the first row is dispatched. The preflight earns its place here more than in `evalgen`: `evalrun` pays for the entire run against the backend — every row, every clarification turn — and only *then* calls the judge, so a bad judge key is discovered after the whole run is spent and fails every row identically. That is not a partial result to salvage; it's a total loss shaped like a bug report. Both models are probed because they are usually separately keyed (cheap classifier, strong judge), and every failure names the role. Running with no `evalrun.toml` prints both model names on stderr — every score in the report comes from the judge model, and a scored CSV carries no record of which model produced it.

**Two outputs, always written together on completion:**

- **Completed CSV** — same 8-column schema as `evalgen` (`question_id, kind, question, expected_answer, source, actual_answer, score, remark`), with the last three columns populated. `source` is carried through untouched and is never shown to the agent — feeding provenance in would measure retrieval-with-hints rather than retrieval. A pre-provenance 7-column file still loads and is upgraded in place on write. Row order preserved so `diff` between runs is meaningful ([DESIGN.md §7.7](./DESIGN.md#77-output--completed-csv)).
- **HTML report** — run summary, per-kind panels (`simple / medium / complex / hard-1 / hard-2` — each with count, mean score, histogram, pass rate), score distribution across all kinds, row-level table with expandable transcripts, and an optional `--baseline <prior.csv>` comparison bar for regression detection ([DESIGN.md §7.8](./DESIGN.md#78-output--html-report)).

Config lives in `evalrun.toml` (optional) or via CLI flags. A strong judge model is recommended:

```toml
prompts_dir = "./prompts"           # drop in eval/score.md to override just the rubric

[backend]
url             = "http://localhost:8000"
request_timeout = 60
session_scope   = "per-question"    # per-question | per-run

[loop]
max_turns = 5

[judge.llm]
provider    = "anthropic"
model       = "claude-opus-4-7"
api_key_env = "ANTHROPIC_API_KEY"
preflight   = true                  # abort at startup if unreachable

[classifier.llm]
provider    = "anthropic"
model       = "claude-haiku-4-5-20251001"
api_key_env = "ANTHROPIC_API_KEY"
preflight   = true

[run]
concurrency = 4
seed        = 42

[report]
baseline = ""                       # optional path to a prior --out CSV
```

End-to-end regression workflow:

```bash
crawl --depth 3 https://docs.example.com/api/
hcag ./kb
evalgen ./kb --out kb-eval.csv --total 100 --seed 42     # once per KB revision
hcag-server --agent-config ./agent.toml --port 8000 &
evalrun kb-eval.csv --backend-url http://localhost:8000 \
     --out kb-eval-scored.csv --report kb-eval-report.html
```

Commit `kb-eval.csv` alongside the KB revision it was generated from, and re-run `evalrun` after each agent, prompt, or KB change to detect quality drift. Pass `--baseline kb-eval-scored.prev.csv` to render side-by-side per-kind pass rates and deltas.

Details: [DESIGN.md §7](./DESIGN.md#part-7--the-evalrun-cli-tool).

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

Two rules govern how the agent uses that catalog, and both exist because the natural failure modes run the other way:

- **The catalog routes; only packet content answers** ([D3b](./DESIGN.md#d3b-the-catalog-routes-only-packet-content-answers)). Its descriptions are build-tool summaries — summaries of summaries, at depth — not evidence. A whole-KB index puts hundreds of fluent, on-topic descriptions in the system prompt, and a model that answers from them produces confident, unsourced answers that *look* grounded. Every factual claim must come from the `## Content` of a loaded packet; if none supports an answer, the agent says so and loads the packet that would.
- **Most turns need no tool call at all** ([§2.7.1](./DESIGN.md#271-reload-discipline--when-not-to-call-check_and_load_kb)). A model handed a retrieval tool tends to call it every turn out of reflex — re-requesting packets it already holds. That costs a full extra round trip per turn, grows the uncached tail, and churns LRU order so eviction gets *worse*. A redundant call is named in its own result so the model sees it bought nothing, and `reload.redundant_rate` reports whether the discipline is holding.

## Web Chat and Voice Widget

A drop-in web widget that exposes the HCAG agent as a self-service support chatbot with an optional voice mode. Ported from a Claude Design handoff and shipped as a demo Next.js app plus a thin FastAPI backend that wraps `AgentRuntime` and mints LiveKit tokens.

**Layout:**

- `hcag/web/`     — Next.js 14 + React + TypeScript frontend (App Router).
- `hcag/server/`  — FastAPI backend (`hcag-server` CLI).
- Voice ties into the existing `hcag-voice` LiveKit worker — no fork.

**What's in the widget:**

- Minimised **launcher** with an optional "need help?" nudge popover.
- **Docked panel** (400×620) — bot/user bubbles, "Best match" answer card with source citations, escalate-to-officer card, thumbs-up/down feedback, transcript download.
- **Markdown rendering** in every assistant bubble ([DESIGN.md §10.6](./DESIGN.md#103-markdown-rendering)) — the agent answers out of Markdown packets, so tables, lists and headings arrive as Markdown and rendering them as plain text is a fidelity loss, not a styling nit. `react-markdown` + `remark-gfm` for GFM tables, with `rehype-sanitize` on a tightened schema: no raw HTML, no `javascript:` URLs. KB-relative image and link targets are meaningless in the browser, so images become a labelled placeholder and links render as plain text rather than as broken hrefs. User bubbles stay plain text — a user's `*` is an asterisk.
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
ANTHROPIC_API_KEY=... hcag-server \
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
| `POST /chat/stream` | Same request, SSE response of §2.14.1 events (`token`, `tool.start`, `tool.end`, `final`). A separate route rather than content negotiation on `/chat`, whose response shape `evalrun` and the RAG baseline both depend on. |
| `POST /livekit/token` | `{ identity, room? }` → `{ url, token, room }`. Mints a LiveKit access token via `livekit-api`. |
| `GET /health` | Sanity probe — returns `kb_root` and live session count. |

**Wheel packaging:** `hcag/web/{node_modules,.next,out,build,dist}` are excluded from the Python wheel in `pyproject.toml`. The frontend source lives under `hcag/web/` to keep the repo cohesive; the Python backend is a proper submodule at `hcag/server/`.

Full setup + env vars: [`hcag/web/README.md`](./hcag/web/README.md).

## Prompts

**No prompt text lives in the Python source.** Every string the model reads — system prompts, tool descriptions, the catalog delimiter, the build-time summarizer, the eval-question generators — is a Markdown file loaded by name at startup ([DESIGN.md §2.15](./DESIGN.md#215-prompts--loaded-by-name-not-hard-coded)):

```
prompts/
├── agent/system.md              # the runtime system prompt
├── agent/catalog_delimiter.md   # the INDEX ONLY block around the catalog
├── tool/check_and_load_kb.md    # what the model reads about the tool
├── voice/system.md              # the voice worker's system prompt
├── memory/redundant_note.md     # the note appended on a redundant reload
├── preprocess/{folder_metadata,scope_own,scope_branch}.md
├── evalgen/{answer_rules,simple,medium,complex,hard1,hard2}.md
└── eval/{score,classify,clarify}.md   # the judge rubric and its two helpers
```

A name like `agent.system` resolves to `agent/system.md`; every character outside `[a-z0-9_-]` is **stripped** from each segment, so a name can never escape the prompts directory. Files in your `prompts_dir` override the copies packaged with `hcag`, per prompt — supply `agent/system.md` alone and you keep the packaged everything-else.

Prompts are templates, substituted with stdlib `string.Template` (`$name`):

```markdown
--- KNOWLEDGE ---
$packets
--- END KNOWLEDGE ---

$catalog

Today's date is $today. Where the knowledge base distinguishes current rules
from ones taking effect on a future date, use this to decide which applies.
```

`string.Template` rather than `str.format` because prompts are full of braces — JSON examples, code fences — and under `.format` every one is a substitution site. The trade is that `$` becomes reserved, which matters in a KB about salaries: a literal dollar sign is written `$$`, and an unescaped `$11,800` is caught at **startup** by `Template.is_valid()` rather than raising on the first turn that renders it.

The point is who can change them: adjusting what the model is told should not require editing Python, a code review, and a release. Two consequences worth knowing:

- **Prompts are read once, at startup.** A conversation must be governed by one set of instructions, and re-reading per turn would invalidate the prompt cache mid-session. Editing a file takes effect on restart.
- **The loader fails closed.** A missing file, an empty file, two names colliding after stripping, or a template missing a required variable like `$catalog` are all startup errors — because every one of those failures is otherwise silent, and a blank system prompt looks like a model quality problem rather than a configuration one.

The `eval.*` prompts are the sharpest illustration of the `Template`-not-`.format` choice. Each tells the model to reply with a literal JSON object, so each contains braces as content — `{"score": 0 | 1 | 2 | 3, ...}`. They were previously loaded by a separate ad-hoc loader that rendered with `str.format`, under which those braces are substitution sites: `eval.classify` and `eval.score` raised `KeyError` before emitting a character, so nothing was ever classified and nothing was ever scored. Being outside the registry is what let that ship — a registered prompt is rendered by the same loader at startup, and this would have been a startup error on the first run.

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

Configuring **neither** means nothing leaves the process — a silent no-op, because not wanting tracing is a choice rather than a misconfiguration. Configuring **both** is a startup error: traces would go somewhere you did not choose and there is no safe default, so the ambiguity is a config bug to fix rather than a mode to run in (to reach two backends, point `otel.endpoint` at a collector and let it fan out).

A destination that is configured but **cannot work** — an unset key env var, an unreachable exporter, the `otel` extra not installed — is reported on stderr and at `ERROR` in the log, and the agent then serves turns with tracing off. It does not refuse to start. This is the one place the fail-closed stance of the build tool (§3.4.9) deliberately does not apply: `hcag` cannot build without an LLM, but the agent's job is answering questions and it does that fine while unable to report on itself. What the strict rule was protecting is the word *silently*, and that is kept — you are told the moment it happens, naming the variable, which is when it can actually be fixed. Writing a key inline in the TOML is still rejected outright, so a secret cannot be committed by accident.

Every CLI (`hcag`, `crawl`, `evalgen`, `evalrun`, `rag`, `hcag-voice`, `hcag-server`) accepts `--verbose` / `-v`, which mirrors the file log to stderr in the same JSON-lines shape. The file sink is unchanged — it stays at whatever level `--log-level` / config specifies — so `--verbose` is purely additive and safe to leave on during development.

## Testing

```bash
pytest -q
```

The suite covers the LRU eviction algorithm, memory module end-to-end, the agent tool loop and streaming turn API (with a `FakeLLM` — no network), catalog roll-up and reading order across a DFS build, the reload discipline, span attributes and trace-destination resolution, the prompt loader, the crawl core (BFS, dedup, main-content extraction, `<form>` unwrapping, image size filter, the no-`X/`-beside-`X.md` layout invariant), `evalgen` including expected-answer completeness and the `source` column, the SSE server route, and the voice startup + transcription paths.

## Design Deep-Dive

Full contents in [DESIGN.md](./DESIGN.md):

- [The HCAG Approach](./DESIGN.md#11-the-hcag-approach)
- [What HCAG Solves](./DESIGN.md#12-what-hcag-solves)
- [When to Use HCAG (vs Alternatives)](./DESIGN.md#13-when-to-use-hcag-vs-alternatives)
- [Key Design Decisions (D1–D11)](./DESIGN.md#18-key-design-decisions)
- [Component Class Diagram](./DESIGN.md#29-component-class-diagram)
- [Sequence Diagrams](./DESIGN.md#210-sequence-diagrams)
- [Observability](./DESIGN.md#211-observability)
- [Tech Stack](./DESIGN.md#213-tech-stack)
- [The `hcag` CLI Tool](./DESIGN.md#part-3--the-hcag-cli-tool)
- [Prompts — Loaded by Name](./DESIGN.md#215-prompts--loaded-by-name-not-hard-coded)
- [The `crawl` CLI Tool](./DESIGN.md#part-4--the-crawl-cli-tool)
- [Voice Agent (LiveKit)](./DESIGN.md#part-5--voice-agent-livekit)
- [The `evalgen` CLI Tool](./DESIGN.md#part-6--the-evalgen-cli-tool)
- [The `evalrun` CLI Tool](./DESIGN.md#part-7--the-evalrun-cli-tool)
- [The `rag` CLI Tool](./DESIGN.md#part-8--the-rag-cli-tool)
- [The RAG Chat Agent (Competing Baseline)](./DESIGN.md#part-9--the-rag-chat-agent-competing-baseline)
- [Web Chat Widget](./DESIGN.md#part-10--web-chat-widget)

## License

MIT
