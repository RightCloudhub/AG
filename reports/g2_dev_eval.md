# Evaluation comparison report (P2-EV-04)

## Summary

- Agentic accuracy: **60.78%**
- Baseline accuracy: **11.76%**
- Accuracy delta (pp): **49.02**
- Agentic evidence recall: **0.8756**
- Agentic latency P50/P95 (ms): **18.0** / **40.0**
- Agentic mean tokens: **0.0**
- Fabrication rate: **0.0**

## Systems

### agentic

- total/correct: 153/93
- accuracy: 60.78%
- evidence_recall: 0.8756
- latency P50/P95: 18.0 / 40.0 ms
- cost mean tokens/calls: 0.0 / 0.0
- by_hops: `{"1": {"total": 34, "correct": 26, "accuracy": 0.7647}, "2": {"total": 78, "correct": 66, "accuracy": 0.8462}, "3": {"total": 41, "correct": 1, "accuracy": 0.0244}}`

### baseline

- total/correct: 153/18
- accuracy: 11.76%
- evidence_recall: 0.2242
- latency P50/P95: 1.0 / 2.0 ms
- cost mean tokens/calls: 0.0 / 0.0
- by_hops: `{"1": {"total": 42, "correct": 2, "accuracy": 0.0476}, "2": {"total": 70, "correct": 12, "accuracy": 0.1714}, "3": {"total": 41, "correct": 4, "accuracy": 0.0976}}`

## Badcases (60)

- `g2-2hop-0045` gold=`Ridge Battery` pred=`James Okonkwo`
- `g2-2hop-0088` gold=`NovaTech Industries` pred=`Helix Compute`
- `g2-2hop-0089` gold=`BrightLink Logistics` pred=`LumenMesh Switch`
- `g2-2hop-0090` gold=`Nexus Harbor` pred=`Atlas Route OS`
- `g2-3hop-0001` gold=`Priya Nair` pred=`Northwind Insight Suite`
- `g2-3hop-0004` gold=`Sofia Alvarez` pred=`LumenMesh Switch`
- `g2-3hop-0006` gold=`Nadia Farouk` pred=`Keystone Ledger`
- `g2-3hop-0007` gold=`Amelia Brooks` pred=`Nimbus Shield`
- `g2-3hop-0008` gold=`James Okonkwo` pred=`Cobalt Thermal Sheet`
- `g2-3hop-0009` gold=`Helena Berger` pred=`Cascade Sequencer`
- `g2-3hop-0011` gold=`Priya Nair` pred=`Granite Array`
- `g2-3hop-0012` gold=`Hiro Tanaka` pred=`Horizon Lens Array`
- `g2-3hop-0018` gold=`Claire Moreau` pred=`Onyx Radar Suite`
- `g2-3hop-0019` gold=`Sara Patel` pred=`Pulse Care Cloud`
- `g2-3hop-0021` gold=`Frank Mueller` pred=`Titan Alloy Plate`
- `g2-3hop-0022` gold=`Sara Patel` pred=`Umbra MRI Core`
- `g2-3hop-0023` gold=`Hiro Tanaka` pred=`Vertex Edge ASIC`
- `g2-3hop-0024` gold=`Frank Mueller` pred=`Xenon Polymer X`
- `g2-3hop-0025` gold=`Nora Lindqvist` pred=`Yellowstone Map API`
- `g2-3hop-0026` gold=`Colonel Eve Grant` pred=`Zenith Actuator`
- `g2-3hop-0027` gold=`Hiro Tanaka` pred=`Amber PlaceRoute`
- `g2-3hop-0028` gold=`Amelia Brooks` pred=`BluePeak Region`
- `g2-3hop-0029` gold=`Camille Roux` pred=`Drift Storefront`
- `g2-3hop-0031` gold=`Pieter de Vries` pred=`Priya Nair`
- `g2-3hop-0032` gold=`Diego Santos` pred=`Harbor Turbine Ctrl`
- `g2-3hop-0033` gold=`James Okonkwo` pred=`Priya Nair`
- `g2-3hop-0034` gold=`Claire Moreau` pred=`Kite Surveyor`
- `g2-3hop-0035` gold=`Nadia Farouk` pred=`Lotus Cost Map`
- `g2-3hop-0037` gold=`Priya Nair` pred=`Nova Edge Kit`
- `g2-3hop-0038` gold=`Yuki Sato` pred=`Orbit Link Modem`
- `g2-3hop-0041` gold=`Helena Berger` pred=`UV Clean Chamber`
- `g2-3hop-0042` gold=`Hiro Tanaka` pred=`Violet PIC Chip`
- `g2-3hop-0043` gold=`Frank Mueller` pred=`Yarrow Carbon Weave`
- `g2-3hop-0044` gold=`Priya Nair` pred=`QuantumEdge Server`
- `g2-3hop-0045` gold=`Priya Nair` pred=`EdgeLite Workstation`
- `g2-3hop-0048` gold=`Amelia Brooks` pred=`# Product & Supply Note: Polaris Stack

Polaris Stack is a cloud platform product produced by Polaris Cloud.

BluePeak Cloud supplies components or services for Polaris Stack.

Polaris Cloud markets Polaris Stack to enterprise and industrial customers, and tracks supplier concentration risk as part `
- `g2-3hop-0049` gold=`Sofia Alvarez` pred=`# Product & Supply Note: Atlas Route OS

Atlas Route OS is a routing software product produced by Atlas Mobility.

Nexus Harbor supplies components or services for Atlas Route OS.

Atlas Mobility markets Atlas Route OS to enterprise and industrial customers, and tracks supplier concentration risk as`
- `g2-3hop-0051` gold=`Elena Varga` pred=`# Product & Supply Note: Delta Press Line

Delta Press Line is a manufacturing line product produced by Delta Fabrication.

Titan Forge supplies components or services for Delta Press Line.

Delta Fabrication markets Delta Press Line to enterprise and industrial customers, and tracks supplier concen`
- `g2-3hop-0052` gold=`Amelia Brooks` pred=`# Product & Supply Note: Frost Lakehouse

Frost Lakehouse is a data platform product produced by Frost Data.

Yellowstone Data supplies components or services for Frost Lakehouse.

Frost Data markets Frost Lakehouse to enterprise and industrial customers, and tracks supplier concentration risk as pa`
- `g2-3hop-0054` gold=`Lina Schneider` pred=`# Product & Supply Note: Kinetic Node

Kinetic Node is a IoT sensor product produced by Kinetic Sensors.

Summit Robotics supplies components or services for Kinetic Node.

Kinetic Sensors markets Kinetic Node to enterprise and industrial customers, and tracks supplier concentration risk as part of `
- `g2-3hop-0055` gold=`Owen Murphy` pred=`# Product & Supply Note: Lattice Train Hub

Lattice Train Hub is a ML training product produced by Lattice AI.

Polaris Cloud supplies components or services for Lattice Train Hub.

Lattice AI markets Lattice Train Hub to enterprise and industrial customers, and tracks supplier concentration risk as`
- `g2-3hop-0058` gold=`David Kim` pred=`# Product & Supply Note: Riverbank Score

Riverbank Score is a credit score product produced by Riverbank Credit.

Sable Insurance AI supplies components or services for Riverbank Score.

Riverbank Credit markets Riverbank Score to enterprise and industrial customers, and tracks supplier concentrati`
- `g2-3hop-0059` gold=`Ingrid Solberg` pred=`# Product & Supply Note: Solstice Panel Pro

Solstice Panel Pro is a solar panel product produced by Solstice Solar.

Redwood Energy supplies components or services for Solstice Panel Pro.

Solstice Solar markets Solstice Panel Pro to enterprise and industrial customers, and tracks supplier concentr`
- `g2-3hop-0060` gold=`Elena Varga` pred=`# Product & Supply Note: Titan Alloy Plate

Titan Alloy Plate is a metal plate product produced by Titan Forge.

Yarrow Materials supplies components or services for Titan Alloy Plate.

Titan Forge markets Titan Alloy Plate to enterprise and industrial customers, and tracks supplier concentration ri`
- `g2-no_answer-0001` gold=`no answer` pred=`# Zephyr Ventures Corporate Profile

Zephyr Ventures is a company in the venture studio sector, headquartered in Palo Alto. It was founded in 2012.

Zephyr Ventures is a subsidiary of Meridian Capital. Through its ownership structure, Meridian Capital holds a controlling interest in Zephyr Ventures.`
- `g2-no_answer-0004` gold=`no answer` pred=`# Apex Holdings Corporate Profile

Apex Holdings is a multinational conglomerate headquartered in Singapore. It operates across technology, logistics, and advanced manufacturing.

Apex Holdings is the parent company of NovaTech Industries and BrightLink Logistics. Through its subsidiary structure, A`
- `g2-no_answer-0005` gold=`no answer` pred=`# Orion Systems Corporate Profile

Orion Systems is a company in the cloud software sector, headquartered in Seattle. It was founded in 1999.

The current CEO of Orion Systems is Amelia Brooks.

Orion Systems is located in Seattle and maintains commercial relationships across the broader technology `
- `g2-no_answer-0006` gold=`no answer` pred=`# Apex Holdings Corporate Profile

Apex Holdings is a multinational conglomerate headquartered in Singapore. It operates across technology, logistics, and advanced manufacturing.

Apex Holdings is the parent company of NovaTech Industries and BrightLink Logistics. Through its subsidiary structure, A`
- `g2-no_answer-0007` gold=`no answer` pred=`# Product & Supply Note: Echo Stream Engine

Echo Stream Engine is a media engine product produced by Echo Media Labs.

Wren Audio AI supplies components or services for Echo Stream Engine.

Echo Media Labs markets Echo Stream Engine to enterprise and industrial customers, and tracks supplier concen`
- `g2-no_answer-0009` gold=`no answer` pred=`# Cascade Bio Corporate Profile

Cascade Bio is a company in the biotech instruments sector, headquartered in Cambridge. It was founded in 2008.

The current CEO of Cascade Bio is Sara Patel.

Cascade Bio produces Cascade Sequencer.

Cascade Bio is located in Cambridge and maintains commercial relat`
