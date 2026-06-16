"""真实两阶段抽取（SPEC §4）：先节点 → 概念归一化 → 在已知节点集合里抽关系 → 写图。

替换 builder/stubs.py 的 SimulatedExtractor。与 gold/测试集**零耦合**。
LLM 走 mindgraph.llm.extraction_llm()（DeepSeek@硅基流动）。

两阶段的意义：一把抽"复杂 schema + 关系"会乱、且关系易张冠李戴。先把节点抽准、
消歧合并，再把关系约束在"已知节点"之间，质量与成本都更好（SPEC §4 要点）。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..llm import LLM
from ..models import Chunk
from ..builder.interfaces import (
    Extractor, GraphStore, DomainSchema, ExtractionConfig,
)
from .resolve import resolve_entities, norm_key

_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_CHARS = 1600  # 单片段进 prompt 的截断


def _load(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8-sig")


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MAX_CHARS else text[:_MAX_CHARS] + "…"


def _render_directives(items: list[str]) -> str:
    return "\n".join(f"- {d}" for d in items) if items else "（无额外指令）"


def _render_few_shot(items: list[str]) -> str:
    return ("示例：\n" + "\n".join(items)) if items else ""


class TwoStageExtractor(Extractor):
    def __init__(self, llm: LLM, concurrency: int = 6):
        self._llm = llm
        self._sem = asyncio.Semaphore(concurrency)

    async def extract(self, chunks: list[Chunk], schema: DomainSchema,
                      config: ExtractionConfig, store: GraphStore) -> None:
        # ---- 阶段一：逐片段抽节点 ----
        node_lists = await asyncio.gather(
            *(self._nodes_for_chunk(c, schema, config) for c in chunks)
        )
        raw_nodes: list[dict[str, Any]] = [n for sub in node_lists for n in sub]

        # ---- 概念归一化（消歧合并）----
        canonical_nodes, alias_map = resolve_entities(raw_nodes)
        store.upsert_nodes(canonical_nodes)

        # 每个 chunk 命中的 canonical 节点（关系抽取的候选端点）
        chunk_nodes: dict[str, set[str]] = defaultdict(set)
        for node in canonical_nodes:
            for cid in node.get("source_chunk_ids", []):
                chunk_nodes[cid].add(node["name"])

        # ---- 阶段二：在已知节点之间抽关系 ----
        edge_lists = await asyncio.gather(
            *(self._edges_for_chunk(c, sorted(chunk_nodes.get(c.id, set())), alias_map, config)
              for c in chunks if chunk_nodes.get(c.id))
        )
        raw_edges: list[dict[str, Any]] = [e for sub in edge_lists for e in sub]
        if raw_edges:
            store.upsert_edges(raw_edges)

    # ------------------------------------------------------------------
    @staticmethod
    def _concepts_block(schema: DomainSchema, limit: int = 60) -> str:
        if not schema.concepts:
            return "（暂无）"
        parts = []
        for c in schema.concepts[:limit]:
            al = schema.aliases.get(c)
            parts.append(f"{c}（别名：{'、'.join(al)}）" if al else c)
        return "，".join(parts)

    async def _nodes_for_chunk(self, chunk: Chunk, schema: DomainSchema,
                               config: ExtractionConfig) -> list[dict[str, Any]]:
        concepts = self._concepts_block(schema)
        directives = list(config.node_directives)
        if config.finer_chunking:
            directives.append("更细粒度地拆分：把含多个论点的长句拆成多个独立的 Claim/Method 节点。")
        user = (_load("extract_nodes")
                .replace("{concepts}", concepts)
                .replace("{directives}", _render_directives(directives))
                .replace("{few_shot}", _render_few_shot(config.node_few_shot))
                .replace("{chunk_id}", chunk.id)
                .replace("{section}", chunk.section)
                .replace("{text}", _clip(chunk.text)))
        raw = await self._call(user, meta={"stage": "nodes", "chunk": chunk.to_dict()})
        out = []
        for n in raw.get("nodes", []):
            if not n.get("name"):
                continue
            n["source_chunk_id"] = chunk.id
            out.append(n)
        return out

    async def _edges_for_chunk(self, chunk: Chunk, node_names: list[str],
                               alias_map: dict[str, str],
                               config: ExtractionConfig) -> list[dict[str, Any]]:
        if not node_names:
            return []
        nodes_block = "\n".join(f"- {n}" for n in node_names)
        user = (_load("extract_edges")
                .replace("{directives}", _render_directives(config.edge_directives))
                .replace("{few_shot}", _render_few_shot(config.edge_few_shot))
                .replace("{nodes}", nodes_block)
                .replace("{chunk_id}", chunk.id)
                .replace("{section}", chunk.section)
                .replace("{text}", _clip(chunk.text)))
        raw = await self._call(user, meta={"stage": "edges", "chunk": chunk.to_dict(),
                                           "node_names": node_names})
        out = []
        for e in raw.get("edges", []):
            frm = alias_map.get(norm_key(e.get("from", "")))
            to = alias_map.get(norm_key(e.get("to", "")))
            if not (frm and to):       # 端点必须是已知 canonical 节点，否则丢弃
                continue
            out.append({"from": frm, "type": e.get("type"), "to": to,
                        "confidence": e.get("confidence", 1.0),
                        "source_chunk_id": chunk.id})
        return out

    async def _call(self, user: str, meta: dict[str, Any]) -> dict[str, Any]:
        system = "你是严谨的知识图谱抽取器，只输出严格 JSON，不要任何解释。"
        async with self._sem:
            try:
                return await self._llm.complete_json(system, user, meta=meta)
            except Exception as e:
                from .._log import logger
                logger.error(f"[extract] {meta.get('stage')} 失败: {e}")
                return {}
