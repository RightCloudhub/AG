"""Compatibility alias: moved to :mod:`agentic_graphrag.app.write_path`.

Refactor §0-A: the application layer (app/) is shared by every driver
(API, CLI, eval, workers); api/ is only the HTTP driver.
"""

import sys

from agentic_graphrag.app.write_path import __name__ as _target

sys.modules[__name__] = sys.modules[_target]
