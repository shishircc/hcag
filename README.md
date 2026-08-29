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
│   ├── runtime/           # Agent runtime
│   │   ├── llm.py         # LLM protocol + LiteLLM adapter
│   │   └── agent.py       # AgentRuntime — bootstrap + tool loop
│   ├── cli/               # `hcag` build tool
│   │   ├── preprocess.py  # Bottom-up: assemble packet.md + assets/
│   │   ├── aggregate.py   # Top-down: emit root catalog.md
│   │   └── main.py        # Typer entry point
│   └── crawl/             # `crawl` CLI — mirror sites into a raw KB
│       ├── urls.py        # Normalize, prefix-scope, URL → ./kb path
│       ├── fetch.py       # httpx wrapper with retries + redirect cap
│       ├── html_conv.py   # HTML → Markdown, extract links + images
│       ├── pdf_conv.py    # PDF → Markdown, extract embedded images
│       ├── core.py        # BFS traversal + structured logging
│       └── main.py        # Typer entry point
└── tests/
```

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # add [otel] for tracing, [image] for MIME detection
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

Given a raw KB tree of `.md` files and images organized by taxonomy:

```bash
# 1. Bottom-up: assemble packet.md at leaves, catalog.md at nodes
hcag preprocess ./my-kb

# 2. Top-down: emit root catalog.md
hcag aggregate ./my-kb
```

Config is read from `./my-kb/hcag.toml` (optional). Example:

```toml
[llm]
provider = "bedrock"
model    = "bedrock/anthropic.claude-3-5-haiku-20241022-v1:0"

[tokenizer]
kind = "tiktoken"

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
- **HTML and PDF.** Both are fetched and converted to Markdown; embedded images are extracted and rewritten to local paths so pages render offline.
- **Depth-limited.** `--depth N` caps hops from any seed (seed is depth 0). Cycles are broken by a visited-URL set — every in-scope URL is fetched at most once.
- **Mirrored layout.** Output lands under `./kb/<domain>/<url-path>/…`. Extracted images are prefixed with the source document's basename so identically-named images from different pages don't collide.
- **Structured logging.** JSON-lines log at `./crawl.log` records every fetch, write, image extraction, and skip decision (out-of-scope / already-visited / depth-cap). Levels: DEBUG · INFO · WARN · ERROR. Any ERROR exits non-zero.

End-to-end with `hcag`:

```bash
crawl --depth 3 https://docs.example.com/api/
hcag preprocess ./kb
hcag aggregate ./kb
```

Details: [DESIGN.md §4](./DESIGN.md#part-4--the-crawl-cli-tool).

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

The agent auto-injects the catalog into its system prompt at bootstrap, then the LLM decides — via the `check_and_load_kb` tool — when to load additional packets. See [DESIGN.md §2.10](./DESIGN.md#210-sequence-diagrams) for the turn-by-turn sequence diagrams.

## Observability

Two independent layers ([DESIGN.md §2.11](./DESIGN.md#211-observability)):

1. **JSON-lines file log** — always on. Captures every key decision.
2. **OpenTelemetry traces** — activated by setting `otel.endpoint`. Emits GenAI-semantic-convention spans plus HCAG-specific ones (`tool.*`, `kb.*`, `hcag.*`). Points at Langfuse, AWS CloudWatch (via ADOT), Grafana Tempo, Honeycomb, or any OTLP receiver.

## Testing

```bash
pytest -q
```

10 tests cover the LRU eviction algorithm, memory module end-to-end, and the agent tool loop (with a `FakeLLM` — no network).

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

## License

MIT
