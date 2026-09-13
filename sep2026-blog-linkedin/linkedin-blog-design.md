# linkedin-blog.md — design brief

Planning document for the LinkedIn post. Decide the open questions at the bottom, then the post gets
written to `linkedin-blog.md` in this folder against this brief.

Sources of truth: benchmark numbers from [`benchmark.md`](../sample-benchmark-report/benchmark.md);
HCAG basics from the earlier article, [Your AI Agent Needs a Knowledge Taxonomy](https://www.linkedin.com/pulse/your-ai-agent-needs-knowledge-taxonomy-small-pays-rich-choudhary-qix9c/);
the opening anchor from ARC Prize, [GPT-6 Astra on ARC-AGI-3](https://arcprize.org/blog/astra), 3 Sep 2026.

---

## 1. The idea the post exists to plant

**The harness decides whether your AI use case wins. The model is table stakes.**

Teams pour their budget into model selection and prompt tuning, then plateau. The leverage they are
walking past sits in the harness: the agent's memory, its knowledge base, its planning loop, its
skills and tools, and the ontology that ties them together. Same model, different harness, different
outcome.

This post argues that in general, then proves it on exactly one component — the knowledge base
harness — by benchmarking conventional RAG against a taxonomy-driven one.

Everything in the post either advances that sentence or is cut.

## 2. What this post is for

**Goal.** Establish "harness" as the category AI engineering teams are under-investing in, and make
the benchmark the evidence that the category is real. Secondary: pull serious builders to the repo
and to the earlier taxonomy article.

**Success looks like** engineering leaders replying with which part of their own harness is weakest.
A methodology challenge in the comments beats 500 likes.

**Failure looks like** reading as either vendor marketing for HCAG or as a generic "agents need
memory" think piece. The benchmark is what separates this from both: a measured 26-point swing with
the model held constant.

## 3. Audience, in priority order

1. **AI engineering leads and architects** who own an agent that is stuck in the 70s and are being
   told the answer is a better model or a fine-tune. They should see their plateau named in the
   first three lines.
2. **AI executives** deciding where the next quarter of effort goes. They read the thesis, look at
   two charts, and take away "budget the harness, not just the model".
3. **Hands-on engineers** who will interrogate the benchmark. They decide whether the post is
   defended or shredded in the comments.

## 4. Format

- **A LinkedIn article, not a feed post.** A feed post caps at 3,000 characters including links and
  hashtags. The argument has two halves, two charts, and a scene, and compressing it to fit that cap
  is what turned the first draft into disconnected fragments. Articles have no practical cap, and the
  earlier taxonomy piece was published the same way, so the pair sit together.
- **Long form, roughly 5,000 words.** There is no cap on a LinkedIn article and no reason to ration
  this material, which carries a third-party result, a full methodology, a measured comparison, a
  failure anatomy and a cost argument. Earlier drafts failed by rationing.
- **No per-section word budgets.** They turned sections into self-contained blocks that started cold
  and stopped abruptly, which is what made the drafts read as disjointed.
- **A short feed post announces the article.** That one obeys the 3,000-character cap, carries the
  Astra hook and the headline numbers, and ends by pointing at the article. Draft it after the
  article, from the article's own opening.
- **Subheads, five to seven.** An article is scanned before it is read. Each subhead should carry
  meaning on its own, so the scan alone delivers the argument.
- **Two images**, both benchmark charts, in the order given in §7, each with a caption.
- **Links in a closing sources block.** No URL before it. See §5a.

## 5. Storyline

The arc is *big claim → define the category → narrow to one component → evidence → mechanism →
the cheap move → invitation*.

| # | Section | What it has to establish |
|---|---|---|
| 1 | **The bigger point** | It is not the model that wins the use case. The anchor, verified: OpenAI's GPT-6 Astra scored **62.7%** on ARC-AGI-3 Semi-Private under the standard harness and **99.9%** under a memory harness that preserves reasoning state between requests and compacts long conversations so the model reuses prior work. Same model, and the better harness cost less. Credit ARC Prize by name in the text; the URL waits for the closing block. |
| 2 | **Define the harness** | Memory, knowledge base, planning loop, skills and tools, ontology. One sentence each at most — this is a definition, not a taxonomy lecture. |
| 3 | **The pivot: what can you do today?** | Astra moved the *memory* component, and almost nobody can ship a harness like that this quarter. The same lever exists on the *knowledge* component, and that one is available today: organise the corpus into a taxonomy and let the agent reason over it instead of ranking chunks. That is HCAG — named in words here, with the earlier article's URL held for the closing block. Then: what is that actually worth? |
| 4 | **The test, in one breath** | Same corpus, same model both sides, 58 questions AI-drafted and validated by a subject-matter expert, independent LLM judge, every artifact published. |
| 5 | **The result** *(chart 1)* | 98% vs 72% of answers acceptable. 78% vs 47% fully correct. Zero misleading answers vs two. |
| 6 | **The pattern** *(chart 2)* | The gap is not flat. It widens with how far the answer is spread: +11 simple, +14 medium, +29 within one document, +44 across two. HCAG holds its quality as questions get harder; RAG slides. |
| 7 | **The scene, then the mechanism** | Open with one real failure in full: the HR officer who asked about a passport update and a Dependant's Pass in one breath, got a complete-sounding answer to the son question, and no answer at all about the passport. Then generalise: that shape is 13 of RAG's 16 failures, because similarity retrieval serves the loudest part of the question and drops the rest. |
| 8 | **The answer to beat 3** | So, concretely, today: a knowledge taxonomy is days of work, not a research programme, and no model change, no fine-tune, no new infrastructure. It is also the component that compounds — every agent that reasons, plans or acts autonomously over that corpus inherits it. |
| 9 | **Honest close + CTA** | One corpus, 58 questions, my own system, and HCAG still failed one. Artifacts are published; take it apart. Which part of your harness is weakest? |
| 10 | **Links** | Three labelled lines: the Astra result, the taxonomy article, the benchmark repo. Nothing else below them. |

**Why this order.** Beat 1 earns attention with someone else's result, which is more persuasive
than opening with my own. Beats 2 and 3 turn that attention into a category and then narrow it
honestly, which is what stops the post reading as a pitch. Beats 5 and 6 are the proof, and beat 6
is what readers will remember. Beat 8 is the ask that costs the reader least and returns most.

**The spine of the argument** is the bridge from beat 1 to beat 3, and it is a question, not a
comparison: *your situation is the same scenario, so what can you do today to improve your knowledge
harness?*

Astra is evidence that swapping one harness component moved a fixed model by 37 points. The reader's
honest reaction is "great, and I cannot build a Provider Adapter this quarter." Beat 3 exists to
answer that: the same kind of lever sits on the knowledge component, it is organisational rather than
research work, and it is available now. The benchmark then says what pulling it was worth — a second
component, the same lesson, which is what makes this a claim about harnesses rather than a pitch for
HCAG.

**Open the loop in beat 3, close it in beat 8.** Beat 3 asks what you can do today and names the
move. Beats 4 to 7 prove it pays. Beat 8 says what doing it costs. Keeping those separate is what
carries a reader through the evidence to the end; collapsing them into one paragraph turns the post
into a recommendation with the proof stapled underneath.

## 5a. Link discipline

**No link appears before the final block.** This is the difference between a post that is read and
a post that donates its reader to someone else.

- **The Astra story is more exciting than mine.** A reader who clicks it in line two lands on a
  frontier-model result with charts of its own and does not come back. The hook has to spend that
  story's *credibility* without spending the reader.
- **Attribution without a URL still earns trust.** "ARC Prize published this on 3 September" is
  specific enough to be checkable and to be honest about whose result it is. Someone who wants the
  source will find it at the bottom, having read the argument first.
- **The same rule protects the earlier article.** Beat 3 needs the reader to know HCAG is already
  written up; it does not need them to leave and read it now.
- **LinkedIn rewards this anyway.** Early outbound links suppress reach, and a post whose first
  action is "go elsewhere" gets less of both dwell time and comments.
- **The closing block is three labelled lines, in reading order**: the Astra result, the taxonomy
  article, the repo. Label each with what it is, so the click is chosen rather than gambled.

In article format the closing block is a labelled sources list. The companion feed post carries no
links in its body either; its link to the article is the one exception, because that is the one click
the post exists to produce.

## 6. Priorities

**P0 — must survive any cut**
- The harness thesis in beat 1, with the Astra anchor and its two numbers attached.
- The five harness components named once.
- The pivot question in beat 3, in the reader's terms: what can you do today to improve your
  knowledge harness?
- The concrete failure scene in beat 7. Without it the post is statistics about someone else's
  system.
- "Same corpus, same model on both sides" fused to the headline number.
- The progression 11 → 14 → 29 → 44, and the claim that the gap widens with dispersion.
- The limits sentence and the pointer to the published artifacts.
- Link discipline: no URL before the closing block (§5a).

**P1 — include if the budget allows**
- SME validation of the question set.
- The partial-coverage mechanism (13 of 16).
- "A taxonomy is days, not a research programme."
- The Astra cost detail: the stronger harness reached 99.9% for about $19k against $26k for the
  standard harness at 62.7%. Better *and* cheaper is harder to argue with than better.

**P2 — cut without hesitation**
- Chunk size, top-k, embedding model, the judge's rubric.
- The excluded image-question category, beyond at most one clause.
- Any history of earlier benchmark runs or model swaps.
- Any restatement of HCAG internals that the earlier article already carries. Link, do not re-explain.

## 7. Visual plan

Two charts, in this order. Both already exist in `sample-benchmark-report/` and must be exported to
PNG at ~1600px wide — LinkedIn does not render SVG.

1. **[`pass-rate-by-difficulty.svg`](../sample-benchmark-report/pass-rate-by-difficulty.svg)** —
   acceptable answers by difficulty, 100/100/100/94 against 89/86/71/50. This is the business
   reading: how often the agent is usable.
2. **[`mean-score-by-difficulty.svg`](../sample-benchmark-report/mean-score-by-difficulty.svg)** —
   answer quality as a share of a perfect answer, 93/100/90/89 against 84/86/74/52. This is the
   quality reading: even where RAG passes, it passes with less.

Both show the same shape, one flat line and one sliding line, which is the point: the pattern holds
on two independent measures. Put the business chart first. Check legibility at phone width before
posting, and write real alt text for both.

## 8. Credibility strategy

The post makes a general claim and then a self-interested measurement. Both halves need defending.

1. **The general claim leans on someone else's published result.** Verified against ARC Prize's own
   post: GPT-6 Astra, ARC-AGI-3 Semi-Private, 62.7% on the standard harness at max reasoning effort
   against 99.9% on the Provider Adapter harness at high effort, published 3 Sep 2026. Name the
   benchmark and the split precisely, say what the harness changed, and carry the link. A vague
   paraphrase ("a model did better with better memory") turns a checkable anchor into an anecdote.
2. **Matched generator.** The same model runs both sides of the benchmark, so the comparison
   isolates the harness rather than model strength. This is also what makes the benchmark evidence
   *for the thesis* rather than just for HCAG.
3. **SME-validated questions**, AI-drafted then reviewed by a subject-matter expert, shipped with
   the report. Say both halves.
4. **Independent judge with an audit trail**: a different model scores every answer and records a
   one-sentence justification, published.
5. **Named limits**, stated by me before a commenter states them: one corpus, 58 questions,
   self-benchmarked, one HCAG failure.

## 9. Voice

Two drafts failed here, and both failures came from rules in this brief. "One idea per line" produced
staccato. "Vary sentence length" was read as licence for verbless fragments. Per-section word budgets
turned the piece into a sequence of blocks rather than an argument that develops. All three are
retracted. What replaces them:

- **Write in developed paragraphs, four to six sentences.** A paragraph should make a claim, support
  it, and hand off to the next one. Two-sentence paragraphs stacked one after another read as notes,
  not as thinking.
- **No verbless fragments.** "No bigger model. No fine-tune. No new infrastructure." is three
  non-sentences doing the work one clause should do. Executives do not read percussion as emphasis;
  they read it as someone padding a thin point.
- **A single-line paragraph must be a complete, composed sentence**, not a fragment, and it belongs
  at a section boundary where it pivots from what came before into what follows. Used that way it is
  a legitimate device and the piece can carry several. Used as percussion, as in "No fine-tune. No
  reranker.", it is the failure this brief keeps having to correct.
- **No lists anywhere in the body.** Every idea is carried by a complete sentence inside a paragraph.
  A list is a way of not writing the connective tissue, and the connective tissue is the argument.
  The two charts carry whatever genuinely wants to be tabular.
- **Carry relationships inside sentences.** Subordinate, do not juxtapose. "Because retrieval never
  returned the passage, a better model could not have recovered it" argues; two adjacent flat
  sentences leave the reader to do the joining and most will not bother.
- **Numbers belong inside a sentence that states their meaning**, and a series of them belongs in a
  single sentence with proper subordination: "eleven points on simple lookups, widening to
  forty-four when the answer spans two documents". Never a stack of parallel stubs, which is a list
  wearing a disguise.
- **Every section opens by connecting to the one before it.** No section may begin with a new topic
  announced cold.
- **One concrete scene, told as narrative**, with what was asked, what came back, and what the person
  would have done next.
- **The voice is analytical and composed**, willing to say which finding matters and why. Confidence
  comes from the structure of the argument, not from short sentences.

Still in force: numbers before adjectives, no em dashes, no engagement bait, neutral about flat RAG,
do not re-teach HCAG, first person and past tense for anything measured.

## 10. Objections to pre-empt

| Objection | Where it is answered |
|---|---|
| "The harness claim is just an anecdote." | Beat 1 cites a published third-party result with the benchmark and split named; beats 5 and 6 add a controlled measurement with the model held constant. |
| "You benchmarked your own system." | Beat 4 (published artifacts, independent judge, SME validation) and beat 9 (stated limits). |
| "Your RAG baseline was weak." | Beat 4: same corpus, same generator, conventional hybrid retrieval. Offer the config in the comments. |
| "58 questions proves nothing." | Beat 9, explicitly, framed as a pattern rather than a precision claim. |
| "This only works on a tidy corpus." | Not in the post. It is a real limitation and gets a real answer in the comments. |
| "You are borrowing someone else's result." | Beat 1 names ARC Prize as the source in the text, and the closing block links it. Credit is explicit; the click is simply deferred. |
| "ARC-AGI is puzzles, not enterprise work." | Fair, and the post must not stretch it. Astra is cited for one narrow claim — changing the harness alone moved the same model — not as evidence about knowledge work. |

## 11. Call to action

One primary, one secondary.

- **Primary:** "Which part of your harness is the weakest — memory, knowledge, planning, tools, or
  ontology?" It converts the thesis into a self-diagnosis and invites the comment worth having.
- **Secondary:** "Questions, answers, scores and judge reasoning are all published — take it apart."

## 12. Hook candidates

First three lines are all that show before "…see more". Each candidate is written to survive that cut.

- **A (recommended).** "GPT-6 Astra scored 62.7% on ARC-AGI-3. The same model scored 99.9% on the
  same benchmark. Nothing changed but the harness."
  *Strongest: a third party's number, checkable, concrete, and it sets up the whole thesis inside the
  three-line cut. The jump does the work; resist adding adjectives to it.*
- **B.** "Most teams are optimising the model. The leverage is in the harness: memory, knowledge
  base, planning loop, tools, ontology. I measured one of those five and the gap was 26 points."
  *Leads with the thesis and lands own evidence fast; weaker cold open than A.*
- **C.** "Same documents. Same model. Same questions. One knowledge architecture answered 98% of
  them acceptably. The other managed 72%."
  *Sharpest evidence hook, but it starts at HCAG vs RAG and loses the bigger arc.*

## 13. Open decisions

1. **How much Astra detail to carry.** The two scores are P0. The cost figures (~$19k against ~$26k)
   are the strongest supporting fact but cost about 20 words and pull the opening toward ARC-AGI
   specifics. Recommend keeping the cost, dropping the reasoning-effort nuance.
2. **Hook A, B or C.** A is recommended.
3. **How to describe the SME.** Generic "subject-matter expert", or something specific about the
   domain and reviewer. Specific is more credible if it can be said.
4. **Name the domain?** "Singapore government work-pass rules" is concrete and aids credibility.
   Confirm there is no reason to keep the corpus unnamed.
5. **Is the repo ready for traffic?** The post's defence is "go and check", which fails if anything
   there contradicts the report.
6. **Title.** The article needs one, and it should carry the thesis rather than the benchmark.
   Candidates are in the article's own publishing notes.
