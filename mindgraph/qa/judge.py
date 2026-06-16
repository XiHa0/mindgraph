"""按 rubric 三项给回答打分（SPEC §3.3）。judge 模型必须独立于抽取模型（§3.4）。"""
from __future__ import annotations

from pathlib import Path

from ..llm import LLM
from ..models import TestItem

_PROMPT_DIR = Path(__file__).parent / "prompts"
_RUBRIC_KEYS = ("要素召回", "论证完整", "无幻觉")


class RubricJudge:
    def __init__(self, llm: LLM):
        self._llm = llm

    async def score(self, item: TestItem, answer: str) -> dict[str, float]:
        """返回 {要素召回, 论证完整, 无幻觉}，缺失项按 0 处理。"""
        tmpl = (_PROMPT_DIR / "judge.md").read_text(encoding="utf-8-sig")
        user = (tmpl
                .replace("{question}", item.question)
                .replace("{gold_elements}", "\n".join(f"- {e}" for e in (item.gold_elements or [])))
                .replace("{must_not}", "\n".join(f"- {e}" for e in (item.must_not or [])) or "（无）")
                .replace("{answer}", answer or "（空回答）"))
        system = "你是严格阅卷裁判，只输出严格 JSON 的三项分数。"
        raw = await self._llm.complete_json(system, user, temperature=0.0,
                                            meta={"task": "judge", "item_id": item.id})
        return {k: _clip01(raw.get(k, 0.0)) for k in _RUBRIC_KEYS}

    @staticmethod
    def weighted(item: TestItem, scores: dict[str, float]) -> float:
        rubric = item.rubric or {k: w for k, w in
                                 zip(_RUBRIC_KEYS, (0.5, 0.3, 0.2))}
        total_w = sum(rubric.values()) or 1.0
        return sum(scores.get(k, 0.0) * w for k, w in rubric.items()) / total_w


def _clip01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0
