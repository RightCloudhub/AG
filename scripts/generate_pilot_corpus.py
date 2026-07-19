#!/usr/bin/env python3
"""Generate a synthetic pilot corpus for P1-GOV-01 / C1.

Writes ≥100 UTF-8 Markdown documents under data/pilot/raw/ in the
corporate_relations domain (aligned with configs/schema/domain_v0.yaml).

The corpus is intentionally synthetic / desensitized — suitable for
engineering smoke, ingest, and multi-hop path design. It does not replace
a product-authorized real-domain corpus when that arrives.

Usage:
  python scripts/generate_pilot_corpus.py
  python scripts/generate_pilot_corpus.py --out data/pilot/raw --count 120
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── Shared universe (stable names for multi-hop connectivity) ──────────────

CORE_COMPANIES = [
    ("Apex Holdings", "Singapore", "conglomerate", 1998),
    ("NovaTech Industries", "Austin", "enterprise computing", 2005),
    ("BrightLink Logistics", "Singapore", "logistics", 2008),
    ("Helix Compute", "San Jose", "enterprise hardware", 2007),
    ("SiliconForge", "Taipei", "semiconductors", 2001),
    ("Harbor Components", "Shenzhen", "power modules", 2010),
    ("Orion Systems", "Seattle", "cloud software", 1999),
    ("Meridian Capital", "London", "private equity", 1995),
]

# Expanded companies: (name, city, industry, founded, parent_or_none, competitors, products)
EXPANDED = [
    (
        "Northwind Analytics",
        "Boston",
        "business intelligence",
        2012,
        "Apex Holdings",
        ["Helix Compute"],
        ["Northwind Insight Suite"],
    ),
    (
        "Cedar Semiconductor",
        "Hsinchu",
        "semiconductors",
        2003,
        None,
        ["SiliconForge"],
        ["CedarLink FPGA"],
    ),
    (
        "Polaris Cloud",
        "Dublin",
        "cloud infrastructure",
        2011,
        "Orion Systems",
        ["NovaTech Industries"],
        ["Polaris Stack"],
    ),
    ("Summit Robotics", "Munich", "industrial robotics", 2009, None, [], ["SummitArm X3"]),
    (
        "Lumen Networks",
        "Stockholm",
        "networking",
        2006,
        None,
        ["BrightLink Logistics"],
        ["LumenMesh Switch"],
    ),
    (
        "Atlas Mobility",
        "Berlin",
        "logistics software",
        2014,
        "BrightLink Logistics",
        [],
        ["Atlas Route OS"],
    ),
    ("Keystone Finance", "Zurich", "fintech", 2013, "Meridian Capital", [], ["Keystone Ledger"]),
    ("Redwood Energy", "Oslo", "clean energy hardware", 2015, None, [], ["Redwood Cell Pack"]),
    (
        "Nimbus Security",
        "Tel Aviv",
        "cybersecurity",
        2010,
        None,
        ["Orion Systems"],
        ["Nimbus Shield"],
    ),
    (
        "Cobalt Materials",
        "Pittsburgh",
        "advanced materials",
        2004,
        None,
        ["Harbor Components"],
        ["Cobalt Thermal Sheet"],
    ),
    ("Ironclad LegalTech", "Chicago", "legal software", 2016, None, [], ["Ironclad Contract AI"]),
    ("Cascade Bio", "Cambridge", "biotech instruments", 2008, None, [], ["Cascade Sequencer"]),
    (
        "Delta Fabrication",
        "Detroit",
        "manufacturing",
        1997,
        "Apex Holdings",
        [],
        ["Delta Press Line"],
    ),
    ("Echo Media Labs", "Los Angeles", "media tech", 2017, None, [], ["Echo Stream Engine"]),
    (
        "Frost Data",
        "Toronto",
        "data platforms",
        2014,
        "Orion Systems",
        ["Northwind Analytics"],
        ["Frost Lakehouse"],
    ),
    (
        "Granite Storage",
        "Phoenix",
        "storage systems",
        2009,
        None,
        ["Helix Compute", "NovaTech Industries"],
        ["Granite Array"],
    ),
    (
        "Horizon Optics",
        "Kyoto",
        "optical components",
        2002,
        None,
        ["SiliconForge"],
        ["Horizon Lens Array"],
    ),
    (
        "Ivory Payments",
        "Singapore",
        "payments",
        2015,
        "Meridian Capital",
        ["Keystone Finance"],
        ["Ivory PayRail"],
    ),
    (
        "Jade Mobility",
        "Seoul",
        "EV components",
        2011,
        None,
        ["Harbor Components"],
        ["Jade Battery Module"],
    ),
    ("Kinetic Sensors", "Zurich", "IoT sensors", 2013, "Summit Robotics", [], ["Kinetic Node"]),
    (
        "Lattice AI",
        "Montreal",
        "ML infrastructure",
        2018,
        "Polaris Cloud",
        ["Nimbus Security"],
        ["Lattice Train Hub"],
    ),
    (
        "Mosaic Retail",
        "Paris",
        "retail analytics",
        2016,
        "Northwind Analytics",
        [],
        ["Mosaic Shelf AI"],
    ),
    (
        "Nexus Harbor",
        "Rotterdam",
        "port logistics",
        2000,
        "BrightLink Logistics",
        ["Atlas Mobility"],
        ["Nexus Berth OS"],
    ),
    ("Onyx Defense", "Arlington", "defense systems", 1994, None, [], ["Onyx Radar Suite"]),
    ("Pulse Health", "Basel", "digital health", 2012, None, ["Cascade Bio"], ["Pulse Care Cloud"]),
    ("Quartz Mining Soft", "Perth", "mining software", 2007, None, [], ["Quartz Pit Planner"]),
    (
        "Riverbank Credit",
        "Frankfurt",
        "credit analytics",
        2011,
        "Meridian Capital",
        ["Keystone Finance"],
        ["Riverbank Score"],
    ),
    (
        "Solstice Solar",
        "Phoenix",
        "solar hardware",
        2010,
        "Redwood Energy",
        [],
        ["Solstice Panel Pro"],
    ),
    (
        "Titan Forge",
        "Birmingham",
        "industrial metals",
        1988,
        "Apex Holdings",
        ["Cobalt Materials"],
        ["Titan Alloy Plate"],
    ),
    (
        "Umbra Imaging",
        "Helsinki",
        "medical imaging",
        2005,
        None,
        ["Cascade Bio"],
        ["Umbra MRI Core"],
    ),
    (
        "Vertex Chips",
        "Bangalore",
        "chip design",
        2006,
        None,
        ["SiliconForge", "Cedar Semiconductor"],
        ["Vertex Edge ASIC"],
    ),
    ("Willow Telecom", "Tokyo", "telecom gear", 1996, "Lumen Networks", [], ["Willow 5G Base"]),
    (
        "Xenon Chemicals",
        "Houston",
        "specialty chemicals",
        1991,
        None,
        ["Cobalt Materials"],
        ["Xenon Polymer X"],
    ),
    (
        "Yellowstone Data",
        "Denver",
        "geospatial data",
        2015,
        "Frost Data",
        ["Northwind Analytics"],
        ["Yellowstone Map API"],
    ),
    (
        "Zenith Aerospace",
        "Toulouse",
        "aerospace components",
        2003,
        None,
        ["Onyx Defense"],
        ["Zenith Actuator"],
    ),
    (
        "Amber Logic",
        "Amsterdam",
        "EDA tools",
        2008,
        "Vertex Chips",
        ["SiliconForge"],
        ["Amber PlaceRoute"],
    ),
    (
        "BluePeak Cloud",
        "Sydney",
        "regional cloud",
        2016,
        "Polaris Cloud",
        ["Orion Systems"],
        ["BluePeak Region"],
    ),
    (
        "Coral Reef Bio",
        "San Diego",
        "marine biotech",
        2014,
        "Cascade Bio",
        [],
        ["Coral Reef Assay"],
    ),
    (
        "Drift Commerce",
        "Hangzhou",
        "e-commerce platform",
        2013,
        None,
        ["Mosaic Retail"],
        ["Drift Storefront"],
    ),
    (
        "Ember Foundry",
        "Dresden",
        "chip foundry",
        2000,
        None,
        ["SiliconForge", "Cedar Semiconductor"],
        ["Ember 7nm Node"],
    ),
    (
        "Falcon Logistics",
        "Dubai",
        "regional logistics",
        2009,
        "BrightLink Logistics",
        ["Nexus Harbor"],
        ["Falcon Lane"],
    ),
    (
        "Glacier Analytics",
        "Reykjavik",
        "climate data",
        2017,
        "Yellowstone Data",
        [],
        ["Glacier Climate Graph"],
    ),
    (
        "Harbor Wind",
        "Copenhagen",
        "offshore wind",
        2012,
        "Redwood Energy",
        ["Solstice Solar"],
        ["Harbor Turbine Ctrl"],
    ),
    ("Indigo Pharma IT", "Basel", "pharma IT", 2010, "Pulse Health", [], ["Indigo Trial OS"]),
    (
        "Jasper Boards",
        "Portland",
        "hardware boards",
        2011,
        "Helix Compute",
        ["Harbor Components"],
        ["Jasper Carrier Board"],
    ),
    (
        "Kite Drones",
        "Austin",
        "commercial drones",
        2015,
        "Summit Robotics",
        ["Zenith Aerospace"],
        ["Kite Surveyor"],
    ),
    (
        "Lotus FinOps",
        "Singapore",
        "finops software",
        2019,
        "Keystone Finance",
        ["Ivory Payments"],
        ["Lotus Cost Map"],
    ),
    (
        "Marble Vault",
        "Geneva",
        "custody tech",
        2018,
        "Meridian Capital",
        ["Riverbank Credit"],
        ["Marble Custody API"],
    ),
    (
        "Nova Edge Labs",
        "Austin",
        "edge computing R&D",
        2020,
        "NovaTech Industries",
        ["Helix Compute"],
        ["Nova Edge Kit"],
    ),
    (
        "Orbit Satcom",
        "Cape Canaveral",
        "satellite comms",
        2004,
        "Zenith Aerospace",
        ["Willow Telecom"],
        ["Orbit Link Modem"],
    ),
    (
        "Pine Grove Soft",
        "Raleigh",
        "developer tools",
        2014,
        "Orion Systems",
        ["Lattice AI"],
        ["Pine Grove IDE"],
    ),
    (
        "Quill Publishing AI",
        "New York",
        "content AI",
        2018,
        "Echo Media Labs",
        [],
        ["Quill Draft Engine"],
    ),
    (
        "Ridge Battery",
        "Nagoya",
        "battery cells",
        2007,
        "Jade Mobility",
        ["Harbor Components"],
        ["Ridge Cell 2170"],
    ),
    (
        "Sable Insurance AI",
        "Hartford",
        "insurtech",
        2016,
        "Riverbank Credit",
        [],
        ["Sable Risk Graph"],
    ),
    (
        "Terra Farm Tech",
        "Des Moines",
        "agritech",
        2013,
        None,
        ["Kinetic Sensors"],
        ["Terra Yield OS"],
    ),
    (
        "Ultraviolet Clean",
        "Osaka",
        "UV disinfection",
        2011,
        None,
        ["Pulse Health"],
        ["UV Clean Chamber"],
    ),
    (
        "Violet Optics",
        "Grenoble",
        "photonics",
        2009,
        "Horizon Optics",
        ["SiliconForge"],
        ["Violet PIC Chip"],
    ),
    ("Wren Audio AI", "Nashville", "audio ML", 2017, "Echo Media Labs", [], ["Wren Transcribe"]),
    (
        "Yarrow Materials",
        "Leeds",
        "composites",
        2005,
        "Titan Forge",
        ["Cobalt Materials"],
        ["Yarrow Carbon Weave"],
    ),
    (
        "Zephyr Ventures",
        "Palo Alto",
        "venture studio",
        2012,
        "Meridian Capital",
        [],
        ["Zephyr Studio OS"],
    ),
]

PEOPLE = [
    ("Elena Varga", "CEO", "Apex Holdings", ["Orion Systems", "Meridian Capital"], "Singapore"),
    ("Marcus Chen", "CEO", "NovaTech Industries", ["Orion Systems"], "Austin"),
    ("Priya Nair", "CEO", "Helix Compute", ["Meridian Capital"], "San Jose"),
    ("Hiro Tanaka", "CEO", "SiliconForge", ["Ember Foundry"], "Taipei"),
    ("Sofia Alvarez", "CEO", "BrightLink Logistics", ["Falcon Logistics"], "Singapore"),
    ("James Okonkwo", "CEO", "Harbor Components", ["Ridge Battery"], "Shenzhen"),
    ("Amelia Brooks", "CEO", "Orion Systems", ["Polaris Cloud"], "Seattle"),
    ("David Kim", "Managing Director", "Meridian Capital", ["Zephyr Ventures"], "London"),
    ("Nora Lindqvist", "CEO", "Northwind Analytics", ["Frost Data"], "Boston"),
    ("Wei Zhang", "CTO", "Cedar Semiconductor", ["Vertex Chips"], "Hsinchu"),
    ("Owen Murphy", "CEO", "Polaris Cloud", ["Orion Systems", "BluePeak Cloud"], "Dublin"),
    ("Lina Schneider", "CEO", "Summit Robotics", ["Kite Drones"], "Munich"),
    ("Erik Johansson", "CEO", "Lumen Networks", ["Willow Telecom"], "Stockholm"),
    ("Aisha Rahman", "CEO", "Atlas Mobility", ["Nexus Harbor"], "Berlin"),
    ("Tom Hughes", "CEO", "Keystone Finance", ["Ivory Payments", "Lotus FinOps"], "Zurich"),
    ("Ingrid Solberg", "CEO", "Redwood Energy", ["Solstice Solar", "Harbor Wind"], "Oslo"),
    ("Yael Cohen", "CEO", "Nimbus Security", ["Lattice AI"], "Tel Aviv"),
    ("Frank Mueller", "CEO", "Cobalt Materials", ["Yarrow Materials"], "Pittsburgh"),
    ("Grace Lee", "CEO", "Ironclad LegalTech", [], "Chicago"),
    ("Sara Patel", "CEO", "Cascade Bio", ["Umbra Imaging", "Coral Reef Bio"], "Cambridge"),
    ("Robert Hale", "CEO", "Delta Fabrication", ["Titan Forge"], "Detroit"),
    (
        "Mia Torres",
        "CEO",
        "Echo Media Labs",
        ["Quill Publishing AI", "Wren Audio AI"],
        "Los Angeles",
    ),
    ("Chris Nguyen", "CEO", "Frost Data", ["Yellowstone Data", "Glacier Analytics"], "Toronto"),
    ("Patricia Wong", "CEO", "Granite Storage", ["Helix Compute"], "Phoenix"),
    ("Kenji Mori", "CEO", "Horizon Optics", ["Violet Optics"], "Kyoto"),
    ("Nadia Farouk", "CEO", "Ivory Payments", ["Keystone Finance"], "Singapore"),
    ("Min-jun Park", "CEO", "Jade Mobility", ["Ridge Battery"], "Seoul"),
    ("Anna Weber", "CTO", "Kinetic Sensors", ["Summit Robotics"], "Zurich"),
    ("Jean Dupont", "CEO", "Lattice AI", ["Polaris Cloud"], "Montreal"),
    ("Camille Roux", "CEO", "Mosaic Retail", ["Drift Commerce"], "Paris"),
    ("Pieter de Vries", "CEO", "Nexus Harbor", ["BrightLink Logistics"], "Rotterdam"),
    ("Colonel Eve Grant", "CEO", "Onyx Defense", ["Zenith Aerospace"], "Arlington"),
    ("Helena Berger", "CEO", "Pulse Health", ["Indigo Pharma IT"], "Basel"),
    ("Mark Sullivan", "CEO", "Quartz Mining Soft", [], "Perth"),
    ("Klara Bauer", "CEO", "Riverbank Credit", ["Sable Insurance AI"], "Frankfurt"),
    ("Diego Santos", "CEO", "Solstice Solar", ["Redwood Energy"], "Phoenix"),
    ("Helen Cartwright", "CEO", "Titan Forge", ["Yarrow Materials"], "Birmingham"),
    ("Mikko Virtanen", "CEO", "Umbra Imaging", ["Cascade Bio"], "Helsinki"),
    ("Ananya Iyer", "CEO", "Vertex Chips", ["Amber Logic"], "Bangalore"),
    ("Yuki Sato", "CEO", "Willow Telecom", ["Lumen Networks"], "Tokyo"),
    ("Rachel Green", "CEO", "Xenon Chemicals", ["Cobalt Materials"], "Houston"),
    ("Sam Ortiz", "CEO", "Yellowstone Data", ["Glacier Analytics"], "Denver"),
    ("Claire Moreau", "CEO", "Zenith Aerospace", ["Orbit Satcom"], "Toulouse"),
    ("Lars Bakker", "CEO", "Amber Logic", ["Vertex Chips"], "Amsterdam"),
    ("Emma Clarke", "CEO", "BluePeak Cloud", ["Polaris Cloud"], "Sydney"),
    ("Jordan Lee", "CEO", "Drift Commerce", ["Mosaic Retail"], "Hangzhou"),
    ("Hans Richter", "CEO", "Ember Foundry", ["SiliconForge"], "Dresden"),
    ("Fatima Al-Hassan", "CEO", "Falcon Logistics", ["BrightLink Logistics"], "Dubai"),
    ("Bjorn Einarsson", "CEO", "Glacier Analytics", ["Yellowstone Data"], "Reykjavik"),
    ("Mei Lin", "CEO", "Nova Edge Labs", ["NovaTech Industries"], "Austin"),
]

PRODUCTS_EXTRA = [
    (
        "QuantumEdge Server",
        "NovaTech Industries",
        ["SiliconForge", "Harbor Components"],
        "enterprise server",
    ),
    ("EdgeLite Workstation", "NovaTech Industries", ["SiliconForge"], "workstation"),
    ("HelixCore Server", "Helix Compute", ["SiliconForge", "Jasper Boards"], "enterprise server"),
    ("CedarLink FPGA", "Cedar Semiconductor", ["Ember Foundry"], "FPGA"),
    ("Polaris Stack", "Polaris Cloud", ["BluePeak Cloud"], "cloud platform"),
    ("SummitArm X3", "Summit Robotics", ["Kinetic Sensors", "Yarrow Materials"], "robot arm"),
    ("LumenMesh Switch", "Lumen Networks", ["Horizon Optics", "Violet Optics"], "network switch"),
    ("Atlas Route OS", "Atlas Mobility", ["Nexus Harbor"], "routing software"),
    ("Keystone Ledger", "Keystone Finance", [], "fintech ledger"),
    ("Redwood Cell Pack", "Redwood Energy", ["Ridge Battery", "Jade Mobility"], "energy storage"),
    ("Nimbus Shield", "Nimbus Security", ["Lattice AI"], "security suite"),
    ("Cobalt Thermal Sheet", "Cobalt Materials", ["Xenon Chemicals"], "thermal material"),
    ("Ironclad Contract AI", "Ironclad LegalTech", [], "legal AI"),
    ("Cascade Sequencer", "Cascade Bio", ["Umbra Imaging"], "lab instrument"),
    ("Delta Press Line", "Delta Fabrication", ["Titan Forge"], "manufacturing line"),
    ("Echo Stream Engine", "Echo Media Labs", ["Wren Audio AI"], "media engine"),
    ("Frost Lakehouse", "Frost Data", ["Yellowstone Data"], "data platform"),
    ("Granite Array", "Granite Storage", ["Harbor Components"], "storage array"),
    ("Horizon Lens Array", "Horizon Optics", ["Violet Optics"], "optics"),
    ("Ivory PayRail", "Ivory Payments", ["Keystone Finance"], "payments rail"),
    ("Jade Battery Module", "Jade Mobility", ["Ridge Battery"], "EV battery"),
    ("Kinetic Node", "Kinetic Sensors", ["Summit Robotics"], "IoT sensor"),
    ("Lattice Train Hub", "Lattice AI", ["Polaris Cloud"], "ML training"),
    ("Mosaic Shelf AI", "Mosaic Retail", ["Drift Commerce"], "retail AI"),
    ("Nexus Berth OS", "Nexus Harbor", ["BrightLink Logistics"], "port OS"),
    ("Onyx Radar Suite", "Onyx Defense", ["Zenith Aerospace"], "radar"),
    ("Pulse Care Cloud", "Pulse Health", ["Indigo Pharma IT"], "health cloud"),
    ("Quartz Pit Planner", "Quartz Mining Soft", [], "mining software"),
    ("Riverbank Score", "Riverbank Credit", ["Sable Insurance AI"], "credit score"),
    ("Solstice Panel Pro", "Solstice Solar", ["Redwood Energy"], "solar panel"),
    ("Titan Alloy Plate", "Titan Forge", ["Yarrow Materials"], "metal plate"),
    ("Umbra MRI Core", "Umbra Imaging", ["Cascade Bio"], "MRI core"),
    ("Vertex Edge ASIC", "Vertex Chips", ["Ember Foundry", "Amber Logic"], "ASIC"),
    ("Willow 5G Base", "Willow Telecom", ["Lumen Networks"], "5G base"),
    ("Xenon Polymer X", "Xenon Chemicals", ["Cobalt Materials"], "polymer"),
    ("Yellowstone Map API", "Yellowstone Data", ["Glacier Analytics"], "map API"),
    ("Zenith Actuator", "Zenith Aerospace", ["Orbit Satcom"], "actuator"),
    ("Amber PlaceRoute", "Amber Logic", ["Vertex Chips"], "EDA tool"),
    ("BluePeak Region", "BluePeak Cloud", ["Polaris Cloud"], "cloud region"),
    ("Coral Reef Assay", "Coral Reef Bio", ["Cascade Bio"], "bio assay"),
    ("Drift Storefront", "Drift Commerce", ["Mosaic Retail"], "e-commerce"),
    ("Ember 7nm Node", "Ember Foundry", ["SiliconForge", "Vertex Chips"], "foundry node"),
    ("Falcon Lane", "Falcon Logistics", ["BrightLink Logistics"], "logistics lane"),
    ("Glacier Climate Graph", "Glacier Analytics", ["Yellowstone Data"], "climate graph"),
    ("Harbor Turbine Ctrl", "Harbor Wind", ["Redwood Energy"], "turbine control"),
    ("Indigo Trial OS", "Indigo Pharma IT", ["Pulse Health"], "trial OS"),
    ("Jasper Carrier Board", "Jasper Boards", ["Helix Compute"], "carrier board"),
    ("Kite Surveyor", "Kite Drones", ["Summit Robotics"], "survey drone"),
    ("Lotus Cost Map", "Lotus FinOps", ["Keystone Finance"], "finops map"),
    ("Marble Custody API", "Marble Vault", ["Meridian Capital"], "custody API"),
    ("Nova Edge Kit", "Nova Edge Labs", ["NovaTech Industries", "SiliconForge"], "edge kit"),
    ("Orbit Link Modem", "Orbit Satcom", ["Zenith Aerospace"], "sat modem"),
    ("Pine Grove IDE", "Pine Grove Soft", ["Orion Systems"], "IDE"),
    ("Quill Draft Engine", "Quill Publishing AI", ["Echo Media Labs"], "content AI"),
    ("Ridge Cell 2170", "Ridge Battery", ["Jade Mobility"], "battery cell"),
    ("Sable Risk Graph", "Sable Insurance AI", ["Riverbank Credit"], "risk graph"),
    ("Terra Yield OS", "Terra Farm Tech", ["Kinetic Sensors"], "agri OS"),
    ("UV Clean Chamber", "Ultraviolet Clean", ["Pulse Health"], "UV chamber"),
    ("Violet PIC Chip", "Violet Optics", ["Horizon Optics"], "photonic chip"),
    ("Wren Transcribe", "Wren Audio AI", ["Echo Media Labs"], "transcription"),
    ("Yarrow Carbon Weave", "Yarrow Materials", ["Titan Forge"], "composite"),
    ("Zephyr Studio OS", "Zephyr Ventures", ["Meridian Capital"], "venture OS"),
]

EVENTS = [
    (
        "Harbor Partnership 2024",
        "2024-03",
        "strategic partnership",
        ["NovaTech Industries", "Harbor Components", "Apex Holdings"],
        "minority partnership strengthening QuantumEdge Server supply chain",
    ),
    (
        "SiliconForge Capacity Expansion 2023",
        "2023-06",
        "capex",
        ["SiliconForge", "Ember Foundry"],
        "joint capacity expansion for advanced chips",
    ),
    (
        "Meridian Apex Growth Fund Close",
        "2022-11",
        "funding",
        ["Meridian Capital", "Apex Holdings", "Zephyr Ventures"],
        "growth fund close supporting Southeast Asia tech",
    ),
    (
        "Helix-Granite Storage Alliance",
        "2023-09",
        "alliance",
        ["Helix Compute", "Granite Storage"],
        "co-selling enterprise storage + server stacks",
    ),
    (
        "BrightLink Falcon Integration",
        "2024-01",
        "integration",
        ["BrightLink Logistics", "Falcon Logistics", "Nexus Harbor"],
        "regional logistics network integration",
    ),
    (
        "Polaris BluePeak Merger Talks",
        "2024-05",
        "M&A talks",
        ["Polaris Cloud", "BluePeak Cloud", "Orion Systems"],
        "regional cloud consolidation discussions",
    ),
    (
        "Vertex Ember Tapeout Event",
        "2023-12",
        "product launch",
        ["Vertex Chips", "Ember Foundry", "Amber Logic"],
        "first Vertex Edge ASIC tapeout on Ember 7nm",
    ),
    (
        "Redwood Solstice Grid Deal",
        "2022-08",
        "commercial deal",
        ["Redwood Energy", "Solstice Solar", "Harbor Wind"],
        "grid-scale storage and solar package",
    ),
    (
        "Cascade Umbra Imaging JV",
        "2021-04",
        "joint venture",
        ["Cascade Bio", "Umbra Imaging"],
        "joint medical instrument R&D",
    ),
    (
        "NovaTech Edge Labs Spin-in",
        "2020-10",
        "reorg",
        ["NovaTech Industries", "Nova Edge Labs", "Apex Holdings"],
        "edge computing lab spun into Nova Edge Labs",
    ),
    (
        "Lumen Willow 5G Pact",
        "2023-02",
        "partnership",
        ["Lumen Networks", "Willow Telecom"],
        "5G equipment interoperability pact",
    ),
    (
        "Keystone Ivory Payments Rail",
        "2024-02",
        "product launch",
        ["Keystone Finance", "Ivory Payments", "Lotus FinOps"],
        "cross-border pay rail launch",
    ),
    (
        "Summit Kite Drone Acquisition",
        "2022-05",
        "acquisition",
        ["Summit Robotics", "Kite Drones"],
        "Summit acquired Kite Drones survey unit",
    ),
    (
        "Onyx Zenith Defense Contract",
        "2021-09",
        "contract",
        ["Onyx Defense", "Zenith Aerospace", "Orbit Satcom"],
        "joint defense components contract",
    ),
    (
        "Frost Yellowstone Data Merge",
        "2023-07",
        "M&A",
        ["Frost Data", "Yellowstone Data", "Orion Systems"],
        "geospatial data assets merged under Frost",
    ),
    (
        "Northwind Mosaic Retail Carveout",
        "2022-12",
        "carveout",
        ["Northwind Analytics", "Mosaic Retail"],
        "retail analytics line carved into Mosaic",
    ),
    (
        "Titan Yarrow Materials Supply Pact",
        "2024-04",
        "supply pact",
        ["Titan Forge", "Yarrow Materials", "Apex Holdings"],
        "long-term carbon composite supply",
    ),
    (
        "Pulse Indigo Pharma IT Bundle",
        "2023-03",
        "bundle",
        ["Pulse Health", "Indigo Pharma IT"],
        "clinical trial + care cloud bundle",
    ),
    (
        "Drift Mosaic Commerce Link",
        "2024-06",
        "partnership",
        ["Drift Commerce", "Mosaic Retail"],
        "storefront + shelf AI commerce link",
    ),
    (
        "Riverbank Sable Risk Graph Launch",
        "2023-11",
        "product launch",
        ["Riverbank Credit", "Sable Insurance AI", "Meridian Capital"],
        "shared risk graph for credit and insurance",
    ),
]


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.rstrip() + "\n", encoding="utf-8")


def _copy_interim(out: Path) -> int:
    src = ROOT / "data" / "raw"
    n = 0
    if not src.is_dir():
        return 0
    for p in sorted(src.glob("*.md")):
        dest = out / f"00_core_{p.name}"
        shutil.copy2(p, dest)
        n += 1
    return n


def _company_profile(
    name: str,
    city: str,
    industry: str,
    founded: int,
    parent: str | None,
    competitors: list[str],
    products: list[str],
    ceo: str | None,
) -> str:
    lines = [
        f"# {name} Corporate Profile",
        "",
        f"{name} is a company in the {industry} sector, headquartered in {city}. "
        f"It was founded in {founded}.",
        "",
    ]
    if parent:
        lines.append(
            f"{name} is a subsidiary of {parent}. Through its ownership structure, "
            f"{parent} holds a controlling interest in {name}."
        )
        lines.append("")
    if ceo:
        lines.append(f"The current CEO of {name} is {ceo}.")
        lines.append("")
    if products:
        prod = ", ".join(products)
        lines.append(f"{name} produces {prod}.")
        lines.append("")
    if competitors:
        for c in competitors:
            lines.append(f"{name} competes with {c} in the {industry} market.")
        lines.append("")
    lines.append(
        f"{name} is located in {city} and maintains commercial relationships "
        f"across the broader technology and industrial ecosystem."
    )
    return "\n".join(lines)


def _person_bio(
    name: str,
    title: str,
    company: str,
    prior: list[str],
    city: str,
) -> str:
    lines = [
        f"# Executive Biography: {name}",
        "",
        f"{name} is the {title} of {company}, based in {city}.",
        "",
    ]
    if prior:
        if len(prior) == 1:
            lines.append(f"Before {company}, {name} worked at {prior[0]}.")
        else:
            joined = ", ".join(prior[:-1]) + f", and {prior[-1]}"
            lines.append(f"Before {company}, {name} worked at {joined}.")
        lines.append("")
    lines.append(
        f"In the role of {title} at {company}, {name} oversees strategy, "
        f"partnerships, and multi-year product roadmaps."
    )
    if prior:
        lines.append("")
        lines.append(
            f"Colleagues note that experience at {' and '.join(prior)} shaped "
            f"{name}'s approach to cross-company collaboration."
        )
    return "\n".join(lines)


def _product_supply(name: str, producer: str, suppliers: list[str], category: str) -> str:
    lines = [
        f"# Product & Supply Note: {name}",
        "",
        f"{name} is a {category} product produced by {producer}.",
        "",
    ]
    if suppliers:
        for s in suppliers:
            lines.append(f"{s} supplies components or services for {name}.")
        lines.append("")
        if len(suppliers) >= 2:
            lines.append(
                f"The multi-supplier stack for {name} includes "
                f"{', '.join(suppliers[:-1])}, and {suppliers[-1]}."
            )
            lines.append("")
    lines.append(
        f"{producer} markets {name} to enterprise and industrial customers, "
        f"and tracks supplier concentration risk as part of product governance."
    )
    return "\n".join(lines)


def _event_doc(
    name: str,
    date: str,
    etype: str,
    participants: list[str],
    summary: str,
) -> str:
    lines = [
        f"# Event Brief: {name}",
        "",
        f"The event {name} took place around {date}. It is classified as a {etype}.",
        "",
        summary.capitalize() + ".",
        "",
    ]
    for p in participants:
        lines.append(f"{p} participated in {name}.")
    lines.append("")
    if len(participants) >= 2:
        lines.append(
            f"Primary participants included {', '.join(participants[:-1])}, and {participants[-1]}."
        )
    return "\n".join(lines)


def _sector_overview(idx: int) -> str:
    sectors = [
        (
            "Enterprise Computing",
            [
                "NovaTech Industries",
                "Helix Compute",
                "Granite Storage",
                "Nova Edge Labs",
            ],
            "servers, storage, and edge kits",
        ),
        (
            "Semiconductor Supply",
            ["SiliconForge", "Cedar Semiconductor", "Vertex Chips", "Ember Foundry", "Amber Logic"],
            "chips, FPGAs, ASICs, and foundry nodes",
        ),
        (
            "Logistics & Ports",
            ["BrightLink Logistics", "Falcon Logistics", "Nexus Harbor", "Atlas Mobility"],
            "shipping lanes, berth systems, and route software",
        ),
        (
            "Energy Hardware",
            ["Redwood Energy", "Solstice Solar", "Harbor Wind", "Jade Mobility", "Ridge Battery"],
            "storage packs, solar, wind, and battery modules",
        ),
        (
            "Cloud & Data Platforms",
            ["Orion Systems", "Polaris Cloud", "BluePeak Cloud", "Frost Data", "Yellowstone Data"],
            "cloud regions, lakehouses, and geospatial APIs",
        ),
        (
            "Capital & Fintech",
            [
                "Meridian Capital",
                "Keystone Finance",
                "Ivory Payments",
                "Riverbank Credit",
                "Zephyr Ventures",
            ],
            "funds, ledgers, payments, and credit scores",
        ),
        (
            "Industrial Automation",
            [
                "Summit Robotics",
                "Kinetic Sensors",
                "Kite Drones",
                "Delta Fabrication",
                "Titan Forge",
            ],
            "robots, sensors, drones, and fabrication lines",
        ),
        (
            "Health & Bio Instruments",
            ["Cascade Bio", "Umbra Imaging", "Pulse Health", "Indigo Pharma IT", "Coral Reef Bio"],
            "sequencers, imaging, care cloud, and assays",
        ),
        (
            "Networking & Optics",
            ["Lumen Networks", "Willow Telecom", "Horizon Optics", "Violet Optics"],
            "switches, 5G bases, and photonic components",
        ),
        (
            "Conglomerate Holdings",
            [
                "Apex Holdings",
                "NovaTech Industries",
                "BrightLink Logistics",
                "Delta Fabrication",
                "Titan Forge",
            ],
            "parent-subsidiary industrial technology holdings",
        ),
    ]
    title, cos, focus = sectors[idx % len(sectors)]
    lines = [
        f"# Sector Overview: {title}",
        "",
        f"This note summarizes competitive and supply dynamics in {title.lower()}, "
        f"with focus on {focus}.",
        "",
    ]
    for c in cos:
        lines.append(f"{c} is an active participant in the {title} sector.")
    lines.append("")
    if len(cos) >= 2:
        lines.append(
            f"Analysts often compare {cos[0]} with {cos[1]} when mapping multi-hop "
            f"ownership, competition, and supplier overlap."
        )
        lines.append("")
    lines.append(
        "Cross-document multi-hop questions in this sector typically traverse "
        "parent companies, CEOs, prior employers, products, and shared suppliers."
    )
    return "\n".join(lines)


def _supply_chain_cross(idx: int) -> str:
    chains = [
        (
            "QuantumEdge and HelixCore Shared Silicon",
            "SiliconForge",
            ["QuantumEdge Server", "HelixCore Server"],
            ["NovaTech Industries", "Helix Compute"],
        ),
        (
            "Logistics Dual-Serve Pattern",
            "BrightLink Logistics",
            ["Falcon Lane", "Nexus Berth OS"],
            ["NovaTech Industries", "Helix Compute", "Falcon Logistics"],
        ),
        (
            "Battery Materials Bridge",
            "Ridge Battery",
            ["Jade Battery Module", "Redwood Cell Pack"],
            ["Jade Mobility", "Redwood Energy"],
        ),
        (
            "Cloud Regional Stack",
            "Polaris Cloud",
            ["Polaris Stack", "BluePeak Region"],
            ["BluePeak Cloud", "Orion Systems"],
        ),
        (
            "Optics Inside Networking",
            "Horizon Optics",
            ["LumenMesh Switch", "Violet PIC Chip"],
            ["Lumen Networks", "Violet Optics"],
        ),
        (
            "Foundry for Edge ASIC",
            "Ember Foundry",
            ["Vertex Edge ASIC", "Ember 7nm Node"],
            ["Vertex Chips", "Amber Logic"],
        ),
        (
            "Composite Industrial Supply",
            "Yarrow Materials",
            ["Titan Alloy Plate", "SummitArm X3"],
            ["Titan Forge", "Summit Robotics"],
        ),
        (
            "Data Platform Merger Path",
            "Frost Data",
            ["Frost Lakehouse", "Yellowstone Map API"],
            ["Yellowstone Data", "Glacier Analytics"],
        ),
        (
            "Fintech Rail Consortium",
            "Keystone Finance",
            ["Keystone Ledger", "Ivory PayRail", "Lotus Cost Map"],
            ["Ivory Payments", "Lotus FinOps"],
        ),
        (
            "Edge Kit from Parent Stack",
            "NovaTech Industries",
            ["QuantumEdge Server", "Nova Edge Kit"],
            ["Nova Edge Labs", "SiliconForge"],
        ),
    ]
    title, hub, products, cos = chains[idx % len(chains)]
    lines = [
        f"# Supply Chain Cross-Cut: {title}",
        "",
        f"{hub} is a central node in this supply chain cross-cut.",
        "",
    ]
    for p in products:
        lines.append(f"{hub} is linked through product or service flows to {p}.")
    lines.append("")
    for c in cos:
        lines.append(f"{c} appears on this multi-company supply path.")
    lines.append("")
    lines.append(
        f"Shared suppliers such as {hub} create multi-hop evidence paths: "
        f"product → supplier → competitor product, or company → logistics partner → peer."
    )
    return "\n".join(lines)


def generate(out: Path, min_count: int) -> int:
    if out.exists():
        for p in out.iterdir():
            if p.name == ".gitkeep":
                continue
            if p.is_file():
                p.unlink()
    out.mkdir(parents=True, exist_ok=True)

    n = 0
    n += _copy_interim(out)

    # CEO lookup
    ceo_of: dict[str, str] = {company: name for name, _t, company, _pr, _c in PEOPLE}

    # Core company profiles (explicit, for entities not fully covered by interim)
    for name, city, industry, founded in CORE_COMPANIES:
        # skip exact duplicates of interim files by using expanded numbering
        body = _company_profile(
            name,
            city,
            industry,
            founded,
            parent=None,
            competitors=[],
            products=[],
            ceo=ceo_of.get(name),
        )
        # Enrich core with known relations
        if name == "Apex Holdings":
            body += (
                "\n\nApex Holdings is the parent company of NovaTech Industries, "
                "BrightLink Logistics, Delta Fabrication, and Titan Forge."
            )
        if name == "NovaTech Industries":
            body += (
                "\n\nNovaTech Industries is a subsidiary of Apex Holdings. "
                "It competes with Helix Compute and produces QuantumEdge Server "
                "and EdgeLite Workstation."
            )
        if name == "Helix Compute":
            body += (
                "\n\nHelix Compute competes with NovaTech Industries and produces "
                "HelixCore Server. SiliconForge supplies processors for HelixCore Server."
            )
        slug = name.lower().replace(" ", "_")
        _write(out / f"10_company_core_{slug}.md", body)
        n += 1

    for name, city, industry, founded, parent, competitors, products in EXPANDED:
        body = _company_profile(
            name, city, industry, founded, parent, competitors, products, ceo_of.get(name)
        )
        slug = name.lower().replace(" ", "_")
        _write(out / f"11_company_{slug}.md", body)
        n += 1

    for name, title, company, prior, city in PEOPLE:
        body = _person_bio(name, title, company, prior, city)
        slug = name.lower().replace(" ", "_").replace("-", "_")
        _write(out / f"20_person_{slug}.md", body)
        n += 1

    for name, producer, suppliers, category in PRODUCTS_EXTRA:
        body = _product_supply(name, producer, suppliers, category)
        slug = name.lower().replace(" ", "_")
        _write(out / f"30_product_{slug}.md", body)
        n += 1

    for name, date, etype, participants, summary in EVENTS:
        body = _event_doc(name, date, etype, participants, summary)
        slug = name.lower().replace(" ", "_").replace("-", "_")
        h = hashlib.sha1(name.encode()).hexdigest()[:6]
        _write(out / f"40_event_{slug[:40]}_{h}.md", body)
        n += 1

    for i in range(10):
        body = _sector_overview(i)
        _write(out / f"50_sector_{i:02d}.md", body)
        n += 1

    for i in range(10):
        body = _supply_chain_cross(i)
        _write(out / f"51_supply_cross_{i:02d}.md", body)
        n += 1

    # Pad with short relationship notes if still under min_count
    pad_i = 0
    while n < min_count:
        a = EXPANDED[pad_i % len(EXPANDED)]
        b = EXPANDED[(pad_i + 7) % len(EXPANDED)]
        body = (
            f"# Relationship Note {pad_i:03d}: {a[0]} and {b[0]}\n\n"
            f"{a[0]} operates in {a[2]} while {b[0]} operates in {b[2]}.\n\n"
            f"Market observers track whether {a[0]} and {b[0]} form supplier, "
            f"partner, or competitive links through shared parents or components.\n\n"
            f"If {a[0]} has parent {a[4] or 'independent ownership'} and "
            f"{b[0]} has parent {b[4] or 'independent ownership'}, multi-hop "
            f"ownership questions may route through Apex Holdings, Orion Systems, "
            f"or Meridian Capital holding structures.\n"
        )
        _write(out / f"90_rel_note_{pad_i:03d}.md", body)
        n += 1
        pad_i += 1

    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "pilot" / "raw",
        help="Output directory for raw markdown docs",
    )
    ap.add_argument(
        "--count",
        type=int,
        default=100,
        help="Minimum number of documents to generate (default 100)",
    )
    args = ap.parse_args()
    n = generate(args.out.resolve(), args.count)
    print(f"Wrote {n} documents to {args.out}")


if __name__ == "__main__":
    main()
