from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence


WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*|[0-9]+|[\u4e00-\u9fff]+")
IDENTIFIER_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
CJK_ALIASES = {
    "自我进化": ("self", "evolution", "evolve"),
    "进化": ("evolution", "evolve"),
    "记忆": ("memory",),
    "晋级": ("promote", "promotion", "verify"),
    "工作流": ("workflow",),
    "工具": ("tool",),
    "策略": ("policy",),
    "沙箱": ("sandbox",),
    "追踪": ("trace", "tracing"),
    "评测": ("eval", "evaluation"),
    "代理": ("agent",),
    "上下文": ("context",),
    "路由": ("route", "router"),
    "测试": ("test", "pytest"),
    "配置": ("config", "configuration"),
    "检索": ("search", "index"),
    "搜索": ("search",),
    "文件": ("file",),
}


def text_tokens(text: str) -> set[str]:
    """Return language-aware lexical tokens without external dependencies."""

    tokens: set[str] = set()
    for raw in WORD_RE.findall(text or ""):
        if raw[0].isascii():
            pieces = IDENTIFIER_BOUNDARY_RE.sub(r"\1 \2", raw).replace("_", " ").replace("-", " ").split()
            tokens.update(piece.lower() for piece in pieces if piece)
            normalized = raw.lower()
            if len(normalized) > 2:
                tokens.add(normalized)
            continue
        chars = list(raw)
        if len(chars) == 1:
            tokens.add(raw)
            continue
        for size in range(2, min(4, len(chars)) + 1):
            tokens.update("".join(chars[index : index + size]) for index in range(len(chars) - size + 1))
    normalized_text = "".join((text or "").lower().split())
    for phrase, aliases in CJK_ALIASES.items():
        if phrase in normalized_text:
            tokens.update(aliases)
    return tokens


def relevance_score(query: str, document: str) -> float:
    """Score lexical relevance for English identifiers and unsegmented CJK text."""

    query_text = " ".join((query or "").lower().split())
    document_text = " ".join((document or "").lower().split())
    if not query_text or not document_text:
        return 0.0
    query_tokens = text_tokens(query_text)
    document_tokens = text_tokens(document_text)
    overlap = query_tokens & document_tokens
    if not overlap:
        return 4.0 if query_text in document_text else 0.0
    coverage = len(overlap) / max(1, len(query_tokens))
    precision = len(overlap) / max(1, len(document_tokens))
    exact = 4.0 if query_text in document_text else 0.0
    return exact + coverage * 3.0 + math.sqrt(precision) + min(2.0, len(overlap) * 0.15)


def rank_texts(query: str, documents: Iterable[str]) -> list[tuple[float, int]]:
    ranked = [(relevance_score(query, document), index) for index, document in enumerate(documents)]
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item for item in ranked if item[0] > 0]


def bounded_sections(
    sections: Sequence[tuple[str, str, int]],
    *,
    max_chars: int,
    section_min_chars: int = 120,
) -> str:
    """Render weighted prompt sections inside one deterministic character budget."""

    max_chars = max(0, int(max_chars))
    active = [(title.strip(), str(content).strip(), max(1, int(weight))) for title, content, weight in sections if str(content).strip()]
    if not active or max_chars <= 0:
        return ""
    headers = [f"{title}:\n" for title, _, _ in active]
    separators = 2 * max(0, len(active) - 1)
    content_budget = max(0, max_chars - sum(len(header) for header in headers) - separators)
    if content_budget <= 0:
        return "\n\n".join(headers)[:max_chars]

    minimum = min(max(0, int(section_min_chars)), content_budget // len(active))
    allocations = [minimum for _ in active]
    remaining = content_budget - minimum * len(active)
    shares = [int(remaining * weight / sum(item[2] for item in active)) for _, _, weight in active]
    if shares:
        shares[0] += remaining - sum(shares)
    allocations = [base + share for base, share in zip(allocations, shares)]

    rendered: list[str] = []
    for header, (_, content, _), allocation in zip(headers, active, allocations):
        if len(content) > allocation:
            marker = "\n[TRUNCATED]"
            content = content[: max(0, allocation - len(marker))] + marker if allocation >= len(marker) else content[:allocation]
        rendered.append(header + content)
    return "\n\n".join(rendered)[:max_chars]
