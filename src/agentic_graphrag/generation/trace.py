"""Compatibility alias: moved to :mod:`agentic_graphrag.core.reasoning`.

Refactor §0-B: contracts and ports live in the core/ shared kernel.

The old import path stays valid; it resolves to the *same* module object, so
monkeypatching either path is equivalent. New code should import from core.
"""

import sys

from agentic_graphrag.core.reasoning import __name__ as _target

sys.modules[__name__] = sys.modules[_target]
