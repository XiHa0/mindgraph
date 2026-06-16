"""GraphStore 的 Neo4j 实现（SPEC §5）。

依赖：`pip install neo4j`，并在 .env 配置 NEO4J_URI/USER/PASSWORD/DATABASE。
建库约束/索引见 mindgraph/schema/constraints.cypher（首次使用前执行一次）。

设计：所有知识节点带 doc_id；reset() 只清理当前 doc，避免误删其它文档的图。
节点写**类型标签 + :KGNode 共享标签**（见 _node_write_queries），MERGE on (id) 幂等；
关系按 (from_name, type, to_name) MERGE。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from ..builder.interfaces import GraphStore, GraphReader

# meta 允许的节点类型（= 合法标签白名单，防 label 注入）
with open(Path(__file__).parent.parent / "schema" / "meta_ontology.json", encoding="utf-8") as _f:
    ALLOWED_NODE_LABELS = set(json.load(_f)["node_types"].keys())


def _slug(s: str) -> str:
    return s.strip().replace(" ", "_")[:120]


def _node_write_queries(nodes: list[dict[str, Any]], doc_id: str):
    """纯函数：把节点按 type 分组，生成"带类型标签 + :KGNode 共享标签"的 MERGE。

    返回 [(cypher, {"rows": [...], "doc": doc_id}), ...]。
    标签来自 meta 白名单（非用户输入），未知类型退化为只写 :KGNode。
    抽出为纯函数以便离线测试（标签不能参数化，必须拼进 cypher，需校验白名单）。
    """
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        ntype = n.get("type", "Concept")
        label = ntype if ntype in ALLOWED_NODE_LABELS else None
        nid = n.get("id") or f"{ntype}:{_slug(n['name'])}"
        row = {"id": nid, "type": ntype, "name": n["name"],
               "props": {k: v for k, v in n.items() if k not in ("type", "id", "name")}}
        by_label[label or "_generic"].append(row)

    queries = []
    for label, rows in by_label.items():
        labels = "KGNode" if label == "_generic" else f"{label}:KGNode"
        cypher = (
            f"UNWIND $rows AS row "
            f"MERGE (x:{labels} {{id: row.id}}) "
            f"SET x.name = row.name, x.type = row.type, x.doc_id = $doc, x += row.props"
        )
        queries.append((cypher, {"rows": rows, "doc": doc_id}))
    return queries


class Neo4jGraphStore(GraphStore, GraphReader):
    def __init__(self, doc_id: str, uri: Optional[str] = None,
                 user: Optional[str] = None, password: Optional[str] = None,
                 database: Optional[str] = None):
        from ..config import settings
        from neo4j import GraphDatabase  # 延迟导入
        self._doc_id = doc_id
        self._db = database or settings.neo4j_database
        self._driver = GraphDatabase.driver(
            uri or settings.neo4j_uri,
            auth=(user or settings.neo4j_user, password or settings.neo4j_password),
        )

    def close(self) -> None:
        self._driver.close()

    def _run(self, cypher: str, **params):
        with self._driver.session(database=self._db) as s:
            return list(s.run(cypher, **params))

    # -- 写 --
    def reset(self) -> None:
        # 只删当前 doc 的知识节点与其关系（Chunk 也按 doc_id 清）
        self._run(
            "MATCH (n {doc_id:$doc}) DETACH DELETE n", doc=self._doc_id)

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        # 期望每个 node: {type, name, id?, summary?, ...}
        # 写**带类型标签 + :KGNode 共享标签**：类型标签匹配 constraints.cypher 的约束/
        # 全文/向量索引；:KGNode 供通用读查询（has_node/node_names/incident_edges）。
        for cypher, params in _node_write_queries(nodes, self._doc_id):
            self._run(cypher, **params)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        rows = [{"from": e["from"], "type": e["type"], "to": e["to"],
                 "conf": e.get("confidence", 1.0),
                 "chunk": e.get("source_chunk_id", "")} for e in edges]
        self._run(
            """
            UNWIND $rows AS row
            MATCH (a:KGNode {doc_id:$doc}) WHERE a.name = row.from
            MATCH (b:KGNode {doc_id:$doc}) WHERE b.name = row.to
            MERGE (a)-[r:REL {type: row.type}]->(b)
            SET r.confidence = row.conf, r.source_chunk_id = row.chunk
            """,
            rows=rows, doc=self._doc_id)

    # -- 读（评测用） --
    def has_node(self, name: str) -> bool:
        res = self._run(
            "MATCH (x:KGNode {doc_id:$doc}) WHERE x.name=$name RETURN count(x)>0 AS ok",
            doc=self._doc_id, name=name)
        return bool(res and res[0]["ok"])

    def has_edge(self, frm: str, etype: str, to: str) -> bool:
        res = self._run(
            """
            MATCH (a:KGNode {doc_id:$doc})-[r:REL {type:$t}]->(b:KGNode {doc_id:$doc})
            WHERE a.name=$frm AND b.name=$to RETURN count(r)>0 AS ok
            """,
            doc=self._doc_id, frm=frm, to=to, t=etype)
        return bool(res and res[0]["ok"])

    # -- GraphReader（检索用） --
    def node_names(self) -> set[str]:
        res = self._run("MATCH (x:KGNode {doc_id:$doc}) RETURN x.name AS n", doc=self._doc_id)
        return {r["n"] for r in res}

    def incident_edges(self, name: str) -> list[tuple[str, str, str]]:
        res = self._run(
            """
            MATCH (a:KGNode {doc_id:$doc})-[r:REL]->(b:KGNode {doc_id:$doc})
            WHERE a.name=$name OR b.name=$name
            RETURN a.name AS f, r.type AS t, b.name AS o
            """,
            doc=self._doc_id, name=name)
        return [(r["f"], r["t"], r["o"]) for r in res]
