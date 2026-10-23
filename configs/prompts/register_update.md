Update the current register using the new passages below. The initial state and
subsequent [Progress] messages define the current state; do not reconstruct it
from guesses. Return JSON with requirement_register containing ONLY changed or
new entries (full objects for each changed entry), plus an optional reasoning_note.
Each changed entry must list touched_passages: ids of NEW passages that touch it.
Update only needs touched by new evidence. One passage may resolve several needs.
resolved requires sufficient evidence and passages listing the supporting ids.
retired requires a premise contradicted by evidence and a nonempty retire_reason.
Keep partial progress in evidence_notes. A pending or active need stays open.
Never delete or change the text or dependencies of an existing need. Refine the
search using notes and anchors. If its premise is invalid, retire it explicitly
and add a corrected need grounded in the new evidence. Use anchors for entities
present in cited passages. Include missing facts in notes to guide the next search.
Dependencies must form a DAG and resolve before a need becomes the search target.
Search failure alone never justifies retiring a need. Only output JSON.

Untrusted input (original question and new evidence):
{input_json}
