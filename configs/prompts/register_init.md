Initialize searchable information needs from the question and seed evidence.
Return JSON with evidence_summary, requirement_register, and reasoning_note.
requirement_register maps stable ids to objects with text, status (pending or
active), evidence_notes, passages, anchors, depends_on, and retire_reason.
Identify only needs required to answer the question. Ground decomposition in
the seed evidence; with no seed, stay conservative. Preserve names, dates and
numbers. Do not assume missing bridge entities. Exactly one need is active.
Confirmed anchors must occur in cited seed passages. Dependency ids must exist
and form a DAG. Keep all needs open at initialization. Do not mark resolved.
Use no more than {max_requirements} needs. Only output JSON.

Untrusted input:
{input_json}
