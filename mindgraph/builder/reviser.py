"""真实修正器（SPEC §4 step 6 / §2.2）：据诊断更新抽取配置，打通自我改进闭环。

规则驱动、确定性、零额外 LLM 成本。核心思想 = 把人工迭代过程自动化：
看哪类错误最多，就往下一轮抽取里加**针对该类**的指令与 few-shot。

防泄漏：所有 few-shot 只用**类型层面**的抽象模式（from_type/edge_type/to_type），
不带入任何具体 gold 名称或答案——教"识别模式"，不教"标准答案"。
"""
from __future__ import annotations

from collections import Counter

from .interfaces import Reviser, DomainSchema, ExtractionConfig, Diagnosis

# 关系类型的中文释义，用于生成可读 few-shot
_EDGE_GLOSS = {
    "SUPPORTS": "论证支撑论断", "REFUTES": "论证反驳论断", "USES": "论证使用论据",
    "EXEMPLIFIES": "例子例证概念/论断", "APPLIES_WHEN": "在某条件下适用",
    "ANSWERS": "论断回答问题", "DEPENDS_ON": "概念依赖概念", "PART_OF": "概念是整体的一部分",
    "REFINES": "概念细化概念", "GENERALIZES": "概念泛化概念", "CAUSES": "导致",
    "ENABLES": "使能", "CONTRASTS_WITH": "与…对比", "REALIZES": "方法实现原则",
    "COMPOSED_OF": "框架由概念组成",
}
_NODE_GLOSS = {
    "Concept": "概念/术语", "Claim": "论断", "Principle": "原则/应然", "Method": "方法/做法",
    "Argument": "论证", "Evidence": "论据/例子", "Condition": "适用条件", "Counterpoint": "反例",
    "Analogy": "类比", "MentalModel": "框架", "Question": "作者在回答的问题",
}


def _edge_pattern_fewshot(pat: tuple[str, str, str]) -> str:
    f, t, o = pat
    gloss = _EDGE_GLOSS.get(t, t)
    return (f"- 当文本表达「{_NODE_GLOSS.get(f, f)} … {gloss} … {_NODE_GLOSS.get(o, o)}」时，"
            f"应抽出关系：{f} -{t}-> {o}")


def _dedup_cap(items: list[str], cap: int) -> list[str]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[-cap:]   # 保留最近的，避免无限膨胀


class DiagnosisReviser(Reviser):
    def __init__(self, max_directives: int = 6, max_few_shot: int = 6,
                 top_k_types: int = 3):
        self._max_d = max_directives
        self._max_fs = max_few_shot
        self._top_k = top_k_types

    async def revise(self, diagnosis: Diagnosis, schema: DomainSchema,
                     config: ExtractionConfig) -> tuple[DomainSchema, ExtractionConfig]:
        dom = diagnosis.dominant()

        if dom == "missing_edge":
            top = [t for t, _ in Counter(diagnosis.missing_edge_types).most_common(self._top_k)]
            if top:
                config.edge_directives.append("重点补抽以下关系类型：" + "、".join(top))
            for pat in self._top_patterns(diagnosis):
                config.edge_few_shot.append(_edge_pattern_fewshot(pat))

        elif dom == "missing_node":
            top = [t for t, _ in Counter(diagnosis.missing_node_types).most_common(self._top_k)]
            if top:
                pretty = "、".join(f"{t}({_NODE_GLOSS.get(t, '')})" for t in top)
                config.node_directives.append("重点补抽以下节点类型：" + pretty)
            # 节点漏抽常因切分把内容切散 → 下一轮更细切分
            config.finer_chunking = True

        elif dom == "wrong_edge":
            config.edge_directives.append(
                "严格按端点类型选择关系类型与方向；拿不准时宁可不连，避免张冠李戴。")

        elif dom == "granularity":
            config.node_directives.append("把含多个论点的长句拆成多个独立节点，避免一节点塞多义。")
            config.finer_chunking = True

        # 去重 + 限长，防止指令无限膨胀
        config.node_directives = _dedup_cap(config.node_directives, self._max_d)
        config.edge_directives = _dedup_cap(config.edge_directives, self._max_d)
        config.node_few_shot = _dedup_cap(config.node_few_shot, self._max_fs)
        config.edge_few_shot = _dedup_cap(config.edge_few_shot, self._max_fs)
        config.extra["last_fix"] = dom
        return schema, config

    def _top_patterns(self, diagnosis: Diagnosis) -> list[tuple[str, str, str]]:
        counts = Counter(diagnosis.missing_edge_patterns)
        return [p for p, _ in counts.most_common(self._top_k)]
