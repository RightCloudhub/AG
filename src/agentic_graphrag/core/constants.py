"""Shared units and defaults; policy-specific values belong in configuration."""

from typing import Final

MILLISECONDS_PER_SECOND: Final = 1_000
SECONDS_PER_DAY: Final = 86_400
BYTES_PER_MEBIBYTE: Final = 1_048_576
DEFAULT_RRF_K: Final = 60
DEFAULT_RECURSION_LIMIT: Final = 15
DEFAULT_PAGE_SIZE: Final = 50
SCORE_PRECISION: Final = 6
PROMPT_SYSTEM_HEADER: Final = "# System"
PROMPT_USER_HEADER: Final = "# User"
