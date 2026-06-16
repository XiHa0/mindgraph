"""测试集生成器（SPEC §3）。

输入 chunks → 配额展开 → 按 cell 调用 judge/强模型出题 → 汇总 TestItem。
用 StubLLM 时整条管线可离线跑通（结构合法的占位题）。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..llm import LLM
from ..models import Chunk, TestItem
from . import quota
from .quota import Slot

_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_CHUNK_CHARS = 800        # 单片段进 prompt 的截断长度
_CROSS_SECTION_EXTRA = 4      # hops=3 时额外附带的跨章节片段数


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8-sig")


def _render_chunks(chunks: list[Chunk]) -> str:
    lines = []
    for c in chunks:
        text = c.text.strip().replace("\n", " ")
        if len(text) > _MAX_CHUNK_CHARS:
            text = text[:_MAX_CHUNK_CHARS] + "…"
        lines.append(f"[{c.id} | {c.section}] {text}")
    return "\n\n".join(lines)


@dataclass
class _Cell:
    section: str
    type: str
    qtype: Optional[str]
    hops: list[int]          # 该 cell 要出的题的 hops 多重集

    @property
    def n(self) -> int:
        return len(self.hops)


def _cells_from_slots(slots: list[Slot]) -> list[_Cell]:
    bucket: dict[tuple[str, str, Optional[str]], list[int]] = defaultdict(list)
    for s in slots:
        bucket[(s.section, s.type, s.qtype)].append(s.hops)
    return [_Cell(section=sec, type=t, qtype=q, hops=hops)
            for (sec, t, q), hops in bucket.items()]


class TestsetGenerator:
    def __init__(self, llm: LLM, concurrency: int = 4):
        self._llm = llm
        self._sem = asyncio.Semaphore(concurrency)

    async def generate(self, chunks: list[Chunk], target: int = 100,
                       min_per_section: int = 8) -> list[TestItem]:
        if not chunks:
            raise ValueError("chunks 为空")

        by_section: dict[str, list[Chunk]] = defaultdict(list)
        for c in chunks:
            by_section[c.section].append(c)
        for lst in by_section.values():
            lst.sort(key=lambda c: c.order)

        section_sizes = {sec: len(lst) for sec, lst in by_section.items()}
        slots = quota.plan(section_sizes, target=target, min_per_section=min_per_section)
        cells = _cells_from_slots(slots)

        results = await asyncio.gather(
            *(self._gen_cell(cell, by_section, chunks) for cell in cells)
        )

        items: list[TestItem] = [it for sub in results for it in sub]
        items = self._dedup(items)
        self._assign_ids(items, list(by_section.keys()))
        return items

    async def _gen_cell(self, cell: _Cell, by_section: dict[str, list[Chunk]],
                        all_chunks: list[Chunk]) -> list[TestItem]:
        section_chunks = by_section[cell.section]
        # hops=3 的题需要跨章节素材
        extra: list[Chunk] = []
        if any(h == 3 for h in cell.hops):
            others = [c for c in all_chunks if c.section != cell.section]
            extra = others[:_CROSS_SECTION_EXTRA]
        ctx_chunks = section_chunks + extra
        chunk_ids = [c.id for c in ctx_chunks]

        from collections import Counter as _Counter
        hops_counts = _Counter(cell.hops)
        hops_breakdown = "，".join(f"hops={h}×{c}" for h, c in sorted(hops_counts.items()))

        prompt_name = "testset_extraction" if cell.type == "extraction" else "testset_reasoning"
        tmpl = _load_prompt(prompt_name)
        user = (tmpl
                .replace("{section}", cell.section)
                .replace("{qtype}", cell.qtype or "")
                .replace("{n}", str(cell.n))
                .replace("{hops_breakdown}", hops_breakdown)
                .replace("{chunks}", _render_chunks(ctx_chunks)))
        system = "你只输出严格 JSON，不要任何解释。"

        meta = {"cell": {"section": cell.section, "type": cell.type,
                         "qtype": cell.qtype, "hops": cell.hops[0] if cell.hops else 1},
                "chunk_ids": chunk_ids, "n": cell.n, "hops_seq": cell.hops}

        async with self._sem:
            try:
                raw = await self._llm.complete_json(system, user, meta=meta)
            except Exception as e:  # 单 cell 失败不拖垮整体
                from .._log import logger
                logger.error(f"[testset] cell 生成失败 {cell.section}/{cell.type}: {e}")
                return []

        out: list[TestItem] = []
        for idx, raw_item in enumerate(raw.get("items", [])[: cell.n]):
            raw_item.setdefault("section", cell.section)
            raw_item.setdefault("type", cell.type)
            if cell.type == "reasoning":
                raw_item.setdefault("qtype", cell.qtype)
            # 按 cell 的 hops 序列**强制**分配，保证 §3.2 hops 配额
            raw_item["hops"] = cell.hops[idx]
            raw_item.setdefault("provenance", chunk_ids[:2])
            raw_item["id"] = "PENDING"
            raw_item["verified"] = False
            try:
                out.append(TestItem.from_dict(raw_item))
            except Exception:
                continue
        return out

    @staticmethod
    def _dedup(items: list[TestItem]) -> list[TestItem]:
        seen: set[str] = set()
        uniq: list[TestItem] = []
        for it in items:
            key = it.question.strip()
            if key and key not in seen:
                seen.add(key)
                uniq.append(it)
        return uniq

    @staticmethod
    def _assign_ids(items: list[TestItem], section_order: list[str]) -> None:
        rank = {sec: i for i, sec in enumerate(section_order)}
        items.sort(key=lambda it: (rank.get(it.section, 999), it.type, it.hops))
        for i, it in enumerate(items, start=1):
            it.id = f"t{i:04d}"
