"""JudgeAnswerScorer：把 检索→回答→评分 组装成 CoverageEvaluator 的 answer_scorer。

用法（接入循环）：
    from mindgraph.qa.scorer import JudgeAnswerScorer
    scorer = JudgeAnswerScorer(reader=store, answerer=LLMAnswerer(answer_llm),
                               judge=RubricJudge(judge_llm))
    evaluator = CoverageEvaluator(answer_scorer=scorer.as_answer_scorer())

返回的可调用对象签名与 CoverageEvaluator 期望一致：(reasoning_gold, store) -> float。
注：store 入参被忽略——scorer 持有自己的 reader/answerer/judge；保留入参是为接口兼容。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..models import TestItem
from ..builder.interfaces import GraphReader, GraphStore
from .retriever import Retriever, KeywordGraphRetriever
from .answerer import Answerer
from .judge import RubricJudge


@dataclass
class ItemResult:
    item_id: str
    qtype: str | None
    weighted: float
    scores: dict[str, float] = field(default_factory=dict)
    answer: str = ""


class JudgeAnswerScorer:
    def __init__(self, *, reader: GraphReader, answerer: Answerer, judge: RubricJudge,
                 retriever: Retriever | None = None, concurrency: int = 4):
        self._retriever = retriever or KeywordGraphRetriever(reader)
        self._answerer = answerer
        self._judge = judge
        self._sem = asyncio.Semaphore(concurrency)
        self.last_results: list[ItemResult] = []   # 供日志/诊断

    async def _score_one(self, item: TestItem) -> ItemResult:
        async with self._sem:
            ctx = await self._retriever.retrieve(item.question)
            answer = await self._answerer.answer(item.question, ctx)
            scores = await self._judge.score(item, answer)
        return ItemResult(item_id=item.id, qtype=item.qtype,
                          weighted=RubricJudge.weighted(item, scores),
                          scores=scores, answer=answer)

    async def __call__(self, reasoning_gold: list[TestItem],
                       store: GraphStore | None = None) -> float:
        if not reasoning_gold:
            return 1.0
        self.last_results = await asyncio.gather(
            *(self._score_one(it) for it in reasoning_gold))
        return sum(r.weighted for r in self.last_results) / len(self.last_results)

    def as_answer_scorer(self):
        """返回 CoverageEvaluator 期望的 (reasoning_gold, store) -> Awaitable[float]。"""
        return self.__call__
