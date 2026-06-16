"""人工校验与冻结（SPEC §3.4）。

流程：
  生成 → save_json（草稿）
       → export_review_markdown（人读，标出建议必审的分层抽样）
       → 人工在草稿 JSON 上逐条把 verified 置 true / 修正 gold / 删除坏题
       → freeze（只保留 verified，重新校验，写出冻结 gold）

冻结后的 gold 在循环里**不再改动**——否则覆盖率曲线失去意义。
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from ..models import TestItem
from .validate import validate, Report


def save_json(items: list[TestItem], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps([it.to_dict() for it in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_json(path: str | Path) -> list[TestItem]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return [TestItem.from_dict(d) for d in data]


def _stratified_sample_ids(items: list[TestItem], fraction: float, seed: int) -> set[str]:
    """按 (section, type) 分层抽样，返回建议必审的 id 集。"""
    rng = random.Random(seed)
    buckets: dict[tuple[str, str], list[TestItem]] = defaultdict(list)
    for it in items:
        buckets[(it.section, it.type)].append(it)
    picked: set[str] = set()
    for bucket in buckets.values():
        k = max(1, round(len(bucket) * fraction))
        picked.update(it.id for it in rng.sample(bucket, min(k, len(bucket))))
    return picked


def export_review_markdown(items: list[TestItem], path: str | Path,
                           sample_fraction: float = 0.3, seed: int = 7) -> None:
    """生成人读校验单。⭐ 标注的是分层抽样、建议必审的条目。"""
    must = _stratified_sample_ids(items, sample_fraction, seed)
    lines = [
        "# 测试集人工校验单",
        "",
        f"- 总数：{len(items)}　建议必审（⭐）：{len(must)}（分层抽样 {sample_fraction:.0%}）",
        "- 校验方式：在**草稿 JSON**里逐条核对，确认无误后把该条 `verified` 改为 `true`；",
        "  发现 gold 错误就直接改 JSON；整条不可用就删除。完成后运行 freeze。",
        "",
    ]
    by_section: dict[str, list[TestItem]] = defaultdict(list)
    for it in items:
        by_section[it.section].append(it)
    for sec, lst in by_section.items():
        lines.append(f"## {sec}（{len(lst)} 条）")
        lines.append("")
        for it in sorted(lst, key=lambda x: x.id):
            star = "⭐ " if it.id in must else ""
            meta = f"`{it.type}`" + (f"/`{it.qtype}`" if it.qtype else "") + f" hops={it.hops}"
            lines.append(f"- {star}**{it.id}** {meta}　来源 {it.provenance}")
            lines.append(f"  - Q: {it.question}")
            if it.type == "extraction" and it.gold_subgraph:
                ns = "，".join(f"{n.type}:{n.name}" for n in it.gold_subgraph.nodes)
                es = "，".join(f"{e.from_} -{e.type}-> {e.to}" for e in it.gold_subgraph.edges)
                lines.append(f"  - 节点: {ns}")
                if es:
                    lines.append(f"  - 关系: {es}")
            else:
                lines.append(f"  - 要素: {it.gold_elements}")
                if it.must_not:
                    lines.append(f"  - 禁止: {it.must_not}")
            lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def freeze(items: list[TestItem], out_path: str | Path,
           target: int = 100, min_per_section: int = 8) -> tuple[Report, list[TestItem]]:
    """只保留 verified 的条目，重新校验后写出冻结 gold。

    返回 (校验报告, 冻结后的 items)。报告里会提示 verified 数是否够。
    """
    verified = [it for it in items if it.verified]
    report = validate(verified, target=target, min_per_section=min_per_section)
    if len(verified) < 100:
        report.quota_warnings.append(
            f"已校验 {len(verified)} 条 < 100：人工校验尚未覆盖足够样本，暂不建议冻结"
        )
    save_json(verified, out_path)
    return report, verified
