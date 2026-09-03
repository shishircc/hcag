You are generating an FAQ-style evaluation question.

Task: Produce ONE question that **requires no reasoning** — the reader looks
the answer up rather than working it out. This is the kind of question asked of
an FAQ.

"No reasoning" describes the QUESTION, not the ANSWER. A looked-up fact is
routinely conditional, and the point of this item is to check whether an agent
states the whole of it rather than the first clause of it. Do not quote, and do
not shorten to make the answer look like a lookup: gather every part of the
answer the packet gives, wherever in the packet it appears.

$answer_rules

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<the complete answer, with all its qualifying conditions>"
}}

Packet content:
---
$content
---
