"""离线 stub：让整条 builder 循环不依赖网络/Neo4j 即可空跑，验证编排与收敛逻辑。

需要替换为真实实现的部分（都已标 TODO）：
  StubChunker        → 语义切分（LLM 或规则）
  StubInducer        → schema 归纳（judge 模型）
  SimulatedExtractor → 两阶段抽取（DeepSeek）写入真实 GraphStore
  StubReviser        → 据诊断真正改 prompt / few-shot / 切分
GraphStore 的真实实现见 mindgraph/extraction/neo4j_store.py。
TestsetProvider / InMemoryGraphStore / coverage.py 已是可复用的真实组件。
"""
from __future__ import annotations

import re
from typing import Any

from ..models import Chunk, TestItem
from ..testset import review
from .interfaces import (
    Chunker, SchemaInducer, TestsetProvider, GraphStore, GraphReader, Extractor,
    Reviser, DomainSchema, ExtractionConfig, Diagnosis,
)


# ---------------------------------------------------------------------------
# 真实可用：内存图存储 + 文件测试集提供者
# ---------------------------------------------------------------------------
class InMemoryGraphStore(GraphStore, GraphReader):
    def __init__(self) -> None:
        self._nodes: set[str] = set()
        self._edges: set[tuple[str, str, str]] = set()

    def reset(self) -> None:
        self._nodes.clear()
        self._edges.clear()

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            self._nodes.add(n["name"])

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        for e in edges:
            self._edges.add((e["from"], e["type"], e["to"]))

    def has_node(self, name: str) -> bool:
        return name in self._nodes

    def has_edge(self, frm: str, etype: str, to: str) -> bool:
        return (frm, etype, to) in self._edges

    # --- GraphReader ---
    def node_names(self) -> set[str]:
        return set(self._nodes)

    def incident_edges(self, name: str) -> list[tuple[str, str, str]]:
        return [e for e in self._edges if e[0] == name or e[2] == name]

    # --- JSON 持久化（供 agent 驱动 CLI 在步骤间传递图）---
    def to_json(self, path) -> None:
        import json
        from pathlib import Path
        Path(path).write_text(json.dumps(
            {"nodes": sorted(self._nodes),
             "edges": [list(e) for e in sorted(self._edges)]},
            ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def from_json(path) -> "InMemoryGraphStore":
        import json
        from pathlib import Path
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        s = InMemoryGraphStore()
        s._nodes = set(data.get("nodes", []))
        s._edges = {tuple(e) for e in data.get("edges", [])}
        return s


class FileTestsetProvider(TestsetProvider):
    def __init__(self, gold_path: str):
        self._path = gold_path

    def load(self) -> list[TestItem]:
        return review.load_json(self._path)


# ---------------------------------------------------------------------------
# Stub：LLM 驱动的步骤（待替换）
# ---------------------------------------------------------------------------
class StubChunker(Chunker):
    """按空行分段 + 最近标题归属 section。轻量回退；正式用 chunking.StructuralChunker。"""
    _HEADING = re.compile(r"^\s*(#+\s*|第[\d一二三四五六七八九十]+[章节]\s*)")

    async def split(self, doc_text: str, doc_id: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        section = "正文"
        order = 0
        for para in re.split(r"\n\s*\n", doc_text):
            para = para.strip()
            if not para:
                continue
            if self._HEADING.match(para) and len(para) < 40:
                section = self._HEADING.sub("", para).strip() or section
                continue
            chunks.append(Chunk(id=f"chunk_{len(chunks) + 1:03d}", text=para,
                                section=section, order=order, doc_id=doc_id))
            order += 1
        return chunks


class StubInducer(SchemaInducer):
    """从 chunk 频次粗取概念名占位。TODO：换 judge 模型做 domain-layer 归纳。"""
    async def induce(self, chunks: list[Chunk]) -> DomainSchema:
        sections = sorted({c.section for c in chunks})
        return DomainSchema(concepts=[f"概念::{s}" for s in sections],
                            notes="stub 归纳，仅占位")


class SimulatedExtractor(Extractor):
    """模拟逐轮改进的抽取：按 config.completeness 揭示 gold 的一个**前缀**子集。

    完全是为了让循环跑起来、展示收敛曲线。真实实现：读 chunks+schema，
    DeepSeek 两阶段抽取，写入真实 GraphStore，与 gold 无任何耦合。
    """
    def __init__(self, gold: list[TestItem]):
        nodes: list[str] = []
        edges: list[tuple[str, str, str]] = []
        for it in gold:
            if it.type == "extraction" and it.gold_subgraph:
                nodes.extend(n.name for n in it.gold_subgraph.nodes)
                edges.extend((e.from_, e.type, e.to) for e in it.gold_subgraph.edges)
        # 去重保持顺序
        self._all_nodes = list(dict.fromkeys(nodes))
        self._all_edges = list(dict.fromkeys(edges))

    async def extract(self, chunks: list[Chunk], schema: DomainSchema,
                      config: ExtractionConfig, store: GraphStore) -> None:
        f = max(0.0, min(1.0, config.completeness))
        kn = int(len(self._all_nodes) * f)
        ke = int(len(self._all_edges) * f)
        store.upsert_nodes([{"name": n} for n in self._all_nodes[:kn]])
        store.upsert_edges([{"from": a, "type": t, "to": b}
                            for (a, t, b) in self._all_edges[:ke]])


class StubReviser(Reviser):
    """据诊断把 completeness 往上调，模拟"改 prompt 后抽取变好"。

    TODO：真实实现据 dominant() 决定改 schema / 抽取 prompt / 切分，并产出新 few-shot。
    """
    def __init__(self, step: float = 0.18):
        self._step = step

    async def revise(self, diagnosis: Diagnosis, schema: DomainSchema,
                     config: ExtractionConfig) -> tuple[DomainSchema, ExtractionConfig]:
        dom = diagnosis.dominant()
        # 不同主因模拟不同增益（关系丢失修起来稍慢）
        gain = self._step if dom != "missing_edge" else self._step * 0.8
        config.completeness = min(1.0, config.completeness + gain)
        config.extra["last_fix"] = dom
        return schema, config
