"""Builder 循环的步骤接口与数据结构（SPEC §4 / §2）。

每个步骤是一个 ABC。真实实现（DeepSeek 抽取、Neo4j 存储、judge 评测）与离线 stub
都实现同一接口，循环编排（loop.py）对二者无感。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import Chunk, TestItem


# ---------------------------------------------------------------------------
# 贯穿循环的数据结构
# ---------------------------------------------------------------------------
@dataclass
class DomainSchema:
    """domain-layer：在冻结的 meta-layer 上长出的、该作者特有的概念/术语/few-shot 种子。"""
    concepts: list[str] = field(default_factory=list)        # 规范概念名
    aliases: dict[str, list[str]] = field(default_factory=dict)  # canonical -> 同义说法
    few_shot: list[dict[str, Any]] = field(default_factory=list)  # 抽取示范
    notes: str = ""


@dataclass
class ExtractionConfig:
    """抽取这一步的可调参数。Reviser 每轮据诊断更新它。

    真实抽取（TwoStageExtractor）消费 directives/few_shot/finer_chunking；
    completeness 仅供离线 SimulatedExtractor 模拟逐轮改进。
    """
    node_directives: list[str] = field(default_factory=list)   # 注入节点抽取 prompt
    edge_directives: list[str] = field(default_factory=list)   # 注入关系抽取 prompt
    node_few_shot: list[str] = field(default_factory=list)     # 节点抽取 few-shot 块
    edge_few_shot: list[str] = field(default_factory=list)     # 关系抽取 few-shot 块
    finer_chunking: bool = False                               # 下轮是否更细切分
    chunk_overlap: int = 0
    completeness: float = 0.4                                  # 仅 SimulatedExtractor 用
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Diagnosis:
    """诊断产出：错误归四类（SPEC §2.2）+ 修法建议。

    type 级统计供 Reviser 生成**针对性**指令/few-shot——只用类型层面信息，
    不带入具体 gold 答案，避免"教到测试集"的泄漏。
    """
    missing_node: int = 0
    missing_edge: int = 0
    wrong_edge: int = 0
    granularity: int = 0
    missing_node_types: dict[str, int] = field(default_factory=dict)      # 节点类型 -> 漏抽数
    missing_edge_types: dict[str, int] = field(default_factory=dict)      # 关系类型 -> 漏连数
    # 抽象关系模式 (from_type, edge_type, to_type)，type 级，无具体名称
    missing_edge_patterns: list[tuple[str, str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.missing_node + self.missing_edge + self.wrong_edge + self.granularity

    def dominant(self) -> str:
        cats = {"missing_node": self.missing_node, "missing_edge": self.missing_edge,
                "wrong_edge": self.wrong_edge, "granularity": self.granularity}
        return max(cats, key=cats.get)


@dataclass
class RoundMetrics:
    """一轮的评测结果（SPEC §2）。"""
    round_index: int
    node_cov: float
    edge_cov: float
    answer_score: float
    diagnosis: Optional[Diagnosis] = None

    @property
    def total_score(self) -> float:
        return self.node_cov + self.edge_cov + self.answer_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_index,
            "node_cov": round(self.node_cov, 4),
            "edge_cov": round(self.edge_cov, 4),
            "answer_score": round(self.answer_score, 4),
            "diagnosis": vars(self.diagnosis) if self.diagnosis else None,
        }


# ---------------------------------------------------------------------------
# 步骤接口
# ---------------------------------------------------------------------------
class Chunker(abc.ABC):
    """step 0：切分。默认结构化（噪声过滤 + 标题分节 + 大小上限），可选 LLM 精修。"""
    @abc.abstractmethod
    async def split(self, doc_text: str, doc_id: str) -> list[Chunk]: ...


class SchemaInducer(abc.ABC):
    """step 1：在 meta-layer 上长出 domain-layer。"""
    @abc.abstractmethod
    async def induce(self, chunks: list[Chunk]) -> DomainSchema: ...


class TestsetProvider(abc.ABC):
    """step 2：提供已冻结的 gold 测试集（交付3 的产物）。"""
    @abc.abstractmethod
    def load(self) -> list[TestItem]: ...


class GraphStore(abc.ABC):
    """图存储（Neo4j / 内存 stub）。"""
    @abc.abstractmethod
    def reset(self) -> None: ...
    @abc.abstractmethod
    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None: ...
    @abc.abstractmethod
    def upsert_edges(self, edges: list[dict[str, Any]]) -> None: ...
    @abc.abstractmethod
    def has_node(self, name: str) -> bool: ...
    @abc.abstractmethod
    def has_edge(self, frm: str, etype: str, to: str) -> bool: ...


class GraphReader(abc.ABC):
    """图的只读检索面（评测/问答用）。GraphStore 实现一般同时实现它。"""
    @abc.abstractmethod
    def node_names(self) -> set[str]: ...
    @abc.abstractmethod
    def incident_edges(self, name: str) -> list[tuple[str, str, str]]:
        """返回与 name 相连的边 (from, type, to)。"""
        ...


class Extractor(abc.ABC):
    """step 3：两阶段抽取（先节点、后关系）并写入 GraphStore。"""
    @abc.abstractmethod
    async def extract(self, chunks: list[Chunk], schema: DomainSchema,
                      config: ExtractionConfig, store: GraphStore) -> None: ...


class Evaluator(abc.ABC):
    """step 4：跑测试集，算 node_cov / edge_cov / answer_score。"""
    @abc.abstractmethod
    async def evaluate(self, gold: list[TestItem], store: GraphStore,
                       round_index: int) -> RoundMetrics: ...


class Diagnoser(abc.ABC):
    """step 5：把 gold 与图的差异归四类。"""
    @abc.abstractmethod
    def diagnose(self, gold: list[TestItem], store: GraphStore) -> Diagnosis: ...


class Reviser(abc.ABC):
    """step 6：据诊断更新 schema / 抽取配置。返回下一轮的 (schema, config)。"""
    @abc.abstractmethod
    async def revise(self, diagnosis: Diagnosis, schema: DomainSchema,
                     config: ExtractionConfig) -> tuple[DomainSchema, ExtractionConfig]: ...
