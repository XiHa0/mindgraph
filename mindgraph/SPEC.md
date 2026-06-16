# MindGraph 规范（v1）

> 目标：把"针对某份文档手工调通 schema + 抽取规则 + 测试集"的过程（源自一套已验证的成功经验）
> 固化成一个**评测驱动的自动循环**。丢进一份文档 → 自动产出一个针对该作者的知识图谱 + 可问答 agent。
>
> 适用语料：**单作者**的思想总结 / 讲课文字稿（10万~50万字）。
> 问答场景：**解释型 + 应用型**两者都要。

本规范是循环里每一步的"契约"。三件事被钉死后，后续模块只是按规范填空：
1. **Schema**（§1）——meta-layer 冻结、domain-layer 由 builder 归纳。
2. **覆盖率指标**（§2）——驱动循环、决定何时收敛。
3. **测试集格式**（§3）——gold 集的结构与配额。

循环编排见 §4，技术栈对接见 §5。

---

## §1 Schema

设计原则：**不要追求万能本体**。分两层——

- **meta-layer（冻结）**：所有"思想/讲课"类文档共享的*认识论/论证结构*。这是让回答"结构化"的东西。
- **domain-layer（每份文档学习）**：该作者特有的概念、术语、心智模型，由 builder 实例化为 meta 节点的实例。

### 1.1 节点类型（meta-layer）

每个节点都带这些**公共属性**：

| 属性 | 类型 | 说明 |
|---|---|---|
| `id` | string | 稳定主键，`{type}:{slug}`，消歧后唯一 |
| `name` | string | 规范名（消歧后的 canonical 名） |
| `summary` | string | 一句话摘要 |
| `aliases` | string[] | 同义说法（entity resolution 产物） |
| `source_chunk_ids` | string[] | 溯源到的原文 chunk |
| `salience` | float 0-1 | 重要度（被引用/连接的频度归一） |
| `author_confidence` | float 0-1 | 抽取置信度 |

类型专有属性：

| 节点类型 | 含义 | 专有属性 |
|---|---|---|
| `Concept` | 概念/术语（domain 层在此大量实例化） | `definition`, `abstraction_level` |
| `Claim` | 论断/主张（作者认为"是什么"） | `polarity: assert\|reject`, `certainty` |
| `Principle` | 原则/应然/价值主张 | `imperative` |
| `Method` | 方法/做法（"怎么做"）— **应用型主力** | `steps[]` |
| `Argument` | 论证（支撑/反驳某 Claim） | `form: deductive\|analogical\|empirical\|...` |
| `Evidence` | 论据/例子/数据/引用 | `kind: example\|data\|anecdote\|quote` |
| `Condition` | 适用条件/边界 — **应用型主力** | `scope` |
| `Counterpoint` | 作者反对的观点/反例 | — |
| `Analogy` | 类比/隐喻（思想文档高频） | `source_domain`, `target_domain` |
| `MentalModel` | 框架（多概念组合） | `components[]` |
| `Question` | 作者在回答的问题 | — |
| `ReasoningPattern` | 作者的思考方式（检索时一并取出） | `trigger`, `template` |

> `Chunk`（原文切片）作为**溯源节点**单独存在，不属于 meta 知识层：
> `(:Chunk {id, text, section, order, doc_id})`，所有知识节点经 `source_chunk_ids` 关联它。

### 1.2 关系类型（meta-layer）

所有关系都带 `source_chunk_id` 与 `confidence`。

```
(:Argument)   -[:SUPPORTS|REFUTES]-> (:Claim)
(:Argument)   -[:USES]->             (:Evidence)
(:Evidence)   -[:EXEMPLIFIES]->      (:Concept|:Claim)
(:Claim|:Method)-[:APPLIES_WHEN]->   (:Condition)
(:Claim)      -[:ANSWERS]->          (:Question)
(:Concept)    -[:DEPENDS_ON|PART_OF|REFINES|GENERALIZES]-> (:Concept)
(:*)          -[:CAUSES|ENABLES]->   (:*)
(:*)          -[:CONTRASTS_WITH]->   (:*)
(:Method)     -[:REALIZES]->         (:Principle)   // 连接"应用"与"理念"
(:MentalModel)-[:COMPOSED_OF]->      (:Concept)
```

### 1.3 两条问答"脊柱"（共用一套节点，所以一份图同时服务两类问题）

```
解释型: Question ←ANSWERS← Claim ←SUPPORTS← Argument →USES→ Evidence
                                 ↘APPLIES_WHEN→ Condition
                                 ↘CONTRASTS_WITH→ Counterpoint

应用型: Condition ←APPLIES_WHEN← Method →REALIZES→ Principle
                                       ↘USES→ Evidence(example)
```

### 1.4 检索子图模板

agent 命中入口节点后，按问题类型展开固定形状的子图（见上两条脊柱），连同关联的
`ReasoningPattern` 一起返回。**返回的不是孤立文本块，而是带形状的子图**——这正是
"教会 AI 怎么用片段"的关键。

---

## §2 覆盖率指标（驱动循环的数字）

```
KnowledgeUnit = 文档里一条原子知识（gold 集枚举：一个定义 / 一条论断 / 一条因果 / 一个例子）

节点覆盖率 node_cov  = 正确表示且可检索的单元数 / gold 单元总数
关系覆盖率 edge_cov  = 正确连出的关系数 / gold 关系总数      ← 思想文档最常系统性丢这个
答案得分  answer_score = 结构化回答题的 rubric 平均分（见 §3.3）
```

### 2.1 收敛判定（同时满足才停）

```
node_cov     >= 0.85
edge_cov     >= 0.75
answer_score >= 0.80
且 连续 2 轮 (node_cov + edge_cov + answer_score) 提升 < 0.02   // 防止抠长尾烧钱
或 已达 MAX_ROUNDS（默认 8）                                      // 硬上限
```

### 2.2 错误归类（诊断步骤产出，决定怎么修）

| 错误类 | 判定 | 默认修法 |
|---|---|---|
| `missing_node` | gold 单元在图中无对应节点 | 调切分（漏读）/ 抽取 prompt 加该类型 few-shot |
| `missing_edge` | 节点都在但孤立、未连出 gold 关系 | 强化"第二阶段关系抽取"的 few-shot |
| `wrong_edge` | 连了，但关系类型选错 | 在 prompt 里补该关系类型的区分定义 |
| `granularity` | 一句话拆太碎 / 多个论点并成一个 | 调切分粒度 / 抽取的合并规则 |

builder 读到错误分布后，决定改 **schema / 抽取 prompt / 切分** 中的哪一项（§4 step 6）。

---

## §3 测试集格式（≥100 条，分层配额）

### 3.1 单条结构（JSON，schema 见 `testset.schema.json`）

```json
{
  "id": "t0042",
  "type": "extraction | reasoning",
  "qtype": "explanatory | applied",        // reasoning 题必填
  "section": "第3章/某主题",                // 来源分层
  "hops": 1,                               // 1=单跳 2=多跳 3=跨章节综合
  "question": "作者认为 X 的根本原因是什么？",

  // —— extraction 题：gold = 期望子图 ——
  "gold_subgraph": {
    "nodes": [{"type": "Claim", "name": "..."}, {"type": "Evidence", "name": "..."}],
    "edges": [{"from": "...", "type": "SUPPORTS", "to": "..."}]
  },

  // —— reasoning 题：gold = 要素清单 + rubric ——
  "gold_elements": ["必须提到条件A", "必须给出原则B", "应引用例子C"],
  "must_not": ["不得断言文档外的D"],          // 反幻觉
  "rubric": {"要素召回": 0.5, "论证完整": 0.3, "无幻觉": 0.2},

  "provenance": ["chunk_017", "chunk_018"],  // gold 溯源
  "verified": false                          // 人工校验后置 true 并冻结
}
```

### 3.2 配额（100 条的硬约束）

| 维度 | 配额 |
|---|---|
| `extraction : reasoning` | ≈ 1 : 1 |
| reasoning 内 `explanatory : applied` | ≈ 1 : 1 |
| 每个主要章节 | ≥ 8 条 |
| `hops` 分布 1 / 2 / 3 | ≈ 50% / 35% / 15% |

100 是下限。50万字文档建议 **150–250 条**。

### 3.3 答案评分（reasoning 题，rubric 三项）

- **要素召回**：`gold_elements` 命中比例。
- **论证完整**：是否给出了该问题脊柱所要求的结构（解释型要 Claim+Argument+Evidence；应用型要 Method+Condition+Principle）。
- **无幻觉**：未触犯 `must_not`，未引入文档外断言。

### 3.4 质量保证铁律

1. gold 集由**强模型生成 + 抽样人工校验**，定稿后 `verified=true` 并**冻结**。
2. 循环里变的是抽取，**测试集不变**——否则覆盖率曲线无意义、无法判断收敛。
3. 评测用**独立 judge 模型**，与抽取模型分离。
4. **DeepSeek 只做全量抽取，绝不判卷**——否则测的是"两个便宜模型互相点头"。

---

## §4 Builder 循环（编排）

```
输入: 一份文档 doc

0. 预处理   : 语义切分（按论证单元，不是定长！）→ 写入 Chunk 节点，标 KnowledgeUnit
1. schema 归纳: 在 meta-layer 上长出 domain-layer（概念表/术语表/few-shot 种子）
2. 测试集生成 : ≥100 条分层 gold（强模型）→ 人工校验冻结（§3）        [交付3]
3. 抽取      : DeepSeek 两阶段（先节点、后关系）→ 写入 Neo4j
4. 评测      : 跑测试集 → node_cov / edge_cov / answer_score（judge 模型）
5. 诊断      : 错误归四类（§2.2），统计分布
6. 修正      : builder 据诊断改 schema / 抽取 prompt / 切分 → 回 step 3
   收敛判定 : 满足 §2.1 则停；否则继续；达 MAX_ROUNDS 停

产物: schema.json + 抽取 prompt + 已填充的图 + 评测报告
```

要点：
- **切分用语义切分**：思想文档一个论证常跨多段，定长切块会把 Claim 与 Evidence 切散，关系就抽不出来。
- **抽取分两阶段**：先抽节点，再"在已知节点集合里"抽关系。一把抽 schema 复杂会乱。
- **概念归一化（entity resolution）**：作者用不同说法指同一概念，需要消歧合并到 `aliases`，否则图里全是重复节点、子图断裂。这是思想文档比普通 KG 更难之处。
- **meta-layer 先冻结、domain-layer 才循环**：连 meta 都每轮改，循环不收敛。

---

## §5 模型职责与运行模式（重要）

**两个模型，职责分明：**

| 模型 | 角色 | 干什么 | 是否需 key |
|---|---|---|---|
| **Model 1 = coding agent（Claude Code/Codex）** | **编排大脑** | 造/校验测试集、定/改 schema、跑抽取代码、入库、读覆盖率+诊断报告**决定要不要再抽一轮 / 改 schema / 改抽取策略** | 否（就是 agent 本身） |
| **Model 2 = worker（推荐 DeepSeek V4 Pro）** | **worker** | 两阶段**抽取** + 运行时**回答** | 是（唯一必需 key，`KG_WORKER_*`） |
| （可选）API judge | 仅判分/自治 | reasoning 题 rubric 判分；自治模式下无人值守判分 | 可选（`KG_JUDGE_*`，`judge_llm(required=False)`） |

> Model 1 不做 token 级抽取——它**编排并判断**；Model 2 做实际抽取与回答。
> 抽取与判分若都用 LLM，则**必须不同模型**（§3.4）。
> 环境变量见 `.env.example`；配置加载在 `mindgraph/config.py`。

**两种运行模式：**

- **agent 驱动模式（默认）**：编排大脑 = coding agent。用工具箱 CLI 逐步驱动、读 JSON 决策：
  ```
  python -m mindgraph.cli chunk    --doc doc.txt --out chunks.json
  # agent 读 chunks → 写 schema.json {"concepts":[...],"aliases":{...}}；造并冻结 gold
  python -m mindgraph.cli extract  --chunks chunks.json --schema schema.json --store neo4j --doc-id 作者X
  python -m mindgraph.cli coverage --gold gold.json --store neo4j --doc-id 作者X --out coverage.json
  python -m mindgraph.cli diagnose --gold gold.json --store neo4j --doc-id 作者X --out diag.json
  # agent 读 coverage.json + diag.json（含 dominant / missing_*_types / 抽象关系模式）→ 决定改什么、是否再抽
  ```
  此模式只需 **DeepSeek 一个 key**；answer_score 用覆盖率代理，agent 另行抽样判分。
- **自治模式（可选）**：`mindgraph.builder.run` 注入 `JudgeAnswerScorer(RubricJudge(judge_llm()))`，
  配一个 API judge 即可无人值守跑 `BuilderLoop` 到收敛。需第二个 key。

**其它对接（独立仓库自包含）：** prompt 内置于各模块的 `prompts/` 目录；配置 `mindgraph/config.py`
（读 `.env`/环境变量）；日志 `mindgraph/_log.py`；图存储 Neo4j（`.env` 配 `NEO4J_*`）。

---

## 附：模块目录规划

```
mindgraph/
├── SPEC.md                  ← 本文件（契约）
├── llm.py                   ← 可替换 LLM 层（DeepSeek worker + 可选 API judge + 离线 Stub）
├── cli.py                   ← agent 驱动工具箱（chunk/extract/coverage/diagnose，默认模式）
├── models.py                ← Chunk / TestItem 等数据模型
├── schema/
│   ├── constraints.cypher   ← Neo4j 约束 + 索引（建库脚本）
│   └── meta_ontology.json   ← meta-layer 节点/关系定义（机器可读）
├── testset.schema.json      ← 测试集 JSON Schema（§3）
├── testset/                 ← [交付3] 生成 + 配额 + 校验 + 人工校验冻结
├── builder/                 ← [交付2] 循环编排 + 收敛 + 覆盖率/诊断 + 修正器 + 离线 stub
├── chunking/                ← 结构化切分（采用已验证配方）
├── induction/               ← schema 归纳（domain 概念表）
├── extraction/              ← 两阶段抽取(extractor) + 实体消歧(resolve) + Neo4j 存储
└── qa/                      ← 检索→回答→judge 打分；也是问答 agent 雏形
```

## 各步骤实现状态

| 步骤 | 实现 | 状态 |
|---|---|---|
| 循环编排 / 收敛 | `builder/loop.py` | ✅ 真实 |
| 覆盖率评测 / 差异诊断 | `builder/coverage.py` | ✅ 真实 |
| 测试集生成 + 校验 + 冻结 | `testset/*` | ✅ 真实 |
| 两阶段抽取 + 实体消歧 | `extraction/extractor.py`,`resolve.py` | ✅ 真实 |
| Neo4j 图存储 | `extraction/neo4j_store.py` | ✅ 真实（标签 :KGNode 简化，见文件 NOTE） |
| judge 评测（answer_score） | `qa/*` | ✅ 真实 |
| 修正器 Reviser（闭环自改进） | `builder/reviser.py:DiagnosisReviser` | ✅ 真实（type 级 few-shot，防泄漏） |
| 结构化切分 | `chunking/structural_chunker.py` | ✅ 真实（常见做法：噪声过滤+分节+~900字+kind标注） |
| schema 归纳 | `induction/inducer.py:LLMSchemaInducer` | ✅ 真实（分窗抽概念→消歧合并→频次排序→domain词表） |
| Neo4j 带类型标签写入 | `extraction/neo4j_store.py:_node_write_queries` | ✅ 真实（:Type:KGNode 双标签，白名单防注入） |

> **全部步骤已真实、无遗留简化**。闭环：诊断 → 修正器注入针对性指令/few-shot → 抽取改善 → 覆盖率上升 → 收敛。
> 切分采用 已验证的切分配方（结构化、零 LLM、先过滤 TOC/页码/OCR 噪声）。
> Neo4j 节点写**类型标签 + :KGNode 共享标签**：前者匹配本文件约束/索引，后者支撑通用读查询。

## 全真接线（一份真实文档跑到收敛）

```python
from mindgraph.builder.loop import BuilderLoop, ConvergenceCriteria
from mindgraph.builder.coverage import CoverageEvaluator, GapDiagnoser
from mindgraph.builder.reviser import DiagnosisReviser
from mindgraph.builder.stubs import FileTestsetProvider
from mindgraph.chunking.structural_chunker import StructuralChunker
from mindgraph.induction.inducer import LLMSchemaInducer
from mindgraph.extraction.extractor import TwoStageExtractor
from mindgraph.extraction.neo4j_store import Neo4jGraphStore
from mindgraph.qa.answerer import LLMAnswerer
from mindgraph.qa.judge import RubricJudge
from mindgraph.qa.scorer import JudgeAnswerScorer
from mindgraph.llm import extraction_llm, judge_llm

store = Neo4jGraphStore(doc_id="作者X")                       # 先跑过 schema/constraints.cypher
# answer_score：配了 API judge 就真实判分，否则不传 scorer→用覆盖率代理（仍可收敛）
_judge = judge_llm(required=False)
scorer = (JudgeAnswerScorer(reader=store, answerer=LLMAnswerer(extraction_llm()),
                            judge=RubricJudge(_judge)) if _judge else None)
loop = BuilderLoop(
    chunker=StructuralChunker(),           # 已验证的结构化切分
    inducer=LLMSchemaInducer(judge_llm(required=False) or extraction_llm()),  # 归纳 domain 概念表
    testset=FileTestsetProvider("testset.gold.json"),   # 交付3 冻结的 gold
    store=store, extractor=TwoStageExtractor(extraction_llm()),
    evaluator=CoverageEvaluator(answer_scorer=scorer),   # scorer=None → 覆盖率代理
    diagnoser=GapDiagnoser(), reviser=DiagnosisReviser(),
    criteria=ConvergenceCriteria())
result = await loop.run(open("doc.txt", encoding="utf-8").read(), doc_id="作者X")
```
