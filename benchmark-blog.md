# We Benchmarked a Knowledge Taxonomy Against Flat RAG. The Gap Wasn't Where We Expected.

> Same questions. Same knowledge base. Same generator model. Same judge. The only variable was
> whether the agent navigated an organised taxonomy or searched a flat index of 659 chunks.
>
> **HCAG scored 91.9% of maximum. Flat RAG scored 65.8%.** On questions needing two documents,
> HCAG was **82% better**. On questions whose answer was in an image, **120% better**. On simple
> FAQ lookups the two were nearly tied — and that turns out to be the most useful result in the
> whole run.

Everything below is reproducible from the CSVs and HTML reports in
[`sample-benchmark-report/`](./sample-benchmark-report/). Nothing here is a projection.

---

## 1. The setup, and why it is a fair test

Two agents answered the **same 37 questions** over the **same knowledge base** — Singapore MOM's
work-pass documentation, 127 source documents — through the **same HTTP contract**, scored by the
**same LLM judge** on the same 0–3 rubric.

| | HCAG | Flat RAG |
|---|---|---|
| Retrieval unit | whole document (folder + its images) | top-8 chunks |
| Selection | an LLM reasoning over a taxonomy catalog | embedding similarity + BM25, fused with RRF |
| Corpus shape | 27 packets in a hierarchy | 659 chunks in one index |
| Generator | `claude-haiku-4-5` | `claude-haiku-4-5` |
| Judge | `claude-opus-4-6` | `claude-opus-4-6` |

**Both agents share a generator model.** What is measured is the retrieval architecture, not model
quality. And the RAG side was not a strawman — see §5, where we spent real effort making it better
and it still lost.

---

## 2. What "organised taxonomy" actually means

Not a vector store with metadata filters. A **file-system hierarchy where every folder is a unit of
knowledge**, organised the way the domain organises itself — domain → topic → subtopic:

```
passes-and-permits/                                     ← domain
├── employment-pass/                                    ← topic
│   ├── eligibility/                                    ← subtopic
│   │   ├── compass-c1-salary-benchmarks/               ← leaf
│   │   ├── compass-c5-skills-bonus-shortage-occupation-list/
│   │   └── compass-c6-strategic-economic-priorities/
│   ├── documents-required/
│   ├── renew-a-pass/
│   ├── notify-mom-of-changes/
│   └── taking-up-secondary-directorship/
└── overseas-networks-expertise-pass/                    ← topic
    ├── apply-for-a-pass/
    └── passes-for-families/                             ← subtopic
        ├── dependants-pass-for-one-pass/
        └── long-term-visit-pass-for-one-pass/
            └── working-in-singapore/                    ← leaf, depth 6
```

Each folder compiles to one **packet**: its documents, its images, and a one-line summary of what
it covers. Every packet's summary — the whole tree, at every depth — is injected into the agent's
system prompt once, at startup. That index is a few thousand tokens; the corpus behind it can be
gigabytes.

The agent then does three things a flat retriever cannot:

1. **Classifies once.** It reads the question, finds the entries that cover it, and loads them by
   id — one hop, at any depth. No walking the tree, no similarity roll.
2. **Loads whole documents.** Not fragments. A document carries its own definitions, caveats and
   scope; a chunk usually strips them away.
3. **Reuses the active set** across every subsequent step of the task, instead of re-retrieving per
   step.

---

## 3. The results, by question difficulty

The questions come in five kinds, generated from the KB itself: `simple` (look it up), `medium`
(reason within one paragraph), `complex` (three concepts from one document), `hard-1` (two
different documents), `hard-2` (the answer is in an **image**, not the text).

| Difficulty | HCAG | Flat RAG | Δ relative |
|---|---:|---:|---:|
| `simple` — FAQ lookup | 100.0% | 87.5% | +14.3% |
| `medium` — one-paragraph reasoning | 88.9% | 77.8% | +14.3% |
| `complex` — 3 concepts, one document | 83.3% | 70.8% | +17.6% |
| `hard-1` — **two documents required** | 95.2% | 52.4% | **+81.8%** |
| `hard-2` — **answer is in an image** | 91.7% | 41.7% | **+120.0%** |
| **Overall** | **91.9%** | 65.8% | **+39.7%** |

*(percent of the 3-point maximum; full per-question data in
[`hcag-kb-eval-scored.csv`](./sample-benchmark-report/hcag-kb-eval-scored.csv) and
[`rag-kb-eval-scored.csv`](./sample-benchmark-report/rag-kb-eval-scored.csv))*

**Read the shape, not the average.** Three levels of near-parity, then a cliff. Flat RAG holds
71–88% while a question lives inside one passage — right at the "70–80% ceiling" the RAG literature
would predict — and then falls to 52% and 42%.

The distribution says more than the mean:

```
score    3    2    1    0        (3 = fully answered, 0 = wrong or refused)
HCAG    28    9    0    0
RAG     15    9   10    3
```

**HCAG never scored below 2 and never refused.** Flat RAG refused 10 of 37 in-scope questions —
"I don't have enough information" — concentrated in exactly the two hard categories. That is the
failure mode that matters operationally: not a visibly wrong answer you can catch, but a fluent
non-answer that looks like a knowledge gap and is actually a retrieval gap.

---

## 4. Why the cliff is exactly there

Two structural facts, not tuning parameters.

**Crossing a document boundary.** A `hard-1` question needs evidence from two documents. A one-shot
retriever must get both to co-rank in a single top-8. HCAG's catalog names both packets and loads
both. Example, verbatim from the run — *"what are the employer's repatriation obligations, and how
does this contrast with ONE Pass cancellation?"*: HCAG scored 3; RAG scored **0**, with the judge
noting *"the chatbot refused to answer, providing no substantive information."* It had retrieved one
half of the question and correctly declined to invent the other.

**Leaving text at all.** A `hard-2` answer lives in an image. HCAG attaches images as first-class
content blocks when it loads the folder they belong to. Flat RAG can only index an LLM-written
*description* of each image — 56 of its 659 chunks — retrieved independently of the document they
came from. Asked for the exact wording of a checkbox on a form, RAG scored 0: the phrase was never
in any description, so no amount of retrieval quality could reach it. **Text-of-image is lossy at
index time, and no re-ranking recovers what the description omitted.**

Notice what these have in common. Neither is fixed by a better embedding model.

---

## 5. We tried to make flat RAG win

This is the part that should decide whether you believe the numbers.

Before running the comparison we spent real effort on the RAG side, and **found and fixed three
genuine defects in our own baseline**:

- **A dead retrieval leg.** A missing embedding credential made `_retrieve` return empty *before*
  full-text search ran — so the "hybrid" retriever had a single point of failure and every answer
  was a confident-sounding refusal. Now the vector leg is skipped and BM25 carries the turn.
- **Chunks that didn't know what document they were in.** Only a document's first chunk contained
  its title. We prefixed every chunk's indexed text with its heading path; measured over ten
  document-naming queries, on-topic hits in the FTS top-8 rose from **35 to 41 (+17%)**.
- **A vocabulary gap.** The corpus says "ONE Pass"; users type "onepass", which appears nowhere, so
  BM25 returned literally zero. We added query-time alias expansion and a vocabulary note for the
  generator.

The comparison run then used the **improved** index. Flat RAG still lost by 39.7% overall and by
82–120% on the hard categories, because none of those fixes addresses a document boundary or an
image. **The gap is architectural, not a tuning deficit.**

---

## 6. The second dataset: taxonomy makes failure *diagnosable*

A benchmark that only produces a score tells you where you are, not what to do. The more important
property of a taxonomy-backed agent is that when it is wrong, **you can see why, and fix it without
touching retrieval.**

We ran a second, separate 16-question set ([`validationbeta.csv`](./sample-benchmark-report/validationbeta.csv))
against HCAG and looked only at rows scoring below 2:

| Run | Mean | Pass rate | Rows below 2 | Artifact |
|---|---:|---:|---:|---|
| Baseline | 2.625 (87.5%) | 87.5% | **2** | [scored](./sample-benchmark-report/beta-01-baseline-scored.csv) · [report](./sample-benchmark-report/beta-01-baseline-report.html) |
| After prompt fix | **2.750 (91.7%)** | **100%** | **0** | [scored](./sample-benchmark-report/beta-02-tuned-scored.csv) · [report](./sample-benchmark-report/beta-02-tuned-report.html) |
| Same prompt, re-run | **2.750 (91.7%)** | **100%** | **0** | [scored](./sample-benchmark-report/beta-03-tuned-rerun-scored.csv) · [report](./sample-benchmark-report/beta-03-tuned-rerun-report.html) |
| A change we reverted | 2.688 (89.6%) | 93.8% | 1 | [scored](./sample-benchmark-report/beta-04-reverted-experiment-scored.csv) · [report](./sample-benchmark-report/beta-04-reverted-experiment-report.html) |

Both failures were **reasoning defects, not retrieval defects** — the right documents were loaded
every time:

- *"Can a self-employed person apply for the ONE Pass?"* → the agent answered **"cannot directly
  apply"** in a reply that had already listed, as criterion 3, the achievements route that waives
  the salary requirement. It had silently narrowed the question to one route. Fixed by making
  eligibility a **procedure**: list every route, test the asker against each, answer "no" only if
  all of them fail.
- *"What documents should I submit…"* → the agent answered from the document list when the desired
  behaviour was to hand over to a human officer. Fixed with an explicit remit boundary.

Two things worth stating plainly. **The improvement reproduced** on an independent re-run of the
identical prompt — 2.750 twice, zero rows below 2. And **one of our three changes made things
worse**: a rule meant to stop the agent borrowing conditions between pass types caused it to
hallucinate a portal name instead, dropping a row from 2 to 1. We reverted it. That negative result
is in the table above, and in the repo, because a benchmark you only publish when it agrees with
you is marketing.

This is the loop the architecture buys you: **a wrong answer is traceable to a named document and a
nameable reasoning step, so it is fixable in a prompt and verifiable in a re-run.** With flat RAG,
"the retriever ranked the wrong chunk" is rarely actionable in the same way.

---

## 7. Why this matters for agentic automation, not just chat

A chatbot answers a question. An **agent takes an action** — and the cost of a wrong retrieval stops
being an unhelpful paragraph and starts being a wrong action taken on a live system.

Every one of these workloads has the same shape: a bounded domain, a body of knowledge that is
already organised by people, and a multi-step task where the same knowledge is consulted repeatedly.

**Predictive and corrective maintenance.** Fault trees, equipment manuals, service bulletins,
tolerances by model and revision. A maintenance agent that retrieves a torque spec from the *wrong
revision* of the manual is worse than no agent. A taxonomy makes "this asset, this model, this
revision" a branch — the other revisions are structurally out of scope, not merely ranked lower.
And `hard-2` is not an academic category here: **the answer is very often in the diagram** — a
labelled component, a wiring topology, a threshold on a chart.

**Fraud investigation workflows.** Policy documents, typology libraries, regulatory thresholds,
escalation matrices. An investigator agent must reason across several of these at once, which is
precisely the `hard-1` shape where flat RAG dropped to 52%. It must also produce an audit trail: a
deterministic, name-able retrieval — "this decision used the AML typology packet and the escalation
matrix" — is a compliance artifact. "The top-8 chunks by cosine similarity" is not.

**Autonomous customer support.** Not one question — a case. Eligibility, then documents, then fees,
then timelines, over many turns. HCAG classifies the branch once and reuses it, so the prompt prefix
stays byte-stable and cache hits accumulate instead of paying retrieval on every turn. The support
agent in this repo also shows the other half: knowing what is **not** its remit and handing over.

**Self-healing telco networks.** Runbooks, topology, vendor-specific command references, change
policy. A remediation agent executing a runbook needs the **whole runbook** — steps, preconditions,
rollback. A chunked runbook is actively dangerous: step 4 retrieved without the precondition in step
2 is a plausible-looking instruction to break production. Whole-document retrieval is not a quality
nicety here; it is the safety property.

The common thread: **agents act over bounded domains, repeatedly, and must be auditable.** All three
are properties of structure, not of a better retriever.

---

## 8. What it costs, and when not to bother

**The taxonomy is the investment.** Days to weeks depending on scope, and it is the biggest cost in
adopting HCAG. If your knowledge is already organised — as maintenance manuals, policy libraries and
runbooks usually are — much of the work is already done. If it is a heap of unstructured documents
with no natural hierarchy, that heap is the project.

**Don't use HCAG when** your KB is small, your questions are genuinely FAQ-shaped, and you need
something shipped this week. Our own numbers say so: on `simple` questions flat RAG scored 87.5% and
was 14% behind. If your traffic is all `simple`, that gap will not pay for a taxonomy.

**Do use it when** answers need more than one document, when the answer is sometimes in a picture,
when the same knowledge is consulted across many steps of a task, or when you have to explain after
the fact which document drove a decision.

---

## 9. Caveats we are not hiding

- **n = 37** for the comparison, 6–8 questions per difficulty bucket. The overall gap (+39.7%) and
  the two large per-bucket gaps (+81.8%, +120%) are wide relative to that noise. The ±0.35-point
  differences on `simple`/`medium`/`complex` are **not** separable from sampling error at this size
  — treat them as directional.
- **n = 16** for the prompt-tuning set, and run-to-run variance on it is around ±0.06 mean.
- **An LLM is the judge.** Scores are reproducible in aggregate, not per row; every row's remark is
  in the CSVs so you can audit any score you doubt.
- **One knowledge base, one domain.** Government policy documentation is well-structured prose with
  tables and forms. We would expect the `hard-2` gap to be larger in domains with more diagrams, and
  the `simple` gap to be smaller in domains that are mostly FAQ.
- **Reference answers were written from the same KB**, so this measures faithful retrieval of known
  content, not open-domain correctness.

---

## 10. Reproduce it

```bash
# 1. Build the taxonomy from a folder tree of documents
hcag --kb ./kb

# 2. Serve the agent
hcag-server --agent hcag --agent-config ./examples/agent.toml --port 8000

# 3. Score it
evalrun validation3.csv --backend-url http://localhost:8000 \
        --out scored.csv --report report.html

# 4. Same questions against the flat-RAG baseline, for comparison
rag --kb ./kb --index ./local_lancedb
hcag-server --agent rag --rag-index ./local_lancedb --port 8000
evalrun validation3.csv --backend-url http://localhost:8000 \
        --out rag-scored.csv --report rag-report.html
```

Everything referenced in this post — the question sets, both agents' per-question answers, the
judge's remark on every row, and the reverted experiment — is in
[`sample-benchmark-report/`](./sample-benchmark-report/README.md).

---

**The one-line version.** On questions a single passage answers, flat RAG is competitive and much
cheaper to build. The moment a question needs two documents (+82%) or something only visible in an
image (+120%), a flat index stops being able to represent the problem — and it fails by refusing,
not by being obviously wrong. If you are building an agent that *acts* on a knowledge repository,
that is the failure mode you cannot afford, and structure is what removes it.
