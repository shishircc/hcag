# RAG Is Fine for FAQs. Your Automation Isn't an FAQ.

> We put two AI agents in front of the same 37 questions, over the same knowledge base, with the
> same AI model and the same scorer. One found its knowledge the way most teams build agents today.
> The other started by organising the knowledge first. On simple lookups they were close. On the
> questions that automation actually consists of, the standard approach failed more often than it
> succeeded — and it failed by *politely refusing*, not by being visibly wrong.

---

A user asked the agent a question. The agent replied, fluently and courteously, that it did not have
enough information to answer. The information was in its knowledge base. It had been there the whole
time. Nobody filed a bug, because nothing looked broken — a polite "I don't have that" reads as a
content gap, not a defect. In our test, the standard agent did this **ten times out of thirty-seven**.

That is the failure mode this piece is about, because it is the one that ends up in production.

## The one thing to remember

**The way your agent *finds* knowledge matters more than the model it runs on — and the default way
breaks precisely where automation earns its keep: answers that span more than one document, or that
live in a diagram, a form, a chart. It doesn't break loudly. It refuses, and the gap disappears into
"the bot can't do that."**

## The one thing to do this week

Ask your team: **"Of the questions our agent currently refuses, how many are actually answerable
from our knowledge base?"**

It costs nothing to ask. If nobody knows the number, that is your first finding. If the number is
material, the rest of this piece tells you what is causing it and what to do about it.

---

## 1. What the data says

Two agents. **Same 37 questions**, same 127-document knowledge base (Singapore government work-pass
rules), **same AI model writing every answer, same independent AI judge** scoring every answer from 0
(wrong or refused) to 3 (complete). The only difference: how each agent found the knowledge.

- **Standard (RAG)** — the approach most teams use today: documents cut into passages, indexed by
  similarity, the eight most similar passages handed to the model.
- **Organised (HCAG)** — the knowledge base arranged as a hierarchy first; the agent reasons about
  which branch the question belongs to and reads the whole relevant document, images included.

The questions were built at five levels, because the useful question is not "how accurate is it?"
but "*where* does it stop being accurate?"

```
Share of maximum score, by question type

                     Organised (HCAG)               Standard (RAG)
Simple lookup        ████████████████████ 100%      ██████████████████░░  88%
One paragraph        ██████████████████░░  89%      ████████████████░░░░  78%
One document         █████████████████░░░  83%      ██████████████░░░░░░  71%
Two documents        ███████████████████░  95%      ██████████░░░░░░░░░░  52%   ◄ cliff
In an image          ██████████████████░░  92%      ████████░░░░░░░░░░░░  42%   ◄ cliff
```

| Question type | Organised | Standard | Gap |
|---|---:|---:|---:|
| Simple lookup | 100.0% | 87.5% | +14% |
| Reason within one paragraph | 88.9% | 77.8% | +14% |
| Combine three points from one document | 83.3% | 70.8% | +18% |
| **Combine two different documents** | **95.2%** | **52.4%** | **+82%** |
| **Answer is in an image, not the text** | **91.7%** | **41.7%** | **+120%** |
| **Overall** | **91.9%** | **65.8%** | **+40%** |

Three levels of a modest lead, then a cliff. Standard RAG holds 71–88% as long as the answer lives
inside one passage — which is exactly the 70–80% ceiling practitioners already quote for it. Then
it drops to 52% and 42%: on questions whose answer is in a picture, **worse than a coin flip**.

And the *kind* of failure is the part to remember:

| | Organised | Standard |
|---|---:|---:|
| Questions passed (scored "covers the key points" or better) | **37 of 37** | 24 of 37 |
| Wrong or refused | **0** | 13 |
| **Refused outright** — "I don't have enough information" | **0** | **10** |

The organised agent was never wrong and never refused; its imperfect answers all "said more than
was asked". The standard agent refused ten in-scope questions, concentrated in exactly the two hard
categories. Those ten do not look like failures in a log. They look like the knowledge base doesn't
cover the topic.

---

## 2. Why — two things a flat index cannot do

Neither is a tuning problem. Neither is fixed by a better model.

**It cannot easily cross a document boundary.** A question needing evidence from two documents
requires both to surface in the same small set of passages from a single search. When one document
dominates, the other never arrives. One question from the run, verbatim: *"What are the employer's
repatriation obligations, and how does this contrast with ONE Pass cancellation?"* The organised
agent loaded both documents and scored 3. The standard agent retrieved one half, correctly declined
to invent the other, and scored **0**.

**It cannot see a picture.** A flat index can only hold a *written description* of each image,
produced by an AI in advance. Whatever the describer didn't think to write down is unfindable
afterwards, at any search quality. Asked for the exact wording of a checkbox on a form, the standard
agent scored 0 — the phrase was in no description. The organised agent reads the form.

**We tried to make the standard approach win first.** Before comparing, we audited our own RAG
baseline and fixed three genuine defects in it — one of which improved its retrieval by a measured
17%. The comparison ran against the *improved* system. It still lost by 40% overall and by 82–120%
on the hard categories, because no retrieval fix touches a document boundary or an image. **The gap
is architectural.** (Details in the appendix.)

---

## 3. What this changes on your roadmap

A chatbot that retrieves the wrong passage writes an unhelpful paragraph. **An agent that retrieves
the wrong passage takes the wrong action.** Look at what is on the automation roadmap and notice
which side of the cliff each item lands on.

**Predictive and corrective maintenance.** The answer is in the manual for *this* model and *this*
revision — and very often in the diagram: a labelled component, a wiring topology, a threshold on a
chart. That is the "in an image" row, where standard RAG scored 42%. An organised knowledge base
makes "this asset, this revision" a branch; the other revisions are structurally out of reach, not
merely ranked lower.

**Fraud and financial-crime investigation.** Typology, threshold, escalation matrix — several
documents reasoned over at once. That is the "two documents" row, 52%. And this workload must leave
an audit trail: "this decision used the sanctions typology and the escalation matrix" is a
compliance artefact. "The eight passages that looked most similar" is not.

**Autonomous customer support.** Not a question — a *case*, across many turns: eligibility, then
documents, then fees. The organised agent finds the branch once and keeps it open for the case. It
also knows what is not its job and hands over to a person; the agent in this repository does exactly
that.

**Self-healing networks and IT operations.** A remediation agent executing a runbook needs the whole
runbook — steps, preconditions, rollback. A runbook cut into passages is actively dangerous: step 4
retrieved without the precondition in step 2 is a plausible-looking instruction to break production.
Whole-document retrieval is the safety property here, not a quality refinement.

**The line to carry upward:** *our automation candidates are cross-document and visual work; the
architecture we default to scores 42–52% on exactly that, and fails silently. The fix is an
organising investment, not a model upgrade.*

---

## 4. What to do

**This week — measure the problem (cost: nothing).** Count the refusals your agent produces and
check how many were answerable. That number is the size of the silent gap.

**This month — size the opportunity (cost: an afternoon).** Take a sample of the questions your
target automation would actually handle and sort them: single passage, cross-document, or visual. The
share in the last two columns is the share of your workload sitting on the wrong side of the cliff.

**This quarter — pilot on one domain (cost: days to weeks).** Pick a bounded domain whose knowledge
is *already organised* — maintenance libraries, policy repositories and runbook collections almost
always are. Build the hierarchy from that structure. Run the same questions through both approaches
with the same judge, exactly as here, and let the numbers decide.

**What to demand from whoever builds it — a team or a vendor:**

- **The comparison must be fair.** Same model, same questions, same scorer, and the baseline tuned in
  good faith. Ask what they fixed in the baseline before comparing. If the answer is "nothing", the
  comparison is a demo.
- **Failures must be diagnosable.** When our organised agent got two questions wrong on a second
  test set, both were traceable to a *named document* and a *specific reasoning step* — and were
  fixed by changing the agent's instructions, not by rebuilding anything. The fix moved the pass rate
  from 87.5% to **100%**, and it reproduced on three separate runs, one of them made by someone not
  involved in the tuning.
- **Negative results must be published.** One of our three attempted fixes made things *worse* — it
  caused the agent to invent a portal name — and we reverted it. It is in the data alongside the rest.
  A benchmark reported only when it agrees with the vendor is marketing.

---

## 5. What it costs, and when to keep what you have

**The investment is organising the knowledge** — days to weeks depending on scope — and it is
one-time. It is also frequently already done, because people organise operational knowledge the same
way: by product, by domain, by procedure. If your knowledge is a heap of unstructured documents with
no natural hierarchy, that heap is the project — and it would have been the project under any
serious knowledge programme.

**Keep standard RAG when** the knowledge base is small, the questions are genuinely FAQ-shaped, and
you need something shipped this quarter. Our own data supports this: on simple lookups RAG scored
87.5%, and a 14% gap will not repay a taxonomy if that is all your traffic looks like.

**Move when** answers routinely need more than one document; when answers are sometimes in a diagram,
form or screenshot; when the same knowledge is consulted across many steps of a task; or when you
will have to explain afterwards which document drove a decision.

---

## 6. How far to trust this

Discount correctly rather than believe wholesale.

- **37 questions**, six to eight per level. The overall gap (+40%) and the two large gaps (+82%,
  +120%) are far wider than the noise at that size. The 14–18% leads on the three easier levels are
  **not** statistically separable from noise — read them as directional.
- **The scorer is an AI judge**, not a panel. Reliable in aggregate, not per row; every row's
  justification is published so any single score can be audited.
- **One knowledge base, one domain** — government policy: structured prose, tables, forms. Expect the
  visual gap to be *larger* in engineering and network domains, and the simple-lookup gap *smaller*
  in FAQ-heavy ones.
- **Reference answers came from the same knowledge base**, so this measures faithful retrieval of
  known content, not open-domain correctness.

---

## 7. For readers who want the mechanism

**What RAG is.** Every document is cut into passages of a few hundred words. Each passage is turned
into a numerical fingerprint and all fingerprints go into one searchable index. A question is
fingerprinted the same way, the most similar passages are pulled, and the model writes an answer from
them. It is quick to build, which is why it is the default. Its ceiling is structural: every passage
in the corpus competes on every question, so "similar but wrong" passages from unrelated documents
are always in the running — and a passage is a fragment that has lost the definitions, caveats and
scope around it.

**What a knowledge taxonomy is.** The structure your organisation already uses, made explicit and
machine-readable: **domain → topic → subtopic**, each folder holding the documents and diagrams that
belong to it. From this benchmark, names abridged:

```
passes-and-permits/                                     ← domain
├── employment-pass/                                    ← topic
│   ├── eligibility/                                    ← subtopic
│   │   ├── salary-benchmarks/
│   │   ├── skills-bonus-shortage-occupations/
│   │   └── strategic-economic-priorities/
│   ├── documents-required/
│   ├── renew-a-pass/
│   └── notify-of-changes/
└── overseas-networks-expertise-pass/                    ← topic
    ├── apply-for-a-pass/
    └── passes-for-families/                             ← subtopic
        ├── dependants-pass/
        └── long-term-visit-pass/
            └── working-in-singapore/
```

**What HCAG does with it.** HCAG — Hierarchical Context Augmented Generation — gives the agent a
one-line summary of every folder in the tree and lets it *reason* about which branch a question
belongs to, the way an experienced colleague knows which manual to reach for. It then reads the whole
relevant document, images included, and keeps it open for the rest of the task instead of searching
again at each step. Everything outside the chosen branch is simply not in play. That is why the
"similar but wrong" passage cannot appear, why a two-document question is one load rather than a
lucky search, and why a diagram arrives as a diagram.

---

## Appendix — for your engineers

### A. Technical setup

| | HCAG | Flat RAG |
|---|---|---|
| Retrieval unit | whole folder (`compiled.md` + `assets/` images) as multimodal content blocks | top-8 chunks, ~500 tokens each |
| Selection | LLM reasoning over a full-depth catalog injected once at bootstrap | vector similarity + BM25, reciprocal-rank fused |
| Corpus | 27 packets over 127 source documents | 659 chunks (603 text + 56 image descriptions), `text-embedding-3-small` |
| Generator | `claude-haiku-4-5` | `claude-haiku-4-5` |
| Judge | `claude-opus-4-6`, 0–3 rubric, transcript-aware | same |
| Question generation | five kinds from the KB itself (`hcag/prompts/evalgen/`) | same set |

The five levels map to the harness's kinds: `simple`, `medium`, `complex`, `hard-1` (cross-packet),
`hard-2` (multimodal).

### B. What we fixed in the RAG baseline before comparing

- **A dead retrieval leg.** A missing embedding credential made `_retrieve` return empty *before*
  full-text search ran, so the "hybrid" retriever had a single point of failure. The vector leg is
  now skipped and BM25 carries the turn; the degradation is logged and surfaced in the stream.
- **Chunks that did not know their document.** Only chunk 0 carried the document title, and a bare
  `#` in the crawled markup was wiping the title from the heading stack. Every chunk's indexed text
  now opens with its heading path. Measured over ten document-naming queries, on-topic hits in the
  FTS top-8 rose from 35 to 41 (**+17%**).
- **A vocabulary gap.** The corpus says "ONE Pass"; users type "onepass", which appears nowhere, so
  BM25 returned zero. Added query-time alias expansion plus a vocabulary note to the generator.

The comparison run used the improved index. The RAG agent code at run time predated the alias fix.

### C. The second test set — the diagnosability claim in numbers

A separate 16-question set run against the organised agent only, examining rows scoring below 2.

| Run | Prompt | Mean (0–3) | Pass | Below 2 |
|---|---|---:|---:|---:|
| An earlier run | pre-fix | 2.688 | 93.8% | 1 |
| Baseline for the fix | pre-fix | 2.625 | 87.5% | 2 |
| After the fix | tuned | **2.750** | **100%** | **0** |
| Same prompt, re-run | tuned | **2.750** | **100%** | **0** |
| Independent run, outside the tuning sequence | tuned | **2.750** | **100%** | **0** |
| A further change, **reverted** | experiment | 2.688 | 93.8% | 1 |

Both failures had the correct documents loaded; the errors were in reasoning. One answered "no" to
an eligibility question while listing, in the same reply, the route that qualified the person — fixed
by making eligibility a procedure (list every route, test each, "no" only if all fail). The other
answered a request that should have been handed to a human officer — fixed with an explicit remit
boundary. The two pre-fix runs disagree by 0.06, the run-to-run noise at this size; the fix moved the
mean by twice that and removed the sub-2 rows entirely. The reverted change targeted the remaining
2s with a rule against borrowing conditions between pass types and caused a hallucinated portal
name instead.

### D. Reproduce

```bash
# Build the taxonomy from a folder tree of documents
hcag --kb ./kb

# Serve and score the organised agent
hcag-server --agent hcag --agent-config ./examples/agent.toml --port 8000
evalrun validation3.csv --backend-url http://localhost:8000 \
        --out scored.csv --report report.html

# Same questions against the flat-RAG baseline
rag --kb ./kb --index ./local_lancedb
hcag-server --agent rag --rag-index ./local_lancedb --port 8000
evalrun validation3.csv --backend-url http://localhost:8000 \
        --out rag-scored.csv --report rag-report.html
```

### E. Data

| Artefact | |
|---|---|
| Head-to-head, HCAG | [scored CSV](./sample-benchmark-report/hcag-kb-eval-scored.csv) · [report](./sample-benchmark-report/hcag-kb-eval-report.html) |
| Head-to-head, RAG | [scored CSV](./sample-benchmark-report/rag-kb-eval-scored.csv) · [report](./sample-benchmark-report/rag-kb-eval-report.html) |
| Second set, pre-fix runs | [earlier run](./sample-benchmark-report/beta-00-prior-run-scored.csv) · [baseline](./sample-benchmark-report/beta-01-baseline-scored.csv) |
| Second set, fixed — three runs | [run 1](./sample-benchmark-report/beta-02-tuned-scored.csv) · [run 2](./sample-benchmark-report/beta-03-tuned-rerun-scored.csv) · [independent run](./sample-benchmark-report/beta-05-independent-rerun-scored.csv) |
| Second set, the reverted change | [scored CSV](./sample-benchmark-report/beta-04-reverted-experiment-scored.csv) · [report](./sample-benchmark-report/beta-04-reverted-experiment-report.html) |
| Question sets | [validation3.csv](./sample-benchmark-report/validation3.csv) · [validationbeta.csv](./sample-benchmark-report/validationbeta.csv) |
| Method, per-question analysis, caveats | [`sample-benchmark-report/README.md`](./sample-benchmark-report/README.md) |
| Design rationale | [`DESIGN.md`](./DESIGN.md) — §1.2 the three problems, §1.3 when to use which, §9.4 the comparison |
