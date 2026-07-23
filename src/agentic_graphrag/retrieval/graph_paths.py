"""Path scoring and candidate assembly for graph multi-hop retrieval."""

from __future__ import annotations

from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation
from agentic_graphrag.retrieval.graph_beam import edge_score, path_signature
from agentic_graphrag.stores.interfaces import PathRecord

_PATH_LENGTH_BONUS = 0.01


def score_paths(
    path_rows: list[PathRecord], sub_question: str | None
) -> list[tuple[float, PathRecord, str]]:
    scored: list[tuple[float, PathRecord, str]] = []
    seen: set[str] = set()
    for path in path_rows:
        sig = path_signature(path.nodes, path.relations)
        if sig in seen:
            continue
        seen.add(sig)
        sc = path_score(path, sub_question)
        content = path_content(path)
        scored.append((sc, path, content))
    scored.sort(key=lambda t: (-t[0], t[2]))
    return scored


def path_score(path: PathRecord, sub_question: str | None) -> float:
    if path.relations:
        edge_scores = [edge_score(r, sub_question) for r in path.relations]
        mean_e = sum(edge_scores) / len(edge_scores)
    else:
        mean_e = 0.0
    sc = mean_e / max(path.length, 1)
    sc = sc + _PATH_LENGTH_BONUS / max(path.length, 1)
    if path.score:
        base = float(path.score)
        sc = max(sc, base * mean_e if mean_e else base)
    return sc


def path_content(path: PathRecord) -> str:
    parts: list[str] = []
    for j, node in enumerate(path.nodes):
        parts.append(node.name)
        if j < len(path.relations):
            parts.append(f"-[{path.relations[j].type}]->")
    return " ".join(parts)


def path_candidates(
    scored: list[tuple[float, PathRecord, str]],
    source_name: str,
    target_name: str,
) -> list[Candidate]:
    out: list[Candidate] = []
    for i, (sc, path, content) in enumerate(scored):
        out.append(
            Candidate(
                id=f"path:{source_name}:{target_name}:{i}",
                source=CandidateSource.GRAPH_PATH,
                content=content,
                score=sc,
                structured={
                    "kind": "path",
                    "nodes": [n.name for n in path.nodes],
                    "relations": [r.type for r in path.relations],
                    "length": path.length,
                    "signature": path_signature(path.nodes, path.relations),
                },
                citations=[Citation(entity_id=n.id, span=n.name) for n in path.nodes],
                metadata={"rank": i},
            )
        )
    return out
