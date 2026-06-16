"""Agent 驱动工具箱（默认模式）。

编排大脑 = coding agent（Claude Code/Codex）。本 CLI 把每个**确定性步骤**暴露成命令，
各步读写 JSON，agent 逐步运行并读结果决策（改 schema/抽取策略/是否再抽一轮）。
只有 `extract` 调 worker 模型（DeepSeek，唯一必需 key）。

典型 agent 驱动一轮：
  python -m mindgraph.cli chunk    --doc doc.txt --out chunks.json
  # （agent 读 chunks/写 schema.json: {"concepts":[...],"aliases":{...}}）
  python -m mindgraph.cli extract  --chunks chunks.json --schema schema.json \
                                    --store memory --graph-out graph.json
  python -m mindgraph.cli coverage --gold gold.json --graph graph.json --out coverage.json
  python -m mindgraph.cli diagnose --gold gold.json --graph graph.json --out diag.json
  # agent 读 coverage.json + diag.json → 决定改什么、要不要再来一轮

自治模式（可选）：见 mindgraph.builder.run（注入 API judge 无人值守跑）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .models import Chunk
from .builder.interfaces import DomainSchema, ExtractionConfig
from .builder.stubs import InMemoryGraphStore
from .builder.coverage import CoverageEvaluator, GapDiagnoser
from .testset import review


def _load_chunks(path: str) -> list[Chunk]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return [Chunk.from_dict(d) for d in data]


def _load_schema(path: str | None) -> DomainSchema:
    if not path:
        return DomainSchema()
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return DomainSchema(concepts=d.get("concepts", []), aliases=d.get("aliases", {}),
                        notes=d.get("notes", ""))


def _load_store(args):
    """从 graph.json（memory）或 Neo4j 载入只读图。"""
    if getattr(args, "graph", None):
        return InMemoryGraphStore.from_json(args.graph)
    from .extraction.neo4j_store import Neo4jGraphStore
    return Neo4jGraphStore(doc_id=args.doc_id)


# ---------------------------------------------------------------------------
def cmd_chunk(args) -> int:
    from .chunking.structural_chunker import StructuralChunker
    text = Path(args.doc).read_text(encoding="utf-8-sig")
    chunks = asyncio.run(StructuralChunker(max_chars=args.max_chars).split(text, args.doc_id))
    Path(args.out).write_text(
        json.dumps([c.to_dict() for c in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
    sections = sorted({c.section for c in chunks})
    print(f"切分 {len(chunks)} chunk，{len(sections)} 节 → {args.out}")
    return 0


def cmd_extract(args) -> int:
    from .llm import extraction_llm
    from .extraction.extractor import TwoStageExtractor
    chunks = _load_chunks(args.chunks)
    schema = _load_schema(args.schema)
    if args.store == "memory":
        store = InMemoryGraphStore()
    else:
        from .extraction.neo4j_store import Neo4jGraphStore
        store = Neo4jGraphStore(doc_id=args.doc_id)
        store.reset()
    asyncio.run(TwoStageExtractor(extraction_llm()).extract(
        chunks, schema, ExtractionConfig(), store))
    if args.store == "memory":
        store.to_json(args.graph_out)
        nodes = len(store.node_names())
        print(f"抽取完成 → {args.graph_out}（节点 {nodes}）")
    else:
        print(f"抽取完成 → Neo4j doc={args.doc_id}（节点 {len(store.node_names())}）")
    return 0


def cmd_coverage(args) -> int:
    gold = review.load_json(args.gold)
    store = _load_store(args)
    metrics = asyncio.run(CoverageEvaluator().evaluate(gold, store, round_index=args.round))
    out = metrics.to_dict()
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"node_cov={metrics.node_cov:.3f} edge_cov={metrics.edge_cov:.3f} "
          f"answer(代理)={metrics.answer_score:.3f} → {args.out}")
    return 0


def cmd_diagnose(args) -> int:
    gold = review.load_json(args.gold)
    store = _load_store(args)
    diag = GapDiagnoser().diagnose(gold, store)
    out = {
        "missing_node": diag.missing_node, "missing_edge": diag.missing_edge,
        "wrong_edge": diag.wrong_edge, "granularity": diag.granularity,
        "dominant": diag.dominant(),
        "missing_node_types": diag.missing_node_types,
        "missing_edge_types": diag.missing_edge_types,
        "missing_edge_patterns": [list(p) for p in diag.missing_edge_patterns[:20]],
        "notes": diag.notes,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"主因={diag.dominant()} 漏节点={diag.missing_node} 漏关系={diag.missing_edge} → {args.out}")
    print("→ agent 据此决定：改 schema 概念表 / 加抽取指令 / 调切分 / 是否再抽一轮")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="mindgraph.cli", description="MindGraph agent 驱动工具箱")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chunk", help="结构化切分文档")
    c.add_argument("--doc", required=True)
    c.add_argument("--doc-id", default="doc")
    c.add_argument("--max-chars", type=int, default=900)
    c.add_argument("--out", default="chunks.json")
    c.set_defaults(func=cmd_chunk)

    e = sub.add_parser("extract", help="两阶段抽取(DeepSeek)→图")
    e.add_argument("--chunks", required=True)
    e.add_argument("--schema", default=None, help="domain 概念表 json（agent 写）")
    e.add_argument("--store", choices=["memory", "neo4j"], default="memory")
    e.add_argument("--graph-out", default="graph.json", help="memory 模式输出")
    e.add_argument("--doc-id", default="doc")
    e.set_defaults(func=cmd_extract)

    v = sub.add_parser("coverage", help="算覆盖率(确定性)")
    v.add_argument("--gold", required=True)
    v.add_argument("--graph", default=None, help="memory 图 json；不给则用 --store neo4j")
    v.add_argument("--doc-id", default="doc")
    v.add_argument("--round", type=int, default=1)
    v.add_argument("--out", default="coverage.json")
    v.set_defaults(func=cmd_coverage)

    d = sub.add_parser("diagnose", help="差异诊断(确定性，供 agent 决策)")
    d.add_argument("--gold", required=True)
    d.add_argument("--graph", default=None)
    d.add_argument("--doc-id", default="doc")
    d.add_argument("--out", default="diag.json")
    d.set_defaults(func=cmd_diagnose)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
