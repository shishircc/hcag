# HCAG vs Flat RAG — benchmark results

Both agents answered the **same 37 questions** against the **same MOM knowledge base**, over the
same HTTP contract (`POST /chat`), scored by the same judge on the same 0–3 rubric. The only
variable is the retrieval architecture: HCAG navigates a taxonomy and loads whole packets; the RAG
baseline runs hybrid (vector + BM25) search over 659 chunks.

| | HCAG | Flat RAG |
|---|---|---|
| **Mean score** (0–3) | **2.76** | 1.97 |
| **Pass rate** (score ≥ 2) | **100%** (37/37) | 64.9% (24/37) |
| **Full marks** (score 3) | 28 | 15 |
| **Answers below "partially correct"** | **0** | 13 |
| **Refusals on in-scope questions** | **0** | 10 |
| Head-to-head | **wins 20** | wins 1 (16 ties) |

The averages understate the interesting part. The gap is small on easy questions and roughly
**four times larger** on the two hardest categories.

---

## Files

| File | What it is |
|---|---|
| [`hcag-kb-eval-report.html`](./hcag-kb-eval-report.html) | HCAG run — full report, per-question answers, judge remarks |
| [`hcag-kb-eval-scored.csv`](./hcag-kb-eval-scored.csv) | HCAG run — scores as data (`question_id, kind, question, expected_answer, source, actual_answer, score, remark`) |
| [`rag-kb-eval-report.html`](./rag-kb-eval-report.html) | RAG run — full report |
| [`rag-kb-eval-scored.csv`](./rag-kb-eval-scored.csv) | RAG run — scores as data |
| [`validation3.csv`](./validation3.csv) | The question set both agents were given |
| `evalrun.log` | Harness log for both runs (start, per-row completion, written artefacts) |
| `hcag-agent.log` | HCAG server log — catalog loads, `check_and_load_kb` calls, active packet set per turn |
| `rag-agent.log` | RAG server log — chunks kept/dropped and context tokens per turn |

The three `.log` files are present in this folder on disk but are **not** committed — `.gitignore`
excludes `*.log`.

---

## Scores by difficulty

| Difficulty | n | HCAG | RAG | Δ | HCAG full marks | RAG full marks | H wins | R wins | ties |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `simple` — FAQ lookup, no reasoning | 8 | **3.00** | 2.62 | +0.38 | 8/8 | 5/8 | 3 | 0 | 5 |
| `medium` — reasoning within one paragraph | 6 | **2.67** | 2.33 | +0.33 | 4/6 | 3/6 | 2 | 0 | 4 |
| `complex` — ≥3 concepts across one packet | 8 | **2.50** | 2.12 | +0.38 | 4/8 | 4/8 | 3 | 1 | 4 |
| `hard-1` — cross-packet, two packets required | 7 | **2.86** | 1.57 | **+1.29** | 6/7 | 1/7 | 6 | 0 | 1 |
| `hard-2` — key fact is in an **image** | 8 | **2.75** | 1.25 | **+1.50** | 6/8 | 2/8 | 6 | 0 | 2 |
| **All** | 37 | **2.76** | 1.97 | +0.78 | 28 | 15 | 20 | 1 | 16 |

The difficulty levels are the generator's five kinds (`hcag/prompts/evalgen/`): `simple` is looked
up, `medium` is interpreted from a single paragraph, `complex` needs three concepts from three
paragraphs of one document, `hard-1` needs two separate documents, and `hard-2` needs something
visible in an image and *not* stated in the surrounding text.

### Read the distribution, not just the mean

```
score   3    2    1    0        (3 = fully answered, 0 = wrong / refused)
HCAG   28    9    0    0
RAG    15    9   10    3
```

**HCAG never scored below 2.** Its 9 imperfect answers were all "correct but noisy" — the right
facts plus extraneous detail, or a figure quoted less precisely than the reference. It was never
wrong and never refused.

RAG's failures are a different shape: **13 of 37 answers were missing key points or wrong**, and
**10 were outright refusals** — "I don't have enough information to answer that from the knowledge
base" — concentrated in `hard-2` (5), `hard-1` (3) and `complex` (2). Nine of those ten scored 0 or
1; one still earned a 2 by refusing only the part it could not support. Per category, the collapse
is stark:

```
RAG simple : 3 3 3 3 3 2 2 2      solid
RAG hard-1 : 3 2 2 2 1 1 0        half the set below "partially correct"
RAG hard-2 : 3 3 1 1 1 1 0 0      six of eight
```

---

## Why the gap widens exactly there

The pattern is the one [`DESIGN.md`](../DESIGN.md) predicts, and each cliff maps to a specific
design decision.

### `simple` / `medium` — a small, steady edge

§1.3.3 says flat RAG is the right tool for "FAQ-style — short, self-contained, answered from a
single passage", and the numbers agree: RAG is at 2.62 on `simple`, its strongest category. HCAG's
edge here (+0.38) is not about finding the answer — both find it — but about **completeness**. HCAG
holds the whole document, so nothing adjacent to the answer is missing; RAG holds the top-k chunks
and scored 2 rather than 3 three times because a detail sat just outside the retrieved window.

### `complex` — whole documents vs fragments

§1.2 Problem 1: *"HCAG retrieves whole leaf documents, not fragments. A full document carries its
own disambiguating context — definitions, caveats, scope — that a chunk usually strips away."*

A `complex` question needs three concepts from three paragraphs of one document. HCAG loads the
document and has all three by construction. RAG needs all three paragraphs to co-rank in a single
query. Example — **q-0023** (ONE Pass financial documents → appeal → cancellation), HCAG 3, RAG 1;
the judge on RAG: *"Correctly covers the appeal deadline (3 months) and the financial document
requirement, but fails to provide the cancellation rules … which is a key part of the question."*
Two of three concepts retrieved, one missed.

### `hard-1` — the cross-packet cliff (+1.29)

§1.2 Problem 2: *"Multi-faceted problems are where flat RAG breaks down: covering them properly
requires several distinct queries, and any one of them can miss the right chunk."*

This is a one-shot retriever being asked a two-document question. §9.4 puts it precisely: HCAG's
*"whole-packet + explicit-load loop supports chains of retrieval"*, while RAG is *"one-shot
retrieval; multi-hop requires all evidence to co-rank in a single query"*. With `top_k = 8` split
across two documents, the second document is often simply absent.

Example — **q-0026** (employer repatriation obligations *contrasted with* ONE Pass cancellation),
HCAG 3, RAG **0**: *"The chatbot refused to answer the question, providing no substantive
information."* HCAG's catalog let it name both packets and load them; the RAG retriever returned
chunks about one half of the question, and the generator — correctly refusing to invent the other
half — declined.

### `hard-2` — the multimodal cliff (+1.50)

The largest gap, and the most structural. **D9. Multimodal loading is first-class**: *"Images under
a folder's `assets/` directory are loaded as multimodal content blocks alongside its `compiled.md`.
Not text descriptions, not deferred loads."* The RAG side, per §9.4, sees *"images only via their
LLM-generated text description"* (§8.4.3) — 56 of its 659 chunks are image descriptions, indexed and
retrieved independently of the document they belong to.

So a `hard-2` question asks for something the description writer had no reason to transcribe:

- **q-0034** — the exact wording of a checkbox on an EP application form. HCAG 3 (*"matches the
  reference answer exactly"*); RAG **0** — the phrase was never in any description, so it could not
  be retrieved at any `top_k`.
- **q-0039** — the Work Pass Division address in a document header plus its logo branding. HCAG 3
  (*"correctly identifies the address … and the Ministry of Manpower logo with blue and orange
  elements"*); RAG **0** — *"failed to provide any of the requested information."*

Text-of-image is lossy at index time, and no amount of retrieval quality recovers what the
description omitted. HCAG defers nothing: the image itself is in the context window.

---

## Where HCAG lost points, and RAG's one win

HCAG's 9 two-scores were 4 `complex`, 2 `medium`, 2 `hard-2`, 1 `hard-1` — and the judge's
complaint is consistently *noise*, not error: "adds potentially inaccurate detail", or a
sector-level breakdown where the reference wanted two specific figures. That is the predictable
cost of putting a whole document in context: everything relevant is there, and so is everything
adjacent.

RAG won exactly one question — **q-0018** (`complex`, COMPASS SEP bonus funding criteria), 3 vs 2.
HCAG had the right facts and diluted them; the tighter chunk context produced the crisper answer.
That is the honest counter-example to the paragraph above.

---

## Prediction vs outcome

§9.4 states the narrative the run was expected to produce. Scoring it:

| §9.4 prediction | Outcome | |
|---|---|---|
| `simple`, `medium` "roughly tied" | HCAG +0.38 / +0.33 — a real but modest edge, not a tie | ~ |
| `complex`, `hard-1` "should favour HCAG" | +0.38 and +1.29 — `hard-1` strongly, `complex` only modestly | ✓ |
| `hard-2` "should strongly favour HCAG" | +1.50, the largest gap in the run | ✓ |

Two honest deviations. First, HCAG's edge on easy questions is small but consistent rather than
absent — it comes from completeness, not from retrieval success. Second, `complex` separated far
less than `hard-1` despite both being "reasoning" categories: whole-*packet* retrieval is apparently
not the binding constraint when all the evidence already lives in one packet, because RAG's chunks
usually co-rank when they come from the same document. **The binding constraint is crossing a
document boundary, or leaving text at all.** §9.4 says a run that contradicts the narrative "is a
signal worth chasing, not a bug in the eval"; this is a mild contradiction worth chasing.

---

## Reproducing this

Each agent is served on the same port in turn, so the eval harness sees one unchanging endpoint:

```
# HCAG side  (this run: 02:41–02:50 UTC)
hcag-server --agent hcag --agent-config ./examples/agent.toml --port 8000
evalrun validation3.csv --backend-url http://localhost:8000 \
        --out hcag-kb-eval-scored.csv --report hcag-kb-eval-report.html

# RAG side  (this run: 03:45–03:52 UTC)
hcag-server --agent rag --rag-index ./local_lancedb --port 8000
evalrun validation3.csv --backend-url http://localhost:8000 \
        --out rag-kb-eval-scored.csv --report rag-kb-eval-report.html
```

(The HCAG artefacts were written under the harness's default names and renamed to the `hcag-`
prefix when copied into this folder; see `eval.csv.written` in `evalrun.log`.)

Both runs: 37 questions, judge `claude-opus-4-6`, classifier `claude-haiku-4-5`, concurrency 4,
seed 42. Both agents generated with `claude-haiku-4-5`; the RAG side embedded with
`text-embedding-3-small` over 659 chunks from 115 files, `top_k = 8`, RRF fusion,
`max_context_tokens = 6000`.

### Caveats — read before quoting these numbers

- **n = 37**, with 6–8 questions per difficulty bucket. The overall gap (+0.78) and the two large
  per-bucket gaps (+1.29, +1.50) are wide relative to that noise; the ±0.35 differences on
  `simple` / `medium` / `complex` are **not** separable from sampling error at this size.
- **The RAG run used the improved index.** The corpus was re-indexed at 11:30 local with the
  heading-path chunk prefix (each chunk now carries `Document > Section` in its indexed text, worth
  +17% on-topic FTS hits in isolated measurement); the RAG run began at 11:45. Its agent-side code,
  however, predates the query-alias and vocabulary fixes. So this is a RAG baseline with the better
  index and the older agent — not penalised by the indexing defect, not yet helped by the alias
  work.
- **Both agents share a generator.** The gap measured here is retrieval architecture, not model
  quality.
- **The judge is an LLM.** Scores are reproducible in aggregate, not per row; the `remark` column in
  each CSV is the audit trail for any single score you doubt.
- **Reference answers came from the same KB**, so this measures faithful retrieval of known content
  — not open-domain correctness.

---

## The one-line version

On questions a single passage answers, flat RAG is competitive and much cheaper to build. The
moment a question needs **two documents** or **something only visible in an image**, the flat index
stops being able to represent the problem — and its failure is a refusal or a half-answer, not an
obvious error. That is the boundary §1.3 draws, and this run puts numbers on it.
