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
    """Render path with true edge direction (forward -> or reverse <-)."""
    parts: list[str] = []
    for j, node in enumerate(path.nodes):
        parts.append(node.name)
        if j < len(path.relations):
            parts.append(_edge_arrow(path, j))
    return " ".join(parts)


def _edge_arrow(path: PathRecord, j: int) -> str:
    rel = path.relations[j]
    cur = path.nodes[j]
    nxt = path.nodes[j + 1] if j + 1 < len(path.nodes) else None
    label = rel.type
    rid = f"#{rel.id}" if rel.id else ""
    if nxt is not None and _is_forward(rel, cur, nxt):
        return f"-[{label}{rid}]->"
    if nxt is not None and _is_forward(rel, nxt, cur):
        return f"<-[{label}{rid}]-"
    # Name-based fallback when ids are missing/mismatched.
    if (rel.head_name or "").lower() == cur.name.lower():
        return f"-[{label}{rid}]->"
    if (rel.tail_name or "").lower() == cur.name.lower():
        return f"<-[{label}{rid}]-"
    return f"-[{label}{rid}]->"


def _is_forward(rel: object, head: object, tail: object) -> bool:
    head_id = getattr(head, "id", None)
    tail_id = getattr(tail, "id", None)
    return bool(
        head_id
        and tail_id
        and getattr(rel, "head_id", None) == head_id
        and getattr(rel, "tail_id", None) == tail_id
    )


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
                    "relation_ids": [r.id for r in path.relations if r.id],
                    "length": path.length,
                    "signature": path_signature(path.nodes, path.relations),
                },
                citations=[Citation(entity_id=n.id, span=n.name) for n in path.nodes],
                metadata={"rank": i},
            )
        )
    return out
