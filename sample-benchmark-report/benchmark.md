# HCAG vs Flat RAG — methodology and results

Run of 2026-09-11. This supersedes the numbers in
[`benchmark-blog.md`](./benchmark-blog.md) and [`README.md`](./README.md), which describe an earlier
37-question run against a much smaller knowledge base. See
[What changed since the blog](#what-changed-since-the-blog) for the deltas.

## Headline

Two retrieval architectures answered the **same 69 questions** against the **same work-pass
knowledge base**, over the same HTTP contract (`POST /chat`), scored by the same judge on the same
0–3 rubric.

| | HCAG | Flat RAG | Gap |
|---|---|---|---|
| **Mean score** (0–3) | **2.754** — 91.8% of max | 2.072 — 69.1% of max | +0.68 pts · +22.7 pp · **+32.9% relative** |
| **Pass rate** (score ≥ 2) | **97.1%** (67/69) | 68.1% (47/69) | +29.0 pp |
| **Full marks** (score 3) | **78.3%** (54/69) | 40.6% (28/69) | +37.7 pp |
| **Below "partially correct"** | **2.9%** (2/69) | 31.9% (22/69) | −29.0 pp |
| Head-to-head | **wins 32** (46.4%) | wins 3 (4.3%) | 34 ties (49.3%) |

Score distribution:

```
score    3    2    1    0
HCAG    54   13    2    0
RAG     28   19   21    1
```

Three readings of the same score are used throughout: **% of max** is the mean over the 3-point
maximum, **pp** is the arithmetic difference between two such percentages, and **% relative**
expresses HCAG's lead as a share of RAG's own score.

---

## Methodology

### The corpus

A crawl of Singapore government work-pass pages. Both agents index the identical tree.

| | HCAG | Flat RAG |
|---|---|---|
| Retrieval unit | whole packet folder (`compiled.md` plus its `assets/` images) as multimodal content blocks | top-8 chunks, reciprocal-rank fused |
| Selection | LLM reasoning over a full-depth catalog injected once at bootstrap | vector similarity plus BM25 |
| Corpus shape | 150 catalog entries, max depth 6, over 511 source documents and 1,766 images | 5,643 LanceDB rows; the last build scanned 511 markdown files and 1,766 images and wrote 3,497 chunks |
| Embedding | none | `text-embedding-3-small`, 1,536 dimensions |
| Retrieval knobs | `max_active_tokens` budget, LRU eviction | `top_k = 8`, RRF, `max_context_tokens = 6000`, adjacent-chunk merge, query-time alias expansion |
| Generator | `claude-sonnet-4-5` (from `agent.toml`) | `claude-haiku-4-5` (from `rag_agent.toml`) |

The generator models were **not** matched in this run. That is a change from the earlier run and it
is the first caveat below.

### The question set

69 questions generated from the knowledge base itself by `evalgen`, using the prompts in
`hcag/prompts/evalgen/`. Each question is written in the voice of one of two personas defined in
[`evalgen-personas.csv`](./evalgen-personas.csv): a candidate applying for or managing their own
pass, and a human-resources person filing on behalf of a company. The persona framing exists to stop
the generator producing exam questions no real person would ask.

Five difficulty kinds, each a separate generator prompt:

| Kind | n | What the question demands |
|---|---:|---|
| `simple` | 19 | A lookup, no reasoning. The answer may still be conditional, and the reference states every condition. |
| `medium` | 7 | Reasoning within a single paragraph. Not a bare quote. |
| `complex` | 15 | Three distinct concepts from three different paragraphs of one packet. |
| `hard-1` | 18 | Two packets, at least three paragraphs across them. Neither packet alone suffices. |
| `hard-2` | 10 | The key fact is visually present in an image and not stated in the surrounding markdown. |

The reference answers are held to a completeness rule: a bare figure that is really conditional
counts as wrong, not short. An incomplete reference would certify incomplete agent answers as
correct.

### The run

Each agent is served on the same port in turn, so the harness sees one unchanging endpoint.

```bash
# HCAG side
hcag-server --agent hcag --agent-config ./agent.toml --port 8000
evalrun sample-benchmark-report/benchmark-set/benchmark-persona-questions.csv \
        --backend-url http://localhost:8000 \
        --out hcag-persona-benchmark.csv --report hcag-persona-benchmark.html

# RAG side
hcag-server --agent rag --rag-index ./local_lancedb --port 8000
evalrun sample-benchmark-report/benchmark-set/benchmark-persona-questions.csv \
        --backend-url http://localhost:8000 \
        --out rag-persona-benchmark.csv --report rag-persona-benchmark.html
```

Judge `claude-opus-4-5`, classifier `claude-haiku-4-5`, concurrency 8, seed 100, up to 5 turns per
question with a clarifier. HCAG report generated 06:43 UTC, RAG report 06:54 UTC on 2026-09-11.

The classifier decides whether a reply is an answer, a clarifying question, or a refusal. The judge
scores 0 to 3 against the reference and writes a one-sentence justification into the `remark`
column, which is the audit trail for any single score you doubt.

### Files

| File | What it is |
|---|---|
| [`benchmark-set/benchmark-persona-questions.csv`](./benchmark-set/benchmark-persona-questions.csv) | the 69-question set both agents were given |
| [`benchmark-set/hcag-persona-benchmark.csv`](./benchmark-set/hcag-persona-benchmark.csv) · [report](./benchmark-set/hcag-persona-benchmark.html) | HCAG run — scores, answers, judge remarks |
| [`benchmark-set/rag-persona-benchmark.csv`](./benchmark-set/rag-persona-benchmark.csv) · [report](./benchmark-set/rag-persona-benchmark.html) | RAG run — same |
| [`beta-set/`](./beta-set) | the separate 16-question prompt-tuning set, HCAG only |

---

## Results by difficulty

| Kind | n | HCAG | RAG | Δ pts | Δ pp | **Δ relative** |
|---|---:|---:|---:|---:|---:|---:|
| `simple` | 19 | 2.79 — 93.0% | 2.42 — 80.7% | +0.37 | +12.3 | +15.2% |
| `medium` | 7 | 2.71 — 90.5% | 2.57 — 85.7% | +0.14 | +4.8 | +5.6% |
| `complex` | 15 | 2.67 — 88.9% | 2.13 — 71.1% | +0.53 | +17.8 | +25.0% |
| `hard-1` | 18 | 2.78 — 92.6% | **1.44 — 48.1%** | **+1.33** | **+44.4** | **+92.3%** |
| `hard-2` | 10 | 2.80 — 93.3% | 2.10 — 70.0% | +0.70 | +23.3 | +33.3% |
| **All** | 69 | **2.75 — 91.8%** | 2.07 — 69.1% | +0.68 | +22.7 | **+32.9%** |

| Kind | HCAG full marks | RAG full marks | H wins | R wins | ties | HCAG below 2 | RAG below 2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `simple` | 15/19 | 11/19 | 5 | 1 | 13 | 0 | 2 |
| `medium` | 6/7 | 5/7 | 1 | 0 | 6 | 1 | 1 |
| `complex` | 11/15 | 7/15 | 6 | 1 | 8 | 1 | 5 |
| `hard-1` | 14/18 | 1/18 | 14 | 0 | 4 | 0 | **11** |
| `hard-2` | 8/10 | 4/10 | 6 | 1 | 3 | 0 | 3 |
| **All** | 54/69 | 28/69 | 32 | 3 | 34 | 2 | 22 |

**One cliff remains, and it is the cross-document one.** Flat RAG holds between 70% and 86% of
maximum on every kind whose evidence lives inside one document. On `hard-1` it falls to 48.1% and
scores full marks on exactly one question out of eighteen. HCAG never drops below 88.9% in any
category.

## Results by persona

| Persona | n | HCAG | RAG | Δ pts | **Δ relative** |
|---|---:|---:|---:|---:|---:|
| `workpass_candidate` | 34 | 2.74 — 91.2% | 2.21 — 73.5% | +0.53 | +24.0% |
| `workpass_human_resource` | 35 | 2.77 — 92.4% | 1.94 — 64.8% | +0.83 | +42.6% |

HCAG scores the same for both personas. Flat RAG is 8.7 pp worse for the HR persona than for the
candidate. HR questions are the multi-part, procedural ones — several obligations at once, filed on
someone else's behalf — and that is the shape a one-shot retriever handles worst.

---

## How the failures are shaped

HCAG produced **no refusal language anywhere in 69 answers**. Flat RAG produced it in **18**, every
one of which scored 1: 11 `hard-1`, 5 `complex`, 2 `hard-2`. Eight of the 18 open with an outright
"I don't have enough information to answer that from the knowledge base"; the other ten answer one
part of a multi-part question and decline the rest.

That is 18 of RAG's 22 sub-2 rows. Its dominant failure is not a wrong answer but a partial one,
and the partiality tracks document boundaries precisely.

Representative rows:

- **q-0044** (`hard-1`) — a found ONE Pass card plus an EP rejection appeal. RAG 1: it returned the
  card-return address correctly and deferred the appeal question to external guidance. HCAG 3: both
  halves, including that only the employer may appeal.
- **q-0040** (`complex`) — what a ONE Pass applicant commits to on signing. RAG 1, refusing
  outright. HCAG 3, listing the restricted-occupation approval and the conduct undertakings.
- **q-0019** (`simple`) — EP qualifying salary for a 34-year-old in financial services. RAG **0**:
  it quoted $8,150 and $8,750 from the general table instead of the financial-services figures of
  $9,000 and $9,650. This is the one question either agent got outright wrong, and it is a
  retrieval error that reads as a confident answer.

**Where HCAG lost points.** Its 13 two-scores are mostly noise rather than error: the right facts
plus adjacent detail the reference did not ask for. That is the predictable cost of putting a whole
document in context. Its two sub-2 rows are real defects, not noise:

- **q-0024** (`medium`, score 1) — asked for the COMPASS C1 salary benchmarks at age 30. It
  explained correctly that benchmarks are sector-specific but never gave the figures.
- **q-0037** (`complex`, score 1) — told the user to cut up and discard a cancelled EP card. It must
  be returned to MOM by post. A factual error on a procedural step.

**RAG's three wins** (q-0006, q-0039, q-0064) are all the same story in reverse: HCAG had the right
facts and diluted them, while the tighter chunk context produced the crisper answer. On q-0006 HCAG
also leaked internal verification notes into the user-facing reply.

---

## What changed since the blog

The blog describes a 37-question run against a 27-packet, 127-document knowledge base with 659
indexed chunks. The corpus has since grown roughly fourfold and the images have been described far
more thoroughly. Comparing like for like, as share of maximum score:

| Kind | HCAG then | HCAG now | RAG then | RAG now |
|---|---:|---:|---:|---:|
| `simple` | 100.0% | 93.0% | 87.5% | 80.7% |
| `medium` | 88.9% | 90.5% | 77.8% | 85.7% |
| `complex` | 83.3% | 88.9% | 70.8% | 71.1% |
| `hard-1` | 95.2% | 92.6% | 52.4% | **48.1%** |
| `hard-2` | 91.7% | 93.3% | 41.7% | **70.0%** |
| **Overall** | 91.9% | 91.8% | 65.8% | **69.1%** |
| **Relative gain** | **+39.7%** | **+32.9%** | | |

Three things to know before quoting the blog:

1. **The multimodal cliff has largely closed for flat RAG.** It went from 41.7% to 70.0% on
   `hard-2`. The earlier index held 56 image descriptions; this one holds 1,766. Description
   coverage, not retrieval architecture, was doing most of the damage in the earlier run. The
   blog's headline claim of "+120% on questions answered by an image" no longer holds — the figure
   is now +33%.
2. **The cross-document cliff did not close.** `hard-1` went from 52.4% to 48.1% despite a better
   index, a larger sample (7 questions then, 18 now) and alias expansion. This is the finding that
   has survived both runs, and it is now the whole of the gap.
3. **HCAG is no longer perfect.** It scored zero sub-2 rows on 37 questions and two on 69. The blog
   says "never fell below 83% on any level"; that is still true per level, but not per row.

The overall relative gain fell from +40% to +33%, and the reason is entirely that flat RAG got
better, not that HCAG got worse.

---

## Caveats — read before quoting these numbers

- **The generators were not matched.** HCAG ran `claude-sonnet-4-5` per `agent.toml`; the RAG agent
  ran `claude-haiku-4-5` per `rag_agent.toml`, confirmed in its bootstrap log. Neither `agent.toml`
  nor the server CLI offers a model override, and the HCAG agent log does not record the model it
  used, so this could not be verified from the run artefacts on the HCAG side. **Some unknown part
  of the overall gap is model quality rather than retrieval architecture.** The earlier 37-question
  run held the generator constant at haiku; this one did not. A re-run with matched generators is
  the single most valuable next measurement, and until it exists the headline +32.9% should be read
  as an upper bound on the architectural effect.
- **n = 69**, with 7 to 19 questions per bucket. The `hard-1` gap (+92.3%) is wide relative to that
  noise. The `medium` gap (+5.6%, n = 7) is not separable from sampling error and should be read as
  no measured difference.
- **The judge is an LLM.** Scores are reproducible in aggregate, not per row. The `remark` column is
  the audit trail.
- **Reference answers came from the same knowledge base**, so this measures faithful retrieval of
  known content, not open-domain correctness.
- **The RAG baseline is a fair but not optimised one.** It has the heading-path chunk prefix, alias
  expansion, RRF fusion and adjacent-chunk merge. It does not have a reranker model, query
  decomposition, or iterative retrieval — and query decomposition is exactly what `hard-1` would
  reward. The `hard-1` result should be read as a limit of one-shot retrieval, not of every possible
  RAG system.

## The one-line version

On anything a single document answers, flat RAG is now competitive and much cheaper to build. The
moment a question needs **two documents**, the flat index stops being able to represent the problem,
and its failure is a refusal or a half-answer rather than an obvious error. Better image
descriptions closed the multimodal gap; nothing in this run closed the cross-document one.
