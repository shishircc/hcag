You are generating a cross-packet reasoning question.

Task: Produce ONE question whose answer requires **two packets** to answer correctly, drawing on **at least three distinct paragraphs spread across those two packets**. Neither packet alone is sufficient.

Your JSON must cite which packet each supporting paragraph came from (by packet id).

$answer_rules

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<the complete synthesized answer, with all its conditions>",
  "cited_packet_ids": ["<packet_id_1>", "<packet_id_2>"]
}}

Packet A ($packet_a_id) — paragraphs:
$paragraphs_a

Packet B ($packet_b_id) — paragraphs:
$paragraphs_b