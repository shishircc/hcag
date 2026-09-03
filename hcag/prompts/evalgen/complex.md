You are generating a whole-packet reasoning question.

Task: Produce ONE question whose answer requires **significant deduction across at least three distinct concepts, each drawn from a different paragraph** shown below. The question must not be answerable from any single paragraph in isolation.

You will be given three or more paragraphs from the same packet. Your JSON must cite which paragraph each supporting concept came from, using the paragraph's index (0-based) as shown.

$answer_rules

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<the complete synthesized answer, with all its conditions>",
  "cited_paragraph_indices": [<int>, <int>, <int>, ...]
}}

Packet: $packet_id
Paragraphs:
$paragraphs