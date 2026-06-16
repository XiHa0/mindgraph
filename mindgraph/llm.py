"""MindGraph 的 LLM 层。

设计目标：**可替换**。循环里所有 LLM 调用走这个接口，便于：
  - 把抽取模型（DeepSeek@硅基流动）与 judge/生成模型（百炼）分开（SPEC §3.4）；
  - 用 StubLLM 离线跑通整条管线（--dry-run），不依赖网络/密钥。

只用 chat.completions + JSON，不用 Agents SDK：批量生成/抽取/判卷用裸客户端更可控。
"""
from __future__ import annotations

import abc
import hashlib
import json
import re
from typing import Any, Optional


class LLM(abc.ABC):
    """统一接口：给定 system+user，返回解析好的 JSON 对象。"""

    name: str = "llm"

    @abc.abstractmethod
    async def complete_json(self, system: str, user: str,
                            temperature: float = 0.2,
                            meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """meta 是给离线 StubLLM 的结构化旁路；真实模型忽略它。"""
        ...


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出里稳健地抠出 JSON 对象（容忍 ```json 代码块/前后噪声）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 直接尝试
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次：截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"模型未返回可解析 JSON：{text[:200]!r}")


class OpenAILLM(LLM):
    """走 OpenAI 兼容端点（硅基流动 / 百炼皆兼容）。"""

    def __init__(self, client: Any, model: str, name: str = "llm"):
        self._client = client
        self._model = model
        self.name = name

    async def complete_json(self, system: str, user: str,
                            temperature: float = 0.2,
                            meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        # 优先用 json_object 强约束；个别模型不支持时回退普通模式 + 抠取。
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                temperature=temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception:
            resp = await self._client.chat.completions.create(
                model=self._model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        return _extract_json(resp.choices[0].message.content or "")


class StubLLM(LLM):
    """离线确定性桩：用于 --dry-run，验证管线 plumbing。

    不产生真实知识，只产生**结构合法**、可被 §3 校验通过的占位 item。
    """

    name = "stub"

    async def complete_json(self, system: str, user: str,
                            temperature: float = 0.2,
                            meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """据 meta 里的 cell 规格回填**结构合法**的占位 item（不产生真实知识）。"""
        meta = meta or {}
        cell = meta.get("cell", {})
        chunk_ids: list[str] = meta.get("chunk_ids", [])
        n: int = meta.get("n", 1)
        hops_seq: list[int] = meta.get("hops_seq") or []
        section = cell.get("section", "")
        typ = cell.get("type", "extraction")
        qtype = cell.get("qtype")
        prov = chunk_ids[:2] or ["chunk_000"]

        items = []
        for i in range(n):
            hops = hops_seq[i] if i < len(hops_seq) else cell.get("hops", 1)
            tag = hashlib.md5(f"{section}{typ}{qtype}{i}".encode()).hexdigest()[:6]
            if typ == "extraction":
                items.append({
                    "type": "extraction", "section": section, "hops": hops,
                    "question": f"[stub] {section} 抽取题 {tag}：该处的论断与论据是什么？",
                    "gold_subgraph": {
                        "nodes": [
                            {"type": "Claim", "name": f"stub-claim-{tag}"},
                            {"type": "Evidence", "name": f"stub-evidence-{tag}"},
                        ],
                        "edges": [
                            {"from": f"stub-argument-{tag}", "type": "SUPPORTS",
                             "to": f"stub-claim-{tag}"},
                        ],
                    },
                    "provenance": prov,
                })
            else:
                items.append({
                    "type": "reasoning", "qtype": qtype or "explanatory",
                    "section": section, "hops": hops,
                    "question": f"[stub] {section} 推理题 {tag}：作者怎么看？该怎么做？",
                    "gold_elements": [f"要素A-{tag}", f"要素B-{tag}"],
                    "must_not": [f"不得断言文档外的-{tag}"],
                    "rubric": {"要素召回": 0.5, "论证完整": 0.3, "无幻觉": 0.2},
                    "provenance": prov,
                })
        return {"items": items}


# ---------------------------------------------------------------------------
# 工厂：从 settings 构造抽取 / judge 两个独立 LLM
# ---------------------------------------------------------------------------
def _build_openai_llm(api_key: Optional[str], base_url: Optional[str],
                      model: Optional[str], name: str) -> Optional[OpenAILLM]:
    if not (api_key and base_url and model):
        return None
    from openai import AsyncOpenAI  # 延迟导入，便于离线环境
    return OpenAILLM(AsyncOpenAI(api_key=api_key, base_url=base_url), model, name=name)


def extraction_llm() -> LLM:
    """worker 模型：抽取 + 回答（推荐 DeepSeek）。唯一必需的 key。"""
    from .config import settings
    llm = _build_openai_llm(settings.worker_api_key, settings.worker_base_url,
                            settings.worker_model, name="worker")
    if llm is None:
        raise RuntimeError("worker(抽取/回答)模型未配置：需 KG_WORKER_API_KEY/KG_WORKER_BASE_URL/KG_WORKER_MODEL")
    return llm


def judge_llm(required: bool = False) -> Optional[LLM]:
    """可选的 API judge（自治模式用）。默认 required=False：未配置则返回 None。

    架构（双模式）：
      - 默认 agent 驱动模式：编排大脑 = coding agent（Claude Code/Codex），无需此模型。
      - 可选自治模式：配置百炼等独立 API 作 judge，可无人值守跑测试集判分/收敛决策。
    judge **必须不同于 worker 模型**。
    """
    from .config import settings
    llm = _build_openai_llm(settings.judge_api_key, settings.judge_base_url,
                            settings.judge_model, name="judge")
    if llm is None and required:
        raise RuntimeError("judge 模型未配置：需 KG_JUDGE_API_KEY/KG_JUDGE_BASE_URL/KG_JUDGE_MODEL")
    return llm
