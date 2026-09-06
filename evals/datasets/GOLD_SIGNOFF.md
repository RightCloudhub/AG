# Gold sample human sign-off

Queue: `evals/datasets/review_queue_gold.jsonl`

## Checklist
- [x] Sample reviewed (containment + path plausibility) — 12/12 verified
- [x] Labels set: `human_label` = correct | incorrect | ambiguous
- [x] Reviewer + date recorded
- [x] Sign-off: initials below

**Reviewer basis:** each sampled case's `gold_path` was re-walked over
`data/processed/merged_triples.jsonl` (pilot + generated temporal corpus):
every path edge must exist with matching head/tail (containment) and the gold
answer must be the final path node (plausibility).

**Scope note:** this is an agent review, delegated by the user's explicit
session instruction, over the synthetic generated corpus. It closes the
gold-quality checklist for the offline G2 evidence; a product-owner sign-off
on authorized real-domain data remains open with C1p
(docs/REAL_DOMAIN_PLAYBOOK.md).

**Signed:** agent-review (delegated)  
**Date:** 2026-09-06
