"""Named constants for offline multi-hop heuristics."""

from __future__ import annotations

# Domain person-name fragments used to filter entity mentions
PERSON_NAME_HINTS = ("elena", "marcus", "priya", "varga", "chen", "nair")

# Product-like entity fragments for competitor / producer rules
PRODUCT_HINTS = ("server", "workstation", "quantum", "edge", "helixcore")

# Question phrases that trigger prior-employer extraction
WORK_PHRASES = (
    "previously work",
    "worked at",
    "work at",
    "work for",
    "worked for",
)

CEO_LEAD_KEYS = ("lead", "helix", "now")
PRODUCT_Q_KEYS = ("producer", "produce", "product")
SUPPLY_SHARED_KEYS = ("also", "among", "shared")
RELATIONSHIP_KEYS = ("relationship", "chain", "path", "connect")
SHARED_EDGE_MARKERS = ("COMPETES_WITH", "SUPPLIES", "SUPPLIES_FOR")

# Multi-part ownership / HQ questions must not be answered as CEO-only.
# Prefer HQ-framed city phrases over bare "city" (too broad for non-HQ questions).
MULTI_HOP_EXTRA_KEYS = (
    "acquir",
    "acquired",
    "acquisition",
    "bought",
    "headquarter",
    "headquarters",
    "hq",
    "which city",
    "what city",
    "hq city",
    "收购",
    "被收购",
    "总部",
    "位于",
    "哪个城市",
    "母公司",
    "旗下",
)

APEX_HOLDINGS = "Apex Holdings"
ORION_MIN_MATCHES = 2
TEXT_PATH_LIMIT = 4
SHARED_CONN_LIMIT = 6

# Demo-corpus HQ fallbacks from seed docs (only verified surfaces).
DEMO_HQ: dict[str, str] = {
    "apex holdings": "Singapore",
    "novatech industries": "Austin",
}
