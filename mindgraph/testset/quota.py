"""分层配额（SPEC §3.2）。

把"目标条数 + 各章节大小"展开成一组待生成 slot：
  extraction : reasoning ≈ 1:1
  reasoning 内 explanatory : applied ≈ 1:1
  每个主要章节 ≥ min_per_section（默认 8）
  hops 1/2/3 ≈ 50% / 35% / 15%
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter


@dataclass(frozen=True)
class Slot:
    section: str
    type: str            # "extraction" | "reasoning"
    qtype: str | None    # reasoning 才有
    hops: int            # 1 | 2 | 3


def _split_half(n: int) -> tuple[int, int]:
    a = round(n / 2)
    return a, n - a


def _hops_split(n: int) -> list[int]:
    """按 50/35/15 给 n 个 slot 分配 hops，返回长度 n 的 hops 列表。"""
    h1 = round(n * 0.50)
    h3 = round(n * 0.15)
    h2 = n - h1 - h3
    if h2 < 0:  # 极小 n 的边界
        h2, h3 = 0, n - h1
    return [1] * h1 + [2] * h2 + [3] * h3


def plan_section(section: str, count: int) -> list[Slot]:
    """单章节内展开 count 个 slot。"""
    n_ext, n_rea = _split_half(count)
    n_exp, n_app = _split_half(n_rea)

    # 先定 (type,qtype) 序列
    pairs: list[tuple[str, str | None]] = (
        [("extraction", None)] * n_ext
        + [("reasoning", "explanatory")] * n_exp
        + [("reasoning", "applied")] * n_app
    )
    hops = _hops_split(len(pairs))
    return [Slot(section=section, type=t, qtype=q, hops=h)
            for (t, q), h in zip(pairs, hops)]


def plan(section_sizes: dict[str, int], target: int = 100,
         min_per_section: int = 8) -> list[Slot]:
    """主入口。

    Args:
        section_sizes: {章节名: 该章节 chunk 数（权重）}
        target: 目标条数（下限；强制章节 floor 后实际可能略高）
        min_per_section: 每个主要章节最少条数
    """
    if not section_sizes:
        raise ValueError("section_sizes 为空，无法规划配额")

    total_w = sum(section_sizes.values()) or 1
    per_section: dict[str, int] = {}
    for sec, w in section_sizes.items():
        proportional = round(target * w / total_w)
        per_section[sec] = max(min_per_section, proportional)

    slots: list[Slot] = []
    for sec, c in per_section.items():
        slots.extend(plan_section(sec, c))
    return slots


def summarize(slots: list[Slot]) -> dict[str, object]:
    """生成配额报告，便于核对是否满足 §3.2。"""
    by_type = Counter(s.type for s in slots)
    by_qtype = Counter(s.qtype for s in slots if s.type == "reasoning")
    by_hops = Counter(s.hops for s in slots)
    by_section = Counter(s.section for s in slots)
    total = len(slots)

    def pct(c: Counter) -> dict[str, str]:
        return {str(k): f"{v} ({v / total:.0%})" for k, v in sorted(c.items(), key=lambda x: str(x[0]))}

    return {
        "total": total,
        "type": pct(by_type),
        "reasoning_qtype": pct(by_qtype),
        "hops": pct(by_hops),
        "sections": dict(sorted(by_section.items())),
        "min_section": min(by_section.values()) if by_section else 0,
    }
