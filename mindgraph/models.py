"""MindGraph 数据模型。

与 SPEC.md / testset.schema.json / schema/meta_ontology.json 保持一致。
故意用标准库 dataclass（不引入 pydantic 到这一层），便于 JSON 序列化与离线测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ----------------------------------------------------------------------------
# 原文切片（溯源层，非知识层）
# ----------------------------------------------------------------------------
@dataclass
class Chunk:
    id: str
    text: str
    section: str
    order: int
    doc_id: str
    # 检索/抽取辅助元数据（常见做法：kind/keywords/risk 等），可空
    meta: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Chunk":
        return Chunk(
            id=d["id"],
            text=d["text"],
            section=d.get("section", ""),
            order=int(d.get("order", 0)),
            doc_id=d.get("doc_id", ""),
            meta=dict(d.get("meta", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------------
# 测试集（gold）
# ----------------------------------------------------------------------------
@dataclass
class GoldNode:
    type: str
    name: str


@dataclass
class GoldEdge:
    from_: str  # 序列化为 "from"
    type: str
    to: str


@dataclass
class GoldSubgraph:
    nodes: list[GoldNode] = field(default_factory=list)
    edges: list[GoldEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [{"from": e.from_, "type": e.type, "to": e.to} for e in self.edges],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "GoldSubgraph":
        return GoldSubgraph(
            nodes=[GoldNode(**n) for n in d.get("nodes", [])],
            edges=[GoldEdge(from_=e["from"], type=e["type"], to=e["to"])
                   for e in d.get("edges", [])],
        )


@dataclass
class TestItem:
    """SPEC §3.1 的一条测试。extraction 题用 gold_subgraph；reasoning 题用 gold_elements+rubric。"""
    id: str
    type: str                          # "extraction" | "reasoning"
    section: str
    hops: int                          # 1 | 2 | 3
    question: str
    provenance: list[str] = field(default_factory=list)
    verified: bool = False

    qtype: Optional[str] = None        # reasoning 必填: "explanatory" | "applied"
    gold_subgraph: Optional[GoldSubgraph] = None
    gold_elements: Optional[list[str]] = None
    must_not: Optional[list[str]] = None
    rubric: Optional[dict[str, float]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "section": self.section,
            "hops": self.hops,
            "question": self.question,
            "provenance": self.provenance,
            "verified": self.verified,
        }
        if self.qtype is not None:
            d["qtype"] = self.qtype
        if self.gold_subgraph is not None:
            d["gold_subgraph"] = self.gold_subgraph.to_dict()
        if self.gold_elements is not None:
            d["gold_elements"] = self.gold_elements
        if self.must_not is not None:
            d["must_not"] = self.must_not
        if self.rubric is not None:
            d["rubric"] = self.rubric
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TestItem":
        return TestItem(
            id=d["id"],
            type=d["type"],
            section=d.get("section", ""),
            hops=int(d.get("hops", 1)),
            question=d["question"],
            provenance=list(d.get("provenance", [])),
            verified=bool(d.get("verified", False)),
            qtype=d.get("qtype"),
            gold_subgraph=(GoldSubgraph.from_dict(d["gold_subgraph"])
                           if d.get("gold_subgraph") else None),
            gold_elements=d.get("gold_elements"),
            must_not=d.get("must_not"),
            rubric=d.get("rubric"),
        )
