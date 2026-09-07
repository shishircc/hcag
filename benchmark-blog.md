# Knowledge Taxonomy - Make an AI Agent 40% More Accurate Without Touching the Model

> When an AI agent gets things wrong, the instinct is to reach for a bigger model. We did the
> opposite: we held the model constant — same model, same questions, same scorer — and changed only
> **how the agent's knowledge was organised**. Accuracy went up **40%** against the method most teams
> use today — and the harder the reasoning a question demanded, the bigger the gain: **14%** on simple
> lookups, **82%** where two documents had to be combined, **120%** where the answer was in an image.

---

Every AI leader has had the conversation. The agent is inaccurate; the proposal on the table is a
larger model, a newer model, a more expensive model. Sometimes that is right. But in our benchmark
the model was never the problem. The agent was not failing to *reason* about the evidence. **It was
never handed the evidence** — because of how the knowledge was stored and found. No model, at any
size, can read a document it was not given or a diagram that was never attached.

## The one thing to remember

**You can improve an AI agent's accuracy dramatically by improving how its knowledge is organised —
not by buying a larger model. With the model held constant, organising the knowledge base into a
hierarchy and letting the agent navigate it raised accuracy 40% over the popular flat-index method
(RAG). And the gain grows with the complexity of the reasoning: +14% on simple lookups, +18% within
one document, +82% across two documents, +120% when the answer is in an image. The harder the
knowledge work, the more organisation is worth.**

## The one thing to do before your next model upgrade

Run the same test we ran: **same questions, same model, same scorer — the knowledge organised one
way versus the other.** It takes days, not a quarter, and it tells you whether your accuracy problem
is in the model or in the knowledge. In our case it was entirely in the knowledge.

If even that is too much for this week, do the free version: take fifty real questions your agent
has handled and sort them into *single passage*, *needs two documents*, *answer is in an image*. The
share in the last two columns is the share of your workload where a bigger model will not help.

---

## 1. What the data says — same model, two ways to find knowledge

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
Share of maximum score, by question type — same model on both sides

                     Organised (HCAG)               Standard (RAG)
Simple lookup        ████████████████████ 100%      ██████████████████░░  88%
One paragraph        ██████████████████░░  89%      ████████████████░░░░  78%
One document         █████████████████░░░  83%      ██████████████░░░░░░  71%
Two documents        ███████████████████░  95%      ██████████░░░░░░░░░░  52%   ◄ cliff
In an image          ██████████████████░░  92%      ████████░░░░░░░░░░░░  42%   ◄ cliff
```

| Question type | Organised | Standard | Gain from organisation alone |
|---|---:|---:|---:|
| Simple lookup | 100.0% | 87.5% | +14% |
| Reason within one paragraph | 88.9% | 77.8% | +14% |
| Combine three points from one document | 83.3% | 70.8% | +18% |
| **Combine two different documents** | **95.2%** | **52.4%** | **+82%** |
| **Answer is in an image, not the text** | **91.7%** | **41.7%** | **+120%** |
| **Overall** | **91.9%** | 65.8% | **+40%** |

Read the right-hand column top to bottom: the gain never falls as the reasoning gets harder, and it
jumps more than fourfold at the point where a question stops fitting inside one passage. That is the
second finding, and for an automation roadmap it is the more useful one — **the work you most want to
automate is the work where organisation pays most.**

Three levels of a modest lead, then a cliff. Standard RAG holds 71–88% as long as the answer lives
inside one passage — exactly the 70–80% ceiling practitioners already quote for it. Then it drops to
52% and 42%: on questions whose answer is in a picture, **worse than a coin flip**. The organised
agent, running the *identical* model, never fell below 83% on any category.

And the kind of failure matters:

| | Organised | Standard |
|---|---:|---:|
| Questions passed (scored "covers the key points" or better) | **37 of 37** | 24 of 37 |
| Wrong or refused | **0** | 13 |
| **Refused outright** — "I don't have enough information" | **0** | **10** |

The organised agent was never wrong and never refused. The standard agent — same model — refused
ten in-scope questions whose answers were in its knowledge base. That is not a model that cannot
reason. It is a model that was not shown the page.

---

## 2. Why a bigger model would not have helped

Both failures below are failures of *what the model was given*, not of what it did with it. Neither
is fixed by more parameters.

**It was not given the second document.** A question needing evidence from two documents requires
both to surface in the same small set of passages from a single similarity search. When one document
dominates the ranking, the other never arrives — and a model cannot reason about a page it never
saw. One question from the run, verbatim: *"What are the employer's repatriation obligations, and
how does this contrast with ONE Pass cancellation?"* The organised agent loaded both documents and
scored 3. The standard agent, same model, received one half, correctly declined to invent the other,
and scored **0**.

**It was not given the picture.** A flat index can only hold a *written description* of each image,
produced by an AI in advance. Whatever the describer didn't write down is unfindable afterwards, at
any search quality and with any model. Asked for the exact wording of a checkbox on a form, the
standard agent scored 0 — the phrase was in no description. The organised agent attaches the image
itself; the same model reads the form.

**We tried to make the standard approach win first.** Before comparing, we audited our own RAG
baseline and fixed three genuine defects in it — one of which improved its retrieval by a measured
17%. The comparison ran against the *improved* system. It still lost by 40% overall and by 82–120%
on the hard categories, because no retrieval fix delivers a document that was not selected or an
image that was never attached. **The gap is in the organisation of the knowledge, not in the model
and not in the tuning.** (Details in the appendix.)

---

## 3. What this changes on your roadmap

A chatbot that is handed the wrong passage writes an unhelpful paragraph. **An agent that is handed
the wrong passage takes the wrong action.** Look at what is on the automation roadmap and notice
which side of the cliff each item lands on — and therefore where a model upgrade would and would not
move the needle.

**Predictive and corrective maintenance.** The answer is in the manual for *this* model and *this*
revision — and very often in the diagram: a labelled component, a wiring topology, a threshold on a
chart. That is the "in an image" row, where the standard approach scored 42%. An organised knowledge
base makes "this asset, this revision" a branch; the other revisions are structurally out of reach.

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
Whole-document retrieval is the safety property here.

**The line to carry upward:** *our automation candidates are cross-document and visual work. The
method we default to scores 42–52% on exactly that with the model we have — and a bigger model
would still not be shown the second document or the diagram. The lever is how the knowledge is
organised, that is a one-time investment rather than a per-call cost, and the harder the reasoning
we ask of the agent, the more it returns.*

---

## 4. What to do

**This week — sort the workload (cost: an afternoon).** Take a sample of the questions your target
automation would handle. Sort them: single passage, cross-document, visual. That split tells you how
much of your accuracy problem a model upgrade can even reach.

**This month — run the same-model test (cost: days).** Organise one bounded domain's knowledge as a
hierarchy — pick one where the knowledge is *already organised* by people; maintenance libraries,
policy repositories and runbook collections almost always are. Run the same questions through both
approaches with the same model and the same judge, exactly as here. Let the numbers decide.

**Then — decide with data.** If organising the knowledge moves accuracy the way it did here, you have
found a gain that does not add to your per-call model bill. If it does not, you have ruled it out
cheaply and the model conversation proceeds on evidence.

**What to demand from whoever builds it — a team or a vendor:**

- **The comparison must hold the model constant.** Same model, same questions, same scorer, and the
  baseline tuned in good faith. Ask what they fixed in the baseline before comparing. If the answer
  is "nothing", it is a demo.
- **Failures must be diagnosable.** When our organised agent got two questions wrong on a second test
  set, both were traceable to a *named document* and a *specific reasoning step* — and were fixed by
  changing the agent's instructions, not by rebuilding anything or changing the model. Pass rate
  went from 87.5% to **100%**, reproduced on three separate runs, one made by someone not involved in
  the tuning.
- **Negative results must be published.** One of our three attempted fixes made things *worse* — it
  caused the agent to invent a portal name — and we reverted it. It is in the data alongside the rest.
  A benchmark reported only when it agrees with the vendor is marketing.

---

## 5. What it costs, and when a model upgrade is still the right call

**Organising the knowledge is a one-time investment** — days to weeks depending on scope — and it is
frequently already half-done, because people organise operational knowledge the same way: by product,
by domain, by procedure. A model upgrade is a configuration change, and then a higher cost on every
call for as long as the agent runs. Which is cheaper depends on your volume; the point is that they
are different kinds of cost, and only one of them was needed here.

**Keep standard RAG, and consider the model, when** the knowledge base is small, the questions are
genuinely FAQ-shaped, and you need something shipped this quarter. Our own data supports this: on
simple lookups the standard approach scored 87.5%, and a 14% gap will not repay a taxonomy if that
is all your traffic looks like.

**Organise the knowledge when** answers routinely need more than one document; when answers are
sometimes in a diagram, form or screenshot; when the same knowledge is consulted across many steps
of a task; or when you will have to explain afterwards which document drove a decision. On that
work, our data says the model is not where the accuracy is.

---

## 6. How far to trust this

Discount correctly rather than believe wholesale.

- **We did not benchmark a larger model.** The claim is not that organisation beats a bigger model
  head-to-head; it is that with the model *held constant*, organisation alone produced these gains —
  so it is the lever to test before paying for a model upgrade, not after.
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
lucky search, and why a diagram arrives as a diagram — for whatever model is doing the reading.

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

### C. Reproduce

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

### D. Data

Final score, as share of maximum. Each figure links to its scored CSV.

| | Beta set (16 questions) | Benchmark (37 questions) |
|---|---:|---:|
| **RAG** | not run | [65.8%](./sample-benchmark-report/rag-kb-eval-scored.csv) |
| **HCAG** | [91.7%](./sample-benchmark-report/beta-05-independent-rerun-scored.csv) | [91.9%](./sample-benchmark-report/hcag-kb-eval-scored.csv) |
