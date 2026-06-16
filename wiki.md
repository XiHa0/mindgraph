# MindGraph Wiki — 完整代码结构

本文档逐模块说明整个代码库：职责、关键类/函数、数据流、接口与扩展点。
设计规范（schema/指标/测试集格式/循环编排）见 [`mindgraph/SPEC.md`](mindgraph/SPEC.md)。

---

## 1. 目录树

```
mindgraph/                       仓库根
├── README.md                     上手指南
├── wiki.md                       本文件
├── requirements.txt / pyproject.toml
├── .env.example                  环境变量模板
└── mindgraph/                   Python 包
    ├── __init__.py
    ├── SPEC.md                   设计规范（契约）
    ├── config.py                 配置：从 .env/环境变量读 worker/judge/neo4j
    ├── _log.py                   自带轻量日志器
    ├── llm.py                    可替换 LLM 层（worker / 可选 judge / 离线 Stub）
    ├── models.py                 数据模型：Chunk / TestItem / GoldSubgraph
    ├── cli.py                    ★ agent 驱动工具箱（chunk/extract/coverage/diagnose）
    ├── testset.schema.json       测试集 JSON Schema
    │
    ├── schema/
    │   ├── meta_ontology.json    meta-layer 节点/关系定义（机器可读）
    │   └── constraints.cypher    Neo4j 约束 + 索引建库脚本
    │
    ├── chunking/
    │   └── structural_chunker.py step 0：结构化切分（噪声过滤+分节+~900字+kind标注）
    │
    ├── induction/
    │   ├── inducer.py            step 1：LLMSchemaInducer（domain 概念表归纳）
    │   └── prompts/induce_concepts.md
    │
    ├── testset/
    │   ├── quota.py              分层配额规划（§3.2）
    │   ├── generator.py          按 cell 生成测试题
    │   ├── validate.py           结构 + 配额双校验
    │   ├── review.py             人工校验单 + verified-only 冻结
    │   ├── run.py                测试集 CLI（generate / freeze）
    │   └── prompts/testset_{extraction,reasoning}.md
    │
    ├── extraction/
    │   ├── extractor.py          step 3：TwoStageExtractor（先节点→后关系）
    │   ├── resolve.py            实体消歧（并查集合并 canonical+aliases）
    │   ├── neo4j_store.py        Neo4jGraphStore（带类型标签写入 + 读查询）
    │   └── prompts/extract_{nodes,edges}.md
    │
    ├── builder/
    │   ├── interfaces.py         ★ 步骤接口（7 个 ABC）+ 数据结构
    │   ├── loop.py               BuilderLoop 编排 + 收敛判定
    │   ├── coverage.py           CoverageEvaluator + GapDiagnoser（确定性）
    │   ├── reviser.py            DiagnosisReviser（闭环自改进）
    │   ├── stubs.py              离线 stub + InMemoryGraphStore + FileTestsetProvider
    │   └── run.py                自治模式 demo CLI
    │
    └── qa/
        ├── retriever.py          KeywordGraphRetriever（问题→子图）
        ├── answerer.py           LLMAnswerer（子图→回答）
        ├── judge.py              RubricJudge（rubric 三项打分）
        ├── scorer.py             JudgeAnswerScorer（检索→回答→判分，组装 answer_scorer）
        └── prompts/{answer,judge}.md
```

★ = 最常用的入口。

---

## 2. 端到端数据流（与 SPEC §4 对应）

```
doc_text
  │ ① chunking.StructuralChunker.split          → list[Chunk]（含 meta: kind/keywords）
  │ ② induction.LLMSchemaInducer.induce         → DomainSchema（concepts/aliases）
  │ ③ testset.*（generate→validate→freeze）      → 冻结 gold（list[TestItem]）
  │ ④ extraction.TwoStageExtractor.extract       → 写 GraphStore（节点→消歧→关系）
  │ ⑤ builder.CoverageEvaluator.evaluate         → RoundMetrics（node/edge/answer）
  │ ⑥ builder.GapDiagnoser.diagnose              → Diagnosis（四类错误 + 类型统计）
  │ ⑦ builder.DiagnosisReviser.revise            → 更新 ExtractionConfig（指令+few-shot）
  └ 回 ④ 直到 builder.BuilderLoop 判定收敛
运行时问答: qa.（retrieve → answer）
```

确定性步骤（⑤⑥、切分、校验、消歧、覆盖率）不需要 LLM；①②③④⑦的 LLM 调用走 `llm.py`。

---

## 3. 核心抽象

### 3.1 步骤接口 `builder/interfaces.py`

所有步骤是 ABC，真实实现与离线 stub 实现同一接口，编排层对二者无感：

| 接口 | 方法 | 真实实现 | 离线 stub |
|---|---|---|---|
| `Chunker` | `async split(doc, doc_id)` | `chunking.StructuralChunker` | `stubs.StubChunker` |
| `SchemaInducer` | `async induce(chunks)` | `induction.LLMSchemaInducer` | `stubs.StubInducer` |
| `TestsetProvider` | `load()` | `stubs.FileTestsetProvider` | — |
| `GraphStore` | `reset/upsert_nodes/upsert_edges/has_node/has_edge` | `extraction.Neo4jGraphStore` | `stubs.InMemoryGraphStore` |
| `GraphReader` | `node_names/incident_edges` | 同上（两个 store 都实现） | 同上 |
| `Extractor` | `async extract(chunks,schema,config,store)` | `extraction.TwoStageExtractor` | `stubs.SimulatedExtractor` |
| `Evaluator` | `async evaluate(gold,store,round)` | `builder.CoverageEvaluator` | — |
| `Diagnoser` | `diagnose(gold,store)` | `builder.GapDiagnoser` | — |
| `Reviser` | `async revise(diag,schema,config)` | `builder.DiagnosisReviser` | `stubs.StubReviser` |

数据结构（同文件）：`DomainSchema`、`ExtractionConfig`、`Diagnosis`、`RoundMetrics`。

### 3.2 数据模型 `models.py`

- `Chunk(id, text, section, order, doc_id, meta)` —— 原文切片（溯源层）。
- `TestItem` —— 一条 gold 测试（extraction 题用 `gold_subgraph`；reasoning 题用 `gold_elements`+`rubric`）。
- `GoldNode / GoldEdge / GoldSubgraph` —— 期望子图。
- 全部标准库 dataclass，带 `to_dict/from_dict`，便于 JSON 往返与离线测试。

### 3.3 LLM 层 `llm.py`

- `LLM`（ABC）`async complete_json(system, user, meta=)` —— 统一接口，返回解析后的 JSON。
- `OpenAILLM` —— OpenAI 兼容端点（DeepSeek 等），优先 `json_object` 强约束。
- `StubLLM` —— 离线确定性桩，据 `meta` 回填结构合法占位（`--dry-run` 用）。
- 工厂：`extraction_llm()`（worker，必需）/ `judge_llm(required=False)`（可选）。

### 3.4 配置 `config.py`

`Settings.load()` 从环境变量/`.env` 读取，提供模块级 `settings`。
干净命名 `KG_WORKER_*` / `KG_JUDGE_*` / `NEO4J_*`，并回退老命名便于迁移。

---

## 4. 各步骤模块详解

### ① 切分 `chunking/structural_chunker.py`
`StructuralChunker(max_chars=900)`：① 过滤噪声（TOC 点线/页码/OCR 占位）② 标题检测分 section
③ 缓冲段落到上限或遇标题切，不跨标题 ④ 超长无换行段按句子拆 ⑤ 标 `kind/keywords` 进 `Chunk.meta`。
零 LLM、确定性。

### ② schema 归纳 `induction/inducer.py`
`LLMSchemaInducer(llm, window_chars=6000)`：分窗扫全文抽候选概念 → 复用 `resolve_entities` 合并
→ 按"被多少窗口提到 + 置信度"排序取 topN → 产出 `DomainSchema(concepts, aliases)`。

### ③ 测试集 `testset/`
- `quota.plan(section_sizes, target)` → 分层 slot（extraction:reasoning≈1:1，qtype≈1:1，每章≥8，hops 50/35/15）。
- `TestsetGenerator.generate(chunks)` → 按 cell 调 LLM 出题，**强制** hops 配额。
- `validate.validate(items)` → 结构（对齐 meta 本体）+ 配额双校验；装了 jsonschema 再交叉校验。
- `review.export_review_markdown / freeze` → 人读校验单（分层抽样标⭐）+ verified-only 冻结。
- 铁律：gold 冻结后循环中不变；判分模型独立于抽取模型（见 SPEC §3.4）。

### ④ 抽取 `extraction/`
- `TwoStageExtractor.extract`：阶段一逐片段抽节点 → `resolve_entities` 消歧 → 写 store；
  阶段二在"该 chunk 命中的 canonical 节点"之间抽关系，端点经 `alias_map` 解析，未知端点丢弃。
  消费 `ExtractionConfig` 的 `node/edge_directives`、`few_shot`、`finer_chunking`（修正器注入）。
- `resolve.resolve_entities`：并查集把"同名 + 互为 alias"的表面说法并组，选最常见表面为 canonical，
  其余进 aliases；返回 `(canonical_nodes, alias_map)`。
- `neo4j_store.Neo4jGraphStore`：节点写 `:Type:KGNode` 双标签（类型标签匹配 constraints.cypher 的
  约束/索引；`:KGNode` 供通用读查询），标签走 meta 白名单防注入（`_node_write_queries`）。

### ⑤⑥ 评测与诊断 `builder/coverage.py`
- `CoverageEvaluator`：node/edge 覆盖率**精确**计算；`answer_score` 三来源——注入 `JudgeAnswerScorer`
  （真实判分）/ 不注入则用 `mean(node,edge)` 覆盖率代理（agent 模式另行抽样判分）。
- `GapDiagnoser`：把 gold 与图的差异归 `missing_node/missing_edge/wrong_edge/granularity`，
  并统计**类型层面**的 `missing_node_types / missing_edge_types / missing_edge_patterns`（供修正器）。

### ⑦ 修正 `builder/reviser.py`
`DiagnosisReviser.revise`：据 `dominant()` 给下一轮 `ExtractionConfig` 加**针对性**指令与
**type 级** few-shot（如 `Argument -SUPPORTS-> Claim`），并按需触发 `finer_chunking`。
指令去重限长防膨胀；few-shot 不含具体 gold 名称，防"教到测试集"。

### 编排 `builder/loop.py`
`BuilderLoop.run(doc, doc_id)`：切分→归纳→载 gold→（每轮：reset→extract→evaluate→diagnose→
收敛判定→revise）。`ConvergenceCriteria`：node≥0.85 & edge≥0.75 & answer≥0.80 且进入平台期，
或达 `max_rounds`。返回 `BuildResult`（含每轮 `RoundMetrics` 历史）。

### 问答 `qa/`
- `KeywordGraphRetriever.retrieve(question)`：中文 bigram 重合召回入口节点 → 扩 1 跳 → 渲染子图文本。
- `LLMAnswerer.answer`：只依据子图作答（问答 agent 雏形）。
- `RubricJudge.score`：rubric 三项（要素召回/论证完整/无幻觉）打分 + 加权。
- `JudgeAnswerScorer`：组装"检索→回答→判分"，作为 `CoverageEvaluator` 的 `answer_scorer`。

---

## 5. 两种运行模式

- **agent 驱动（默认）**：`cli.py` 暴露 `chunk/extract/coverage/diagnose` 四个命令，各步读写 JSON；
  编排大脑 = coding agent，读 `coverage.json`/`diag.json` 决策。只需 worker 一个 key。
- **自治（可选）**：`builder/run.py` / SPEC「全真接线」用 `BuilderLoop` + 注入 `JudgeAnswerScorer(API judge)`
  无人值守跑到收敛。需第二个 key。

---

## 6. 扩展点（替换某一步只需实现对应接口）

| 想替换 | 实现接口 | 提示 |
|---|---|---|
| 切分策略（如 embedding 语义切分） | `Chunker` | 产出带 `meta` 的 `Chunk` 列表即可 |
| 抽取后端（如别的 LLM/规则） | `Extractor` + `GraphStore` | 与 gold 解耦，只管写图 |
| 图存储（如其它图库） | `GraphStore` + `GraphReader` | 实现读写两面 |
| 召回（如全文/向量索引） | `Retriever` | constraints.cypher 已建全文/向量索引 |
| 判分（如人评/别的 judge） | `CoverageEvaluator(answer_scorer=...)` | 签名 `(reasoning_gold, store)->float` |

---

## 7. 离线验证（无需 LLM / Neo4j）

```bash
# 工具箱：切分 + 覆盖率 + 诊断（用 memory 图）
python -m mindgraph.cli chunk    --doc doc.txt --out chunks.json
python -m mindgraph.cli coverage --gold gold.json --graph graph.json --out coverage.json
python -m mindgraph.cli diagnose --gold gold.json --graph graph.json --out diag.json

# 测试集管线（StubLLM 离线跑通）
python -m mindgraph.testset.run generate --demo --dry-run --out-dir ./out

# 自治循环 demo（StubReviser + SimulatedExtractor，演示收敛曲线）
python -m mindgraph.builder.run --gold ./out/testset.gold.json
```
