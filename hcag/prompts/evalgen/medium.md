You are generating a single-paragraph reasoning question.

Task: Produce ONE question whose answer requires **reasoning grounded in the
paragraph below**. All supporting facts must appear in this one paragraph, and
the answer must NOT be a bare quote — the reader must interpret or combine
facts within the paragraph.

$answer_rules

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<the complete reasoned answer, with all its conditions>"
}}

Packet: $packet_id
Paragraph:
---
$paragraph
---
