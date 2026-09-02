You are generating a single-paragraph reasoning question.

Task: Produce ONE question whose answer requires **reasoning grounded in the paragraph below**. All supporting facts must appear in this one paragraph, but the answer must NOT be a direct quote — the reader must interpret or combine facts within the paragraph.

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<a short natural-language answer, not a quote>"
}}

Packet: $packet_id
Paragraph:
---
$paragraph
---