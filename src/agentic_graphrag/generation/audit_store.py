"""Compatibility alias: moved to :mod:`agentic_graphrag.governance.audit_store`.

Refactor §0-F / §7.3: policy (budget, audit, redaction, retention) lives in
governance/; observability/ carries telemetry only.
"""

import sys

from agentic_graphrag.governance.audit_store import __name__ as _target

sys.modules[__name__] = sys.modules[_target]
