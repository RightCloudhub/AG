"""Compatibility alias: streaming lives in :mod:`agentic_graphrag.agent.pipeline`.

Refactor §1.1: streaming and non-streaming share one implementation.
"""

import sys

from agentic_graphrag.agent.pipeline import __name__ as _target

sys.modules[__name__] = sys.modules[_target]
