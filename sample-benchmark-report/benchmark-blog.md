# Knowledge Taxonomy, Part 2 — The Numbers Behind "Your AI Agent Needs a Knowledge Taxonomy"

> *Part 1 —
> [Your AI Agent Needs a Knowledge Taxonomy — a Small Investment That Pays Rich Dividends](https://www.linkedin.com/pulse/your-ai-agent-needs-knowledge-taxonomy-small-pays-rich-choudhary-qix9c)
> — made the case: flat RAG plateaus around 70–80%, the missing ingredient is structure rather than
> a better model, and HCAG is the pattern that turns a taxonomy into an agent. This part is the
> evidence. If you have not read Part 1, the argument is there; this piece only measures it.*

---

Part 1 asked you to accept several claims on reasoning alone. We then built both agents, gave them
the same knowledge base, the same model and the same scorer, and ran the same questions through
each. This is what came back — claim by claim, including the two claims we did **not** manage to
test.

## The headline

**With the model held constant, organising the knowledge base and letting the agent navigate it
raised accuracy 40% over flat RAG. And the harder the reasoning a question demanded, the larger the
gain — 14% on simple lookups, 82% when two documents had to be combined, 120% when the answer was in
an image.**

The model was the one thing we did not change. Whatever accuracy appeared came from the knowledge
organisation alone.

---

## What was measured, and how

Two agents, **same 37 questions**, same 127-document knowledge base (Singapore government work-pass
rules), **same AI model writing every answer, same independent AI judge** scoring each from 0 (wrong
or refused) to 3 (complete). The only variable: how each agent found its knowledge.

- **Flat RAG** — passages indexed by similarity, the eight most similar handed to the model.
- **HCAG** — the taxonomy from Part 1; the agent reasons about which branch applies and loads the
  whole relevant document, images included.

The questions were generated from the knowledge base itself at five levels of difficulty — because
the question Part 1 raised was not "how accurate?" but "*where* does the ceiling sit?"

| Level | What answering takes |
|---|---|
| Simple | Look it up |
| One paragraph | Reason within a single passage |
| One document | Combine three points from one document |
| **Two documents** | Combine evidence from two different documents |
| **In an image** | The answer is in a form, diagram or screenshot, not the text |

---

## Claim 1 — "Flat RAG plateaus around 70–80%"

Part 1 stated the ceiling. Here is where it sits and where it ends.

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

**Measured: yes, and the ceiling is real.** Flat RAG holds 71–88% for as long as an answer lives
inside one passage — exactly the range Part 1 quoted. Then it falls off a cliff: 52% when two
documents are needed, 42% when the answer is in a picture. On the visual questions it is worse than
a coin flip. HCAG, running the identical model, never fell below 83% on any level.

---

## Claim 2 — "The missing ingredient is structure, not the model"

**Measured: yes, by construction.** Both agents used the same model. The +40% cannot have come from
model quality, because model quality was identical. Look at *how* the standard agent failed:

| | HCAG | Flat RAG |
|---|---:|---:|
| Questions passed (key points covered or better) | **37 of 37** | 24 of 37 |
| Wrong or refused | **0** | 13 |
| **Refused outright** — "I don't have enough information" | **0** | **10** |

Ten refusals, on questions whose answers were in the knowledge base. That is not a model that cannot
reason. It is a model that was **never handed the page** — because a single similarity search does
not reliably surface the second document, and cannot surface an image it only holds a written
description of. No larger model reads a document it was not given. Part 1 said the lever is
structure; the refusals are what "not structure" looks like in a log.

**We tried to make flat RAG win first.** Before comparing we audited our own RAG baseline and fixed
three real defects — one improved its retrieval by a measured 17%. The comparison ran against the
*improved* system. It still lost by 40%, because no retrieval fix delivers a document that was never
selected. Details in the appendix.

---

## Claim 3 — "Strong at reasoning and planning, because the agent sees complete documents"

**Measured: yes, and it is the clearest result in the run.** Read the Gain column of the table
above top to bottom. It never falls as the reasoning gets harder, and it jumps more than fourfold at
the exact point where a question stops fitting inside one passage. The gains are smallest where a
fragment is enough and largest where only a whole document — or two — will do. That is Part 1's
"complete documents, not stitched-together excerpts" claim, with numbers on it.

**A second, independent test set confirms it from the other direction.** We later ran 16 further
questions through both agents. That set happens to contain **no two-document and no visual
questions** — only single-passage and single-document work. If the gain really tracks reasoning
difficulty, the gap on that set should be small. It is: **HCAG 93.8%, flat RAG 87.5%, a gain of 7%**
rather than 40%. Same architectures, same model, a workload drawn from the easy half of the
spectrum, and the advantage nearly disappears. That is also the most precise answer we have to *when
is this not worth doing* — which Part 1 could only gesture at.

---

## Claim 4 — "High accuracy, because similar-but-wrong retrievals cannot happen"

**Measured: yes.** HCAG scored below 2 on zero questions and refused zero. Its nine imperfect
answers were all judged "correct but included more than was asked" — the cost of loading a whole
document, and a far cheaper failure than a wrong one. Flat RAG's failures were the opposite shape:
13 answers missing key points or wrong, 10 of them refusals. When the organised agent *was* wrong on
the second test set — twice — both errors were traced to a named document and a specific reasoning
step, fixed by changing the agent's instructions rather than the index, and the fix reproduced on
three separate runs. A further revision of the instructions then took the same set to 93.8% with
still no failures. That traceability is the "behaviour you can reason about" that Part 1 promised.

---

## Claims 5 and 6 — "Fast" and "Cost-effective" — not measured here

Part 1 argued that classifying once and reusing the active set makes HCAG faster and cheaper per
task, via prompt caching. **This benchmark did not measure latency or cost.** It scored accuracy
only. Those claims stand on the design argument in Part 1, not on data in this piece, and we would
rather say so than let the accuracy numbers imply support they do not give. Latency and cost are the
next thing to measure.

---

## What this means for the roadmap

Part 1 named the sweet spot: knowledge-heavy reasoning inside a bounded branch — autonomous support
over large corpora, root-cause analysis, SOP-grounded operations. The data now says *why* those, and
sharpens the boundary.

**Where the gain is 40–120%.** Any workload where answers routinely span more than one document, or
sometimes live in a diagram, form or screenshot: maintenance manuals by model and revision, fraud
typologies and escalation matrices reasoned over together, runbooks that must be executed whole. On
this work, our data says the model is not where the accuracy is — and a bigger model would still not
be shown the second document.

**Where the gain is 7–14%.** Single-passage lookups and FAQ traffic. Flat RAG scored 87.5% on simple
questions; on the easy-only second set the whole gap was 7%. If that is your traffic, Part 1's "when
not to use it" applies, and now has a number attached.

**The line to carry upward:** *the accuracy gain from organising our knowledge was 40% with the
model unchanged, and it grows with the difficulty of the work we most want to automate. It is a
one-time investment, not a per-call cost.*

---

## What to do before your next model upgrade

**Sort the workload (an afternoon).** Take fifty real questions your target automation would handle
and sort them: single passage, two documents, in an image. The last two columns are the share of
your accuracy problem a model upgrade cannot reach.

**Run the same-model test (days).** One bounded domain whose knowledge is already organised —
maintenance libraries, policy repositories and runbook collections nearly always are. Same questions,
same model, same judge, knowledge organised one way versus the other. Everything needed to reproduce
our run is in the appendix.

**Demand three things from whoever builds it.** The comparison holds the model constant and the
baseline is tuned in good faith — ask what they fixed in it first. Failures are traceable to a
document and a reasoning step, not "the search ranked the wrong passage". And negative results are
published: one of our three attempted fixes on the second set made things *worse*, and it is in the
data with the rest.

---

## How far to trust this

- **37 questions**, six to eight per level. The overall gap and the two large gaps are far wider than
  the noise at that size. The 14–18% leads on the three easier levels are **not** statistically
  separable from noise — read them as directional.
- **The second set is 16 questions.** On its three single-document questions flat RAG scored 100%
  against HCAG's 88.9% — one answer scoring 2 instead of 3, and it held on the latest run of both
  agents. At n = 3 that is a row, not a trend, but it is the one category RAG won and it is not being
  hidden. It is also consistent with the thesis:
  the easier the question, the less organisation buys.
- **We did not benchmark a larger model.** The claim is not that organisation beats a bigger model
  head-to-head; it is that with the model held constant, organisation alone produced these gains.
- **The scorer is an AI judge**, reliable in aggregate, not per row. Every row's justification is
  published.
- **One knowledge base, one domain** — government policy. Expect the visual gap to be larger in
  engineering and network domains, and the simple-lookup gap smaller in FAQ-heavy ones.
- **Reference answers came from the same knowledge base**; this measures faithful retrieval of known
  content, not open-domain correctness.

---

*Part 1 explained why a taxonomy should work. This part shows that, on accuracy, it did — by 40%
overall and by 82–120% on exactly the work that automation consists of — and that the two claims it
does not yet support, speed and cost, are the next ones to measure.*

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
| **RAG** | [87.5%](./sample-benchmark-report/rag-kb-eval-scored-beta.csv) | [65.8%](./sample-benchmark-report/rag-kb-eval-scored.csv) |
| **HCAG** | [93.8%](./sample-benchmark-report/hcag-kb-eval-scored-beta.csv) | [91.9%](./sample-benchmark-report/hcag-kb-eval-scored.csv) |
