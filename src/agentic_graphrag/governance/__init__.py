"""Governance: policy, not telemetry (refactor §0-F / §7.3).

* :mod:`.budget` / :mod:`.budget_policy` — the single resource meter (LLM calls,
  tokens, cost) and tenant/user/query budget levels.
* :mod:`.audit_store` / :mod:`.audit_events` — reasoning-chain audit and
  security audit events.
* :mod:`.redaction` — PII redaction.
* :mod:`.retention` — retention windows.

Wired by :mod:`agentic_graphrag.bootstrap`; business code should depend on the
ports in :mod:`agentic_graphrag.core.ports` rather than importing these directly.
"""
