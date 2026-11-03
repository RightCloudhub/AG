# Paraphrase robustness — 2026-11-03

On the 40 selected dev questions, original accuracy stays at **40/40 (100%)**. Chinese variants rise from **8/40 to 38/40**, and typo variants from **3/40 to 39/40**. Strict six-form consistency remains low: **0/40 → 3/40 (7.5%)**. Reordered clauses and colloquial language remain substantial gaps.

Baseline code: `844d2b0f56432303836dd525fa00ef78e7a5469b` on `test/paraphrase-robustness`, before the matching changes in this worktree. The fixed variants were authored and evaluated before the matcher changes. No online LLM generated the dataset or ran in unit tests.

**Dataset and protocol.** `evals/datasets/paraphrase.jsonl` has 240 rows: 40 g2_dev originals plus five variants each. Original IDs are preserved as `<source-id>::<variant>`, with separate `metadata.original_id` and `metadata.variant`. All answer/path/evidence/category/hop fields are copied unchanged from the source. The mix is 8 one-hop paths, 16 two-hop questions, and 16 three-hop questions, covering all six answerable template families present in g2_dev. Chinese questions retain canonical proper names; the typo forms contain one or two character errors in names or relation words. Annotation metadata explicitly records agent authorship, not human sign-off.

Because g2_dev has no four-hop questions, the user approved a separate supplement: `evals/datasets/paraphrase_4hop.jsonl`, two groups / twelve forms. These extend the Atlas Route OS and Lattice Train Hub questions to producer → parent → parent → CEO. Every edge and the unique answer at each hop are verified against the pilot graph; their changed questions and golds are never presented as unchanged g2_dev examples.

Both main runs use the production QueryApp evaluation runner, the same pilot graph in memory, triage enabled, and answer/retrieval caches disabled. Live mode was **not run: no non-placeholder LLM_API_KEY is configured**. The new `--live` mode retains the same in-memory graph, enables the configured LLM, and fails explicitly when credentials are missing.

**Accuracy by variant.** Accuracy uses the repository’s existing `score_pair`. Each row has 40 questions; missing/error results count as wrong. There were zero query errors in either run. Deltas are percentage points.

| Variant | Before | After | Delta | Paired McNemar p |
|---|---:|---:|---:|---:|
| original | 40/40 (100%) | 40/40 (100%) | +0.0 | 1 |
| synonym | 35/40 (87.5%) | 37/40 (92.5%) | +5.0 | 0.5 |
| reordered | 23/40 (57.5%) | 23/40 (57.5%) | +0.0 | 1 |
| chinese | 8/40 (20%) | 38/40 (95%) | +75.0 | <0.000001 |
| typo | 3/40 (7.5%) | 39/40 (97.5%) | +90.0 | <0.000001 |
| casual | 15/40 (37.5%) | 16/40 (40%) | +2.5 | 1 |

The Chinese and typo gains are significant in these paired dev comparisons. The smaller synonym and casual changes are not significant. The pooled result is 124/240 → 193/240, with 69 gains and no losses; the six forms within a group are correlated, so the pooled p-value is only diagnostic. These selected synthetic dev questions do not establish robustness on natural queries, heldout data, or live LLMs.

**Stricter answer checks.** The legacy scorer can accept a partial answer or an answer containing additional entities. For the 32 entity-valued groups, the evaluator also requires exactly the gold entity set, ignoring only entities already in the original question. It uses the original question for exclusions in every variant, so a typo or translation cannot change that rule.

| Variant | Exact set before | Exact set after | Delta |
|---|---:|---:|---:|
| original | 32/32 (100.00%) | 32/32 (100.00%) | +0.00 |
| synonym | 24/32 (75.00%) | 25/32 (78.12%) | +3.12 |
| reordered | 15/32 (46.88%) | 15/32 (46.88%) | +0.00 |
| chinese | 0/32 (0.00%) | 30/32 (93.75%) | +93.75 |
| typo | 1/32 (3.12%) | 30/32 (93.75%) | +90.62 |
| casual | 5/32 (15.62%) | 6/32 (18.75%) | +3.12 |

Consistency counts a group only when all six forms pass the scorer and yield the same correct answer. Entity answers require the exact gold set; path/text answers require identical normalized text and every gold token, including the relation. Thus six identical wrong answers, differing partial sets, or stuffed entity answers do not earn consistency. This is conservative: different valid wording of a non-entity answer may fail it. All-six-correct under the legacy scorer alone is 1/40 → 10/40; the stricter consistency score is 0/40 → 3/40. Frozen baseline predictions were rescored with the same final metric definition as the candidate.

**Separate four-hop supplement.** These two groups are excluded from the main accuracy and consistency denominators. Small denominators make these smoke checks, not a statistical claim.

| Variant | Before | After | Delta |
|---|---:|---:|---:|
| original | 2/2 | 2/2 | +0 pp |
| synonym | 2/2 | 2/2 | +0 pp |
| reordered | 0/2 | 0/2 | +0 pp |
| chinese | 0/2 | 2/2 | +100 pp |
| typo | 0/2 | 2/2 | +100 pp |
| casual | 0/2 | 0/2 | +0 pp |

Four-hop consistency stays 0/2: reordered and casual forms still fail.

**Changes and remaining debt.** The matcher now normalizes Unicode width, case for matching, typographic punctuation and whitespace; protects known names from synonym replacement; loads schema-level synonyms and Chinese relation cues from `configs/question_matching.yaml`; and handles fully consumed Chinese possessive chains using the existing direction-checked relation frames. An indefinite article before an existing frame is also accepted.

Fuzzy linking uses full-name SequenceMatcher similarity ≥0.90, a ≥0.05 margin over the runner-up, at least 8 characters, equal word count and at most 2 characters of length difference. Exact names take priority, and fuzzy matching does not cross punctuation boundaries. Short/ambiguous names remain unresolved. These are conservative guards, not an edit-distance guarantee; translated proper names and general grammar repair remain unsupported. Bounded caches and an upper-bound similarity precheck avoid repeated lexicon comparisons without changing these thresholds.

The ten-group planner test compares sub-question count, canonical relation questions, dependencies, placeholders and anchor entity. Before fixes: 5 passing pairs and 15 documented expected failures. After: 16 passing pairs and four strict `xfail`s:

- `g2-2hop-0005::typo`: `parnet` is not a recognized parent relation cue.
- `g2-2hop-0045::synonym`: “firms employed … in the past” needs a grammar rule.
- `g2-2hop-0090::synonym`: the verb “rivals” is not a leading relation frame.
- `g2-2hop-0090::typo`: `prodcuer` is not a recognized producer relation cue.

An XPASS fails the test so resolved gaps cannot remain hidden. The high legacy typo accuracy does not prove all typo plans are correct: `g2-2hop-0090::typo` still has the wrong plan despite passing the permissive answer scorer. Reordered clauses and conversational coreference need work beyond this limited lexical fix. Neither `generation/offline_answer.py` nor `generation/offline_heuristics/` was edited. No CI floor was lowered.

**Timing and validation.** Main-suite P95 latency is 66 → 70 ms; for its originals it is 71 → 70 ms. Chinese and typo questions now execute more successful reasoning steps, with P95 increasing from 43 → 70 ms and 42 → 73 ms respectively. A standalone G2 comparison retains 200/200 accuracy (McNemar p=1), with P95 68 → 60 ms. LLM calls remain zero. These are single-run local timings, not controlled performance benchmarks.

- `ruff check src tests scripts`: passed.
- `ruff format --check src tests scripts`: passed.
- `mypy`: passed, 299 source files.
- `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q`: **675 passed, 4 xfailed; 89.70% coverage**. One pre-existing Starlette/httpx deprecation warning.
- `python scripts/check_code_metrics.py`: passed.
- Unchanged floor checks: G2 **200/200** (floor 196); POC **31/31** with and without triage (floor 30); compositional probe **54/54** (floor 52); negative probe **20/20** (floor 19). Gold audit: **0 flagged / 200**.

The first full pytest process stalled in an API lifespan test under the previous restricted session. It was terminated; a fresh run with a diagnostic timeout completed. No tests were skipped to bypass the stall.

**Reproduction.** Use the project environment (this run used Python 3.12.13 from the existing `/home/arch1/AG/.venv`), with `PYTHONPATH=src`:

```bash
python -m agentic_graphrag eval-paraphrase --no-llm --run-name paraphrase_candidate
python -m agentic_graphrag eval-paraphrase --no-llm \
  --cases evals/datasets/paraphrase_4hop.jsonl --run-name paraphrase_4hop_candidate
# Only when a real key is configured:
python -m agentic_graphrag eval-paraphrase --live --run-name paraphrase_live
python -m agentic_graphrag eval-paraphrase --live \
  --cases evals/datasets/paraphrase_4hop.jsonl --run-name paraphrase_4hop_live
python scripts/eval_compare.py reports/paraphrase_baseline.jsonl \
  reports/paraphrase_candidate.jsonl --cases evals/datasets/paraphrase.jsonl
python -m pytest tests/unit/test_paraphrase_planner.py -q -rx
```

To rerun the baseline, use the baseline commit with only the new evaluator/CLI and fixed datasets overlaid, leaving all matching modules at the baseline version. The checked-in pre-fix predictions permit comparisons without reconstructing that checkout.

Artifacts: [before](paraphrase_baseline.jsonl), [after](paraphrase_candidate.jsonl), [before summary](paraphrase_baseline_summary.json), [after summary](paraphrase_candidate_summary.json), [per-variant paired comparison](paraphrase_by_variant_paired.json), [pooled comparison](paraphrase_paired.json), [G2 comparison](paraphrase_g2_paired.json), [4-hop before](paraphrase_4hop_baseline_summary.json), [4-hop after](paraphrase_4hop_candidate_summary.json).

Dataset SHA-256: `099157442e882c2974f96f6a4a89b3ed29dcc6468fde59c8c3c96e4e2ac49fff`.
Supplement SHA-256: `b86dab56955975547effc524301ef258a73348361b7188e0e1a1f90861c86878`.
Pilot triples SHA-256: `d1f5717fa99e51daf48e6f4633089573bda24eb413ede3683793fdc248a34efe`.
