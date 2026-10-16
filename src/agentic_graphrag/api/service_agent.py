"""Compatibility alias: moved to :mod:`agentic_graphrag.app.query_agent`.

Refactor §0-A: the application layer (app/) is shared by every driver
(API, CLI, eval, workers); api/ is only the HTTP driver.
"""

import sys

from agentic_graphrag.app.query_agent import __name__ as _target

sys.modules[__name__] = sys.modules[_target]
