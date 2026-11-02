"""Small BeautifulSoup Markdown renderer for extraction fallback."""

from __future__ import annotations

import re

from bs4 import Comment, Doctype, NavigableString, PageElement, Tag

_BLOCKS = frozenset({"p", "div", "main", "article", "section", "header", "body", "blockquote"})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
MIN_FENCE_LENGTH = 3


def render_html(node: PageElement) -> str:
    if isinstance(node, (Comment, Doctype)):
        return ""
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    if node.name == "table":
        return "\n\n" + _table(node) + "\n\n"
    if node.name == "pre":
        return "\n\n" + _code(node) + "\n\n"
    if node.name in {"ul", "ol"}:
        return "\n\n" + _list(node) + "\n\n"
    return _element(node, "".join(render_html(child) for child in node.children))


def _element(node: Tag, content: str) -> str:
    if node.name in _HEADINGS:
        return f"\n\n{'#' * int(node.name[1])} {content.strip()}\n\n"
    if node.name in _BLOCKS:
        return f"\n\n{content.strip()}\n\n"
    if node.name == "br":
        return "\n"
    if node.name == "hr":
        return "\n\n---\n\n"
    if node.name == "code":
        return f"`{content}`"
    if node.name in {"b", "strong"}:
        return f"**{content.strip()}**"
    if node.name in {"i", "em"}:
        return f"*{content.strip()}*"
    return content


def _table(table: Tag) -> str:
    rows = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        rows.append([cell.get_text(" ", strip=True).replace("|", r"\|") for cell in cells])
    rows = [row for row in rows if row]
    if not rows:
        return ""
    width = max(map(len, rows))
    padded = [row + [""] * (width - len(row)) for row in rows]
    padded.insert(1, ["---"] * width)
    return "\n".join("| " + " | ".join(row) + " |" for row in padded)


def _code(node: Tag) -> str:
    text = node.get_text().rstrip("\n")
    runs = [len(match.group()) for match in re.finditer(r"`+", text)]
    fence = "`" * max(MIN_FENCE_LENGTH, max(runs, default=0) + 1)
    return f"{fence}\n{text}\n{fence}"


def _list(node: Tag) -> str:
    items = []
    for index, item in enumerate(node.find_all("li", recursive=False), start=1):
        content = "".join(render_html(child) for child in item.children).strip()
        marker = f"{index}. " if node.name == "ol" else "- "
        lines = content.splitlines()
        if lines:
            items.append(marker + lines[0] + "".join("\n    " + line for line in lines[1:]))
    return "\n".join(items)
