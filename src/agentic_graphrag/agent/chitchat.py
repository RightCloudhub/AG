"""Short-circuit friendly replies for greetings and capability questions.

Avoids running multi-hop retrieval for small-talk / meta prompts that would
otherwise end as ``无法基于现有知识回答``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from uuid import uuid4

from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain

_GREETING_MAX_LEN = 40
_CAPABILITY_MAX_LEN = 80

# Pure greetings (whole-string match after strip / punctuation strip).
_GREETING_RE = re.compile(
    r"^(?:"
    r"hi|hello|hey|howdy|yo|"
    r"good\s*(?:morning|afternoon|evening|day)|"
    r"你好|您好|嗨|哈喽|嘿|早|早上好|下午好|晚上好|午安|"
    r"在吗|在不在|你好啊|你好呀"
    r")[\s!！。.?？~～]*$",
    re.I,
)

# Capability / self-intro questions.
_CAPABILITY_RE = re.compile(
    r"(?:"
    r"what\s+can\s+you\s+(?:do|answer|help|handle)|"
    r"what\s+do\s+you\s+(?:do|know|support)|"
    r"how\s+can\s+you\s+help|"
    r"who\s+are\s+you|"
    r"what\s+are\s+you|"
    r"your\s+capabilities?|"
    r"help\s*me\s*$|"
    r"^help[\s!！.？?]*$|"
    r"你可以回答什么|你能回答什么|你会回答什么|"
    r"你能做什么|你可以做什么|你会做什么|"
    r"你有什么功能|你的功能|你会什么|"
    r"介绍一下你自己|你是谁|你是什么|"
    r"怎么用你|如何使用|能帮我什么"
    r")",
    re.I,
)

_GREETING_ANSWER = (
    "你好！我是 AgenticGraphRAG，一个基于知识图谱的多跳问答助手。\n\n"
    "你可以问我图谱中的实体关系问题，例如公司母公司、CEO、供应链等。"
    "试试：「Apex Holdings 的 CEO 是谁？」或 "
    "「BrightLink Logistics 的母公司的 CEO 是谁？」"
)

_CAPABILITY_ANSWER = (
    "我是 AgenticGraphRAG，专注于知识图谱多跳推理问答。\n\n"
    "我可以帮你：\n"
    "1. 单跳事实：某公司的 CEO、母公司、产品、供应商等\n"
    "2. 多跳关系：例如「子公司的母公司的 CEO」「竞品的生产商」\n"
    "3. 可审计推理：展示子问题分解、检索证据与图路径\n\n"
    "示例问题：\n"
    "· Who is the CEO of Apex Holdings?\n"
    "· Who is the CEO of the parent company of BrightLink Logistics?\n"
    "· What is the parent company of NovaTech Industries?\n\n"
    "直接输入你的问题即可；开启「强制 Agentic」会走完整多跳规划。"
)


class ChitchatKind(StrEnum):
    GREETING = "greeting"
    CAPABILITY = "capability"


def match_chitchat(question: str) -> ChitchatKind | None:
    """Return chitchat kind if the question is greeting/capability meta; else None."""
    q = (question or "").strip()
    if not q:
        return None
    compact = re.sub(r"\s+", " ", q)
    if len(compact) <= _GREETING_MAX_LEN and _GREETING_RE.match(compact):
        return ChitchatKind.GREETING
    if len(compact) <= _CAPABILITY_MAX_LEN and _CAPABILITY_RE.search(compact):
        return ChitchatKind.CAPABILITY
    return None


def try_chitchat_answer(question: str) -> ReasoningChain | None:
    """Build a friendly answered chain, or None when not chitchat."""
    kind = match_chitchat(question)
    if kind is None:
        return None
    text = _GREETING_ANSWER if kind == ChitchatKind.GREETING else _CAPABILITY_ANSWER
    return ReasoningChain(
        query_id=str(uuid4()),
        question=question.strip(),
        route="chitchat",
        answer=text,
        status=QueryStatus.ANSWERED,
        claims=[],
        steps=[],
        metadata={
            "chitchat": kind.value,
            "confidence": {"level": "high", "score": 1.0, "reasons": ["chitchat"]},
        },
    )
