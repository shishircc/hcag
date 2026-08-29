# DESIGN.md — Hierarchical Context Augmented Generation (HCAG)

An LLM agent backed by a hierarchical knowledge base. Instead of flat-index RAG over chunks, HCAG navigates a taxonomy, retrieves whole leaf documents, classifies the problem's branch once, and reuses that retrieval across the reasoning steps of a task.

---

## Table of Contents

- [Part 1 — High-Level Design](#part-1--high-level-design)
  - [1.1 The HCAG Approach](#11-the-hcag-approach)
  - [1.2 What HCAG Solves](#12-what-hcag-solves)
    - [Problem 1 — Knowledge Isolation](#problem-1--knowledge-isolation)
    - [Problem 2 — Complex Reasoning](#problem-2--complex-reasoning)
    - [Problem 3 — Speed and Cost](#problem-3--speed-and-cost)
  - [1.3 When to Use HCAG (vs Alternatives)](#13-when-to-use-hcag-vs-alternatives)
    - [1.3.1 Choose HCAG when…](#131-choose-hcag-when)
    - [1.3.2 Choose Agentic Search when…](#132-choose-agentic-search-when)
    - [1.3.3 Choose Flat RAG when…](#133-choose-flat-rag-when)
    - [1.3.4 At a Glance](#134-at-a-glance)
    - [1.3.5 Combining Approaches](#135-combining-approaches)
  - [1.4 What the Agent Provides](#14-what-the-agent-provides)
  - [1.5 Design Goals](#15-design-goals)
  - [1.6 Non-Goals](#16-non-goals)
  - [1.7 Core Concepts](#17-core-concepts)
  - [1.8 Key Design Decisions](#18-key-design-decisions)
  - [1.9 Component Boundary](#19-component-boundary)
  - [1.10 Tool Surface](#110-tool-surface)
  - [1.11 Out of Scope](#111-out-of-scope)
- [Part 2 — Detailed Design](#part-2--detailed-design)
  - [2.1 KB Layout](#21-kb-layout)
  - [2.2 `catalog.md` Schema](#22-catalogmd-schema)
  - [2.3 Tool Contracts](#23-tool-contracts)
    - [2.3.1 `get_catalog`](#231-get_catalog)
    - [2.3.2 `check_and_load_kb`](#232-check_and_load_kb)
    - [2.3.3 Selection semantics](#233-selection-semantics)
  - [2.4 Active-Set Protocol](#24-active-set-protocol)
  - [2.5 Token Budget & Eviction Algorithm](#25-token-budget--eviction-algorithm)
  - [2.6 Packet Loading (Multimodal Assembly)](#26-packet-loading-multimodal-assembly)
  - [2.7 System Prompt Composition (Bootstrap)](#27-system-prompt-composition-bootstrap)
  - [2.8 Error Handling](#28-error-handling)
  - [2.9 Component Class Diagram](#29-component-class-diagram)
  - [2.10 Sequence Diagrams](#210-sequence-diagrams)
    - [2.10.1 Cold start + first turn (additive load)](#2101-cold-start--first-turn-additive-load)
    - [2.10.2 Follow-up turn — no reload needed](#2102-follow-up-turn--no-reload-needed)
    - [2.10.3 Follow-up turn — additive load within budget](#2103-follow-up-turn--additive-load-within-budget)
    - [2.10.4 Follow-up turn — load with eviction](#2104-follow-up-turn--load-with-eviction)
  - [2.11 Observability](#211-observability)
    - [2.11.1 Configuration](#2111-configuration)
    - [2.11.2 OTEL Trace Model](#2112-otel-trace-model)
    - [2.11.3 Local File Logging](#2113-local-file-logging)
    - [2.11.4 What the Two Layers Together Answer](#2114-what-the-two-layers-together-answer)
  - [2.12 Prompt-Cache Alignment (realizing Problem 3)](#212-prompt-cache-alignment-realizing-problem-3)
  - [2.13 Tech Stack](#213-tech-stack)
    - [2.13.1 Language and Runtime](#2131-language-and-runtime)
    - [2.13.2 LLM Access — Provider-Neutral](#2132-llm-access--provider-neutral)
    - [2.13.3 Configuration, CLI, Content, Tokenization](#2133-configuration-cli-content-tokenization)
    - [2.13.4 Observability](#2134-observability)
    - [2.13.5 Testing and Packaging](#2135-testing-and-packaging)
    - [2.13.6 Dependency Summary](#2136-dependency-summary)
    - [2.13.7 Credentials](#2137-credentials)
    - [2.13.8 Deliberate Non-Dependencies](#2138-deliberate-non-dependencies)
  - [2.14 Open Questions / Future Work](#214-open-questions--future-work)
- [Part 3 — The `hcag` CLI Tool](#part-3--the-hcag-cli-tool)
  - [3.1 Purpose](#31-purpose)
  - [3.2 KB Input Model](#32-kb-input-model)
  - [3.3 CLI Overview](#33-cli-overview)
  - [3.4 `hcag preprocess` — Detailed Semantics](#34-hcag-preprocess--detailed-semantics)
    - [3.4.1 Traversal order](#341-traversal-order)
    - [3.4.2 Per-folder classification](#342-per-folder-classification)
    - [3.4.3 Packet generation (leaf and mixed folders)](#343-packet-generation-leaf-and-mixed-folders)
    - [3.4.4 Catalog generation (taxonomy node and mixed folders)](#344-catalog-generation-taxonomy-node-and-mixed-folders)
    - [3.4.5 Packet ID scheme](#345-packet-id-scheme-d34)
    - [3.4.6 Asset policy](#346-asset-policy)
    - [3.4.7 Overwrite policy](#347-overwrite-policy-d-cli-1)
    - [3.4.8 Failure modes](#348-failure-modes)
  - [3.5 `hcag aggregate` — Detailed Semantics](#35-hcag-aggregate--detailed-semantics)
    - [3.5.1 Input](#351-input)
    - [3.5.2 Algorithm](#352-algorithm)
    - [3.5.3 Root `catalog.md` output shape](#353-root-catalogmd-output-shape)
    - [3.5.4 Failure modes](#354-failure-modes)
  - [3.6 Configuration](#36-configuration)
  - [3.7 Generated File Formats — Summary](#37-generated-file-formats--summary)
  - [3.8 End-to-End Workflow](#38-end-to-end-workflow)
  - [3.9 Observability (CLI)](#39-observability-cli)
  - [3.10 Non-Goals for the CLI](#310-non-goals-for-the-cli)
- [Part 4 — The `crawl` CLI Tool](#part-4--the-crawl-cli-tool)
  - [4.1 Purpose](#41-purpose)
  - [4.2 Invocation](#42-invocation)
  - [4.3 Traversal Semantics](#43-traversal-semantics)
    - [4.3.1 Seed prefix scope](#431-seed-prefix-scope)
    - [4.3.2 Visited-URL tracking](#432-visited-url-tracking)
    - [4.3.3 Depth](#433-depth)
  - [4.4 Document Types](#44-document-types)
    - [4.4.1 HTML](#441-html)
    - [4.4.2 PDF](#442-pdf)
    - [4.4.3 Images](#443-images)
  - [4.5 Output Layout](#45-output-layout)
  - [4.6 Relationship to `hcag`](#46-relationship-to-hcag)
  - [4.7 Observability (CLI)](#47-observability-cli)
  - [4.8 Non-Goals](#48-non-goals)
- [Part 5 — Voice Agent (LiveKit)](#part-5--voice-agent-livekit)
  - [5.1 Purpose](#51-purpose)
  - [5.2 Component Boundary](#52-component-boundary)
    - [5.2.1 Voice Class Diagram](#521-voice-class-diagram)
  - [5.3 Real-Time Architecture](#53-real-time-architecture)
  - [5.4 Session Startup](#54-session-startup)
    - [5.4.1 Warm-start with initial packets](#541-warm-start-with-initial-packets)
    - [5.4.2 Prompt-cache warm-up call](#542-prompt-cache-warm-up-call)
    - [5.4.3 Startup Sequence Diagram](#543-startup-sequence-diagram)
  - [5.5 Real-Time Turn Pipeline](#55-real-time-turn-pipeline)
  - [5.6 STT / TTS Provider Selection](#56-stt--tts-provider-selection)
  - [5.7 Live Transcription Channel (Web Client Contract)](#57-live-transcription-channel-web-client-contract)
  - [5.8 Configuration](#58-configuration)
  - [5.9 CLI](#59-cli)
  - [5.10 Observability](#510-observability)
  - [5.11 Non-Goals](#511-non-goals)
- [Part 6 — The `evalgen` CLI Tool](#part-6--the-evalgen-cli-tool)
  - [6.1 Purpose](#61-purpose)
  - [6.2 KB Input Model](#62-kb-input-model)
  - [6.3 Invocation](#63-invocation)
  - [6.4 Question Types](#64-question-types)
    - [6.4.1 `simple`](#641-simple)
    - [6.4.2 `medium`](#642-medium)
    - [6.4.3 `complex`](#643-complex)
    - [6.4.4 `hard-1` (cross-packet)](#644-hard-1-cross-packet)
    - [6.4.5 `hard-2` (multimodal)](#645-hard-2-multimodal)
  - [6.5 Quantity Control](#65-quantity-control)
  - [6.6 Generation Algorithm](#66-generation-algorithm)
  - [6.7 Output CSV Schema](#67-output-csv-schema)
  - [6.8 Configuration](#68-configuration)
  - [6.9 Failure Modes](#69-failure-modes)
  - [6.10 Observability (CLI)](#610-observability-cli)
  - [6.11 Non-Goals](#611-non-goals)

---

# Part 1 — High-Level Design

## 1.1 The HCAG Approach

HCAG is a knowledge-taxonomy pattern for agentic AI applications. Rather than searching a flat index of chunks on every query, an HCAG agent classifies the problem — **domain → subdomain → topic** — and lets that classification decide which documents from the knowledge base are active. The classification happens **once per task** (or, at most, a few times when a task genuinely spans multiple branches), and the same active set is reused across the many reasoning, tool-use, and generation steps that follow.

**The core mechanism.**

1. The KB is organized as a tree: **domain → subdomain → topic → leaf documents**.
2. On receiving a task, the agent classifies the task's branch(es) and loads the corresponding leaf documents in full.
3. The agent then performs all subsequent reasoning steps — planning, tool use, drafting, refining — against that stable active set.
4. Only if a new step genuinely requires knowledge outside the active branch does the agent re-classify and load additional documents.

**How this differs from flat RAG.**

| | Flat RAG | HCAG |
|---|---|---|
| Retrieval unit | Chunks (fragments of documents) | Whole leaf documents |
| When retrieval fires | Every query, often every step | Once per task, reused across steps |
| Candidate pool per retrieval | All chunks from all documents (potentially millions) | Only leaves under the classified branch (a handful) |
| What competes | Every chunk in the corpus | Nothing — the taxonomy has already gated the corpus |
| Failure mode | "Similar but wrong" chunks from unrelated documents | Wrong branch classification (rarer, and detectable) |
| Continuity across steps | Working set flickers as each step retrieves anew | Working set is stable across the task |

The essential shift is **from repeated similarity search over a flat pool to a one-shot taxonomic classification that gates the pool**. Because only a few branches are ever active, thousands of unrelated documents cannot compete for the model's attention. Because leaves are retrieved whole, disambiguating context (definitions, caveats, scope) travels with the content instead of being sheared off by chunking. And because classification is amortized across the task, retrieval cost is paid once instead of at every reasoning step.

The rest of §1 makes this concrete: the three problems this solves (§1.2), when to reach for HCAG vs alternatives (§1.3), what the agent provides (§1.4), the design decisions that realize it (§1.8), and the component boundary that isolates the KB behind a memory module (§1.9).

## 1.2 What HCAG Solves

Given the approach in §1.1, HCAG addresses three failure modes of flat RAG.

### Problem 1 — Knowledge Isolation

The KB is organized as a **taxonomy**, not a flat index. At any point in a task, only one or two branches are active; the remaining 90%+ of the corpus is out of scope and cannot compete during retrieval. Flat RAG has no such gate — every chunk in the store is a candidate on every query, which is where "similar but wrong" retrievals come from, and those wrong retrievals are a common source of hallucination.

HCAG also retrieves **whole leaf documents**, not fragments. A full document carries its own disambiguating context — definitions, caveats, scope — that a chunk usually strips away. This is why packets are folder-scoped (see D2): the atomic unit of loading is a whole document plus its images, not a slice.

### Problem 2 — Complex Reasoning

Multi-faceted problems are where flat RAG breaks down: covering them properly requires several distinct queries, and any one of them can miss the right chunk. Once HCAG selects the correct branch, the model has the **complete body of knowledge that the problem needs**, in one shot. It reasons over full material rather than assembling an answer from partial excerpts.

### Problem 3 — Speed and Cost

Two effects compound:

| Mechanism | Effect |
|---|---|
| Prompt caching across repeated LLM calls sharing a stable prefix | 90%+ reduction in token cost on repeat calls, lower latency |
| Domain → subdomain → topic classification done **once** per task, retrieval reused across reasoning steps | Removes one or more retrieval round-trips per step |

The agent classifies the problem's domain / subdomain / topic up front and then stays with that active set for most of the task. Retrieval cost is paid once, not at every reasoning step. Decisions D5 (agent-driven, explicit reload) and D6 (delta-only responses) exist to protect this property: they keep the prompt prefix byte-stable across turns so cache hits accumulate.

## 1.3 When to Use HCAG (vs Alternatives)

HCAG is not a universal replacement for retrieval. It sits on a spectrum whose other main points are **Agentic Search** and **flat RAG**. The right choice depends on latency budget, cost sensitivity, task shape, and how much unpredictability the application can tolerate.

### 1.3.1 Choose HCAG when…

- You need **fast responses at low cost** on a **knowledge-heavy** task.
- The task requires **strong reasoning and planning grounded in specific knowledge** — diagnostic assistants, technical support agents, policy or compliance advisors, developer copilots against a large internal documentation set.
- The KB is large enough that injecting all of it every turn is impractical, but structured enough that a taxonomy exists or can be created.
- You want **predictable behavior**: the classification is inspectable, the loaded packets are enumerable, and the same question routes to the same knowledge.
- Prompt-cache hit rate matters — long conversations against the same branch.

**Prototypical use cases.** The pattern fits when the task is knowledge-heavy, largely stays within one branch of a well-defined taxonomy, and demands strong reasoning grounded in that branch:

- **Autonomous customer support over large, complex knowledge.** A support agent handling technical questions against a big product or policy corpus. Each conversation anchors in one or two product areas, classification stays stable across turns, and multi-turn reasoning benefits from the same active documents.
- **Root cause analysis.** A diagnostic agent walks a fault tree — symptoms → subsystem → component — retrieves the runbooks, telemetry references, and known-issue notes for the classified branch, and reasons through causes and fixes against that stable set. The taxonomy *is* the diagnostic hierarchy.
- **Autonomous operation workflows.** An operational agent executing a multi-step procedure (incident response, change management, provisioning, compliance action) grounded in the SOPs for a specific operation type. Classification picks the workflow; the packet holds the procedure and prerequisites; the agent plans and executes across many steps against that same knowledge without re-retrieving.

**Prerequisite — taxonomy investment.** HCAG's quality is bounded by the quality of the taxonomy. If a good taxonomy for your domain does not already exist, expect meaningful upfront work to design one — deciding what the branches are, where the boundaries fall, and how leaf documents map onto them. A poorly-crafted taxonomy will cause branch misclassification (the model activates the wrong branch) and coverage gaps (relevant knowledge is spread across branches that never co-activate). Existing documentation ontologies (product areas, service catalogs, policy chapters) are often good starting points, but rarely usable as-is. Budget for this. HCAG shifts effort from *repeated retrieval quality tuning* (RAG's ongoing cost) to *one-time taxonomy design* (HCAG's upfront cost).

### 1.3.2 Choose Agentic Search when…

- The task is **open-ended** — a research question, an investigative report, a market scan — where the agent should decide *what* to look for as it works.
- **Predictability is less important than coverage.** You want the agent to surprise you with connections across the KB or the open web.
- Backend batch processing is acceptable; the user is not waiting synchronously for a snappy response.
- Latency in the seconds-to-minutes range is fine; cost per task is amortized over the value of the output.

Agentic Search means the agent iteratively issues search queries (over web, KB, or both), reads results, refines queries, and synthesizes. There is no single upfront classification; the search plan evolves with what the agent finds.

**Prototypical use case:** "Write me a report on how our competitors are positioning against feature X." The agent decides what to search for, follows leads, cross-references sources. Wrong or missing sources are recoverable in-loop.

### 1.3.3 Choose Flat RAG when…

- You need a **quick MVP** — ship a knowledge-grounded feature this week.
- The **KB is small** and unlikely to grow into taxonomy territory.
- The questions are **FAQ-style** — short, self-contained, answered from a single passage.
- The task requires **little multi-step reasoning** — the answer lives in one passage, not synthesized across many.
- You can accept a **~70–80% accuracy ceiling**. The "similar but wrong" chunks that Problem 1 (§1.2) describes will be a persistent noise floor.
- You do **not** want to invest in taxonomy design; embedding + chunking gives you a working system on day one.

Flat RAG is the right first-cut for many products; it becomes limiting when the task shifts from "answer a question" to "reason across a body of knowledge," or when the KB grows past what a flat similarity search can gate well.

### 1.3.4 At a Glance

| | HCAG | Agentic Search | Flat RAG |
|---|---|---|---|
| **Best task shape** | Knowledge-heavy reasoning within a bounded domain | Open-ended research and report generation | FAQ / single-passage lookup |
| **Latency** | Low (single classification + cached prefix) | Medium–high (multi-step search loop) | Low |
| **Cost per task** | Low (retrieval paid once, cache reused) | High (many LLM calls, many searches) | Low |
| **Predictability** | High | Low (varies by query) | Medium |
| **Accuracy ceiling** | High when taxonomy is well-designed | Task-dependent, hard to bound | ~70–80% for non-trivial questions |
| **Setup cost** | Medium–high (**design a good taxonomy** + run `hcag`) | Low (define search tools) | Low (embed and index) |
| **Prerequisite** | A well-crafted taxonomy for the domain (often the biggest cost) | Well-defined search corpus/tools | A chunkable KB and an embedding model |
| **Fails when…** | Taxonomy is missing, poor quality, or the task genuinely spans many branches | Open-ended goals get lost mid-loop | Question requires reasoning across multiple documents, or KB is large enough that similar-but-wrong retrievals dominate |

### 1.3.5 Combining Approaches

The three are not mutually exclusive:

- **HCAG + Agentic Search fallback.** Use HCAG for the common case; if the agent's classification confidence is low, or the active set fails to answer, fall back to agentic search over the same KB (or the open web).
- **HCAG + Flat RAG within a packet.** For very large leaf documents, an embedding index over the packet's content can support fine-grained lookup while the taxonomy still gates the outer scope.
- **Agentic Search grounded on HCAG catalogs.** The agent's search tool can search over catalog metadata as one of its sources, mixing taxonomic navigation with open exploration.

## 1.4 What the Agent Provides

To realize the three properties above, the agent:

1. Sees a **catalog** (the taxonomy) describing everything available in the KB.
2. Loads specific **knowledge packets** (folders of markdown + images = whole leaf documents) on demand.
3. **Classifies the task's branch once** and persists that active set across turns rather than re-selecting each turn.
4. Reloads **only when the agent itself decides** its current knowledge is insufficient.
5. Handles **multimodal** content (packet.md + associated images) as first-class.

## 1.5 Design Goals

- **Taxonomic gating.** Only packets from the selected branch(es) of the KB can enter the model's context; the rest of the corpus is out of scope by construction, not by similarity score.
- **Whole-document retrieval.** Leaf documents load in full; no chunking, no fragment assembly.
- **Classify once, reason many times.** The task's branch is decided up front and reused across reasoning steps; the active set stays put unless the agent judges it insufficient.
- **Explicit, agent-driven memory control.** The agent decides when to expand its working knowledge, not a background retriever.
- **Prompt-cache friendliness.** The prompt prefix (system prompt + prior tool results) stays byte-stable across turns so cache hits accumulate and per-call cost drops.
- **Delta-only transport.** Reloads move only what changed (newly-loaded packets + newly-evicted IDs), never re-transmitting stable packets.
- **Bounded working memory.** A token budget caps the active set; LRU eviction reclaims space when the agent asks for more than fits.
- **Multimodal by default.** Images referenced by a packet ride with the packet as multimodal content blocks.
- **Framework-agnostic contract.** The design describes interfaces and protocols; a concrete implementation may bind to any agent SDK.
- **Deterministic packet units.** A packet is a folder; boundaries are physical, not inferred.

## 1.6 Non-Goals

- ~~Catalog generation~~ — **in scope**, covered by the `hcag` CLI in Part 3. The runtime agent still assumes a valid `catalog.md` already exists at query time; the CLI is what produces it.
- **Semantic embedding retrieval.** No vector store, no similarity search. Selection is LLM-driven, informed by catalog metadata.
- **Multi-user / concurrent-session** orchestration. Single conversation, single agent instance.
- **Write-back / KB mutation** from the agent. The KB is read-only from the agent's perspective.
- **Persistence of the active set across process restarts.** Session-scoped only.

## 1.7 Core Concepts

| Term | Definition |
|---|---|
| **Knowledge Base (KB)** | A file-system tree of packet folders rooted at a KB directory, plus a single `catalog.md` at the root. |
| **Packet** | A folder containing exactly one `packet.md` and an optional `assets/` subdirectory of images. The atomic loadable unit. |
| **Catalog** | A single `catalog.md` at KB root that enumerates every packet with metadata (id, path, title, short + long description, token size estimate). |
| **Active Set** | The set of packets currently loaded into the agent's working context in the current conversation. |
| **Delta** | The pair `(loaded, evicted)` returned when the active set changes — only new packet content is transmitted; only evicted IDs are named. |
| **Token Budget** | A hard upper bound on the total tokens the active set may occupy. Enforced by the memory module via LRU eviction. |

## 1.8 Key Design Decisions

Each decision below is a choice made deliberately over specific alternatives.

### D1. Hierarchy = file-system tree
The KB is a nested directory tree. Hierarchy is physical (folders), not conceptual (taxonomy) or temporal (memory tiers). **Rationale:** Simplest mental model; the directory is the source of truth; no separate taxonomy to keep in sync.

### D2. Packet = folder (`packet.md` + `assets/`)
Each packet folder has exactly one `packet.md` for text content and an optional `assets/` subdirectory for images. Subfolders that themselves contain `packet.md` are independent packets. **Rationale:** Physical boundaries; images travel with their text; no need for manifest files or section-parsing.

### D3. Catalog = single `catalog.md` at KB root
One file at the root, generated by the `hcag` CLI (Part 3) via a two-pass build: (a) `hcag preprocess` writes per-level catalog.md files and per-leaf packet.md files, (b) `hcag aggregate` merges the per-level catalog.md files into the root catalog.md that the runtime consumes. Manually re-triggered when the KB changes. **Rationale:** One place to look at query time; no distributed index to reconcile at runtime; a standardized offline pipeline lets KB authors focus on extracting raw markdown from source documents.

### D4. Catalog auto-injected into system prompt (fetched via memory module)
At conversation start, the agent runtime calls `memory_module.get_catalog()` and injects the returned catalog into the system prompt. The agent always "knows" what exists. `get_catalog` remains available as a tool for re-inspection mid-session, but the common path is a single bootstrap call. **Rationale:** Removes an entire round-trip class from the per-turn common path; agent can decide from the outset whether loading is needed.

### D4a. Memory module is the sole KB accessor
Neither the agent runtime nor the LLM ever reads the KB file system directly — not for the catalog, not for packets, not for images. Every byte of KB content is fetched via the memory module's tools (`get_catalog`, `check_and_load_kb`). **Rationale:** The KB backing store is an implementation detail of the memory module. Today it is a local file tree; tomorrow it can become an object store, a versioned KV, or a remote service — with zero change to the agent contract. This isolation is enforced at the layering boundary: the runtime has no KB path, no reader, no direct dependency on the file system for KB content.

### D5. Classify once, agent-driven explicit reload
The agent classifies the task's domain / subdomain / topic at the first turn and loads the corresponding leaf packet(s) via `check_and_load_kb`. On subsequent turns it does **not** re-classify or re-select — it calls `check_and_load_kb` only when it judges its current active set insufficient for the new request. No per-turn re-evaluation, no background retriever, no topic-shift heuristic. **Rationale:** Prevents active-set churn; preserves prompt-cache locality across turns (Problem 3); matches the observation that most multi-step reasoning within a task stays inside the same branch.

### D6. Delta-only responses from `check_and_load_kb`
The tool returns only **newly loaded packets** (with content) and **newly evicted packet IDs** (without content). It does not re-send content of packets already in the active set. **Rationale:** Minimizes token traffic **and** — critically for Problem 3 — keeps prior tool-result blocks byte-stable in history, so the prompt prefix remains cacheable turn after turn.

### D7. Agent tracks the active set; passes it in each call
The memory module is **stateless across calls**. The agent LLM tracks currently-loaded packet IDs (they are in its conversation history) and passes them as an argument to `check_and_load_kb`. **Rationale:** Framework-agnostic; no session store; the ground truth is the conversation itself.

### D8. Token-budget-bounded active set with LRU eviction
The module enforces a hard token budget. If new loads would exceed budget, the module evicts least-recently-used packets from the caller-supplied active set to make room, and reports the eviction in the delta. **Rationale:** Predictable context growth; the agent never has to reason about tokens itself.

### D9. Multimodal loading is first-class
Images under a packet's `assets/` directory are loaded as multimodal content blocks alongside the packet's markdown. Not text descriptions, not deferred loads. **Rationale:** The agent should see what the packet contains, in full fidelity, from the moment it is loaded.

### D10. Framework-agnostic contracts
The design specifies interfaces (tool schemas, return shapes, active-set protocol) but not a specific SDK, language, or LLM binding. **Rationale:** Portable across Claude Agent SDK (Python/TS), raw Anthropic SDK, or any other agent runtime.

## 1.9 Component Boundary

```
┌──────────────────────────────────────────────────────────┐
│                    AGENT RUNTIME                         │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │   LLM (holds active-set IDs in its context)        │  │
│  └───────────────┬────────────────────────────────────┘  │
│                  │ tool calls                            │
│                  │ (get_catalog, check_and_load_kb)      │
│                  │                                       │
│  Bootstrap ▲     │                                       │
│  get_catalog│    │                                       │
│             │    ▼                                       │
│  ┌────────────────────────────────────────────────────┐  │
│  │           Memory Module (per-call stateless)       │  │
│  │  - get_catalog                                     │  │
│  │  - check_and_load_kb(context, requested, active)   │  │
│  │  - Token budgeting + LRU eviction                  │  │
│  │  - Multimodal packet assembly                      │  │
│  │  - **SOLE OWNER OF KB ACCESS**                     │  │
│  └───────────────┬────────────────────────────────────┘  │
└──────────────────┼───────────────────────────────────────┘
                   │ reads (private to module)
        ┌──────────▼──────────┐
        │  KB (backing store) │
        │  catalog.md         │
        │  <packet>/packet.md │
        │  <packet>/assets/*  │
        └─────────────────────┘
```

The **Memory Module** is the only component that touches the KB. Everything else — the agent runtime (for bootstrap), the LLM (for per-turn loads) — goes through it. Because of this, the backing store can be swapped (local FS → object store → remote KV service) without any change to the agent or LLM contract.

## 1.10 Tool Surface

Two tools are exposed to the agent:

| Tool | Purpose | When the agent calls it |
|---|---|---|
| `get_catalog` | Return the current catalog. | Rare — catalog is auto-injected. Used only if agent wants to re-examine metadata mid-session. |
| `check_and_load_kb` | Given a natural-language description of what the agent needs and the current active-set IDs, load any missing packets (with eviction if needed) and return the delta. | When the agent judges its current active set insufficient for the user's request. |

## 1.11 Out of Scope

- Catalog generation pipeline (batch scanner + summarizer).
- Multi-tenant / concurrent-session state.
- Persistence of active set across process restarts.
- Vector search or embedding-based retrieval.
- KB write-back from the agent.
- Cross-packet reference resolution (e.g., a packet linking to another by ID).

---

# Part 2 — Detailed Design

## 2.1 KB Layout

```
<kb_root>/
├── catalog.md                        # Root catalog, offline-generated
├── billing/
│   ├── refunds/
│   │   ├── packet.md
│   │   └── assets/
│   │       ├── refund_flow.png
│   │       └── refund_states.png
│   └── invoices/
│       ├── packet.md
│       └── assets/
│           └── invoice_layout.png
├── auth/
│   ├── oauth/
│   │   └── packet.md                 # No images
│   └── sso/
│       ├── packet.md
│       └── assets/
│           └── sso_sequence.png
└── ...
```

**Rules:**

- A directory is a **packet** iff it contains `packet.md` at its immediate level.
- A packet's `assets/` directory is optional and, if present, contains images referenced by the packet.
- Non-packet directories serve only as organizational intermediates.
- Packet IDs are assigned by the catalog generator (stable, opaque strings). They are **not** derived from paths at runtime by the memory module — the module reads IDs from the catalog.

## 2.2 `catalog.md` Schema

`catalog.md` is a human-readable + machine-parseable markdown document. Each catalog entry is a section with a YAML front-matter-style block or a table row (choice is left to the generator; the module only requires that the following fields be recoverable per entry):

| Field | Type | Description |
|---|---|---|
| `id` | string | Stable, opaque packet identifier. |
| `path` | string | Relative path (from KB root) to the packet folder. |
| `title` | string | Human-readable title. |
| `short_description` | string | One-line summary — shown in catalog listings. |
| `long_description` | string | Multi-sentence description — used by the LLM to decide relevance. |
| `token_size_estimate` | integer | Precomputed total token count for `packet.md` + image blocks. Used by the module for budgeting **without** loading. |

**Illustrative rendering** (one entry):

```markdown
### `bill.refunds`

- **path**: `billing/refunds/`
- **title**: Refund Processing
- **short**: How refunds are issued, states, and edge cases.
- **long**: Covers the full refund lifecycle: eligibility rules, state machine
  (pending → approved → issued → settled), partial refunds, chargebacks,
  and reconciliation. Includes two diagrams (flow, state machine).
- **tokens**: 3420
```

## 2.3 Tool Contracts

### 2.3.1 `get_catalog`

**Input:** none.

**Output:** the current `catalog.md` content (string). Equivalent to what is auto-injected at conversation start; provided in case the agent wants to re-examine.

### 2.3.2 `check_and_load_kb`

**Input:**

| Field | Type | Description |
|---|---|---|
| `context` | string | The agent's own description of what it currently needs from the KB (natural language). Used only for logging/observability — the module does not perform selection from this. |
| `requested_packet_ids` | list<string> | The packet IDs the agent wants **added** to its active set. |
| `active_packet_ids` | list<string> | The packet IDs the agent believes are currently active (its self-tracked set). Ordered by most-recently-used **last**. |

**Output (delta):**

| Field | Type | Description |
|---|---|---|
| `loaded` | list<Packet> | Newly-loaded packets (full content — markdown + image content blocks + per-packet metadata header). |
| `evicted` | list<string> | Packet IDs that were evicted from the active set to make room. |
| `active_after` | list<string> | The authoritative active-set IDs after this call, in LRU order (most-recently-used last). Serves as a reconciliation aid. |
| `errors` | list<Error> | Per-packet errors (e.g., unknown ID, load failure). Non-fatal — the call still returns whatever succeeded. |

**Packet content shape (per loaded packet):**

```
[
  { "type": "text",  "text": "--- packet: bill.refunds ---\nTitle: Refund Processing\n..." },
  { "type": "text",  "text": "<contents of packet.md>" },
  { "type": "image", "source": {...} },   # for each image under assets/
  { "type": "image", "source": {...} },
  ...
]
```

A textual **metadata header** precedes each packet so the LLM can always identify what it is looking at from context alone. Images follow the markdown.

### 2.3.3 Selection semantics

The agent picks `requested_packet_ids` by consulting the catalog (already in its context). The module does **not** perform semantic matching. If `requested_packet_ids` is a subset of `active_packet_ids`, the module returns an empty delta (no-op).

## 2.4 Active-Set Protocol

- The **agent** is the tracker of the active set. Its knowledge of "what is loaded" is the sequence of prior `check_and_load_kb` tool results in its own conversation history.
- The **module** is stateless across calls; it treats `active_packet_ids` as authoritative input each call.
- The module returns `active_after` so the agent can reconcile in case of eviction. The agent trusts `active_after` over its own prior tracking.

**Ordering (LRU):**

- On each call, the module treats the concatenation `active_packet_ids ++ requested_packet_ids` (with duplicates removed, keeping the last occurrence) as the LRU-ordered candidate set. Most-recently-used is at the tail.
- Eviction, when needed, removes from the **head** (least-recently-used).

## 2.5 Token Budget & Eviction Algorithm

**Configuration:** `MAX_ACTIVE_TOKENS` — a fixed budget for the active set (excluding conversation, system prompt, and other overhead — this is the packet-content budget only).

**Algorithm** (executed inside `check_and_load_kb`):

```
Input: active_ids (ordered LRU), requested_ids
Let catalog = load_catalog()
Let to_add = [id for id in requested_ids if id not in active_ids]

# Build LRU-ordered candidate set: existing (in LRU order) + newly-requested at the tail
Let ordered = dedup_keep_last(active_ids + to_add)

# Sum token estimates from catalog
Let total = sum(catalog[id].token_size_estimate for id in ordered)
Let evicted = []

# Evict from the head (LRU) until total fits within budget
While total > MAX_ACTIVE_TOKENS and len(ordered) > 0:
    victim = ordered.pop_front()
    if victim in to_add or victim == ordered_tail:
        # Special case: cannot evict a packet the agent just requested;
        # if a single requested packet exceeds budget alone, return an error.
        raise BudgetExceeded(victim)
    total -= catalog[victim].token_size_estimate
    evicted.append(victim)

# Load only the newly-added packets (existing active packets are already in
# the agent's context from prior tool results and are NOT re-transmitted)
Let loaded = [load_packet(id) for id in to_add if id in ordered]

Return { loaded, evicted, active_after: ordered }
```

**Guarantees:**

- Packets in the delta's `loaded` list are always a subset of `to_add`.
- Packets in `evicted` are always drawn from the input `active_ids` (never from the same-call `to_add`).
- `active_after` equals the LRU-ordered final active set.

## 2.6 Packet Loading (Multimodal Assembly)

Given a packet ID, the module:

1. Looks up `path` from the catalog.
2. Reads `<kb_root>/<path>/packet.md` as UTF-8.
3. Enumerates `<kb_root>/<path>/assets/*` (if the folder exists) for image files.
4. Emits, in order:
   - A text metadata header block (packet ID, title, short description).
   - The raw markdown text of `packet.md`.
   - One image content block per file under `assets/`, in a stable order (e.g., lexicographic filename).

Images are read from disk and passed as multimodal image content blocks to the agent runtime (encoding — base64, URL, file reference — is chosen by the runtime binding; the module contract is "multimodal content block").

## 2.7 System Prompt Composition (Bootstrap)

The agent runtime **never** reads the KB directly. At conversation start it obtains the catalog by calling `memory_module.get_catalog()` and injects the returned string into the system prompt:

```
<static agent instructions>
<usage guidance for get_catalog and check_and_load_kb>

--- KNOWLEDGE CATALOG ---
<catalog returned by memory_module.get_catalog()>
--- END CATALOG ---
```

The agent is instructed to:

- Consult the catalog before answering domain questions.
- Call `check_and_load_kb` **only when** its currently-loaded packets are insufficient.
- Pass its currently-known active IDs and its requested IDs.
- Trust `active_after` from the tool result as authoritative.
- Never assume it can read the KB directly — every packet must be obtained via `check_and_load_kb`.

## 2.8 Error Handling

| Condition | Behavior |
|---|---|
| Unknown packet ID in `requested_packet_ids` | Skip; add an entry to `errors[]`; other loads proceed. |
| `packet.md` missing on disk | Add to `errors[]`; do not add packet to active set. |
| Image under `assets/` unreadable | Include the packet with a placeholder text block noting the missing image; add to `errors[]`. |
| Single requested packet exceeds `MAX_ACTIVE_TOKENS` | Return `errors[]` entry with reason `BudgetExceeded`; do not load; active set unchanged. |
| Catalog file missing at startup | Startup failure — the agent cannot function without a catalog. |

## 2.9 Component Class Diagram

The class diagram below shows the runtime object model of the HCAG agent: the components introduced conceptually in §1.9 rendered as classes, interfaces, and relationships. The diagram deliberately elides configuration types, logging, and tracing surfaces (those are documented separately in §2.11); it focuses on the domain classes that participate in serving a turn.

The **memory module** is modeled as an interface (`MemoryModule`) with a concrete file-system implementation (`FileSystemMemoryModule`). The KB backing store is a further-abstracted interface (`KBStorage`) so that the file-system implementation can be swapped for an object store, a versioned KV, or a remote service (D4a) without touching the agent runtime or the LLM contract. The two data-transfer objects on the tool boundary (`CheckAndLoadRequest`, `Delta`) are first-class classes so that framework bindings can serialize them to whatever tool-call format their SDK requires.

The voice agent (Part 5) **composes** the classes below rather than modifying them — its `VoiceSession` wraps an `AgentRuntime` and adds STT/TTS adapters plus a transcription publisher. The voice-specific extension is diagrammed in §5.2.1.

```mermaid
classDiagram
    direction LR

    class AgentRuntime {
        +LLM llm
        +MemoryModule memory
        +Config config
        +bootstrap() void
        +run_turn(user_msg) response
    }

    class LLM {
        <<interface>>
        +chat(messages, tools) response
    }

    class MemoryModule {
        <<interface>>
        +get_catalog() Catalog
        +check_and_load_kb(request) Delta
    }

    class FileSystemMemoryModule {
        -KBStorage storage
        -EvictionPolicy eviction
        -TokenBudget budget
        +get_catalog() Catalog
        +check_and_load_kb(request) Delta
        -load_packet(id, entry) Packet
    }

    class KBStorage {
        <<interface>>
        +read_catalog() string
        +read_packet_markdown(path) string
        +list_assets(path) list~string~
        +read_asset(path) bytes
    }

    class LocalFsStorage {
        -string kb_root
        +read_catalog() string
        +read_packet_markdown(path) string
        +list_assets(path) list~string~
        +read_asset(path) bytes
    }

    class Catalog {
        +list~CatalogEntry~ entries
        +get(id) CatalogEntry
        +raw_markdown() string
    }

    class CatalogEntry {
        +string id
        +string path
        +string title
        +string short_description
        +string long_description
        +int token_size_estimate
    }

    class CheckAndLoadRequest {
        +string context
        +list~string~ requested_packet_ids
        +list~string~ active_packet_ids
    }

    class Delta {
        +list~Packet~ loaded
        +list~string~ evicted
        +list~string~ active_after
        +list~LoadError~ errors
    }

    class Packet {
        +string id
        +string title
        +list~ContentBlock~ content
    }

    class ContentBlock {
        <<abstract>>
    }

    class TextBlock {
        +string text
    }

    class ImageBlock {
        +bytes data
        +string mime_type
    }

    class LoadError {
        +string packet_id
        +string reason
    }

    class TokenBudget {
        +int max_active_tokens
        +sum_estimate(ids, catalog) int
        +fits(total) bool
    }

    class EvictionPolicy {
        <<interface>>
        +plan(active, incoming, budget, catalog) EvictionPlan
    }

    class LRUEvictionPolicy {
        +plan(active, incoming, budget, catalog) EvictionPlan
    }

    class EvictionPlan {
        +list~string~ ordered_active_after
        +list~string~ evicted
        +list~string~ to_load
    }

    AgentRuntime --> LLM : invokes
    AgentRuntime --> MemoryModule : bootstrap and per-turn
    LLM ..> MemoryModule : tool-calls

    FileSystemMemoryModule ..|> MemoryModule
    FileSystemMemoryModule --> KBStorage : reads via
    FileSystemMemoryModule --> EvictionPolicy : delegates
    FileSystemMemoryModule --> TokenBudget : enforces

    LocalFsStorage ..|> KBStorage
    LRUEvictionPolicy ..|> EvictionPolicy

    MemoryModule ..> Catalog : returns
    MemoryModule ..> Delta : returns
    MemoryModule ..> CheckAndLoadRequest : accepts
    EvictionPolicy ..> EvictionPlan : returns

    Catalog "1" *-- "*" CatalogEntry
    Delta "1" *-- "*" Packet : loaded
    Delta "1" *-- "*" LoadError : errors
    Packet "1" *-- "*" ContentBlock : content

    TextBlock --|> ContentBlock
    ImageBlock --|> ContentBlock
```

### 2.9.1 Class Responsibilities

| Class | Responsibility | Key references |
|---|---|---|
| `AgentRuntime` | Owns the conversation loop. On bootstrap calls `MemoryModule.get_catalog()` to inject the catalog into the system prompt (§2.7). On each user turn, invokes the LLM; forwards any `check_and_load_kb` tool calls to the memory module. | §1.9, §2.7, §2.10 |
| `LLM` (interface) | Abstract chat interface. Any concrete binding (Anthropic SDK, framework SDK) implements this. | §1.5 (framework-agnostic) |
| `MemoryModule` (interface) | The tool contract exposed to the LLM. Stateless across calls (D7). | §1.10, §2.3 |
| `FileSystemMemoryModule` | Concrete implementation. Composes a `KBStorage`, an `EvictionPolicy`, and a `TokenBudget`. Assembles `Packet` objects from storage-returned bytes. | §2.5, §2.6 |
| `KBStorage` (interface) | Backing-store abstraction. The seam that lets the KB move off local disk later (D4a). | §1.9, D4a |
| `LocalFsStorage` | Default implementation: reads catalog and packet files from a local KB root. | §2.1 |
| `Catalog` / `CatalogEntry` | Parsed catalog and per-packet metadata. `Catalog.raw_markdown()` returns the exact string for system-prompt injection. | §2.2 |
| `CheckAndLoadRequest` | Input DTO for `check_and_load_kb`. Carries `context`, `requested_packet_ids`, `active_packet_ids`. | §2.3.2 |
| `Delta` | Output DTO. Contains `loaded` (new content), `evicted` (IDs only), `active_after` (authoritative), and per-packet `errors`. | §2.3.2, D6 |
| `Packet` | A loaded packet: id, title, and an ordered list of `ContentBlock`s (metadata header text, packet markdown, and images). | §2.6 |
| `ContentBlock` / `TextBlock` / `ImageBlock` | Polymorphic content blocks. `ImageBlock` carries raw bytes + MIME type; runtime bindings render to their SDK's image format. | §2.6, D9 |
| `TokenBudget` | Holds `max_active_tokens`; sums per-entry `token_size_estimate` from the catalog. | §2.5 |
| `EvictionPolicy` (interface) | Decides which packets to evict when adding new ones would exceed budget. Pluggable — LRU is the default. | §2.5, D8 |
| `LRUEvictionPolicy` | Default: order candidates LRU, evict from head until `TokenBudget.fits()`. Produces an `EvictionPlan`. | §2.5 |
| `EvictionPlan` | Result of an eviction pass: `ordered_active_after`, `evicted` IDs, `to_load` IDs. Consumed by `FileSystemMemoryModule` to drive storage reads. | §2.5 |
| `LoadError` | Per-packet error (unknown ID, missing file, budget-exceeded). Bundled inside `Delta.errors` so partial success is representable. | §2.8 |

### 2.9.2 Extension Points

Three points in the class model are designed for substitution:

1. **`KBStorage`** — swap `LocalFsStorage` for `S3Storage`, `GitVersionedStorage`, `RemoteHttpStorage`, etc. Zero changes elsewhere.
2. **`EvictionPolicy`** — swap `LRUEvictionPolicy` for `LFUEvictionPolicy`, `PinnedFirstEvictionPolicy` (allow pinning a packet), or `TaskAwareEvictionPolicy` (weight by classification confidence).
3. **`LLM`** — bind to any framework or provider. `AgentRuntime` depends only on the `chat(messages, tools)` shape.

`MemoryModule` itself is also an interface, so in principle an alternative implementation (e.g., an in-process cache-only module wrapping another) could replace `FileSystemMemoryModule` without touching the agent.

## 2.10 Sequence Diagrams

**Diagram conventions.** To keep Mermaid syntax portable, message labels are short verbs, and structured data (packet ID lists, delta contents) is shown in `Note` blocks. No semicolons, brackets, or pipes appear inside labels or notes.

### 2.10.1 Cold start + first turn (additive load)

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant R as Agent Runtime
    participant L as LLM
    participant M as Memory Module
    participant K as KB

    Note over R,K: BOOTSTRAP
    R->>M: get_catalog
    M->>K: read catalog.md
    K-->>M: catalog contents
    M-->>R: catalog
    R->>L: init system prompt with catalog

    Note over U,L: FIRST TURN
    U->>R: How do partial refunds work
    R->>L: user message
    Note over L: Consults catalog<br/>Needs bill.refunds<br/>active is empty
    L->>M: check_and_load_kb
    Note over L,M: requested = bill.refunds<br/>active = empty
    M->>K: read billing/refunds/packet.md
    M->>K: read billing/refunds/assets
    K-->>M: text and images
    M-->>L: delta
    Note over L,M: loaded = bill.refunds<br/>evicted = empty<br/>active_after = bill.refunds
    L-->>R: answer
    R-->>U: answer
```

### 2.10.2 Follow-up turn — no reload needed

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant R as Agent Runtime
    participant L as LLM
    participant M as Memory Module

    Note over U,L: active_after from prior turn is bill.refunds

    U->>R: What about chargebacks
    R->>L: user message
    Note over L: bill.refunds already loaded<br/>Covers chargebacks<br/>No reload needed
    L-->>R: answer from loaded packet
    R-->>U: answer

    Note over L,M: Memory module not called<br/>Active set unchanged
```

### 2.10.3 Follow-up turn — additive load within budget

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant R as Agent Runtime
    participant L as LLM
    participant M as Memory Module
    participant K as KB

    Note over U,L: active = bill.refunds<br/>Well under budget

    U->>R: How do invoices connect to refunds
    R->>L: user message
    Note over L: Needs bill.invoices<br/>bill.refunds still relevant
    L->>M: check_and_load_kb
    Note over L,M: requested = bill.invoices<br/>active = bill.refunds
    Note over M: LRU order becomes<br/>bill.refunds then bill.invoices<br/>Sum under budget<br/>No eviction
    M->>K: read billing/invoices/packet.md and assets
    K-->>M: text and images
    M-->>L: delta
    Note over L,M: loaded = bill.invoices<br/>evicted = empty<br/>active_after = bill.refunds then bill.invoices
    L-->>R: answer
    R-->>U: answer
```

### 2.10.4 Follow-up turn — load with eviction

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant R as Agent Runtime
    participant L as LLM
    participant M as Memory Module
    participant K as KB

    Note over U,L: active = bill.refunds then bill.invoices then auth.oauth<br/>Near budget

    U->>R: Walk me through SSO
    R->>L: user message
    Note over L: Needs auth.sso<br/>bill items less relevant now
    L->>M: check_and_load_kb
    Note over L,M: requested = auth.sso<br/>active = bill.refunds then bill.invoices then auth.oauth
    Note over M: LRU candidate order<br/>bill.refunds bill.invoices auth.oauth auth.sso<br/>Sum over budget so evict head
    Note over M: Evict bill.refunds<br/>Still over budget<br/>Evict bill.invoices<br/>Now within budget
    M->>K: read auth/sso/packet.md and assets
    K-->>M: text and images
    M-->>L: delta
    Note over L,M: loaded = auth.sso<br/>evicted = bill.refunds and bill.invoices<br/>active_after = auth.oauth then auth.sso
    Note over L: Drops refunds and invoices from working memory<br/>Reasons over oauth and sso
    L-->>R: answer
    R-->>U: answer

    Note over L: If a later turn needs bill.refunds again<br/>the agent re-requests it<br/>Module re-loads from KB
```

## 2.11 Observability

Observability has two independent layers:

1. **OTEL traces for AI observability** — *optional*, configuration-driven. When an OTEL exporter endpoint is configured, the agent emits distributed traces of turns, LLM calls, and tool calls. Consumers can point this at Langfuse, AWS CloudWatch, Grafana Tempo, Honeycomb, or any OpenTelemetry-compatible backend without code changes.
2. **Local file logging** — *always on*. Structured log lines at `DEBUG` / `INFO` / `WARN` / `ERROR` levels written to a local log file. Ensures that key decisions (which branch was classified, which packets loaded, which evicted, why) are followable post-hoc even when tracing is disabled.

### 2.11.1 Configuration

Observability is driven by configuration only — no code changes to switch backends.

| Config key | Type | Default | Effect |
|---|---|---|---|
| `otel.endpoint` | URL (string) | unset | If set, initialize OTEL SDK with an OTLP exporter pointing here. If unset, tracing is a no-op. |
| `otel.protocol` | `http/protobuf` or `grpc` | `http/protobuf` | OTLP transport. |
| `otel.headers` | map<string,string> | empty | Auth headers (e.g., Langfuse public/secret, AWS SigV4 side-car, bearer tokens). |
| `otel.service_name` | string | `hcag-agent` | `service.name` resource attribute. |
| `log.file_path` | path (string) | `./hcag.log` | Local log file destination. |
| `log.level` | `DEBUG` \| `INFO` \| `WARN` \| `ERROR` | `INFO` | Threshold for file logging. |
| `log.rotation` | struct (size/time) | size 50MB, keep 5 | Optional rotation policy. |

Example destinations:

- **Langfuse:** `otel.endpoint = https://cloud.langfuse.com/api/public/otel`, headers include the Langfuse public/secret key pair.
- **AWS CloudWatch (via ADOT):** `otel.endpoint = http://localhost:4318` pointing at a local ADOT collector, which forwards to CloudWatch.
- **Grafana Tempo / Honeycomb / any OTLP receiver:** point `otel.endpoint` at their OTLP ingest URL.

### 2.11.2 OTEL Trace Model

When enabled, the agent emits a span hierarchy per user turn. Spans follow OpenTelemetry **GenAI semantic conventions** where they exist and custom `hcag.*` attributes where they do not.

```
conversation.turn                              [span]
├─ attrs: turn.index, session.id, user.message.chars
│
├─ gen_ai.chat                                 [span] LLM call
│  ├─ attrs: gen_ai.system=anthropic,
│  │         gen_ai.request.model,
│  │         gen_ai.usage.input_tokens,
│  │         gen_ai.usage.output_tokens,
│  │         gen_ai.usage.cache_read_input_tokens
│  │
│  └─ tool.check_and_load_kb                   [span] tool call
│     ├─ attrs: hcag.tool.requested_ids,
│     │         hcag.tool.active_ids_in,
│     │         hcag.tool.context (truncated),
│     │         hcag.tool.loaded_ids,
│     │         hcag.tool.evicted_ids,
│     │         hcag.tool.active_ids_after,
│     │         hcag.tool.tokens_used,
│     │         hcag.tool.tokens_budget
│     │
│     ├─ kb.packet.load                        [span] per loaded packet
│     │  ├─ attrs: hcag.packet.id,
│     │  │         hcag.packet.path,
│     │  │         hcag.packet.markdown_bytes,
│     │  │         hcag.packet.image_count,
│     │  │         hcag.packet.token_estimate
│     │  └─ status: OK | ERROR (with error message)
│     │
│     └─ kb.eviction                           [span, only if evictions occurred]
│        └─ attrs: hcag.eviction.ids,
│                  hcag.eviction.reason=budget,
│                  hcag.eviction.tokens_reclaimed
│
└─ gen_ai.chat (final answer)                  [span]
   └─ attrs: gen_ai.usage.*
```

**Bootstrap-only span (once per conversation):**

```
memory.bootstrap                               [span]
└─ tool.get_catalog                            [span]
   ├─ attrs: hcag.catalog.entries,
   │         hcag.catalog.bytes
   └─ status: OK | ERROR
```

**Attribute policy.** Never put full packet content, full user messages, or full LLM outputs into span attributes — attributes are size-bounded and often sent unfiltered to third-party services. Truncate to a configurable byte cap (default 512 chars) and prefer IDs + sizes over payloads. Full payloads belong in the file log, at DEBUG.

### 2.11.3 Local File Logging

The file log is the **decision log**. Its job is to make it possible to reconstruct, after the fact, what the agent decided and why — without needing an OTEL backend running. It is always on.

**Levels and what belongs at each:**

| Level | What is logged |
|---|---|
| `ERROR` | Failures that abort a step or turn: catalog missing at startup, packet load failure, budget-exceeded on a single requested packet, tool contract violation. |
| `WARN` | Recoverable oddities: unknown packet ID skipped, image unreadable (packet still returned), unusually large delta, active-set thrash detected (N reloads within M turns). |
| `INFO` | Key decisions: bootstrap complete (catalog entries, bytes), turn start, `check_and_load_kb` call with counts (requested, active-in, loaded, evicted, budget), branch classification result (which domain/subdomain/topic the agent picked). |
| `DEBUG` | Full detail: catalog contents digest, per-packet metadata, full requested/active/loaded/evicted ID lists, per-packet token accounting, full tool arguments and results (subject to a max-size cap). |

**Format.** JSON-lines, one record per line, with fields: `ts` (ISO-8601), `level`, `event`, `session_id`, `turn`, `trace_id` (correlates with OTEL when enabled), and event-specific fields. Example:

```json
{"ts":"2026-08-23T14:22:07Z","level":"INFO","event":"check_and_load_kb.result","session_id":"s-abc","turn":3,"trace_id":"7f2a...","requested":["auth.sso"],"active_in":["bill.refunds","bill.invoices","auth.oauth"],"loaded":["auth.sso"],"evicted":["bill.refunds","bill.invoices"],"active_after":["auth.oauth","auth.sso"],"tokens_used":6820,"tokens_budget":8000}
```

**Correlation.** Every log record includes `trace_id` (and `span_id` where available). When OTEL is enabled, a support engineer can pivot from a log line to the corresponding trace in Langfuse / CloudWatch and vice versa.

### 2.11.4 What the Two Layers Together Answer

- **"Why did the agent load bill.refunds on turn 3?"** — INFO log has the classification decision and the requested IDs; DEBUG log has the reasoning context; OTEL span has the timing and token counts.
- **"Is the active set thrashing?"** — WARN log fires on excessive reload rate; OTEL dashboard shows load/evict spans per turn over time.
- **"Where is the token cost going?"** — OTEL `gen_ai.usage.*` attributes aggregated across `gen_ai.chat` spans; file log gives per-turn budget snapshots.
- **"Did prompt caching hit?"** — OTEL `gen_ai.usage.cache_read_input_tokens` per LLM span.


## 2.12 Prompt-Cache Alignment (realizing Problem 3)

The "classify once, reuse across steps" property in §1.1 and §1.2 only pays off if the prompt prefix that the model sees stays byte-stable across turns. Concrete implementation guidance:

1. **Stable system prompt.** The catalog is injected once at conversation start and does not change mid-session. If catalog re-inspection is needed, use the `get_catalog` tool (which appears as a per-turn tool result, not a system-prompt mutation).
2. **Stable tool-result blocks.** A prior `check_and_load_kb` response, once emitted into history, is never rewritten. Delta semantics (D6) guarantee this — subsequent calls append new tool results rather than modifying old ones.
3. **Deterministic packet serialization.** For a given packet ID, the module must emit byte-identical content (same metadata header, same markdown, same image ordering) across calls. Any nondeterminism (e.g., variable timestamps in headers) breaks caching.
4. **Cache-control markers.** In runtimes that expose them (e.g., Anthropic prompt caching), mark the system prompt and each `check_and_load_kb` tool result as a cache breakpoint. Combined with (1)–(3), this yields the 90%+ token-cost reduction on subsequent reasoning steps within the same task.
5. **Avoid unnecessary reloads.** The agent should not call `check_and_load_kb` "just to refresh" — every call that produces a delta (even an empty one) is a new tool-result block. D5 forbids this.

## 2.13 Tech Stack

Design decisions above are language-agnostic; this section pins the implementation choices. The overriding constraint: **the LLM is invoked through a provider-neutral library, not a vendor SDK**. This preserves D10 (framework-agnostic contracts) at the implementation layer and lets the same code target Anthropic direct today and AWS Bedrock (or others) tomorrow with only a config change.

### 2.13.1 Language and Runtime

- **Python 3.11+**
- Rationale: `tomllib` is stdlib (config parsing), mature OTEL SDK, first-class LiteLLM support, wide availability of tokenizers, natural fit for CLI + service in one package.

### 2.13.2 LLM Access — Provider-Neutral

**Chosen library: LiteLLM (`litellm`).**

LiteLLM exposes a single `litellm.completion(model, messages, tools, ...)` API that transparently dispatches to 100+ providers. Provider selection is a config-only switch — no code changes to move between Anthropic-direct and Bedrock.

| Provider | LiteLLM `model` string | Credentials source |
|---|---|---|
| Anthropic direct | `claude-3-5-sonnet-20241022`, `claude-3-5-haiku-20241022`, etc. | `ANTHROPIC_API_KEY` env var |
| AWS Bedrock (Anthropic models) | `bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0` | Standard AWS credential chain (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`, or IAM role) |
| AWS Bedrock (other models) | `bedrock/<vendor>.<model>` | Same |
| Ollama (local) | `ollama/llama3`, `ollama/mistral` | `OLLAMA_API_BASE` (default `http://localhost:11434`) |
| OpenAI-compatible (future) | `openai/<model>` with `api_base` | `OPENAI_API_KEY` |

**Consequences of the LiteLLM choice, relevant to HCAG:**

- **Tool use.** LiteLLM normalizes tool-call format across providers. HCAG's `get_catalog` and `check_and_load_kb` are defined once and work identically on Anthropic and Bedrock.
- **Prompt caching.** LiteLLM supports Anthropic's `cache_control` markers (§2.12); on Bedrock, prompt caching is enabled per Bedrock's own semantics. The HCAG runtime tags the system prompt and each `check_and_load_kb` tool-result block as cache breakpoints; LiteLLM forwards this to whichever provider is active.
- **OTEL integration.** LiteLLM emits GenAI-semantic-convention spans natively when the OTEL SDK is initialized; this satisfies most of §2.11.2's `gen_ai.chat` span layer with zero extra glue. HCAG adds only the `tool.*`, `kb.*`, `hcag.*` spans.

The runtime's `LLM` interface (§2.9) is bound to LiteLLM by a single thin adapter class — the rest of the code sees only the interface.

### 2.13.3 Configuration, CLI, Content, Tokenization

| Concern | Library | Notes |
|---|---|---|
| Config schema and validation | **Pydantic v2** | `hcag.toml` (CLI) and `agent.toml` (runtime) are loaded into typed models |
| TOML parsing | **`tomllib`** (stdlib) | Python 3.11+ |
| YAML front-matter in `packet.md` / `catalog.md` | **python-frontmatter** + **PyYAML** | Reading and writing |
| CLI framework | **Typer** (built on Click) | Typed subcommands (`hcag preprocess`, `hcag aggregate`) |
| Tokenization (build-time estimates) | **tiktoken** (default, `cl100k_base` proxy) | Runtime never re-tokenizes; the design's `token_size_estimate` is read from catalog |
| Image MIME detection (CLI, optional) | **Pillow** | Runtime uses file extension only |

Markdown content is treated as opaque UTF-8 text; no markdown-parser dependency is required for the memory module. The CLI concatenates source `.md` files verbatim between separators, so no round-tripping through a markdown AST.

### 2.13.4 Observability

| Layer | Library | Notes |
|---|---|---|
| Traces | **`opentelemetry-api`**, **`opentelemetry-sdk`**, **`opentelemetry-exporter-otlp-proto-http`** | Initialized only when `otel.endpoint` is configured (§2.11.1). `otel.protocol=grpc` swaps to `opentelemetry-exporter-otlp-proto-grpc`. |
| File log | **stdlib `logging`** + custom JSON formatter | No extra dependency; JSON-lines format per §2.11.3 |
| GenAI spans | **LiteLLM native OTEL** | Emits `gen_ai.chat` spans with the usage attributes described in §2.11.2 |
| HCAG-specific spans | Direct OTEL SDK calls | `tool.*`, `kb.*`, `hcag.*` per §2.11.2 |

The two layers stay independent (§2.11): tracing may be disabled entirely; file logging is always on.

### 2.13.5 Testing and Packaging

- **Testing:** `pytest` + `pytest-mock`. LLM calls in tests are stubbed via a `FakeLLM` implementing the same `LLM` protocol as the LiteLLM adapter (see §2.9). No live network calls in the default test suite.
- **Packaging:** `pyproject.toml` with PEP 621 metadata. Package-manager-neutral (works with `uv`, `pip`, `poetry`). The `hcag` command is registered as a console script.

### 2.13.6 Dependency Summary

```
Required (runtime + CLI):
  litellm                                        # LLM provider abstraction
  pydantic>=2                                    # Config validation
  typer                                          # CLI
  python-frontmatter                             # packet.md / catalog.md front-matter
  pyyaml                                         # YAML
  tiktoken                                       # Token estimation

Optional (feature-flagged by config):
  opentelemetry-api                              # tracing (enabled when otel.endpoint is set)
  opentelemetry-sdk
  opentelemetry-exporter-otlp-proto-http
  opentelemetry-exporter-otlp-proto-grpc         # only for otel.protocol=grpc
  pillow                                         # image MIME detection in CLI

Dev:
  pytest
  pytest-mock
```

### 2.13.7 Credentials

The provider is picked in config; the corresponding env vars must be present at runtime and CLI build time.

| Provider | Required env vars |
|---|---|
| **Anthropic direct** (default) | `ANTHROPIC_API_KEY` |
| **AWS Bedrock** | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` — or an attached IAM role / AWS profile |
| Ollama / local | `OLLAMA_API_BASE` (optional; defaults to `http://localhost:11434`) |
| OpenAI-compatible (future) | `OPENAI_API_KEY` |

### 2.13.8 Deliberate Non-Dependencies

The implementation does **not** import these libraries, and the design forbids adding them at the LLM call site:

- **`anthropic`** — the vendor SDK. Adding it would tie HCAG to one provider and violate D10. LiteLLM handles Anthropic transparently.
- **`openai`** — same rationale.
- **`boto3` at the call site** — Bedrock is reached via LiteLLM, not by direct `boto3.client('bedrock-runtime')` calls in HCAG code. (`boto3` may appear transitively via LiteLLM's Bedrock backend; that transitive presence is acceptable because it does not leak into HCAG's own API surface.)
- **`langchain` / `llama-index`** — heavy retrieval frameworks. HCAG's retrieval pattern is agent-driven and controlled by the memory module (Part 2); a chain/graph framework would add layers without benefit here.

## 2.14 Open Questions / Future Work

1. **Catalog scaling.** If the catalog itself grows beyond a comfortable system-prompt size, we may need a summarized catalog + on-demand `get_catalog_entry(id)` tool. Deferred.
2. **Partial packet loading.** Packets are all-or-nothing today. If some packets become very large, section-level loading could be introduced without changing the tool surface (packet IDs would become `packet_id#section`).
3. **Cross-packet links.** Packets may reference each other by ID in prose; today the agent must interpret and re-request. A "referenced_ids" hint in the catalog could enable eager prefetch.
4. **Prompt-cache alignment.** Because delta responses do not retransmit stable packets, prior tool results remain byte-stable in history — good for prompt caching. Explicit cache-control markers on tool-result blocks may further improve hit rates; runtime-specific.
5. **Session persistence.** Currently the active set is implicit in the conversation history. A resumable-session feature would require serializing active-set IDs (not content).

---

# Part 3 — The `hcag` CLI Tool

## 3.1 Purpose

`hcag` is a command-line tool that transforms a **raw KB folder tree** — where subject-matter experts have dropped `.md` files and images according to a taxonomy of their choosing — into a **normalized KB** that the runtime memory module (Part 2) can serve directly. It standardizes:

- The **format** of `packet.md` and `catalog.md`.
- The **metadata schema** each catalog entry must carry (id, path, title, short/long description, token estimate).
- The **layout** of leaf packet folders (`packet.md` + `assets/`).

This lets KB teams focus on the one thing that requires human judgment — extracting well-organized markdown from source documents — and delegates everything else (layout normalization, image relocation, metadata generation, catalog assembly) to the tool.

## 3.2 KB Input Model

Before `hcag` runs, the tree looks like whatever the KB team produced. Only two rules apply on input:

1. **Only markdown files (`.md`) and recognized image types** (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`) contribute to the KB. Any other file encountered during preprocessing is **silently ignored** (a `WARN` is logged for observability). This lets teams keep incidental artifacts — `.DS_Store`, editor lock files, source documents like `.docx` / `.pdf` kept alongside extracted markdown, `README` notes, etc. — inside the KB tree without breaking the build.
2. **`packet.md` and `catalog.md` are HCAG-owned output artifacts, never input.** If they exist in a folder from a prior run, they are ignored for input-classification purposes — their contents are never treated as source markdown to be merged. Preprocessing either regenerates them from the true sources or skips per the overwrite policy (§3.4.7); it does not concatenate them into a new packet.
3. **The folder structure encodes the taxonomy.** Depth is unrestricted; there is no required schema for folder names beyond being valid filesystem names.

A **leaf** in taxonomy terms is a folder that contains at least one `.md` file — regardless of whether it also has subfolders (see §3.3). A **taxonomy node** is a folder that contains at least one subfolder.

**Mixed folders are legal and become both.** A folder that has both subfolders and `.md` files at its own level becomes simultaneously (a) a taxonomy node — gets a `catalog.md` listing its children, and (b) a packet — gets a `packet.md` assembled from its own `.md` files. This is a first-class case, not an edge case: it lets a taxonomy node carry its own overview content (e.g., a `billing/` folder that contains both `billing/refunds/` and `billing/invoices/` **and** a top-level `billing.md` overview).

Example raw KB before `hcag preprocess`:

```
raw_kb/
├── billing/
│   ├── overview.md
│   ├── glossary.md
│   ├── billing_ecosystem.png
│   ├── refunds/
│   │   ├── refund_policy.md
│   │   ├── refund_states.md
│   │   ├── flow.png
│   │   └── state_machine.png
│   └── invoices/
│       ├── invoicing.md
│       └── layout.png
└── auth/
    ├── oauth/
    │   └── oauth_spec.md
    └── sso/
        ├── sso_intro.md
        ├── sso_flows.md
        └── sso_seq.png
```

## 3.3 CLI Overview

Two subcommands, each a distinct pass:

| Command | Purpose |
|---|---|
| `hcag preprocess <root>` | Walks the tree bottom-up. At every folder that qualifies as a packet, assembles `packet.md` and moves images into `assets/`. At every folder that qualifies as a taxonomy node, writes a `catalog.md` describing its immediate children. |
| `hcag aggregate <root>` | Reads the per-level `catalog.md` files produced by `preprocess` and merges them into a single root-level `catalog.md` — the file the runtime memory module serves via `get_catalog`. |

**Design decisions embedded in this structure:**

- Two passes, not one. Preprocess and aggregate can be re-run independently — e.g., editorial edits to a single leaf require re-preprocessing only that subtree and then re-aggregating.
- Aggregate **reads intermediate catalog.md files**; it does not re-scan packets. This is fast and trusts the prior pass. If intermediates are stale, re-run preprocess.
- No `hcag build` super-command. Chaining is left to the caller (`hcag preprocess raw_kb && hcag aggregate raw_kb`).

## 3.4 `hcag preprocess` — Detailed Semantics

### 3.4.1 Traversal order

Bottom-up post-order: process children before parents. Necessary because a parent's `catalog.md` references its children by ID and needs each child's generated title / description / token estimate to already exist.

### 3.4.2 Per-folder classification

For each folder `F` encountered:

1. Let `has_md = any .md file directly in F (excluding generated packet.md)`
2. Let `has_subdirs = any subdirectory of F`
3. Classify:
   - `has_md AND NOT has_subdirs` → **leaf packet** (packet-only)
   - `has_subdirs AND NOT has_md` → **taxonomy node** (catalog-only)
   - `has_md AND has_subdirs` → **mixed** (both packet AND catalog)
   - Neither → skip with WARN

### 3.4.3 Packet generation (leaf and mixed folders)

For folders classified as packet:

1. **Collect source .md files** in stable order (lexicographic by filename). Only true source `.md` files count — `packet.md` and `catalog.md` are HCAG-owned output artifacts and are **excluded from the source set** even if present in the folder (§3.2 rule 2). If `packet.md` already exists from a prior run and `--force` is not set, skip.
2. **Concatenate** the source .md files into a single `packet.md` with a heading separator between each source:
   ```markdown
   <!-- HCAG:PACKET id=billing.refunds -->
   ---
   id: billing.refunds
   title: <LLM-generated>
   short_description: <LLM-generated>
   long_description: <LLM-generated>
   token_size_estimate: <computed>
   source_files:
     - refund_policy.md
     - refund_states.md
   ---

   # <LLM-generated title>

   <content of refund_policy.md, image refs rewritten to assets/...>

   ---

   <content of refund_states.md, image refs rewritten to assets/...>
   ```
3. **Copy all images** in the folder (referenced or not — see §3.4.6) into `F/assets/`. The originals are left in place. Rewrite every image reference in the merged content to `assets/<filename>`.
4. **Preserve the original source files.** After merge, the source `.md` files and the original image files remain untouched at their locations; they are the KB team's authoring surface and the source of truth for future re-runs. `packet.md` and everything under `assets/` are derived artifacts. On the next `hcag preprocess --force`, the sources are re-read and both are regenerated.
5. **Compute token size estimate** on the final `packet.md` + image count using a configured tokenizer (see §3.6). Store in front-matter.
6. **Generate metadata via LLM.** Send the merged content to the configured LLM (§3.6) with a fixed prompt that requests exactly the fields `title`, `short_description` (one line), and `long_description` (2–4 sentences). Write them into the front-matter.

### 3.4.4 Catalog generation (taxonomy node and mixed folders)

For folders classified as taxonomy node (or the taxonomy side of a mixed folder):

1. Enumerate the folder's **immediate children** that were classified as packet, mixed, or taxonomy node.
2. For each child that is a packet (leaf or mixed), pull metadata from its just-written `packet.md` front-matter.
3. For each child that is a pure taxonomy node, pull metadata from its just-written `catalog.md` header block (title + short description — see below).
4. **Write `catalog.md`** describing the children:

   ```markdown
   <!-- HCAG:CATALOG level=billing -->
   ---
   node_title: <LLM-generated title for this taxonomy node>
   node_short_description: <LLM-generated one-line summary of this branch>
   ---

   # <node_title>

   <node_short_description>

   ## Children

   ### `billing.refunds`
   - **kind**: packet
   - **path**: `refunds/`
   - **title**: Refund Processing
   - **short**: How refunds are issued, states, and edge cases.
   - **long**: Covers the full refund lifecycle...
   - **tokens**: 3420

   ### `billing.invoices`
   - **kind**: packet
   - **path**: `invoices/`
   - **title**: Invoice Generation
   - **short**: ...
   - **long**: ...
   - **tokens**: 2810
   ```

5. The `node_title` and `node_short_description` are **also LLM-generated**, from the children's short descriptions concatenated. This gives the parent's catalog.md a meaningful roll-up that the aggregate pass will use as taxonomy breadcrumbs.

### 3.4.5 Packet ID scheme (D3.4)

Packet and taxonomy IDs are the **dotted path from the KB root**, using folder names as segments.

- `raw_kb/billing/refunds/` → id `billing.refunds`
- `raw_kb/auth/sso/` → id `auth.sso`
- `raw_kb/billing/` (mixed folder) → taxonomy id `billing`, packet id `billing` (same string — context disambiguates: catalog.md refers to it as a taxonomy node; packet.md as a packet). If this collision is inconvenient in downstream consumers, use `billing._` for the packet side; the CLI supports a `--mixed-suffix` flag (default `_`).

**Rationale:** Human-readable, stable as long as folder names are stable, computable without any state. Changing folder names is a deliberate ID-change operation.

### 3.4.6 Asset policy

- **All images are copied to `assets/`**, whether referenced by any MD or not. Originals are **not** moved or deleted — they remain at their authored location. Rationale: images the KB team dropped into a folder are intentional even if not yet linked; keeping a copy in `assets/` ensures they travel with the packet at load time, while preserving the original preserves the authoring workflow and lets re-runs regenerate `assets/` from source.
- **External references** (an MD referencing `../other/img.png`) are resolved: the image is copied into the leaf's `assets/` and the reference rewritten. The original at the external path is untouched. A WARN is logged because an external reference usually indicates the source content was authored assuming a different layout.
- **Non-MD, non-image files** are **silently ignored** — the file is left in place, a `WARN` log line records what was skipped (path + reason), and preprocessing proceeds. Rationale: KB teams often keep original source documents (`.docx`, `.pdf`), editorial notes (`README`), or OS metadata (`.DS_Store`, `Thumbs.db`) inside the tree; failing the build over them is more disruptive than useful. The runtime never sees these files because the memory module reads only `catalog.md`, `packet.md`, and files under `assets/`.

### 3.4.7 Overwrite policy (D-CLI-1)

Default: **skip folders that already contain generated artifacts** (`packet.md` or `catalog.md` with a `<!-- HCAG:PACKET -->` / `<!-- HCAG:CATALOG -->` marker). This protects re-runs from clobbering hand-edits.

- `--force` regenerates unconditionally.
- `--force-packets` regenerates only packets.
- `--force-catalogs` regenerates only catalog.md files.

If a file exists without the HCAG marker, the tool errors — it will not overwrite what it did not create.

### 3.4.8 Failure modes

| Condition | Behavior |
|---|---|
| Non-MD/non-image file present | WARN, ignored, preprocessing continues |
| Folder with no `.md` and no subfolders | WARN, skip |
| LLM call fails for a packet | ERROR for that packet; continue with siblings; final exit non-zero if any packet failed |
| Image referenced by MD but not found | WARN, leave the (broken) reference in packet.md |
| Existing `packet.md` without HCAG marker | ERROR (would clobber hand-written content) |

## 3.5 `hcag aggregate` — Detailed Semantics

### 3.5.1 Input

Requires that `hcag preprocess` has been run at least once. Reads every intermediate `catalog.md` in the tree.

### 3.5.2 Algorithm

1. Walk the tree top-down. At each folder, read its `catalog.md` (if present) to obtain `node_title`, `node_short_description`, and the list of children with metadata.
2. Build an in-memory taxonomy tree: nodes carry title/short; leaves carry the full packet metadata.
3. Emit `<root>/catalog.md` in the schema defined by §2.2, extended with a **taxonomy breadcrumb** field per entry so the LLM sees where each packet sits in the tree.

### 3.5.3 Root `catalog.md` output shape

```markdown
<!-- HCAG:ROOT_CATALOG generated_at=2026-08-23T00:00:00Z -->

# Knowledge Catalog

## Taxonomy Overview

- **billing** — Billing operations across refunds, invoices, and reconciliation.
  - **billing.refunds** — Refund processing.
  - **billing.invoices** — Invoice generation.
- **auth** — Authentication and authorization.
  - **auth.oauth** — OAuth 2.0 support.
  - **auth.sso** — Single sign-on integrations.

## Packets

### `billing.refunds`
- **path**: `billing/refunds/`
- **breadcrumb**: billing → refunds
- **title**: Refund Processing
- **short**: How refunds are issued, states, and edge cases.
- **long**: Covers the full refund lifecycle: eligibility, state machine, partial refunds, chargebacks, reconciliation.
- **tokens**: 3420

### `billing.invoices`
- **path**: `billing/invoices/`
- **breadcrumb**: billing → invoices
- **title**: Invoice Generation
- **short**: ...
- **long**: ...
- **tokens**: 2810
```

The `## Taxonomy Overview` section gives the LLM the shape of the tree (useful for classification — Problem 1); the `## Packets` section gives every packet's metadata (used for `check_and_load_kb` decisions).

### 3.5.4 Failure modes

| Condition | Behavior |
|---|---|
| Intermediate `catalog.md` missing at a folder that has subfolders with packets | ERROR — instruct user to re-run `hcag preprocess` |
| Duplicate packet IDs discovered | ERROR — usually caused by symlinks or copy-paste; must be resolved manually |

## 3.6 Configuration

`hcag` reads a config file (`hcag.toml` or `hcag.yaml`) at the KB root, or accepts flags:

```toml
[llm]
provider = "anthropic"            # anthropic | openai | bedrock | ollama | llamacpp
model    = "claude-haiku-4-5"     # provider-specific model id
api_key_env = "ANTHROPIC_API_KEY" # env var to read
endpoint = ""                     # override for local/self-hosted (Ollama, llama.cpp)

[llm.prompts]
# Paths to prompt template files, overridable
packet_metadata  = "prompts/packet_metadata.md"
node_metadata    = "prompts/node_metadata.md"

[tokenizer]
kind = "tiktoken"                 # tiktoken | anthropic | rough
# "rough" = chars/4 heuristic; "tiktoken" and "anthropic" call the real tokenizer

[assets]
mixed_suffix = "_"                # for packet ID on mixed folders

[log]
file_path = "./hcag-build.log"
level     = "INFO"
```

**Local model support.** The `[llm]` block accepts `provider = "ollama"` or `provider = "llamacpp"` with a local `endpoint`. This lets KB teams without cloud credentials build a KB against a locally-hosted model. Metadata quality varies with model choice.

## 3.7 Generated File Formats — Summary

### `packet.md` (per leaf or mixed folder)

- HTML comment marker: `<!-- HCAG:PACKET id=<dotted-id> -->`
- YAML front-matter: `id`, `title`, `short_description`, `long_description`, `token_size_estimate`, `source_files`
- Body: concatenated markdown of source files, with image refs rewritten to `assets/<name>`

### `catalog.md` (per non-root taxonomy or mixed folder)

- HTML comment marker: `<!-- HCAG:CATALOG level=<dotted-id> -->`
- YAML front-matter: `node_title`, `node_short_description`
- Body: node title, short description, and a `## Children` section listing each immediate child with kind/path/metadata

### `catalog.md` (root, emitted by `aggregate`)

- HTML comment marker: `<!-- HCAG:ROOT_CATALOG generated_at=<iso> -->`
- Body: `## Taxonomy Overview` (tree of nodes and packets with short descriptions) + `## Packets` (flat list with full metadata for every packet, including breadcrumb)
- **This is the file the runtime memory module's `get_catalog` returns.**

## 3.8 End-to-End Workflow

```
1. KB team drops raw .md and image files into taxonomy folders.
   $ ls raw_kb/billing/refunds/
     refund_policy.md  refund_states.md  flow.png  state_machine.png

2. Run preprocess (bottom-up: writes packet.md at leaves, catalog.md at nodes).
   $ hcag preprocess raw_kb/

3. Run aggregate (top-down: assembles root catalog.md).
   $ hcag aggregate raw_kb/

4. Point the runtime memory module at raw_kb/ (now normalized).
   The agent's get_catalog will serve raw_kb/catalog.md.
```

**Re-run after editorial edits:**

```
# Edit refund_policy.md, add a new section
$ vim raw_kb/billing/refunds/packet.md   # or edit sources and re-run
$ hcag preprocess raw_kb/ --force-packets --only billing/refunds/
$ hcag aggregate raw_kb/
```

## 3.9 Observability (CLI)

`hcag` writes a build log to the path in `[log]` config (default `./hcag-build.log`), using the same JSON-lines format as the runtime file log (§2.11.3). Levels:

- `INFO`: pass start/end, per-folder classification, LLM call summary, per-packet token estimate, catalog counts.
- `DEBUG`: full LLM prompts and responses, full front-matter written, file moves.
- `WARN`: skipped folders, external image references, unreferenced images copied, non-.md/non-image files ignored.
- `ERROR`: aborts (see failure-mode tables above).

The CLI also honors the `OTEL_EXPORTER_OTLP_ENDPOINT` env var: if set, build spans (`hcag.preprocess.folder`, `hcag.llm.call`, `hcag.aggregate.walk`) are exported for build-time observability. This is symmetric with §2.11 — runtime and build tooling share the same observability model.

## 3.10 Non-Goals for the CLI

- **Content editing.** `hcag` does not rewrite the meaning of source markdown; it only concatenates, moves images, and adds metadata front-matter.
- **Vector embedding generation.** Explicitly not produced; HCAG retrieval is taxonomic, not embedding-based (§1.1).
- **Runtime hot-reload.** The CLI is a build tool. Runtime picks up new artifacts on next agent bootstrap; no watcher.
- **KB validation beyond schema.** Fact-checking, link-checking across packets, and stale-content detection are separate concerns.

---

# Part 4 — The `crawl` CLI Tool

## 4.1 Purpose

`crawl` takes a set of seed URLs and builds a local Markdown knowledge base from the pages they lead to. Each seed is fetched, converted to Markdown, and its outbound links are followed recursively — staying within the site regions defined by the seed URL prefixes. The output is a local `./kb/` tree whose directory shape mirrors the domains and URL paths of the crawled sites, ready to hand to `hcag preprocess` (Part 3) as raw KB input.

## 4.2 Invocation

```
$ crawl --depth <N> <seed_url> [<seed_url> ...]
```

- `<seed_url>` — one or more starting URLs. Each seed defines both a starting point and a prefix scope (§4.3.1). At least one seed is required.
- `--depth <N>` — maximum link-following depth from any seed. `N=0` fetches only the seed documents themselves; `N=1` also fetches documents reachable in one hop from a seed; and so on.

Output is written under `./kb/` in the current working directory (§4.5).

## 4.3 Traversal Semantics

### 4.3.1 Seed prefix scope

Each seed URL doubles as a **prefix scope**. A discovered link is followed only if its URL begins with the same string as at least one of the seed URLs. This keeps the crawl inside the sites and subpaths the operator explicitly named, and prevents it from escaping to unrelated domains or wandering up to parent paths.

- A seed of `https://docs.example.com/api/v2/` allows following `https://docs.example.com/api/v2/auth.html` but **not** `https://docs.example.com/api/v1/anything` (different subpath) or `https://blog.example.com/…` (different subdomain).
- With multiple seeds, the allowed set is the union of their prefixes: a link is in scope if it matches **any** seed's prefix.

Rationale: the seed set defines both *where to start* and *what belongs in the KB* with a single knob — the operator does not have to state the site boundary a second time.

### 4.3.2 Visited-URL tracking

`crawl` maintains a set of every URL it has already fetched. If a link resolves to a URL already in that set, it is skipped — neither re-fetched nor recursed into. Every in-scope URL is therefore fetched and converted at most once per invocation, and cycles between pages cannot cause repeat work or infinite loops.

### 4.3.3 Depth

The seed URL sits at depth `0`. A document reached by following a link from a depth-`k` document is at depth `k+1`. Links discovered *inside* a document whose depth equals `--depth` are **not** followed; the document itself is still fetched, converted, and written, but no further descent occurs from it.

## 4.4 Document Types

### 4.4.1 HTML

HTML pages are fetched, parsed, and converted to Markdown. The links to follow are the `href` values of `<a>` elements in the document. Relative hrefs are resolved against the fetched document's URL before the prefix-scope check (§4.3.1).

### 4.4.2 PDF

Linked `.pdf` documents are treated as first-class pages: fetched, converted to Markdown (extracted text with document structure preserved), and written to the same layout as HTML output. PDFs do not contribute outbound links for further traversal.

### 4.4.3 Images

Images embedded in HTML pages (`<img src>`) and images embedded inside PDF documents are extracted and saved as separate files alongside the Markdown output (§4.5). Every image reference in the generated Markdown is rewritten to point at the local saved file rather than the original remote URL, so the Markdown renders correctly offline.

Images are content of their containing document, not link targets: they are neither depth-counted nor prefix-checked.

## 4.5 Output Layout

Output is rooted at `./kb/`, with the domain as the first path segment and the URL path preserved below it. For a page at `https://webdomain/topic-domain/topic/subtopic/something.html`:

- Markdown goes to `./kb/webdomain/topic-domain/topic/subtopic/something.md`.
- An embedded image named `apple.jpg` goes to `./kb/webdomain/topic-domain/topic/subtopic/something-apple.jpg`.

Rules:

- **Domain first.** Content from different sites lands in distinct top-level folders under `./kb/`, so multiple seed domains stay cleanly separated.
- **Path preservation.** Below the domain, the directory structure mirrors the URL path, so the shape of the source site is legible in the output tree.
- **Extension.** Output Markdown always uses the `.md` extension, regardless of the source (`.html`, `.htm`, `.pdf`, or a directory-index URL that ends with `/`).
- **Image naming.** Each extracted image is written with a filename of the form `<document-basename>-<image-name>`. Prefixing with the source document's basename guarantees that identically-named images extracted from different pages do not collide when they land in the same directory.

## 4.6 Relationship to `hcag`

`crawl` produces the raw markdown-and-image tree that `hcag preprocess` (§3.4) consumes. The two tools compose end-to-end:

```
$ crawl --depth 3 https://docs.example.com/api/
$ hcag preprocess kb/
$ hcag aggregate kb/
```

`crawl` is responsible only for turning a set of remote sites into a mirrored local Markdown tree. It does not classify folders, produce `packet.md` / `catalog.md`, call an LLM, or make any decisions about the KB's taxonomy — those remain `hcag`'s job.

## 4.7 Observability (CLI)

`crawl` emits a log line for every meaningful event during a crawl — each URL fetched, each Markdown document written, each image extracted, and each candidate link that was skipped — using the same JSON-lines format as the rest of the toolchain (§2.11.3, §3.9). This makes a completed crawl auditable after the fact: given the log, an operator can reconstruct exactly which URLs were visited, which were skipped and why, and which files ended up in `./kb/`.

**Configuration.** The log path defaults to `./crawl.log` and can be overridden with `--log-file <path>`. Level is controlled with `--log-level {debug,info,warn,error}` (default `info`). If `OTEL_EXPORTER_OTLP_ENDPOINT` is set, spans (`crawl.fetch`, `crawl.convert`, `crawl.image.extract`) are also exported, matching the observability model used by the runtime (§2.11) and by `hcag` (§3.9).

**Levels.**

- `INFO`:
  - Crawl start line with the resolved seed list, `--depth`, and the output root.
  - One line per fetched document: URL, depth, content type, byte size, elapsed fetch time, and the output Markdown path.
  - One line per extracted image: source document URL, remote image URL, and the local file path written under `./kb/`.
  - Crawl end summary: totals for pages fetched, pages converted, images extracted, links skipped (out-of-scope / already-visited), wall-clock elapsed, and log-level counts.
- `DEBUG`:
  - HTTP request/response headers, redirect chains, and retry attempts.
  - Markdown-conversion internals (heading count, link count, image count per document) and PDF-extraction internals (page count, image count).
  - For each page, the full list of `<a href>` values discovered, each tagged with its disposition: `queued`, `skipped:out-of-scope`, `skipped:visited`, or `skipped:depth-cap`.
- `WARN`:
  - Fetch returned a non-2xx status for an in-scope URL (URL is dropped, siblings continue).
  - Fetched content type is neither HTML nor PDF (URL is dropped).
  - Image extraction failed for a specific asset — the containing page is still written; the image reference is left pointing at the original remote URL and flagged in the log.
  - `href` value could not be parsed or resolved against the base URL.
  - Redirect chain exceeded the safety cap and was terminated.
  - Output path collision detected and resolved by disambiguation (e.g., two URLs mapping to the same local filename).
- `ERROR`:
  - Crawl cannot start: no valid seeds, seed URL malformed, or `./kb/` not writable.
  - Fetch aborted after retries due to network failure or timeout.
  - Fatal I/O error while writing Markdown or an image file.

If any `ERROR`-level event is logged during a run, `crawl` exits with a non-zero status; `WARN`-level events do not affect exit status but are reflected in the end-of-run summary.

## 4.8 Non-Goals

- **Content editing.** `crawl` does not rewrite prose, summarize pages, or filter noise; the HTML/PDF → Markdown conversion is mechanical.
- **JavaScript execution.** Only the initial fetched HTML is parsed. Pages whose content is constructed client-side are captured only to the extent that content is present in the server-rendered response.
- **Auth-gated content.** Login flows, cookies, and custom headers beyond a plain fetch are out of scope.
- **Non-HTML, non-PDF assets.** Videos, archives, and other binary formats are neither followed as links nor mirrored into `./kb/`.
- **Incremental re-crawl.** Each invocation fetches every in-scope URL once; change detection and freshness re-crawling are not provided.

---

# Part 5 — Voice Agent (LiveKit)

## 5.1 Purpose

A real-time voice interface to the HCAG agent, embeddable on a website. The user speaks; the agent transcribes, reasons over the HCAG active set, and speaks the answer back — with the running transcript and the streaming assistant response rendered live in the browser.

Two properties matter beyond the baseline agent (§1.4):

1. **Fast first-turn latency.** A voice conversation cannot afford a cold-start `check_and_load_kb` round trip on the user's first sentence. The voice session is therefore started with a **configured set of initial packet IDs** that are loaded into the active set before the room opens (§5.4.1), so the very first user turn already has the relevant knowledge in memory.
2. **Sub-second inter-turn latency.** After the first turn, subsequent LLM calls must ride the prompt cache. The voice session issues a synthetic **cache warm-up call** immediately after packet loading (§5.4.2), so the prefix that all real turns will share is committed to the provider's prompt cache before the user starts talking.

## 5.2 Component Boundary

The voice agent **wraps** the runtime defined in Parts 1–2 rather than replacing it. Everything about the HCAG active-set protocol (§2.4), token budget (§2.5), packet loader (§2.6), and prompt-cache alignment (§2.12) is reused verbatim. The voice layer adds:

- A **LiveKit worker process** that joins a LiveKit room per user session.
- A **STT adapter** (Deepgram or ElevenLabs) that streams partial and final transcripts from the user's audio track.
- A **TTS adapter** (ElevenLabs or Deepgram) that streams synthesized audio from assistant text back to the room.
- A **transcription publisher** that mirrors both sides of the conversation onto a LiveKit text/data channel so the browser can render live captions and streaming assistant text.
- A **web client** (LiveKit JS SDK) that publishes the mic track, subscribes to the agent's audio track, and renders the transcription channel.

The `AgentRuntime`, `MemoryModule`, and `catalog.md` / `packet.md` artifacts are unchanged. Swapping the voice front-end for a text front-end does not touch the reasoning path.

### 5.2.1 Voice Class Diagram

Extends the core class diagram (§2.9). `VoiceSession` is the new orchestrator; it composes an `AgentRuntime` (reused verbatim) with an `STTAdapter`, a `TTSAdapter`, and a `TranscriptionPublisher`. The two startup phases (§5.4.1, §5.4.2) are pure functions that operate on a runtime — modeled here as free-standing operations rather than methods so they can be exercised independently in tests and in the `dry-run` CLI (§5.9).

```mermaid
classDiagram
    direction LR

    class VoiceSession {
        +VoiceAgentConfig cfg
        +AgentRuntime runtime
        +STTAdapter stt
        +TTSAdapter tts
        +TranscriptionPublisher publisher
        +string turn_id
        +start() void
        +on_user_partial(text) void
        +on_user_final(text) void
        +on_llm_delta(text) void
        +on_llm_final(text) void
        +cancel_current_turn() void
    }

    class VoiceAgentConfig {
        +string kb_root
        +int max_active_tokens
        +list~string~ initial_packet_ids
        +LLMConfig llm
        +LiveKitConfig livekit
        +STTConfig stt
        +TTSConfig tts
        +WarmupConfig warmup
    }

    class AgentRuntime {
        <<from §2.9>>
        +bootstrap() void
        +run_turn(user_msg) response
    }

    class STTAdapter {
        <<interface>>
        +stream(audio) async_iter~SttEvent~
        +close() void
    }

    class TTSAdapter {
        <<interface>>
        +stream(text) async_iter~AudioFrame~
        +cancel() void
        +close() void
    }

    class DeepgramSTT
    class ElevenLabsSTT
    class ElevenLabsTTS
    class DeepgramTTS

    class TranscriptionPublisher {
        +int seq
        +bind(sink) void
        +emit(kind, turn_id, text) TranscriptionMessage
    }

    class TranscriptionMessage {
        +int seq
        +string kind
        +string turn_id
        +string text
    }

    class PreloadResult {
        +list~string~ loaded_ids
        +list~string~ skipped_unknown
        +int tokens_used
        +int elapsed_ms
        +bool budget_exceeded
    }

    class WarmupResult {
        +bool ran
        +int elapsed_ms
        +int prompt_tokens
        +int cache_write_tokens
    }

    class preload_initial_packets {
        <<function>>
        +preload_initial_packets(runtime, ids, logger) PreloadResult
    }

    class warmup_prompt_cache {
        <<function>>
        +warmup_prompt_cache(runtime, logger, enabled, prompt) WarmupResult
    }

    VoiceSession --> AgentRuntime : wraps
    VoiceSession --> STTAdapter : uses
    VoiceSession --> TTSAdapter : uses
    VoiceSession --> TranscriptionPublisher : publishes via
    VoiceSession --> VoiceAgentConfig : configured by

    DeepgramSTT ..|> STTAdapter
    ElevenLabsSTT ..|> STTAdapter
    ElevenLabsTTS ..|> TTSAdapter
    DeepgramTTS ..|> TTSAdapter

    TranscriptionPublisher ..> TranscriptionMessage : emits

    preload_initial_packets ..> AgentRuntime : mutates history of
    preload_initial_packets ..> PreloadResult : returns
    warmup_prompt_cache ..> AgentRuntime : reads prefix of
    warmup_prompt_cache ..> WarmupResult : returns
```

**How this composes with §2.9.** `AgentRuntime`, `MemoryModule`, `Catalog`, `Delta`, and the packet loader are unchanged — they appear in this diagram only where `VoiceSession` reaches into them. Every real-turn call still goes through `AgentRuntime.run_turn`, so §2.10's sequence diagrams apply to the reasoning path even inside a voice session.

## 5.3 Real-Time Architecture

```mermaid
flowchart LR
    subgraph Browser
        Mic[Mic Track]
        Spk[Speaker Track]
        UI[Transcript UI]
    end
    subgraph LiveKit
        Room[LiveKit Room]
    end
    subgraph Worker[Voice Agent Worker]
        STT[STT Adapter]
        VA[Voice Session]
        RT[AgentRuntime]
        TTS[TTS Adapter]
        Pub[Transcription Publisher]
    end
    subgraph HCAG[HCAG Core]
        MM[Memory Module]
        KB[(KB on disk)]
    end
    LLM[[LLM provider]]

    Mic -- audio --> Room
    Room -- audio --> STT
    STT -- text --> VA
    VA -- prompt --> RT
    RT -- turn --> LLM
    LLM -- streaming text --> RT
    RT -- reply --> VA
    VA -- text --> TTS
    TTS -- audio --> Room
    Room -- audio --> Spk
    VA -- partials + finals --> Pub
    Pub -- data channel --> Room
    Room -- data channel --> UI
    RT -- get_catalog --> MM
    RT -- check_and_load_kb --> MM
    MM --> KB
```

**One worker per session.** A new LiveKit room implies a new worker instance holding its own `AgentRuntime`, its own active set, and its own STT/TTS stream handles. Cross-session state is not shared (each user's classification and packet load are independent).

## 5.4 Session Startup

Session startup runs to completion before the browser is allowed to send audio. Its two phases are ordered:

### 5.4.1 Warm-start with initial packets

The voice agent accepts an ordered list of packet IDs — `initial_packet_ids` — via config (§5.8) or CLI (§5.9). Before opening the room to input:

1. Instantiate `AgentRuntime` with the standard bootstrap (§2.7): read `catalog.md`, inject into the system prompt.
2. For each ID in `initial_packet_ids`, call `memory_module.check_and_load_kb(requested=[id], active=<current>)` and apply the returned delta exactly as an in-turn call would. This produces a byte-identical sequence of tool-result blocks in history — the same shape a normal turn would create — so the cache-alignment rules (§2.12) apply unchanged.
3. If the union of initial packets exceeds `MAX_ACTIVE_TOKENS`, startup fails with an explicit error (`errors[].reason = "BudgetExceeded"`). Voice sessions do not silently drop preloads — the operator misconfigured the initial set.
4. Unknown packet IDs are logged as `voice.startup.unknown_packet` WARN entries and skipped; startup continues with the remainder. Rationale: an outdated deploy config should not brick the room.

The initial-packet list is a **classification hint**, not a policy override. Nothing prevents the agent from calling `check_and_load_kb` mid-conversation to load additional branches; the preload only guarantees that the *first* user sentence lands on a warm active set.

### 5.4.2 Prompt-cache warm-up call

After initial packets are loaded, the voice agent issues a synthetic LLM call whose sole purpose is to commit the shared prefix to the provider's prompt cache:

```
system: <static prefix> + <catalog> + <preload tool-result blocks>
user:   "Ready. Await user turn."
```

with `cache_control` breakpoints placed at the end of the system block and after each preload tool-result block (§2.12). The response is discarded. The provider now holds a cache entry keyed on the exact prefix that every real turn in this session will share, so the first real turn incurs a cache hit rather than a full-prefix read.

**Ordering matters.** The warm-up call must run *after* §5.4.1 — the byte-stable prefix is only defined once the initial packets have been folded in. Running it earlier caches a prefix the real turns will never send, wasting the write.

**Cost accounting.** The warm-up call is a full-prefix cache *write*, priced above a cold prompt at most providers. It is paid once per session and amortizes across every subsequent turn. If a session ends before the first real turn, the write is a loss; §5.10 tracks this as `voice.warmup.wasted` for tuning.

### 5.4.3 Startup Sequence Diagram

The two-phase startup, end-to-end. The room is created up front but the browser is told to wait until `system.ready` is emitted — nothing from the mic is transported to the STT adapter before the cache is primed.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant W as Voice Worker
    participant R as LiveKit Room
    participant V as Voice Session
    participant A as AgentRuntime
    participant M as Memory Module
    participant L as LLM
    participant P as Transcription Publisher

    Note over W,P: DISPATCH — room-join event received

    W->>R: join room
    R-->>W: connected
    W->>V: construct VoiceSession(cfg, runtime)
    V->>P: bind publisher to room

    Note over V,M: PHASE 1 — WARM-START (§5.4.1)

    V->>A: bootstrap
    A->>M: get_catalog
    M-->>A: catalog
    A-->>V: system prompt ready

    V->>A: preload_initial_packets(ids)
    A->>M: check_and_load_kb(requested=ids, active=empty)
    M-->>A: delta (loaded packets)
    A-->>V: PreloadResult

    alt budget exceeded
        V->>P: emit system.error(reason=budget_exceeded)
        V->>R: close room
        Note over V,R: startup aborted
    end

    Note over V,L: PHASE 2 — CACHE WARM-UP (§5.4.2)

    V->>A: warmup_prompt_cache(prompt)
    A->>L: chat(history + stub user, cache_control)
    L-->>A: response (discarded)
    A-->>V: WarmupResult (cache_write_tokens)

    Note over V,B: ROOM OPENS TO INPUT

    V->>P: emit system.ready
    P->>R: publish on hcag.transcription
    R-->>B: system.ready
    Note over B: UI unlocks mic — first real turn can begin
```

Failure paths are explicit: an unknown packet ID logs `voice.startup.unknown_packet` and continues (step 8 loops on the remaining IDs); a budget-exceeded delta shortcuts to `system.error` and closes the room; a warm-up call that errors is a WARN, not a fatal — the session proceeds without a primed cache (first real turn eats the full-prefix read).

## 5.5 Real-Time Turn Pipeline

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant R as LiveKit Room
    participant S as STT Adapter
    participant V as Voice Session
    participant A as AgentRuntime
    participant L as LLM
    participant T as TTS Adapter

    Note over B,T: WARM ROOM — initial packets loaded, cache primed

    B->>R: mic audio frames
    R->>S: audio stream
    S-->>V: partial transcript
    V->>R: publish partial (data channel)
    R-->>B: render caption

    S-->>V: final transcript
    V->>R: publish final (data channel)
    V->>A: user_text = final
    A->>L: turn (cached prefix + user_text)
    Note over L: Cache hit on prefix<br/>Streams response tokens

    loop streaming
        L-->>A: token
        A-->>V: token
        V->>R: publish assistant partial (data channel)
        V->>T: token chunk
        T-->>R: audio frame
        R-->>B: play audio
        R-->>B: render assistant text
    end

    L-->>A: end of turn
    A-->>V: final assistant text
    V->>R: publish assistant final
```

**Barge-in.** If the STT adapter emits a new final transcript while TTS is still speaking, the voice session cancels the in-flight TTS stream and the in-flight LLM stream, publishes an `assistant.interrupted` marker on the data channel, and starts a new turn. The `AgentRuntime` sees the cancellation as a turn-boundary event and does not persist the truncated assistant message into history.

**Streaming while thinking.** Assistant text is published to the data channel as tokens arrive from the LLM, *before* TTS has finished synthesizing the corresponding audio. This lets the browser render text ahead of audio — perceptually the response feels instantaneous even if audio has a few hundred ms of TTS latency.

## 5.6 STT / TTS Provider Selection

Both STT and TTS are provider-neutral behind a small adapter interface. Two providers are supported out of the box:

| Role | Providers | Selection knob |
|---|---|---|
| STT (audio → text) | `deepgram`, `elevenlabs` | `stt.provider` (config) or `--stt-provider` (CLI) |
| TTS (text → audio) | `elevenlabs`, `deepgram` | `tts.provider` (config) or `--tts-provider` (CLI) |

Each provider block additionally accepts:

- `model` — provider-specific model identifier (e.g. `nova-2-general` for Deepgram STT, `eleven_turbo_v2_5` for ElevenLabs TTS). Overridable at the CLI with `--stt-model` / `--tts-model`.
- `api_key_env` — env var to read the credential from; the raw key is never written to config.
- `language` (STT) / `voice_id` (TTS) — optional provider-specific tuning.
- `endpoint` — override URL for self-hosted or region-pinned endpoints.

**CLI overrides win.** Precedence is `CLI flag > env var > config file > adapter default`. The startup log line records the resolved provider, model, and endpoint for each of STT and TTS (`voice.startup.resolved`) so a mis-set flag is obvious at a glance.

**Provider swap is per session, not per turn.** Changing providers mid-session would invalidate the cache warm-up (system prompt is unaffected, but audio format contracts break). Provider choice is fixed at session start.

## 5.7 Live Transcription Channel (Web Client Contract)

Transcription and streaming text are published on a LiveKit **data channel** named `hcag.transcription`. Payloads are JSON, one message per event, monotonically increasing `seq`:

```json
{ "seq": 42, "kind": "user.partial",     "text": "how do partial ref",     "turn_id": "t_9" }
{ "seq": 43, "kind": "user.final",       "text": "how do partial refunds work", "turn_id": "t_9" }
{ "seq": 44, "kind": "assistant.delta",  "text": "Partial refunds are ", "turn_id": "t_9" }
{ "seq": 45, "kind": "assistant.delta",  "text": "issued when...",       "turn_id": "t_9" }
{ "seq": 46, "kind": "assistant.final",  "text": "Partial refunds are issued when...", "turn_id": "t_9" }
{ "seq": 47, "kind": "assistant.interrupted", "turn_id": "t_9" }
```

- `kind` values are namespaced (`user.*`, `assistant.*`, `system.*`) so the client can render user captions and assistant text in different UI slots without keyword-matching text content.
- `turn_id` groups deltas with their finalizing message and lets the client discard stale partials after an `assistant.interrupted`.
- The `system.*` namespace carries session lifecycle events (`system.ready` once §5.4.2 completes; `system.error` on unrecoverable failure) so the browser can show a "connecting…" state until the room is warm.
- Audio itself is **not** on the data channel — it flows through the standard LiveKit audio track. The data channel is text-only.

The web client is not otherwise specified here: any application that speaks the LiveKit JS SDK and consumes `hcag.transcription` per the schema above is a valid front-end.

## 5.8 Configuration

Voice agent configuration lives in `voice.toml`, layered on top of the same `AgentConfig` used by the text runtime:

```toml
kb_root           = "./my-kb"
max_active_tokens = 32000

# Preloaded packet IDs (§5.4.1). Ordered — earlier IDs take budget first.
initial_packet_ids = ["billing.refunds", "billing.invoices", "auth.oauth"]

[llm]
provider = "anthropic"
model    = "claude-3-5-haiku-20241022"

[livekit]
url        = "wss://my-app.livekit.cloud"
api_key_env    = "LIVEKIT_API_KEY"
api_secret_env = "LIVEKIT_API_SECRET"
room_prefix    = "hcag-"       # rooms are named "<prefix><session-id>"

[stt]
provider    = "deepgram"                  # deepgram | elevenlabs
model       = "nova-2-general"
api_key_env = "DEEPGRAM_API_KEY"
language    = "en-US"
endpoint    = ""                          # optional override

[tts]
provider    = "elevenlabs"                # elevenlabs | deepgram
model       = "eleven_turbo_v2_5"
voice_id    = "21m00Tcm4TlvDq8ikWAM"
api_key_env = "ELEVENLABS_API_KEY"
endpoint    = ""

[warmup]
enabled = true                            # skip only for local dev with cache-less providers

[log]
file_path = "./hcag-voice.log"
level     = "INFO"
```

**Reused blocks.** `[llm]`, `[log]`, and OTEL config follow the same schemas as §2.13 and §3.6 — the voice agent does not fork configuration models for the pieces it shares with the text runtime.

## 5.9 CLI

The voice agent runs as a long-lived worker process:

```
$ hcag-voice serve --config voice.toml [flags]
```

Flags override the corresponding `voice.toml` fields:

| Flag | Overrides | Notes |
|---|---|---|
| `--kb-root PATH` | `kb_root` | |
| `--initial-packets id1,id2,...` | `initial_packet_ids` | Comma-separated. Order preserved. |
| `--stt-provider {deepgram,elevenlabs}` | `stt.provider` | |
| `--stt-model NAME` | `stt.model` | Any provider-valid model ID. |
| `--tts-provider {elevenlabs,deepgram}` | `tts.provider` | |
| `--tts-model NAME` | `tts.model` | |
| `--tts-voice ID` | `tts.voice_id` | TTS-only. |
| `--livekit-url URL` | `livekit.url` | |
| `--no-warmup` | `warmup.enabled = false` | Skip §5.4.2 (dev only). |
| `--log-file PATH` / `--log-level LEVEL` | `log.file_path` / `log.level` | Same shape as §3.9 / §4.7. |

`serve` blocks in the foreground. It joins the LiveKit dispatcher, accepts room-join events, and spins up one `AgentRuntime + STT + TTS` triple per session. Shutdown on `SIGTERM` drains in-flight sessions (finishes the current TTS utterance, publishes `system.error` with reason `shutdown`, closes the room) before exiting.

A one-shot `hcag-voice dry-run` subcommand runs §5.4.1 and §5.4.2 without joining any room and prints the resolved provider/model/preload summary. Useful in CI to catch a bad initial-packet ID before deploy.

## 5.10 Observability

Reuses the JSON-lines log format (§2.11.3) and OTEL trace model (§2.11.2). Voice-specific events:

- `INFO`:
  - `voice.startup.resolved` — resolved STT/TTS provider + model + endpoint, and the effective `initial_packet_ids`.
  - `voice.startup.preload_done` — packet IDs loaded, tokens consumed, wall-clock elapsed.
  - `voice.warmup.done` — prompt-cache warm-up latency and reported cache write size.
  - `voice.session.opened` / `voice.session.closed` — room name, session ID, duration, turn count.
  - `voice.turn.completed` — turn ID, user-final length, assistant-final length, first-token latency, first-audio latency, total turn duration.
- `DEBUG`:
  - STT partials/finals with timestamps.
  - Assistant token deltas.
  - TTS chunk boundaries.
  - Barge-in cancellation points.
- `WARN`:
  - `voice.startup.unknown_packet` — packet ID in `initial_packet_ids` not present in catalog.
  - `voice.warmup.wasted` — session closed before any real turn used the primed cache.
  - STT/TTS transient errors that were retried successfully.
  - Data-channel publish backpressure.
- `ERROR`:
  - `voice.startup.budget_exceeded` — initial packets exceed `MAX_ACTIVE_TOKENS`; startup aborted.
  - STT/TTS/LiveKit connection failures that terminate the session.
  - LLM streaming errors that could not be recovered mid-turn (the current turn ends with a `system.error` on the data channel; the session stays alive for the next turn).

New OTEL spans:

- `voice.session` (root, per room) with attributes for STT/TTS provider and model, initial-packet count, warmup latency.
- `voice.turn` (child) with first-token / first-audio latency attributes.
- `voice.stt.stream` and `voice.tts.stream` bracket the per-turn STT and TTS work so the audio pipeline is visible alongside the LLM call.

## 5.11 Non-Goals

- **Multi-party audio.** One user, one agent per room. Conference-style N-to-1 or N-to-N sessions are out of scope.
- **Speaker diarization.** Single-speaker input assumed. Distinguishing multiple speakers on the mic track is a provider-level feature and not exposed here.
- **Client-side reasoning.** The browser is a thin transport for audio in, audio out, and transcript rendering. No inference runs in the browser.
- **Persistent conversation memory across sessions.** Each room is a fresh `AgentRuntime`. Long-term user memory is a separate concern.
- **Voice cloning / custom voices.** Whatever the TTS provider offers is what is available; the voice agent does not build or manage voice models itself.
- **Failover between STT/TTS providers mid-session.** Provider is fixed at session start (§5.6). Switching mid-session would invalidate the warm cache and audio contracts.

---

# Part 6 — The `evalgen` CLI Tool

## 6.1 Purpose

`evalgen` generates evaluation question / expected-answer pairs from a normalized KB, for use in scoring retrieval quality and answer quality against the runtime agent (Part 2) or the voice agent (Part 5). It gives KB owners a repeatable way to build an eval set that is grounded in the same content the agent will serve at runtime — so regressions in retrieval selection, packet coverage, or multimodal reasoning surface as measurable score drops on a fixed set of questions.

The tool is a **question / expected-answer generator only**. It does not run the agent, score responses, or persist verdicts; those columns are left empty in the CSV output and filled in by a separate evaluation pass (§6.7).

## 6.2 KB Input Model

`evalgen` consumes a KB directory that has already been normalized by `hcag preprocess` (§3.4):

- Each leaf (or mixed) folder contains a `packet.md` with HCAG front-matter (id, title, descriptions, token estimate).
- Images referenced by a packet live in that packet's `assets/` subfolder.
- A root `catalog.md` (from `hcag aggregate`, §3.5) is optional for generation but, when present, is used to bias cross-packet pairing toward taxonomically-related packets (§6.4.4).

`evalgen` reads packets as-is; it does not modify the KB. Source `.md` files outside `packet.md` and images outside `assets/` are ignored — the tool operates only on the artifacts the runtime actually serves.

## 6.3 Invocation

```
$ evalgen <kb_root> --out <output.csv> [--total <N> | --simple <n1> --medium <n2> --complex <n3> --hard-1 <n4> --hard-2 <n5>] [options]
```

| Parameter | Required | Description |
|---|---|---|
| `<kb_root>` | yes | Path to the normalized KB directory (the same directory handed to `hcag aggregate`). |
| `--out <path>` | yes | Path to the output CSV file. Overwritten if it exists. |
| `--total <N>` | one-of | Total number of question/answer pairs to generate, split equally across the five types (§6.5). |
| `--simple <n>` | one-of | Explicit count of `simple` questions to generate. |
| `--medium <n>` | one-of | Explicit count of `medium` questions to generate. |
| `--complex <n>` | one-of | Explicit count of `complex` questions to generate. |
| `--hard-1 <n>` | one-of | Explicit count of `hard-1` (cross-packet) questions to generate. |
| `--hard-2 <n>` | one-of | Explicit count of `hard-2` (multimodal) questions to generate. |
| `--seed <int>` | no | Random seed for packet/paragraph selection. Fixed seed → reproducible eval set for a given KB revision. |
| `--id-prefix <str>` | no | Prefix for `question_id` values (default `q`). Useful when merging multiple eval sets. |
| `--config <path>` | no | Path to `evalgen.toml` (§6.8). Defaults to `<kb_root>/evalgen.toml` if present. |

**Mutual exclusivity.** `--total` and any of the `--<kind> <n>` flags are mutually exclusive. Pass **either** a single `--total` **or** one-to-five explicit per-type flags; mixing the two forms is a startup error. Per-type flags default to `0` when omitted, so `--simple 20 --hard-2 5` generates exactly 25 questions of only those two kinds.

Example invocation:

```
$ evalgen kb/ --out kb-eval.csv --total 100 --seed 42
```

Generates 100 pairs — 20 `simple`, 20 `medium`, 20 `complex`, 20 `hard-1`, 20 `hard-2` — using seed `42`, and writes them to `kb-eval.csv`. Equivalent explicit form:

```
$ evalgen kb/ --out kb-eval.csv \
    --simple 20 --medium 20 --complex 20 --hard-1 20 --hard-2 20 --seed 42
```

## 6.4 Question Types

Each row's `kind` column carries one of five string tags corresponding to how the question was constructed. The tags are stable — the evaluation pass filters and scores by `kind`.

### 6.4.1 `simple`

- **Definition.** Answerable verbatim from a single packet. FAQ-style factual question with no reasoning; the answer text appears literally in the packet body.
- **Source.** One packet, one contiguous span (typically a sentence or short paragraph).
- **Expected answer.** A verbatim or near-verbatim quote from the packet — the LLM extracts a self-contained fact; no rewording beyond trimming.
- **Signal.** Measures whether the agent retrieved and read the correct packet at all. A `simple` failure usually means retrieval, not reasoning, is broken.

### 6.4.2 `medium`

- **Definition.** Requires **reasoning grounded in a single paragraph** of a single packet. The answer is not a verbatim quote — the reader must interpret or combine facts within one paragraph.
- **Source.** One packet, one paragraph (a contiguous block delimited by blank lines in `packet.md`).
- **Expected answer.** A short natural-language answer whose supporting facts all appear in the chosen paragraph, but which is not a direct quotation of it.
- **Signal.** Measures within-passage comprehension once retrieval has succeeded.

### 6.4.3 `complex`

- **Definition.** Requires **significant deduction across at least three distinct concepts, drawn from at least three different paragraphs within a single `packet.md`**.
- **Source.** One packet; at least three distinct paragraphs, each contributing a different concept the answer depends on.
- **Expected answer.** A synthesized answer that cannot be produced from any single paragraph in isolation. The generation prompt requires the LLM to identify each paragraph's contribution before composing the answer, so the eval remains auditable.
- **Signal.** Measures whole-packet reasoning — whether the agent uses everything a loaded packet contains, not just the first hit.

### 6.4.4 `hard-1` (cross-packet)

- **Definition.** Requires **two packets** to answer correctly, drawing on **at least three different paragraphs spread across those two packets** (e.g., 2 + 1, or 1 + 2). Neither packet alone is sufficient.
- **Source.** A pair of packets. When a root `catalog.md` is present, pairs are biased toward siblings or cousins in the taxonomy (topically adjacent) because those are the pairs the agent is most likely to load together; when no catalog is present, pairs are drawn uniformly at random from the packet set.
- **Expected answer.** A synthesized answer whose supporting facts are split across the two packets, with at least three distinct paragraphs contributing.
- **Signal.** Measures the `check_and_load_kb` selection loop (§2.3.2) — specifically whether the agent recognizes it needs a second packet and loads it, rather than answering from only the first.

### 6.4.5 `hard-2` (multimodal)

- **Definition.** Requires an **image from the packet's `assets/` folder to be read together with the packet markdown**. The image must hold information **essential** to the answer, so that the question cannot be answered from the markdown alone and the model must perform multimodal reasoning across text and image.
- **Source.** One packet whose `assets/` folder contains at least one image. Only packets with images are eligible; packets with no assets are silently skipped for this kind.
- **Expected answer.** A short answer whose key fact is visually present in the image (a label on a diagram, a value in a chart, a state in a state-machine figure, a component in a screenshot) and only weakly implied — or not implied at all — by the surrounding markdown.
- **Signal.** Measures the multimodal loading path (§2.6) — whether images are actually attached to the LLM call and whether the model uses them.
- **Availability.** If the requested `--hard-2` count exceeds the number of image-bearing packets, `evalgen` generates as many as it can and logs a `WARN` indicating the shortfall. It does **not** substitute another kind to reach the requested total.

## 6.5 Quantity Control

`evalgen` accepts the requested question count in exactly one of two forms:

1. **Single total (`--total N`).** `evalgen` divides `N` equally across the five kinds. If `N` is not divisible by 5, the remainder is distributed one at a time in the order `simple, medium, complex, hard-1, hard-2` — so `--total 12` produces `3, 3, 2, 2, 2`.
2. **Explicit per-type counts (`--simple n1 --medium n2 --complex n3 --hard-1 n4 --hard-2 n5`).** Any subset may be passed; omitted kinds default to `0`. The total emitted is exactly the sum.

If a per-type count exceeds the maximum feasible for that kind (e.g., more `hard-2` than image-bearing packets), `evalgen` emits as many as it can, logs a `WARN` naming the shortfall (`requested=N, generated=M, kind=hard-2, reason=insufficient_image_packets`), and continues with the remaining kinds. The run's exit code is non-zero only for `ERROR`-level events (§6.9), not shortfalls.

## 6.6 Generation Algorithm

Broadly, for each kind, `evalgen`:

1. Selects the required packet(s) and paragraph(s) per the kind's rules (§6.4), using the configured `--seed` for reproducibility.
2. Sends the selected content — packet markdown plus any required images for `hard-2` — to the configured LLM (§6.8) with a fixed per-kind prompt template. The prompt instructs the model to produce one `question` and one `expected_answer` grounded strictly in the supplied content.
3. Validates the LLM's response against per-kind constraints (e.g., `complex` must cite at least three paragraphs; `hard-2` must reference at least one image). On validation failure, the item is retried up to a configurable cap (default 2); persistent failures are dropped with a `WARN`.
4. Assigns a stable `question_id` of the form `<prefix>-<zero-padded-index>` (e.g., `q-0001`) in generation order.
5. Appends the row to the output CSV with the `actual_answer`, `score`, and `remark` columns left empty (§6.7).

Kinds are generated in the fixed order `simple → medium → complex → hard-1 → hard-2`, so `question_id`s cluster by kind — useful when diff-ing eval runs.

## 6.7 Output CSV Schema

`evalgen` writes a single CSV file with a header row and one row per generated pair. Columns are fixed and ordered:

| Column | Written by `evalgen` | Description |
|---|---|---|
| `question_id` | yes | Stable identifier (`<prefix>-<zero-padded-index>`), unique within the file. |
| `kind` | yes | One of `simple`, `medium`, `complex`, `hard-1`, `hard-2`. |
| `question` | yes | The generated question text, single-line where possible; multi-line values are quoted per RFC 4180. |
| `expected_answer` | yes | The reference answer produced against the KB. |
| `actual_answer` | **empty** | Populated during evaluation by whatever harness runs the agent. |
| `score` | **empty** | Integer 0–3, populated during evaluation. `0`=wrong, `1`=partially correct, `2`=mostly correct, `3`=fully correct. `evalgen` always writes this empty. |
| `remark` | **empty** | Free-text notes from the evaluator (missing packet, wrong image, hallucination, etc.). `evalgen` always writes this empty. |

CSV formatting rules:

- UTF-8, LF line endings, RFC 4180 quoting.
- Header row is always present.
- The final three columns (`actual_answer`, `score`, `remark`) are always emitted as empty fields — never omitted, so downstream tools can open the file with a fixed 7-column schema.

Example (header + two rows):

```csv
question_id,kind,question,expected_answer,actual_answer,score,remark
q-0001,simple,"How long does a standard refund take to process?","5–7 business days.",,,
q-0021,hard-2,"According to the refund state machine, which state immediately follows ""pending_review""?","approved",,,
```

## 6.8 Configuration

`evalgen` reads an optional `evalgen.toml` (or per-invocation flags):

```toml
[llm]
provider = "anthropic"            # anthropic | openai | bedrock | ollama | llamacpp
model    = "claude-opus-4-7"      # generation quality benefits from a strong model
api_key_env = "ANTHROPIC_API_KEY"
endpoint = ""                     # override for local/self-hosted

[llm.prompts]
simple  = "prompts/eval_simple.md"
medium  = "prompts/eval_medium.md"
complex = "prompts/eval_complex.md"
hard_1  = "prompts/eval_hard1.md"
hard_2  = "prompts/eval_hard2.md"

[generation]
max_retries_per_item = 2          # retry cap on validation failure
paragraph_min_chars  = 120        # ignore too-short "paragraphs" for medium/complex/hard-1
cross_packet_bias    = "taxonomy" # taxonomy | uniform — pair selection for hard-1

[log]
file_path = "./evalgen.log"
level     = "INFO"
```

Local model support mirrors `hcag` (§3.6): `provider = "ollama"` or `"llamacpp"` with a local `endpoint` runs the whole generation without cloud credentials. Question quality varies with model choice; `hard-2` in particular requires a multimodal-capable model.

## 6.9 Failure Modes

| Condition | Behavior |
|---|---|
| `<kb_root>` has no packets | ERROR — nothing to generate against; exit non-zero. |
| Both `--total` and any `--<kind>` flag passed | ERROR at startup — mutually exclusive. |
| No image-bearing packets and `hard-2 > 0` requested | WARN, `hard-2` count reduced to `0`; other kinds proceed. |
| Requested `hard-2` count exceeds image-bearing packets | WARN, shortfall logged; produce as many as feasible. |
| LLM call fails for a single item | WARN, item dropped; run continues with next item. |
| LLM validation fails past `max_retries_per_item` | WARN, item dropped; run continues. |
| Output CSV path not writable | ERROR at startup — fail fast rather than partial write. |
| Config references a prompt template that does not exist | ERROR at startup. |

If any `ERROR`-level event fires, `evalgen` exits with a non-zero status. `WARN`-level shortfalls do not affect exit status but are surfaced in the end-of-run summary.

## 6.10 Observability (CLI)

`evalgen` writes a JSON-lines log to the path in `[log]` config (default `./evalgen.log`), matching the format used by the runtime (§2.11.3), `hcag` (§3.9), and `crawl` (§4.7):

- `INFO`: run start (KB path, requested counts, resolved counts after feasibility check), per-item generation summary (`question_id`, `kind`, source packet id(s), token usage), run end summary (per-kind generated/dropped counts, wall-clock elapsed).
- `DEBUG`: full LLM prompts and responses per item, chosen paragraph offsets, image paths attached for `hard-2`.
- `WARN`: kind shortfalls, dropped items (with reason), packets skipped for `hard-2` (no images), duplicate question detection (if the same question text is generated twice, the second is dropped).
- `ERROR`: startup failures, unwritable output path, KB with no packets.

If `OTEL_EXPORTER_OTLP_ENDPOINT` is set, spans (`evalgen.run`, `evalgen.item`, `evalgen.llm.call`) are exported — symmetric with §2.11, §3.9, §4.7.

## 6.11 Non-Goals

- **Running the agent.** `evalgen` only produces question / expected-answer pairs. Executing the agent against those questions, filling `actual_answer`, and scoring are separate concerns handled by a downstream eval harness.
- **Judging answers.** The `score` and `remark` columns are always empty on output; `evalgen` does not implement an LLM-as-judge or any other scoring mechanism.
- **Ground-truth curation.** Generated `expected_answer` values are grounded in the KB but are themselves LLM output. Human review of the eval set before use is expected; `evalgen` does not claim editorial correctness.
- **Adversarial or jailbreak questions.** All questions are strictly grounded in the KB's own content. Prompt-injection probes, safety evals, and out-of-distribution questions are out of scope.
- **Cross-run diffing or eval-set versioning.** Each invocation writes a fresh CSV. Snapshotting eval sets under version control and diffing successive runs is left to the caller (e.g., commit the CSV alongside the KB revision it was generated from).
