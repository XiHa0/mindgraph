"""从检索到的子图生成结构化回答。

LLMAnswerer 即"被评测的问答 agent"的最小形态——评测时用它产出回答交给 judge。
答题模型可与抽取/judge 不同；默认复用抽取链路的客户端无所谓，关键是 judge 必须独立。
"""
from __future__ import annotations

import abc
from pathlib import Path

from ..llm import LLM
from .retriever import RetrievedContext

_PROMPT_DIR = Path(__file__).parent / "prompts"


class Answerer(abc.ABC):
    @abc.abstractmethod
    async def answer(self, question: str, context: RetrievedContext) -> str: ...


class LLMAnswerer(Answerer):
    def __init__(self, llm: LLM):
        self._llm = llm

    async def answer(self, question: str, context: RetrievedContext) -> str:
        tmpl = (_PROMPT_DIR / "answer.md").read_text(encoding="utf-8-sig")
        user = tmpl.replace("{context}", context.render()).replace("{question}", question)
        system = "你只依据给定图谱上下文作答，只输出严格 JSON。"
        raw = await self._llm.complete_json(system, user, meta={"task": "answer",
                                                                "question": question})
        return (raw.get("answer") or "").strip()
