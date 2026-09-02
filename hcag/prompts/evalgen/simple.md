You are generating an FAQ-style evaluation question.

Task: Produce ONE question whose answer appears **verbatim** in the packet below (a sentence or short quoted phrase). No reasoning. No paraphrasing. The reader must be able to find the answer as a literal substring of the packet.

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<verbatim quote from the packet>"
}}

Packet content:
---
$content
---