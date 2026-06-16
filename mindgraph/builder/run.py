"""Builder 循环 CLI（演示用，全 stub，离线）。

  # 用交付3 冻结的 gold 跑一遍循环，看收敛曲线：
  python -m mindgraph.builder.run --gold ./_kg_out/testset.gold.json

真实运行时把 stub 换成：语义切分 / judge 归纳 / DeepSeek 两阶段抽取 / Neo4jGraphStore /
judge 打分（注入 CoverageEvaluator 的 answer_scorer）。
"""
from __future__ import annotations

import argparse
import asyncio
import json

from ..models import TestItem
from .interfaces import GraphStore
from .loop import BuilderLoop, ConvergenceCriteria
from .coverage import CoverageEvaluator, GapDiagnoser
from .stubs import (
    InMemoryGraphStore, FileTestsetProvider, StubInducer,
    SimulatedExtractor, StubReviser,
)
from ..chunking.structural_chunker import StructuralChunker


def _demo_doc() -> str:
    secs = ["导论", "核心概念", "方法论", "常见误区", "应用案例", "总结"]
    parts = []
    for s in secs:
        parts.append(f"# {s}")
        for j in range(6):
            parts.append(f"（{s} 第{j + 1}段）作者论述了一个论断，给出论据、适用条件与示例。")
    return "\n\n".join(parts)


def _make_demo_answer_scorer(gold: list[TestItem]):
    """演示用 answer_scorer：用抽取覆盖的代理值模拟 judge 打分（真实场景换 judge 模型）。"""
    node_universe: list[str] = []
    for it in gold:
        if it.type == "extraction" and it.gold_subgraph:
            node_universe.extend(n.name for n in it.gold_subgraph.nodes)
    node_universe = list(dict.fromkeys(node_universe))

    async def scorer(reasoning_gold: list[TestItem], store: GraphStore) -> float:
        if not node_universe:
            return 1.0
        hit = sum(1 for n in node_universe if store.has_node(n))
        return hit / len(node_universe)

    return scorer


def main() -> int:
    ap = argparse.ArgumentParser(prog="mindgraph.builder.run")
    ap.add_argument("--gold", required=True, help="冻结的 gold 测试集 json")
    ap.add_argument("--max-rounds", type=int, default=8)
    args = ap.parse_args()

    provider = FileTestsetProvider(args.gold)
    gold = provider.load()

    loop = BuilderLoop(
        chunker=StructuralChunker(),
        inducer=StubInducer(),
        testset=provider,
        store=InMemoryGraphStore(),
        extractor=SimulatedExtractor(gold),
        evaluator=CoverageEvaluator(answer_scorer=_make_demo_answer_scorer(gold)),
        diagnoser=GapDiagnoser(),
        reviser=StubReviser(),
        criteria=ConvergenceCriteria(max_rounds=args.max_rounds),
    )

    result = asyncio.run(loop.run(_demo_doc(), doc_id="demo"))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n停止原因：{result.stop_reason}　收敛：{result.converged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
