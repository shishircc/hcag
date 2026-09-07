# Knowledge Taxonomy, Part 2 — What the Benchmark Changes in an Enterprise AI Plan

> *Part 1 —
> [Your AI Agent Needs a Knowledge Taxonomy — a Small Investment That Pays Rich Dividends](https://www.linkedin.com/pulse/your-ai-agent-needs-knowledge-taxonomy-small-pays-rich-choudhary-qix9c)
> — argued that most knowledge-grounded agents plateau around 70–80% because of how they find
> knowledge, not which model they run, and that organising the knowledge base into a hierarchy fixes
> it. This part puts numbers on that argument and asks the practical question: if it holds, what
> changes in how you build, buy, budget and govern AI agents?*

**If you skipped Part 1**, three sentences carry you. *Flat RAG* — what most teams run — cuts
documents into passages, indexes them by similarity, and hands the model the eight passages that
look most like the question. A *knowledge taxonomy* is the structure your organisation already
uses — manuals by model and revision, policies by domain, runbooks by system — made explicit and
machine-readable as domain → topic → subtopic. *HCAG* is the agent pattern that uses it: reason
about which branch a question belongs to, load the whole relevant document (images included), and
keep it open for the rest of the task.

---

## If you read nothing else

- **Same model, same questions, same scorer — the only change was how the knowledge was organised.
  Accuracy rose 40%.** None of that gain came from the model, because the model did not change.
- **The gain tracks the difficulty of the reasoning**: 14% on simple lookups, 82% when an answer
  needs two documents, 120% when it is in an image. The work you most want to automate is the work
  where organising pays most.
- **The standard approach fails silently.** It refused 10 of 37 in-scope questions — "I don't have
  enough information" — with the answer sitting in its knowledge base. That reads as a content gap.
  Nobody files a bug against it.
- **This changes five things in an enterprise plan**: the model-upgrade budget, the status of the
  knowledge base, the retrieval architecture you specify, how you evaluate, and what you put on the
  risk register. §3 takes each in turn. The investment is one-time organisation, not per-call cost.

---

## 1. The evidence

Two agents, **same 37 questions**, same 127-document knowledge base (Singapore government work-pass
rules), **same AI model writing every answer, same independent AI judge** scoring each from 0 (wrong
or refused) to 3 (complete). Questions were generated from the knowledge base itself at five levels,
because the useful question is not "how accurate?" but "*where* does the ceiling sit?"

| Level | What answering takes |
|---|---|
| Simple lookup | Look it up |
| One paragraph | Reason within a single passage |
| One document | Combine three points from one document |
| **Two documents** | Combine evidence from two different documents |
| **In an image** | The answer is in a form, diagram or screenshot, not the text |

```
Share of maximum score, by question type — same model on both sides

                     HCAG                           Flat RAG
Simple lookup        ████████████████████ 100%      ██████████████████░░  88%
One paragraph        ██████████████████░░  89%      ████████████████░░░░  78%
One document         █████████████████░░░  83%      ██████████████░░░░░░  71%
Two documents        ███████████████████░  95%      ██████████░░░░░░░░░░  52%   ◄ cliff
In an image          ██████████████████░░  92%      ████████░░░░░░░░░░░░  42%   ◄ cliff
```

| Level | HCAG | Flat RAG | Gain |
|---|---:|---:|---:|
| Simple lookup | 100.0% | 87.5% | +14% |
| One paragraph | 88.9% | 77.8% | +14% |
| One document | 83.3% | 70.8% | +18% |
| **Two documents** | **95.2%** | **52.4%** | **+82%** |
| **In an image** | **91.7%** | **41.7%** | **+120%** |
| **Overall** | **91.9%** | 65.8% | **+40%** |

Flat RAG holds 71–88% for as long as an answer lives inside one passage — the ceiling Part 1
described. Then it falls: 52% when two documents are needed, 42% when the answer is in a picture,
worse than a coin flip. HCAG, on the identical model, never fell below 83% on any level.

**And the same comparison on a second, easier set.** We later ran 16 further questions through
both agents — same model, same judge. That set has **no two-document and no visual questions**; it
sits entirely in the top three rows above. Read the two tables together:

| Second set — question type | HCAG | Flat RAG | HCAG uplift |
|---|---:|---:|---:|
| Simple lookup (9) | 96.3% | 92.6% | +4% |
| One paragraph (5) | 93.3% | 86.7% | +8% |
| Should decline and hand over (2) | 83.3% | 66.7% | +25% |
| **Overall (16)** | **93.8%** | 87.5% | **+7%** |

Same two architectures; a workload drawn from the easy half of the spectrum; the advantage shrinks
from 40% to 7%. HCAG passed all 16 with no row below 2; flat RAG failed two — both on questions it
answered with "I don't have enough information".

**How they failed matters more than how often.**

| | HCAG | Flat RAG |
|---|---:|---:|
| Questions passed (key points covered or better) | **37 of 37** | 24 of 37 |
| Wrong or refused | **0** | 13 |
| **Refused outright** — "I don't have enough information" | **0** | **10** |

HCAG's nine imperfect answers were all "correct but said more than was asked". Flat RAG's failures
were refusals on questions the knowledge base could answer. That is not a model that cannot reason;
it is a model that was **never handed the page** — because one similarity search does not reliably
surface the second document, and cannot surface an image it only holds a written description of.
No larger model reads a document it was not given.

**The baseline was not set up to lose.** Before comparing, we audited our own flat-RAG system and
fixed three real defects in it — one improved its retrieval by a measured 17%. The comparison ran
against the improved version. It still lost by 40%, because no retrieval fix delivers a document
that was never selected. Details in the appendix.

---

## 2. Where it pays, and where it doesn't

Look at your automation roadmap and sort each item by the question type it mostly asks.

**Where the gain is 40–120%.** Answers that routinely span more than one document, or sometimes
live in a diagram, form or screenshot: maintenance manuals by model and revision, fraud typologies
reasoned over together with escalation matrices, runbooks that must be executed whole, support cases
that run across eligibility, documents, fees and timelines. On this work the model is not where the
accuracy is, and a bigger model would still not be shown the second document.

**Where the gain is 7–14%.** Single-passage lookups and FAQ traffic. Flat RAG scored 87.5% on
simple questions; on the easy-only second set the whole gap was 7%. If that is your traffic, keep
what you have — the organising effort will not repay itself, and now you have the number that says so.

---

## 3. What this changes in an enterprise AI plan

**1. The model-upgrade budget.** The default response to an inaccurate agent is a larger model. This
benchmark held the model fixed and moved accuracy 40% by changing what the model was *given*. The
implication is a sequencing rule: before approving a model upgrade for accuracy, run the same-model
test — same questions, same model, same judge, knowledge organised one way versus the other. It
takes days and it separates the two causes. A model upgrade is a per-call cost for the life of the
agent; organising the knowledge is a one-time cost. Spend the one-time cost first if the test says
the accuracy is there.

**2. The status of the knowledge base.** In a flat-RAG programme the knowledge base is an input —
documents go into an index and the index is the asset. In a taxonomy-backed programme the
*structure* is the asset: it needs an owner, usually the domain that already maintains those
manuals or policies; it needs a change process, because a mis-filed document is now a mis-routed
answer; and it needs to be versioned, because "this asset, this revision" is a branch. Most
operational knowledge already has this structure informally. The work is making it explicit and
assigning it an owner — a knowledge-management decision, not an engineering one.

**3. The retrieval architecture you specify.** Whether you build or buy, the requirement changes from
"index our documents" to three capabilities: the agent selects by *reasoning over a catalog* of what
each part of the knowledge base covers, not by similarity alone; it loads *whole documents with their
images*, not passages; and it can say afterwards *which documents an answer used*. Ask any platform
or vendor for those three directly. A system that can only return the top passages by similarity
will reproduce the cliff above regardless of the model behind it.

**4. How you evaluate.** The single most useful artefact in this benchmark is not the result but the
question set: built from the knowledge base itself, at five levels of difficulty, scored by a
consistent judge, with per-question justifications published. That is a standing capability, not a
one-off. It is what lets you tell a retrieval failure from a model failure, a real regression from
noise, and a vendor claim from a demo. Three rules for it: hold the model constant when comparing
architectures; tune the baseline in good faith and say what you fixed; publish the negative
results — one of our three attempted fixes on the second set made things worse, and we say so.

**5. The risk register.** Two entries. *Silent refusal*: an agent that declines in-scope questions
looks like a content gap and quietly becomes "the bot can't do that" — the standard approach did
this ten times in thirty-seven, and the only way to see it is to count refusals against what the
knowledge base actually contains. *Untraceable action*: an agent that acts on a wrong passage takes
a wrong action, and "the eight most similar passages" is not an audit trail. For any agent that acts
— in operations, in maintenance, in financial-crime workflows — the ability to name the document
behind a decision is a control, not a feature.

**The line to carry upward:** *organising our knowledge moved agent accuracy 40% with the model
unchanged, and the gain grows with the difficulty of the work we most want to automate. It is a
one-time investment in an asset we mostly already have, and it comes with an audit trail the
alternative cannot give.*

---

## 4. A ninety-day plan with a decision gate

**Weeks 1–2 — size the opportunity (cost: an afternoon).** Take fifty real questions your target
automation would handle. Sort them: single passage, two documents, in an image. The last two columns
are the share of your accuracy problem a model upgrade cannot reach. Separately, count the questions
your current agent refuses and check how many the knowledge base could have answered.

**Weeks 3–8 — the same-model pilot (cost: days of effort, one domain).** Pick a bounded domain whose
knowledge is already organised. Make the structure explicit, assign it an owner, and run the same
questions through both approaches with the same model and the same judge. Everything needed to
reproduce our run is in the appendix.

**The gate.** Proceed if the organised approach clears three tests on your own questions: a material
gain on the two-document and visual rows; zero refusals on questions the knowledge base covers; and
for every answer, a named document behind it. If it clears none, you have ruled out the cheaper fix
for the price of an afternoon and a short pilot, and the model conversation proceeds on evidence.

**Weeks 9–13 — scale, and measure what this benchmark did not.** Extend to the next domain, and
instrument latency and cost per task. Part 1 argued both improve; this piece did not measure either
(see §5). Do not budget on them until your pilot has.

---

## 5. What this benchmark did not prove

Two of Part 1's claims — that the organised approach is *faster* and *cheaper per task* because it
retrieves once and reuses — were **not measured here**. This benchmark scored accuracy only. Those
claims rest on the design argument, not on data in this piece, and we would rather say so than let
the accuracy numbers imply support they do not give. For planning, that means: treat speed and cost
as hypotheses for the pilot to test, not as savings to book.

We also did **not** benchmark a larger model. The claim is not that organisation beats a bigger
model head-to-head; it is that with the model held constant, organisation alone produced these gains
— so it is the lever to test *before* paying for a model upgrade, not after.

---

## 6. Scorecard against Part 1

For readers of Part 1, each claim it made and what the data says.

| Part 1 claimed | Measured? | Result |
|---|---|---|
| Flat RAG plateaus around 70–80% | Yes | 71–88% inside one passage; 52% and 42% beyond it |
| The missing ingredient is structure, not the model | Yes, by construction | Same model both sides; +40% |
| Strong reasoning because the agent sees whole documents | Yes | Gain rises with difficulty, more than fourfold at the one-passage boundary; the easy-only set shows it collapsing to 7% |
| High accuracy — similar-but-wrong retrievals cannot happen | Yes | 0 wrong, 0 refused, 37 of 37 passed |
| Faster per task | **No** | Not measured — pilot hypothesis |
| Cheaper per task | **No** | Not measured — pilot hypothesis |

One further property showed up in the second set: when the organised agent *was* wrong — twice —
both errors were traced to a named document and a specific reasoning step, and fixed by changing the
agent's instructions rather than rebuilding anything. A later revision took that set to 93.8% with
no failures. That is the "behaviour you can reason about" Part 1 promised, and it is the property
that makes a knowledge-grounded agent maintainable rather than merely accurate once.

---

## 7. How far to trust this

- **37 questions**, six to eight per level. The overall gap and the two large gaps are far wider than
  the noise at that size. The 14–18% leads on the three easier levels are **not** statistically
  separable from noise — read them as directional.
- **The second set is 16 questions**, nine of them simple lookups. The +4% on those nine is one
  answer's worth of difference and is not separable from noise; the set's overall +7% rests mainly on
  the five one-paragraph questions and the two the agent should decline. Read the per-type rows as
  directional.
- **The scorer is an AI judge**, reliable in aggregate, not per row. Every row's justification is
  published — see Appendix D for the per-question files.
- **One knowledge base, one domain** — government policy. Expect the visual gap to be larger in
  engineering and network domains, and the simple-lookup gap smaller in FAQ-heavy ones.
- **Reference answers came from the same knowledge base**; this measures faithful retrieval of known
  content, not open-domain correctness.

---

*Part 1 explained why a taxonomy should work. This part shows that on accuracy it did — by 40%
overall, and by 82–120% on exactly the work that automation consists of — and sets out what that
changes: sequence the knowledge before the model, treat the taxonomy as an owned asset, specify
retrieval by capability, evaluate as a standing practice, and put silent refusal on the risk
register. Speed and cost are the next things to measure.*

---

## Appendix — for readers who want the detail

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

Final score, as share of maximum. Each figure links to its scored CSV, which carries every question,
both agents' answers, the score and the judge's one-sentence justification per row.

| | Beta set (16 questions) | Benchmark (37 questions) |
|---|---:|---:|
| **RAG** | [87.5%](./beta-set/beta-rag-kb-eval-scored.csv) | [65.8%](./benchmark-set/benchmark-rag-kb-eval-scored.csv) |
| **HCAG** | [93.8%](./beta-set/beta-hcag-kb-eval-scored.csv) | [91.9%](./benchmark-set/benchmark-hcag-kb-eval-scored.csv) |
