"""覆盖率评测与差异诊断（SPEC §2）。

这两个组件是**真实可复用**的（不是 stub）：给定任意 GraphStore 与冻结 gold，
精确计算 node_cov / edge_cov 并把差异归类。answer_score 通过可注入的打分器获得——
默认是代理值；真实场景注入 judge 模型按 rubric 打分（SPEC §3.4）。
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from ..models import TestItem
from .interfaces import Evaluator, Diagnoser, GraphStore, RoundMetrics, Diagnosis

# answer_scorer: (reasoning gold, store) -> 0..1
AnswerScorer = Callable[[list[TestItem], GraphStore], Awaitable[float]]


class CoverageEvaluator(Evaluator):
    """node/edge 覆盖率是**确定性硬指标**，永远精确计算。

    answer_score（reasoning 题的结构化回答质量）有三种来源（双模式）：
      - agent 驱动模式：注入 None → 用覆盖率代理值占位，由 agent 另行抽样判分覆盖。
      - 自治模式：注入 JudgeAnswerScorer(API judge) → 真实 rubric 打分。
    代理值 = mean(node_cov, edge_cov)：表示"图是否撑得起回答"，让无 judge 时循环也能收敛，
    且不会用 0 假分卡死，也不会用 1 假分蒙混。真实判分以注入的 scorer 为准。
    """
    def __init__(self, answer_scorer: Optional[AnswerScorer] = None):
        self._answer_scorer = answer_scorer

    async def evaluate(self, gold: list[TestItem], store: GraphStore,
                       round_index: int) -> RoundMetrics:
        node_total = node_hit = 0
        edge_total = edge_hit = 0
        for it in gold:
            if it.type != "extraction" or not it.gold_subgraph:
                continue
            for n in it.gold_subgraph.nodes:
                node_total += 1
                if store.has_node(n.name):
                    node_hit += 1
            for e in it.gold_subgraph.edges:
                edge_total += 1
                if store.has_edge(e.from_, e.type, e.to):
                    edge_hit += 1

        node_cov = node_hit / node_total if node_total else 1.0
        edge_cov = edge_hit / edge_total if edge_total else 1.0

        reasoning_gold = [it for it in gold if it.type == "reasoning"]
        if not reasoning_gold:
            answer_score = 1.0
        elif self._answer_scorer is not None:
            answer_score = await self._answer_scorer(reasoning_gold, store)
        else:
            answer_score = (node_cov + edge_cov) / 2  # 覆盖率代理（见类 docstring）

        return RoundMetrics(round_index=round_index, node_cov=node_cov,
                            edge_cov=edge_cov, answer_score=answer_score)


class GapDiagnoser(Diagnoser):
    """把 gold 与图的差异归类（SPEC §2.2）。

    base 实现可精确判定 missing_node / missing_edge；wrong_edge / granularity 需要
    "图里实际抽到了什么"的语义比对，留给注入式增强（真实实现可对接 judge）。
    """
    def diagnose(self, gold: list[TestItem], store: GraphStore) -> Diagnosis:
        from collections import Counter
        d = Diagnosis()
        mnt: Counter = Counter()      # 漏抽节点类型
        met: Counter = Counter()      # 漏连关系类型
        for it in gold:
            if it.type != "extraction" or not it.gold_subgraph:
                continue
            name2type = {n.name: n.type for n in it.gold_subgraph.nodes}
            present = {n.name: store.has_node(n.name) for n in it.gold_subgraph.nodes}
            for n in it.gold_subgraph.nodes:
                if not present[n.name]:
                    d.missing_node += 1
                    mnt[n.type] += 1
            for e in it.gold_subgraph.edges:
                if not (present.get(e.from_, False) and present.get(e.to, False)):
                    continue  # 端点本身缺，归 missing_node，不重复计
                if not store.has_edge(e.from_, e.type, e.to):
                    d.missing_edge += 1
                    met[e.type] += 1
                    d.missing_edge_patterns.append(
                        (name2type.get(e.from_, "?"), e.type, name2type.get(e.to, "?")))
        d.missing_node_types = dict(mnt)
        d.missing_edge_types = dict(met)
        if d.missing_edge > d.missing_node and d.missing_edge > 0:
            d.notes.append("关系系统性丢失：强化第二阶段关系抽取 few-shot")
        elif d.missing_node > 0:
            d.notes.append("节点漏读：检查切分粒度 / 抽取 prompt 覆盖的节点类型")
        return d
