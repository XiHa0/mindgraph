"""LLMSchemaInducer：分窗扫全文抽候选概念 → 合并 → 排序 → domain 词表。

map：把 chunks 拼成 ~window_chars 的窗口，逐窗用强模型抽候选概念（比逐 chunk 抽取粗，
     窗口可以更大、调用更少）。
reduce：复用 extraction.resolve.resolve_entities 做实体消歧合并，按"被多少窗口提到 +
     置信度"排序，取 topN 作为规范概念表，并产出别名表。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..llm import LLM
from ..models import Chunk
from ..builder.interfaces import SchemaInducer, DomainSchema
from ..extraction.resolve import resolve_entities

_PROMPT = Path(__file__).parent / "prompts" / "induce_concepts.md"


class LLMSchemaInducer(SchemaInducer):
    def __init__(self, llm: LLM, window_chars: int = 6000, max_concepts: int = 80,
                 concurrency: int = 4):
        self._llm = llm
        self._window_chars = window_chars
        self._max_concepts = max_concepts
        self._sem = asyncio.Semaphore(concurrency)

    async def induce(self, chunks: list[Chunk]) -> DomainSchema:
        windows = self._windows(chunks)
        cand_lists = await asyncio.gather(*(self._concepts_for_window(w) for w in windows))
        raw: list[dict[str, Any]] = [c for sub in cand_lists for c in sub]
        if not raw:
            return DomainSchema(notes="未归纳出概念（文档过短或抽取为空）")

        canonical, _alias_map = resolve_entities(raw)
        # 排序：被多少窗口提到（source_chunk_ids 数）优先，其次置信度
        canonical.sort(
            key=lambda n: (len(n.get("source_chunk_ids", [])), n.get("author_confidence") or 0),
            reverse=True)
        top = canonical[: self._max_concepts]

        concepts = [n["name"] for n in top]
        aliases = {n["name"]: n["aliases"] for n in top if n.get("aliases")}
        return DomainSchema(
            concepts=concepts, aliases=aliases,
            notes=f"归纳 {len(concepts)} 概念（候选 {len(canonical)}，窗口 {len(windows)}）")

    def _windows(self, chunks: list[Chunk]) -> list[list[Chunk]]:
        windows: list[list[Chunk]] = []
        cur: list[Chunk] = []
        size = 0
        for c in chunks:
            if cur and size + len(c.text) > self._window_chars:
                windows.append(cur)
                cur, size = [], 0
            cur.append(c)
            size += len(c.text)
        if cur:
            windows.append(cur)
        return windows

    async def _concepts_for_window(self, window: list[Chunk]) -> list[dict[str, Any]]:
        win_id = window[0].id if window else "w"
        text = "\n\n".join(c.text for c in window)
        user = _PROMPT.read_text(encoding="utf-8-sig").replace("{window}", text)
        system = "你只输出严格 JSON，不要任何解释。"
        async with self._sem:
            try:
                raw = await self._llm.complete_json(system, user,
                                                    meta={"task": "induce", "window": win_id})
            except Exception as e:
                from .._log import logger
                logger.error(f"[induce] 窗口 {win_id} 失败: {e}")
                return []
        out = []
        for c in raw.get("concepts", []):
            if not c.get("name"):
                continue
            # 每个候选挂上"窗口 id"作为来源，reduce 时用来数频次
            c["source_chunk_id"] = win_id
            c["confidence"] = c.get("salience", c.get("confidence"))
            out.append(c)
        return out
