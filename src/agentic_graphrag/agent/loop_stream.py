"""Compatibility alias: streaming lives in :mod:`agentic_graphrag.agent.pipeline`.

Refactor §1.1: streaming and non-streaming share one implementation.
"""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_graphrag.agent.pipeline import (
        EVENT_FINAL_CHAIN as EVENT_FINAL_CHAIN,
    )
    from agentic_graphrag.agent.pipeline import (
        AgentStreamEmptyError as AgentStreamEmptyError,
    )
    from agentic_graphrag.agent.pipeline import (
        iter_query_progress as iter_query_progress,
    )

from agentic_graphrag.agent.pipeline import __name__ as _target

sys.modules[__name__] = sys.modules[_target]
