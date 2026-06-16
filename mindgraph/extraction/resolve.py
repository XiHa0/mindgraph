"""概念归一化 / 实体消歧（SPEC §4 要点）。

思想文档里作者常用不同说法指同一概念。若不合并，图里全是重复节点、子图断裂——
这是思想类 KG 比普通 KG 更难之处。

base 实现：用**并查集**把"同名 + 互为 alias"的表面说法并到一组，确定性合并。
即使别名节点是在另一个片段里被独立抽出的，也能正确归并。
可选增强：对 key 不同但语义相近的节点做 embedding/LLM 二次合并（留 hook，未默认开启）。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any


def norm_key(name: str) -> str:
    """归一化 key：去空白、去常见标点、转小写。"""
    s = name.strip().lower()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[“”\"'《》()（）【】\[\],，。.、:：;；!！?？]", "", s)
    return s


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def resolve_entities(raw_nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """把抽取出的原始节点合并为 canonical 节点。

    Args:
        raw_nodes: [{type, name, summary?, aliases?, source_chunk_id?, confidence?}]
    Returns:
        (canonical_nodes, alias_map)
        canonical_nodes: 合并后的节点列表（带 id/name/type/aliases/source_chunk_ids/...）
        alias_map: norm_key(任意表面说法) -> canonical name，供关系抽取阶段解析端点
    """
    uf = _UnionFind()
    # 1) 用 name↔alias 关系把表面 key 并起来
    for n in raw_nodes:
        nk = norm_key(n["name"])
        uf.find(nk)
        for a in n.get("aliases", []) or []:
            uf.union(nk, norm_key(a))

    # 2) 按并查集根分组
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in raw_nodes:
        groups[uf.find(norm_key(n["name"]))].append(n)

    canonical_nodes: list[dict[str, Any]] = []
    alias_map: dict[str, str] = {}

    for root, members in groups.items():
        surfaces = [m["name"] for m in members]
        declared_aliases = [a for m in members for a in (m.get("aliases") or [])]
        # canonical：出现最多的真实表面说法（不含仅作为 alias 出现的）
        canonical_name = Counter(surfaces).most_common(1)[0][0]
        ntype = Counter(m.get("type", "Concept") for m in members).most_common(1)[0][0]

        all_surface = set(surfaces) | set(declared_aliases)
        aliases = sorted({s for s in all_surface if norm_key(s) != norm_key(canonical_name)})
        chunk_ids = sorted({c for m in members for c in
                            ([m["source_chunk_id"]] if m.get("source_chunk_id") else [])})
        confs = [float(m["confidence"]) for m in members if m.get("confidence") is not None]
        summary = next((m.get("summary") for m in members if m.get("summary")), "")

        canonical_nodes.append({
            "id": f"{ntype}:{norm_key(canonical_name)}",
            "type": ntype,
            "name": canonical_name,
            "aliases": aliases,
            "summary": summary,
            "source_chunk_ids": chunk_ids,
            "author_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        })

        alias_map[norm_key(canonical_name)] = canonical_name
        for a in aliases:
            alias_map[norm_key(a)] = canonical_name

    return canonical_nodes, alias_map
