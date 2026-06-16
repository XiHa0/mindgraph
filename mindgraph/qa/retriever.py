"""子图检索：把问题映射到入口节点，扩展固定形状的子图，渲染成文本上下文。

base 实现用字符 bigram 重合做入口召回（中文友好、无需 embedding）。
生产可换成 schema/constraints.cypher 里的全文/向量索引召回 + 按 SPEC §1.3 脊柱扩展。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..builder.interfaces import GraphReader


def _bigrams(s: str) -> set[str]:
    s = s.replace(" ", "")
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def _overlap(q: str, name: str) -> float:
    if name and name in q:
        return 1.0
    a, b = _bigrams(q), _bigrams(name)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class RetrievedContext:
    seeds: list[str] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)

    def render(self) -> str:
        if not self.nodes:
            return "（图中未检索到相关内容）"
        node_block = "、".join(self.nodes)
        if self.edges:
            edge_block = "\n".join(f"- {a} —{t}→ {b}" for a, t, b in self.edges)
        else:
            edge_block = "（无关系边）"
        return f"相关节点：{node_block}\n关系：\n{edge_block}"


class Retriever(abc.ABC):
    @abc.abstractmethod
    async def retrieve(self, question: str) -> RetrievedContext: ...


class KeywordGraphRetriever(Retriever):
    def __init__(self, reader: GraphReader, max_seeds: int = 4, hops: int = 1,
                 min_score: float = 0.12):
        self._reader = reader
        self._max_seeds = max_seeds
        self._hops = hops
        self._min_score = min_score

    async def retrieve(self, question: str) -> RetrievedContext:
        names = self._reader.node_names()
        scored = sorted(
            ((n, _overlap(question, n)) for n in names),
            key=lambda x: x[1], reverse=True)
        seeds = [n for n, s in scored if s >= self._min_score][: self._max_seeds]

        nodes: set[str] = set(seeds)
        edges: set[tuple[str, str, str]] = set()
        frontier = set(seeds)
        for _ in range(self._hops):
            nxt: set[str] = set()
            for n in frontier:
                for (a, t, b) in self._reader.incident_edges(n):
                    edges.add((a, t, b))
                    other = b if a == n else a
                    if other not in nodes:
                        nxt.add(other)
                    nodes.update((a, b))
            frontier = nxt
            if not frontier:
                break

        return RetrievedContext(seeds=seeds, nodes=sorted(nodes), edges=sorted(edges))
