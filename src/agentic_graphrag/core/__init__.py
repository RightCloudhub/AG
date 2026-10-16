"""Shared kernel (refactor §0-B).

Everything the domain packages (agent, retrieval, generation, knowledge) share
lives here and depends on nothing else in the project:

* contracts — :mod:`.evidence` (Candidate, Citation), :mod:`.reasoning`
  (ReasoningChain, Claim, ToolCallTrace)
* ports — :mod:`.ports.stores` (Graph/Vector/Fulltext/Doc), :mod:`.ports.state`
  (limiter/budget/audit stores), :mod:`.ports.llm` (LLMClient, Message, Tier)
* domain events — :mod:`.events` (GraphCommitted, IndexUpdated, ReviewDecided,
  EventBus)
* :mod:`.versioning` — IndexVersion

Adapters implement ports; :mod:`agentic_graphrag.bootstrap` wires them.
"""
