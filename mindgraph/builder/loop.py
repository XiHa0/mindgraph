"""Builder 循环编排（SPEC §4）+ 收敛判定（SPEC §2.1）。

注意：
  - meta-layer 冻结、domain-layer 才循环（否则不收敛）。
  - 测试集冻结、循环里只变抽取（否则覆盖率曲线无意义）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import Chunk, TestItem
from .interfaces import (
    Chunker, SchemaInducer, TestsetProvider, GraphStore, Extractor,
    Evaluator, Diagnoser, Reviser,
    DomainSchema, ExtractionConfig, RoundMetrics,
)


@dataclass
class ConvergenceCriteria:
    """SPEC §2.1。"""
    node_cov: float = 0.85
    edge_cov: float = 0.75
    answer_score: float = 0.80
    plateau_eps: float = 0.02     # 连续 2 轮 total_score 提升 < eps 即停
    max_rounds: int = 8

    def thresholds_met(self, m: RoundMetrics) -> bool:
        return (m.node_cov >= self.node_cov
                and m.edge_cov >= self.edge_cov
                and m.answer_score >= self.answer_score)


@dataclass
class BuildResult:
    converged: bool
    stop_reason: str
    history: list[RoundMetrics] = field(default_factory=list)
    schema: Optional[DomainSchema] = None
    config: Optional[ExtractionConfig] = None

    def to_dict(self) -> dict:
        return {
            "converged": self.converged,
            "stop_reason": self.stop_reason,
            "rounds": [m.to_dict() for m in self.history],
            "final_schema_concepts": len(self.schema.concepts) if self.schema else 0,
        }


class BuilderLoop:
    def __init__(self, *, chunker: Chunker, inducer: SchemaInducer,
                 testset: TestsetProvider, store: GraphStore, extractor: Extractor,
                 evaluator: Evaluator, diagnoser: Diagnoser, reviser: Reviser,
                 criteria: ConvergenceCriteria | None = None, logger=None):
        self.chunker = chunker
        self.inducer = inducer
        self.testset = testset
        self.store = store
        self.extractor = extractor
        self.evaluator = evaluator
        self.diagnoser = diagnoser
        self.reviser = reviser
        self.criteria = criteria or ConvergenceCriteria()
        if logger is None:
            from .._log import logger as _logger
            logger = _logger
        self.log = logger

    async def run(self, doc_text: str, doc_id: str) -> BuildResult:
        c = self.criteria

        # step 0：切分（一次性）
        chunks: list[Chunk] = await self.chunker.split(doc_text, doc_id)
        self.log.info(f"[loop] 切分得到 {len(chunks)} 个 chunk")

        # step 1：schema 归纳（domain-layer 初值）
        schema: DomainSchema = await self.inducer.induce(chunks)
        self.log.info(f"[loop] 归纳 domain 概念 {len(schema.concepts)} 个")

        # step 2：加载冻结的 gold 测试集
        gold: list[TestItem] = self.testset.load()
        if len(gold) < 100:
            self.log.warning(f"[loop] gold 仅 {len(gold)} 条 < 100，覆盖率统计可信度下降")

        config = ExtractionConfig()
        history: list[RoundMetrics] = []

        for r in range(1, c.max_rounds + 1):
            # step 3：重抽（每轮从干净图开始，避免历史残留干扰评测）
            self.store.reset()
            await self.extractor.extract(chunks, schema, config, self.store)

            # step 4：评测
            metrics = await self.evaluator.evaluate(gold, self.store, r)
            # step 5：诊断
            metrics.diagnosis = self.diagnoser.diagnose(gold, self.store)
            history.append(metrics)
            self.log.info(
                f"[loop] 第{r}轮 node={metrics.node_cov:.2f} edge={metrics.edge_cov:.2f} "
                f"ans={metrics.answer_score:.2f} 主因={metrics.diagnosis.dominant()}")

            # 收敛判定
            stop, reason = self._should_stop(history, r)
            if stop:
                return BuildResult(converged=c.thresholds_met(metrics), stop_reason=reason,
                                   history=history, schema=schema, config=config)

            # step 6：修正 → 下一轮
            schema, config = await self.reviser.revise(metrics.diagnosis, schema, config)

        return BuildResult(converged=False, stop_reason="reached_max_rounds",
                           history=history, schema=schema, config=config)

    def _should_stop(self, history: list[RoundMetrics], r: int) -> tuple[bool, str]:
        c = self.criteria
        m = history[-1]
        if c.thresholds_met(m):
            # 阈值达标后，还要求最近一轮提升很小（已平台期）才停，避免过早停在刚好达标
            if len(history) >= 2:
                delta = m.total_score - history[-2].total_score
                if delta < c.plateau_eps:
                    return True, "converged_thresholds_and_plateau"
            else:
                return True, "converged_thresholds"
        # 阈值未达标，但已平台期（连续 2 轮几乎不涨）→ 提前止损
        if len(history) >= 3:
            d1 = history[-1].total_score - history[-2].total_score
            d2 = history[-2].total_score - history[-3].total_score
            if d1 < c.plateau_eps and d2 < c.plateau_eps:
                return True, "plateau_below_threshold"
        if r >= c.max_rounds:
            return True, "reached_max_rounds"
        return False, ""
