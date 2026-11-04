"""Public benchmark head-to-head runner (plan/workstreams/triage-bench-ops.md §4).

Everything here runs offline against tiny fixtures; every live public-benchmark
stage (download, indexing, querying, external baselines) is **Not Executed**
until an operator runs it with pinned data, credentials and environments.
Optional external packages (LightRAG, HippoRAG 2) are never imported here; they
run in isolated subprocess environments (``evals/public/envs/<system>/``).
"""

NOT_EXECUTED = "Not Executed"
UNVERIFIED = "UNVERIFIED"

__all__ = ["NOT_EXECUTED", "UNVERIFIED"]
