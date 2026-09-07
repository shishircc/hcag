# Knowledge Taxonomy, Part 2 — The Agent Was Never Shown the Page

> *This is the second of two pieces. The first,
> [Your AI Agent Needs a Knowledge Taxonomy — a Small Investment That Pays Rich Dividends](https://www.linkedin.com/pulse/your-ai-agent-needs-knowledge-taxonomy-small-pays-rich-choudhary-qix9c),
> argued that most knowledge-grounded agents stall around 70–80% accuracy because of how they find
> their knowledge, not because of which model they run, and that organising the knowledge into a
> hierarchy is the fix. That was an argument. This is the measurement, and what it changes in an
> enterprise AI plan.*

Somewhere in your organisation this quarter, an AI agent will give a wrong answer, and someone will
propose a larger model. It is the obvious response. The agent got it wrong; a smarter agent will get
it right. Sometimes that is even true.

We ran the test that meeting never runs. We built two agents over the same knowledge base, gave them
the same model, asked them the same questions and had the same judge score every answer. The only
difference between them was how each one found the knowledge it answered from. One searched a flat
index of passages, the way most agents are built today. The other navigated an organised hierarchy
and read whole documents. **The organised agent was 40% more accurate, and the model had not
changed.** On the questions that automation actually consists of, the ones needing more than one
document or an answer that lives in a diagram, the gain was not 40% but 82% and 120%.

That is the headline, but it is not the most useful thing we learned. Three findings matter more for
anyone planning an AI programme, and the rest of this piece is about them. The standard approach
does not fail loudly; it fails by politely refusing, in a way that looks like the knowledge was
missing when it was not. The gain from organising the knowledge grows with the difficulty of the
work, which means it is largest exactly where automation is most valuable and smallest on the FAQ
traffic most pilots start with. And the whole effect comes from something most enterprises already
own: their knowledge is already organised by product, by domain and by procedure, and nobody has
handed that structure to the agent.

If you have not read Part 1, here is the argument in brief. Flat RAG, which most teams run, cuts
every document into fragments of a few hundred words, indexes all of them, and answers each question
by pulling back the fragments that look most similar to it. That design has three problems, and no
amount of tuning removes them. The first is noise. When thousands or millions of fragments compete in
every search, fragments that are similar but not relevant come back alongside the right ones, and
the model reasons over them as though they were evidence; a great deal of what gets called
hallucination starts there. The second is compression. A question that spans several concepts has to
be squeezed into a single query, and whatever that query fails to express is never retrieved, so the
answer is assembled from part of the picture. The third is deduction. When answering means reasoning
from a rule to its consequence, the fragment that matters is the one that states the rule, and there
is no way to hand a chain of reasoning to a vector or keyword search and ask it to find that. A
knowledge taxonomy addresses all three at once. Organise the knowledge into domains, topics and
subtopics, and for any given problem only a few branches are ever in play, and within them only one
document or a handful, which the agent keeps open for the whole of the task. The rest of the corpus
cannot compete because it is not in the pool; the agent reads complete documents rather than
fragments; and it reasons over that isolated, coherent material instead of over whatever a search
happened to return. That isolation is what HCAG, the agent pattern from Part 1, exists to deliver.

---

## The test we ran

The knowledge base was a real one: 127 documents of Singapore government work-pass rules, the kind
of policy corpus that a support or compliance agent would sit on. We generated 37 questions from the
documents themselves, at five levels of difficulty, because "how accurate is it?" turns out to be
the wrong question. The right one is *where* accuracy stops, and to see that you need questions that
get progressively harder in a specific way. The easiest could be looked up in a single passage. The
next needed reasoning within one paragraph, then combining three points from one document. The two
hardest levels are where the test earns its keep: questions whose evidence sits in two different
documents, and questions whose answer is in an image — a form, a diagram, a screenshot — rather than
in the text at all.

Both agents used the same model to write every answer, and the same independent AI judge scored each
one from zero, meaning wrong or refused, to three, meaning complete. Everything else was held
constant. Here is what came back, as a share of the maximum score:

```
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

Read the flat-RAG column from the top. It holds between 71% and 88% for as long as an answer lives
inside one passage, which is precisely the 70–80% ceiling that Part 1 described and that
practitioners quote. Then it falls off a cliff: 52% when two documents are needed, 42% when the
answer is in a picture, which on the visual questions is worse than a coin flip. The organised agent,
running the identical model, never fell below 83% on any level.

---

## The failure you will not see in a log

The size of the gap is less important than its shape, and the shape of the failure matters more
than either. The organised agent passed all 37 questions; nine of its answers were marked down, and
every one of those nine was judged "correct but said more than was asked," which is the cost of
loading a whole document and a cheap failure to have. The flat-RAG agent passed 24 of 37. Of the 13
it failed, ten were not wrong answers at all. They were refusals. The agent said, fluently and
politely, that it did not have enough information to answer, and the information was in its
knowledge base the entire time.

Consider what that means once the agent is in production. A wrong answer is embarrassing, but it is
visible; someone will catch it and file a bug. A courteous "I don't have that information" for
something the knowledge base does contain looks like a content gap. Nobody investigates a content
gap. It quietly becomes "the bot can't do that," a class of questions routes back to humans forever,
and the accuracy figure on the dashboard stays exactly where it was. In our run the standard agent
did this ten times in thirty-seven, concentrated in the two hard categories, and the only way to see
it was to count refusals against what the knowledge base actually held.

---

## Why a bigger model would not have helped

Look at where those refusals came from and the case for a model upgrade dissolves. A question that
needs evidence from two documents requires both to surface in the same handful of passages from a
single similarity search, and when one document dominates the ranking the other simply never
arrives. The model is then asked to answer from half the evidence, and a well-behaved model
correctly declines to invent the other half. One question from the run asked what an employer's
repatriation obligations are and how they contrast with cancelling a different pass. The organised
agent loaded both documents and scored full marks. The flat-RAG agent, on the same model, received
one half and scored zero. It was not failing to reason. It was never shown the page.

The image questions make the same point more starkly. A flat index cannot hold a picture; it holds a
written description of the picture, produced by an AI in advance. Whatever the describer did not
think to write down is unfindable afterwards, at any search quality and with any model behind it.
Asked for the exact wording of a checkbox on a form, the standard agent scored zero because that
phrase had never made it into any description. The organised agent attaches the image itself when
it loads the document, and the same model reads the form.

It is worth saying that the flat-RAG baseline was not set up to lose. Before running the comparison
we audited our own RAG system and found three genuine defects, one of which improved its retrieval
by a measured 17%. The comparison ran against the improved version, and it still lost by 40%,
because no retrieval fix can deliver a document that was never selected or an image that was never
attached. The gap is in the organisation of the knowledge, not in the model and not in the tuning.

---

## The gain grows with the difficulty, and vanishes on FAQs

The second finding is visible in the Gain column of the table, and it is the one a roadmap should be
built on. Read that column from the top. The gain never falls as the reasoning gets harder, and it
jumps more than fourfold at the exact point where a question stops fitting inside one passage. The
organised approach helps least where a fragment is enough and most where only a whole document, or
two, will do.

If that is really what is happening, then a set of questions with no two-document and no visual
questions ought to show a much smaller gap. We had such a set: 16 further questions we later ran
through both agents, with the same model and the same judge, drawn entirely from the easy half of
the spectrum.

| Second set — question type | HCAG | Flat RAG | HCAG uplift |
|---|---:|---:|---:|
| Simple lookup (9) | 96.3% | 92.6% | +4% |
| One paragraph (5) | 93.3% | 86.7% | +8% |
| Should decline and hand over (2) | 83.3% | 66.7% | +25% |
| **Overall (16)** | **93.8%** | 87.5% | **+7%** |

The advantage shrinks from 40% to 7%. The organised agent still passed all sixteen with no
failures, and flat RAG still failed two, both by refusing, but on this workload the two approaches
are close. That is the most precise answer we can give to a question Part 1 could only gesture at:
if your traffic is FAQ lookups, keep what you have, because the organising effort will not repay
itself, and now there is a number that says so. It is also the reason so many pilots reach the wrong
conclusion. They start with simple questions, see a small gap, and never test the work the agent was
bought for.

---

## What this changes in an enterprise AI plan

Put those findings together and they change five things about how a knowledge-grounded agent should
be planned, and none of them is a model decision.

The first is the sequence in which you spend. The default response to an inaccurate agent is a
larger model, and a model upgrade is a per-call cost for the life of the agent. This benchmark moved
accuracy 40% without touching the model, by changing what the model was given. The implication is a
rule: before a model upgrade is approved for accuracy, run the same-model test, with the same
questions, the same model and the same judge, and the knowledge organised one way versus the other.
It takes days, it separates the two causes, and if the accuracy is in the knowledge you spend the
one-time cost instead of the recurring one.

The second is the status of the knowledge base itself. In a flat-RAG programme the documents are an
input and the index is the asset. In a taxonomy-backed programme the structure is the asset. It
needs an owner, and the natural owner is the domain that already maintains those manuals or
policies. It needs a change process, because a mis-filed document is now a mis-routed answer. And it
needs versioning, because "this asset, this revision" is a branch in the tree. Most operational
knowledge already has this structure informally; the work is making it explicit and giving it a
name on an org chart, which is a knowledge-management decision rather than an engineering one.

The third is what you specify when you build or buy. The requirement stops being "index our
documents" and becomes three capabilities. The agent must select what to read by reasoning over a
catalogue of what each part of the knowledge base covers, not by similarity alone. It must load
whole documents together with their images rather than passages. And it must be able to say
afterwards which documents an answer used. Those three can be put to any platform or vendor as
direct questions, and a system that can only return the most similar passages will reproduce the
cliff in the chart above whatever model sits behind it.

The fourth is evaluation. The most reusable artefact in this benchmark is not the result but the
question set: built from the knowledge base itself, tiered by difficulty, scored by a consistent
judge, with a one-sentence justification published for every row. That is a standing capability,
not a one-off exercise. It is what lets a team distinguish a retrieval failure from a model failure,
a regression from noise, and a vendor claim from a demonstration. It comes with three rules. Hold
the model constant when comparing architectures. Tune the baseline in good faith and say what you
fixed. And publish the results that went the wrong way: one of our three attempted fixes on the
second set made things worse, and we say so.

The fifth is the risk register, which gains two entries. The first is silent refusal, described
above, whose only detection is counting refusals against what the knowledge base contains. The
second is untraceable action. An agent that answers a question from the wrong passage produces an
unhelpful paragraph, but an agent that acts on the wrong passage takes the wrong action, and "the
eight most similar passages" is not an audit trail. For any agent that acts, in operations, in
maintenance, in financial-crime workflows, the ability to name the document behind a decision is a
control rather than a feature, and the organised approach provides it by construction.

If one line has to travel upward, it is this: organising our knowledge moved agent accuracy 40%
with the model unchanged, the gain grows with the difficulty of the work we most want to automate,
and it is a one-time investment in an asset we mostly already have, with an audit trail the
alternative cannot give.

---

## The next ninety days

None of this requires a programme to start with. In the first two weeks, take fifty real questions
your target automation would handle and sort them into three piles: answerable from a single
passage, needing two documents, and answered by an image. The last two piles are the share of your
accuracy problem that a model upgrade cannot reach. In the same fortnight, count the questions your
current agent refuses and check how many the knowledge base could actually have answered; that
number is the size of the silent gap, and most teams have never measured it.

From week three, run the same-model pilot on one bounded domain whose knowledge is already
organised, which describes most maintenance libraries, policy repositories and runbook collections.
Make the structure explicit, give it an owner, and run the same questions through both approaches
with the same model and the same judge. Everything needed to reproduce our run is in the appendix.

Then apply a gate. Proceed if the organised approach clears three tests on your own questions: a
material gain on the two-document and visual rows, no refusals on questions the knowledge base
covers, and a named document behind every answer. If it clears none of them, you have ruled out the
cheaper fix for the price of an afternoon and a short pilot, and the model conversation can proceed
on evidence rather than instinct. If it clears them, extend to the next domain and instrument the
two things this benchmark did not, which brings us to what we have not shown.

---

## What we have not shown

Part 1 made two further claims: that the organised approach is faster per task, and cheaper, because
it retrieves once and reuses what it loaded. This benchmark measured neither. It scored accuracy
only, and those two claims rest on the design argument rather than on data in this piece. We would
rather say so than let the accuracy numbers imply a support they do not give. For planning purposes,
treat speed and cost as hypotheses for the pilot to test, not as savings to book.

We also did not benchmark a larger model. The claim here is not that organisation beats a bigger
model in a head-to-head. It is that with the model held constant, organisation alone produced these
gains, which makes it the lever to test before paying for a model upgrade rather than after.

---

## For readers of Part 1

Part 1 made six claims. Here is how each one fared.

| Part 1 claimed | Measured? | Result |
|---|---|---|
| Flat RAG plateaus around 70–80% | Yes | 71–88% inside one passage; 52% and 42% beyond it |
| The missing ingredient is structure, not the model | Yes, by construction | Same model both sides; +40% |
| Strong reasoning because the agent sees whole documents | Yes | Gain rises with difficulty, more than fourfold at the one-passage boundary, and collapses to 7% on an easy-only set |
| High accuracy because similar-but-wrong retrievals cannot happen | Yes | 0 wrong, 0 refused, 37 of 37 passed |
| Faster per task | **No** | Not measured; a pilot hypothesis |
| Cheaper per task | **No** | Not measured; a pilot hypothesis |

One property showed up that Part 1 promised without quite naming. When the organised agent was
wrong on the second set, which happened twice, both errors were traced to a specific document and a
specific step in the agent's reasoning, and both were fixed by changing the agent's instructions
rather than rebuilding anything. A later revision of those instructions took the set to 93.8% with
no failures at all. That is what "behaviour you can reason about" looks like in practice, and it is
the property that makes a knowledge-grounded agent maintainable rather than merely accurate on the
day it was measured.

---

## How far to trust this

The comparison rests on 37 questions, six to eight at each level. The overall gap and the two large
gaps at the hard end are far wider than the noise at that size; the 14–18% leads on the three easier
levels are not statistically separable from noise, and should be read as directional. The second set
is 16 questions, nine of them simple lookups, and the +4% on those nine is one answer's worth of
difference, so its overall +7% rests mainly on the five one-paragraph questions and the two the
agent should decline. The scorer is an AI judge, reliable in aggregate rather than per row, and every
row's justification is published in the files linked from the appendix so that any single score can
be audited. Everything was measured on one knowledge base in one domain, government policy, which is
structured prose with tables and forms; we would expect the visual gap to be larger in engineering
and network domains and the simple-lookup gap smaller in FAQ-heavy ones. And the reference answers
were written from the same knowledge base, so this measures faithful retrieval of known content, not
open-domain correctness.

---

Part 1 explained why a taxonomy should work. This part shows that on accuracy it did, by 40% overall
and by 82–120% on exactly the work that automation consists of, and it sets out what that changes:
sequence the knowledge before the model, treat the taxonomy as an owned asset, specify retrieval by
capability, evaluate as a standing practice, and put silent refusal on the risk register. Speed and
cost are the next things to measure.

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

The first defect was a dead retrieval leg: a missing embedding credential made `_retrieve` return
empty before full-text search ran, so the "hybrid" retriever had a single point of failure. The
vector leg is now skipped and BM25 carries the turn, and the degradation is logged and surfaced in
the stream. The second was chunks that did not know their own document: only the first chunk carried
the document title, and a bare `#` in the crawled markup was wiping the title from the heading stack.
Every chunk's indexed text now opens with its heading path, and over ten document-naming queries the
on-topic hits in the full-text top-8 rose from 35 to 41, a 17% improvement. The third was a
vocabulary gap: the corpus says "ONE Pass" while users type "onepass", which appears nowhere, so BM25
returned nothing. We added query-time alias expansion and a vocabulary note for the generator. The
comparison run used the improved index; the RAG agent code at run time predated the alias fix.

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
both agents' answers, the score and the judge's one-sentence justification for each row.

| | Beta set (16 questions) | Benchmark (37 questions) |
|---|---:|---:|
| **RAG** | [87.5%](./beta-set/beta-rag-kb-eval-scored.csv) | [65.8%](./benchmark-set/benchmark-rag-kb-eval-scored.csv) |
| **HCAG** | [93.8%](./beta-set/beta-hcag-kb-eval-scored.csv) | [91.9%](./benchmark-set/benchmark-hcag-kb-eval-scored.csv) |
