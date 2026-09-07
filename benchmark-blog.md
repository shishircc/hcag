# Your RAG Agent Has a Ceiling. We Measured Where It Is — and What Moves It.

> A head-to-head test of two ways to give an AI agent access to your organisation's knowledge:
> the standard approach most teams use today, and one that starts by organising the knowledge
> first. Same questions, same knowledge, same AI model, same scorer. **The organised approach
> answered 92% of the maximum score; the standard approach answered 66%** — and the gap was not
> spread evenly. On the easy questions the two were close. On the hard ones, the standard approach
> failed more often than it succeeded.

Every number in this piece can be traced to a spreadsheet row in
[`sample-benchmark-report/`](./sample-benchmark-report/README.md). Nothing here is projected or
extrapolated.

---

## If you read nothing else

- **Both approaches share the same AI model.** What was tested is how the agent *finds* the knowledge,
  not how clever it is. That is a decision your architecture makes, not your model vendor.
- **The standard approach (RAG) is fine for simple lookups** — it scored 87.5% on those, 14% behind.
  If your agent only answers FAQs, keep it.
- **It breaks in two specific situations**: when an answer needs two documents (**52%**, half the
  questions failed), and when the answer is in a diagram or image (**42%**). The organised approach
  scored 95% and 92% on the same questions.
- **The failure is silent.** The standard agent refused to answer 10 of 37 in-scope questions — "I
  don't have enough information" — even though the information was in its knowledge base. That looks
  like a content gap. It is a retrieval gap, and nobody will file a bug about it.
- **The cost is a one-time organising effort**, measured in days to weeks, and much of it is already
  done if your knowledge is stored the way most operational knowledge is — by product, by domain, by
  procedure.

---

## 1. What you are probably running today

Most teams building a knowledge-grounded AI agent use **RAG — Retrieval-Augmented Generation**. The
mechanics, in one paragraph: every document is cut into small passages ("chunks", a few hundred
words each), each passage is turned into a numerical fingerprint, and all the fingerprints go into
one big searchable index. When a user asks a question, the system finds the handful of passages whose
fingerprints look most similar to the question and hands those to the AI model to write an answer
from.

It works, and it is quick to build. That is why it is the default. It also has a well-known ceiling —
practitioners typically quote **70–80% accuracy on non-trivial questions** — and teams spend a great
deal of effort trying to tune past it: better fingerprints, smarter chunking, re-ranking, hybrid
search. Those gains are real but marginal, and the cost per point of accuracy rises.

The reason for the ceiling is structural. Every passage in the entire corpus competes on every
question, so "similar but wrong" passages from unrelated documents are always in the running. And a
passage is a fragment: it has lost the definitions, caveats and scope that surrounded it in the
original document.

---

## 2. The alternative: organise the knowledge first

Your organisation already organises its knowledge. Maintenance manuals are filed by equipment model
and revision. Policies are filed by domain. Runbooks are filed by system. Nobody keeps this as one
undifferentiated pile — because people, too, find things by knowing which section to look in.

A **knowledge taxonomy** is that structure, made explicit and machine-readable: a hierarchy of
**domain → topic → subtopic**, where each folder holds the documents (and diagrams) that belong to
it. Here is the one from this benchmark — a government work-pass knowledge base — with folder names
abridged for readability:

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

**HCAG — Hierarchical Context Augmented Generation** — is the approach that lets an AI agent use
that structure. Instead of searching fingerprints across everything, the agent:

1. **Finds the right branch.** It is given a one-line summary of every folder in the tree and uses
   its own reasoning to decide which folder(s) the question belongs to — the way an experienced
   colleague knows which manual to reach for. Everything outside that branch is simply not in play.
2. **Reads the whole document**, with its diagrams, not a fragment of it. Definitions, exceptions
   and preconditions arrive together.
3. **Keeps that material open** for the rest of the task, rather than searching again at every
   step.

The trade: you invest in organising the knowledge up front. In return, the agent never sees the 90%
of the corpus that could mislead it, and it never works from a fragment.

---

## 3. The test

Two agents answered the **same 37 questions** over the **same knowledge base** — 127 documents of
Singapore government work-pass rules — scored by the **same independent AI judge** on a 0–3 scale
(0 = wrong or refused, 3 = complete and correct). The only difference was how each agent found its
knowledge.

| | Organised (HCAG) | Standard (RAG) |
|---|---|---|
| What the agent reads | the whole relevant document, with its images | the 8 most similar passages |
| How it chooses | reasons over the taxonomy | fingerprint similarity |
| AI model writing the answer | **same** | **same** |
| Scorer | **same** | **same** |

The questions were deliberately built at five levels of difficulty, because "how accurate is it?"
is the wrong question — the right one is "*where* does it stop being accurate?"

| Level | What it takes to answer |
|---|---|
| **Simple** | Look it up — a straightforward FAQ |
| **Medium** | Read one paragraph and reason about it |
| **Complex** | Combine three points from one document |
| **Cross-document** | Combine evidence from **two different documents** |
| **Visual** | The answer is in an **image** — a form, a diagram, a table in a screenshot — not in the text |

Full method and the RAG baseline's own engineering are in the appendix. One thing to know now: the
RAG baseline was **not** set up to lose. We found and fixed three genuine defects in it before running
the comparison, one of which improved its retrieval by a measured 17%. It still lost.

---

## 4. What we found

**Overall: HCAG 91.9% of maximum, RAG 65.8%.** HCAG passed every question (37 of 37 scored at least
"covers the key points"); RAG passed 24 of 37.

But the average hides the finding:

| Level | HCAG | RAG | HCAG's advantage |
|---|---:|---:|---:|
| Simple | 100.0% | 87.5% | +14% |
| Medium | 88.9% | 77.8% | +14% |
| Complex | 83.3% | 70.8% | +18% |
| **Cross-document** | **95.2%** | **52.4%** | **+82%** |
| **Visual** | **91.7%** | **41.7%** | **+120%** |

Three levels of a modest, steady lead — then a cliff. The standard approach holds 71–88% as long as a
question lives inside one passage, which is exactly the 70–80% ceiling practitioners already
describe. The moment a question needs two documents, or a picture, it drops to 52% and 42% — worse
than a coin flip on the visual questions.

**The shape of the failure matters more than the size.**

```
Score      3     2     1     0        (3 = complete · 0 = wrong or refused)
HCAG      28     9     0     0
RAG       15     9    10     3
```

HCAG never scored below 2 — never wrong, never refused. Its nine imperfect answers were all "correct
but included more than was asked". RAG produced **10 refusals on questions the knowledge base
could answer** — the agent said it did not have the information — concentrated in exactly the two hard
categories.

Consider what that means in production. A wrong answer is embarrassing but visible; someone will
catch it. A fluent, polite "I don't have that information" for something that *is* in the knowledge
base looks like a content gap. Nobody investigates it. It quietly becomes "the bot can't do that",
and a class of questions routes back to humans forever.

---

## 5. Why the gap is structural — and why tuning RAG won't close it

Two facts about the standard approach, neither of which a better fingerprint model changes.

**It cannot easily cross a document boundary.** A cross-document question needs evidence from two
places. A one-shot search must get both into the same top-8 in a single query — and when one
document dominates the similarity ranking, the other never arrives. The organised agent simply loads
both folders. One question from the run, verbatim: *"What are the employer's repatriation
obligations, and how does this contrast with ONE Pass cancellation?"* HCAG scored 3. RAG scored 0 —
it retrieved one half of the question and, correctly, declined to invent the other.

**It cannot see a picture.** The standard approach can only index a *written description* of each
image, produced by an AI in advance. Anything the describer did not think to write down is
unfindable, no matter how good the search is afterwards. Asked for the exact wording of a checkbox
on a form, RAG scored 0: the phrase was never in any description. The organised agent attaches the
image itself when it loads the folder — the model reads the form.

**We tried to make RAG win first.** Before the comparison we audited our own RAG baseline and fixed
three real defects — a failure mode that silently disabled half its search, passages that did not
carry the name of the document they came from, and a vocabulary mismatch between how users ask and
how the documents are written. The comparison ran against the *improved* system. It lost by 40%
overall and by 82–120% on the hard categories, because none of those fixes touches a document
boundary or an image. **The gap is architectural.** Details for your engineers are in the appendix.

---

## 6. Why this matters if your agent acts, not just chats

A chatbot that retrieves the wrong passage produces an unhelpful paragraph. **An agent that retrieves
the wrong passage takes the wrong action.** Every automation programme currently on an executive's
roadmap has the same shape — a bounded domain, knowledge that people have already organised, and a
multi-step task that consults that knowledge repeatedly — and each one lands on the hard side of the
cliff above.

**Predictive and corrective maintenance.** Fault trees, service bulletins, tolerances by model and
revision. An agent that pulls a torque specification from the wrong revision of a manual is worse
than no agent. In a taxonomy, "this asset, this model, this revision" is a branch — the other
revisions are *structurally* out of reach, not merely ranked lower. And the visual category is not
academic here: the answer is very often in the diagram — a labelled component, a wiring topology, a
threshold on a chart. RAG scored 42% on visual questions.

**Fraud and financial-crime investigation.** Policy, typology libraries, regulatory thresholds,
escalation matrices. An investigator agent must reason across several of these at once — precisely
the cross-document shape where RAG scored 52%. It must also leave an audit trail. "This decision used
the sanctions typology document and the escalation matrix" is a compliance artefact; "the eight
passages that looked most similar" is not.

**Autonomous customer support.** Not one question — a case, over many turns: eligibility, then
documents, then fees, then timelines. The organised agent finds the branch once and keeps it open
for the whole case. The support agent in this repository also demonstrates the other half of the
job: recognising what is *not* its remit and handing over to a person.

**Self-healing networks and IT operations.** Runbooks, topology, vendor command references, change
policy. A remediation agent executing a runbook needs the **whole runbook** — steps, preconditions,
rollback. A runbook cut into passages is actively dangerous: step 4 retrieved without the
precondition in step 2 is a plausible-looking instruction to break production. Whole-document
retrieval is not a quality refinement here. It is the safety property.

The thread through all four: **agents work over bounded domains, repeatedly, and must be auditable
afterwards.** Those are properties of *structure*. A better search engine does not supply them.

---

## 7. What it costs, and when RAG is still the right call

**The taxonomy is the investment.** Organising the knowledge base into a hierarchy takes days to
weeks depending on scope, and it is the largest cost of adopting this approach. It is also
frequently already done: maintenance libraries, policy repositories and runbook collections are
almost always organised by domain already. If yours is a heap of unstructured documents with no
natural hierarchy, that heap is the project — and it would have been the project for any serious
knowledge programme.

**Stay with RAG when** the knowledge base is small, the questions are genuinely FAQ-shaped, and you
need something shipped this quarter. Our own numbers support this: on simple questions RAG scored
87.5%, and a 14% gap will not repay a taxonomy if that is all your traffic looks like.

**Move to an organised approach when** any of the following is true:

- Answers routinely need **more than one document**.
- Answers are sometimes **in a diagram, form or screenshot** rather than in the text.
- The same knowledge is consulted **across many steps of a task**, not once per question.
- You will have to **explain afterwards which document drove a decision** — regulated processes,
  safety-critical operations, anything auditable.

**Four questions to ask your team on Monday:**

1. Of the questions our agent currently refuses, how many *are* answerable from the knowledge base?
   (If nobody knows, that is the first finding.)
2. What share of our real questions need two or more documents?
3. How much of our knowledge is in images, forms and diagrams — and what does our current system do
   with those?
4. Is our knowledge base already organised by domain? If so, how much of the taxonomy exists today?

---

## 8. When it goes wrong, can your team fix it?

A benchmark that only produces a score tells you where you are. The more important operational
question is what happens when the agent is wrong.

We ran a second, separate set of 16 questions against the organised agent and examined only the
failures. There were two. **In both, the agent had retrieved the right documents** — the error was in
how it reasoned about them. One was an eligibility question it answered "no" to while listing, in the
same reply, the route that would have qualified the person. The other was a request the agent should
have handed to a human officer rather than answered itself.

Both were fixed by changing the agent's *instructions* — not by rebuilding the index, not by
re-tuning retrieval. The effect, measured:

| | Score | Pass rate | Failures |
|---|---:|---:|---:|
| Before the fix (two separate runs) | 87.5% – 89.6% | 87.5% – 93.8% | 2 and 1 |
| **After the fix — three separate runs** | **91.7%** | **100%** | **0** |

The improvement reproduced on all three runs of the corrected agent, including one made
independently by someone not involved in the tuning. For scale: the two *pre-fix* runs disagree
with each other by about 2 points of score — that is the normal run-to-run noise on a set this
size. The fix moved the score by twice that and removed the failures entirely.

Two things your team should take from this, and expect from any vendor:

- **The failure was traceable to a named document and a specific reasoning step.** That is what
  made it fixable in a day. "The search ranked the wrong passage" rarely is.
- **One of our three attempted fixes made things worse** — it caused the agent to hallucinate a portal
  name — and we reverted it. That negative result is published alongside the others, with its data. A
  benchmark that is only reported when it agrees with the vendor is marketing.

---

## 9. How far to trust these numbers

We would rather you discount them correctly than believe them wholesale.

- **The main comparison is 37 questions**, six to eight per difficulty level. The overall gap (+40%)
  and the two large gaps (+82%, +120%) are far wider than the noise at that size. The 14–18% leads on
  the three easier levels are **not** statistically separable from noise — treat those as directional.
- **The second set is 16 questions**, and its run-to-run noise is about 2 points of score.
- **The scorer is an AI judge**, not a panel of people. Scores are reliable in aggregate, not for
  any single row. Every row's justification is published, so any individual score can be audited.
- **One knowledge base, one domain.** Government policy documentation: well-structured prose, tables
  and forms. We would expect the visual gap to be *larger* in domains with more diagrams (engineering,
  networks) and the simple-question gap to be *smaller* in domains that are mostly FAQ.
- **The reference answers were written from the same knowledge base**, so this measures faithful
  retrieval of known content — not open-domain correctness.

---

## The one-line version

On questions a single passage can answer, standard RAG is competitive and cheaper to build. The
moment a question needs two documents (+82%) or something only visible in an image (+120%), a flat
index stops being able to represent the problem — and it fails by *declining*, not by being visibly
wrong. If you are building an agent that **acts** on your organisation's knowledge, that is the
failure you cannot afford, and organising the knowledge is what removes it.

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

The five difficulty levels map to the harness's kinds: `simple`, `medium`, `complex`, `hard-1`
(cross-packet), `hard-2` (multimodal).

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
