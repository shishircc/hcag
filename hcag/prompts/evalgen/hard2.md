You are generating a multimodal question.

Task: Produce ONE question whose answer **requires reading the attached image** together with the packet markdown. The key fact of the answer must be **visually present in the image** (a label on a diagram, a value in a chart, a state in a state-machine figure, a component in a screenshot) and NOT stated in the surrounding markdown alone. The question must not be answerable from the markdown by itself.

$answer_rules

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<the complete answer; its key fact comes from the image>",
  "image_reference": "<what in the image supports the answer, one sentence>"
}}

Packet: $packet_id
Packet markdown:
---
$content
---