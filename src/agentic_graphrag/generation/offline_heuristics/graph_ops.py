"""EdgeView helpers for offline multi-hop answer heuristics."""

from __future__ import annotations


class EdgeView:
    """Thin wrapper over (head, rel, tail) edges with small query helpers."""

    def __init__(self, edges: list[tuple[str, str, str]]) -> None:
        self._edges = edges

    def find_edges(self, rel: str) -> list[tuple[str, str]]:
        rel_u = rel.upper()
        return [(h, t) for h, r, t in self._edges if r == rel_u]

    @staticmethod
    def related_to(name_sub: str, node: str) -> bool:
        return name_sub.lower() in node.lower() or node.lower() in name_sub.lower()

    def parents_of(self, company_hints: list[str]) -> set[str]:
        parents: set[str] = set()
        for h, t in self.find_edges("PARENT_OF"):
            if any(self.related_to(c, t) for c in company_hints):
                parents.add(h)
        for h, t in self.find_edges("SUBSIDIARY_OF"):
            if any(self.related_to(c, h) for c in company_hints):
                parents.add(t)
        return parents

    def ceos_of(self, companies: set[str] | list[str]) -> list[str]:
        out: list[str] = []
        for h, t in self.find_edges("CEO_OF"):
            if any(self.related_to(c, t) for c in companies):
                out.append(h)
        return out

    def join_unique(self, items: list[str]) -> str:
        return " and ".join(dict.fromkeys(items))
