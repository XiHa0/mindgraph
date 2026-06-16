"""测试集校验（SPEC §3 + meta 本体一致性）。

两类检查：
  1. 逐条结构合法性（对齐 testset.schema.json 与 meta_ontology.json）。
  2. 配额合规性（§3.2）。
不强依赖 jsonschema；若已安装则额外做一次 JSON Schema 交叉校验。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..models import TestItem

_SCHEMA_DIR = Path(__file__).parent.parent / "schema"
_ID_RE = re.compile(r"^t[0-9]{4,}$")

with open(_SCHEMA_DIR / "meta_ontology.json", encoding="utf-8") as _f:
    _ONT = json.load(_f)
_NODE_TYPES = set(_ONT["node_types"].keys())
_EDGE_TYPES = _ONT["edge_types"]


@dataclass
class Report:
    ok: bool = True
    item_errors: dict[str, list[str]] = field(default_factory=dict)   # id -> [errors]
    quota_warnings: list[str] = field(default_factory=list)
    summary: dict[str, object] = field(default_factory=dict)

    def add(self, item_id: str, msg: str) -> None:
        self.item_errors.setdefault(item_id, []).append(msg)
        self.ok = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "n_items": self.summary.get("total", 0),
            "n_item_errors": sum(len(v) for v in self.item_errors.values()),
            "item_errors": self.item_errors,
            "quota_warnings": self.quota_warnings,
            "summary": self.summary,
        }


def _check_edge(report: Report, item_id: str, e_from_type: str | None,
                etype: str, e_to_type: str | None) -> None:
    if etype not in _EDGE_TYPES:
        report.add(item_id, f"未知关系类型 {etype}")
        return
    # 端点类型校验（节点 name→type 映射可得时）
    spec = _EDGE_TYPES[etype]
    if e_from_type and "*" not in spec["from"] and e_from_type not in spec["from"]:
        report.add(item_id, f"关系 {etype} 起点类型应为 {spec['from']}，实为 {e_from_type}")
    if e_to_type and "*" not in spec["to"] and e_to_type not in spec["to"]:
        report.add(item_id, f"关系 {etype} 终点类型应为 {spec['to']}，实为 {e_to_type}")


def _validate_item(report: Report, it: TestItem) -> None:
    iid = it.id
    if not _ID_RE.match(it.id):
        report.add(iid, f"id 不合法：{it.id}")
    if it.type not in ("extraction", "reasoning"):
        report.add(iid, f"type 不合法：{it.type}")
    if not (1 <= it.hops <= 3):
        report.add(iid, f"hops 越界：{it.hops}")
    if not it.question.strip():
        report.add(iid, "question 为空")
    if not it.provenance:
        report.add(iid, "provenance 为空（无法溯源）")

    if it.type == "extraction":
        if not it.gold_subgraph or not it.gold_subgraph.nodes:
            report.add(iid, "extraction 题缺 gold_subgraph.nodes")
            return
        name2type = {n.name: n.type for n in it.gold_subgraph.nodes}
        for n in it.gold_subgraph.nodes:
            if n.type not in _NODE_TYPES:
                report.add(iid, f"未知节点类型 {n.type}")
        for e in it.gold_subgraph.edges:
            _check_edge(report, iid, name2type.get(e.from_), e.type, name2type.get(e.to))

    elif it.type == "reasoning":
        if it.qtype not in ("explanatory", "applied"):
            report.add(iid, f"reasoning 题 qtype 不合法：{it.qtype}")
        if not it.gold_elements:
            report.add(iid, "reasoning 题缺 gold_elements")
        if not it.rubric:
            report.add(iid, "reasoning 题缺 rubric")
        else:
            s = sum(it.rubric.values())
            if abs(s - 1.0) > 0.01:
                report.add(iid, f"rubric 权重和应为 1.0，实为 {s:.2f}")


def _check_quota(report: Report, items: list[TestItem], target: int,
                 min_per_section: int) -> None:
    total = len(items)
    by_type = Counter(it.type for it in items)
    by_qtype = Counter(it.qtype for it in items if it.type == "reasoning")
    by_hops = Counter(it.hops for it in items)
    by_section = Counter(it.section for it in items)

    report.summary = {
        "total": total,
        "type": dict(by_type),
        "reasoning_qtype": dict(by_qtype),
        "hops": dict(sorted(by_hops.items())),
        "sections": dict(sorted(by_section.items())),
    }

    if total < target:
        report.quota_warnings.append(f"条数 {total} < 目标 {target}")
    if total < 100:
        report.quota_warnings.append(f"条数 {total} < 硬下限 100")

    ext, rea = by_type.get("extraction", 0), by_type.get("reasoning", 0)
    if rea and not (0.6 <= ext / rea <= 1.7):
        report.quota_warnings.append(f"extraction:reasoning = {ext}:{rea} 偏离 1:1")

    exp, app = by_qtype.get("explanatory", 0), by_qtype.get("applied", 0)
    if app and not (0.6 <= exp / app <= 1.7):
        report.quota_warnings.append(f"explanatory:applied = {exp}:{app} 偏离 1:1")

    for sec, c in by_section.items():
        if c < min_per_section:
            report.quota_warnings.append(f"章节「{sec}」仅 {c} 条 < {min_per_section}")

    if total:
        h1 = by_hops.get(1, 0) / total
        if not (0.35 <= h1 <= 0.65):
            report.quota_warnings.append(f"hops=1 占比 {h1:.0%} 偏离 ~50%")


def _jsonschema_crosscheck(report: Report, items: list[TestItem]) -> None:
    try:
        import jsonschema  # 可选
    except ImportError:
        return
    with open(Path(__file__).parent.parent / "testset.schema.json", encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.Draft7Validator(schema)
    for it in items:
        for err in validator.iter_errors(it.to_dict()):
            report.add(it.id, f"[jsonschema] {err.message}")


def validate(items: list[TestItem], target: int = 100,
             min_per_section: int = 8) -> Report:
    report = Report()
    ids = Counter(it.id for it in items)
    for dup, c in ids.items():
        if c > 1:
            report.add(dup, f"id 重复 {c} 次")
    for it in items:
        _validate_item(report, it)
    _check_quota(report, items, target, min_per_section)
    _jsonschema_crosscheck(report, items)
    return report
