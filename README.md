# MindGraph

把**单作者**的文档 / 讲课文字稿（10万~50万字），通过一个**评测驱动的循环**，自动构建成
**结构化知识图谱（Neo4j）+ 可问答 agent**。

> 一句话：朴素 RAG 检索到的是孤立文本块——它没教会模型**怎么用**这些片段。
> MindGraph 把作者的**论证结构**（论断、论据、方法、适用条件、因果……）抽成带形状的子图，
> 检索时返回的不是碎片，而是带骨架的子图，回答因此是结构化的。

---

## 核心思路

1. **两层 schema**——不要追求万能本体。
   - **meta-layer（冻结）**：所有"思想/讲课"类文档共享的*认识论/论证结构*（见下）。
   - **domain-layer（每文档归纳）**：该作者特有的概念、术语、别名。
2. **评测驱动的闭环**——造测试集 → 抽取 → 算覆盖率 → 诊断差异 → 针对性修正 → 再抽，直到收敛。
   "可通用"的不是 schema，而是**造 schema 的这个循环**。
3. **两个模型，职责分明**：

   | 模型 | 角色 | 干什么 | 需要 key |
   |---|---|---|---|
   | **编排大脑 = coding agent（Claude Code/Codex）** | 决策 | 造/校验测试集、定/改 schema、跑抽取、读报告**决定要不要再抽一轮 / 改 schema / 改策略** | 否（就是 agent 本身） |
   | **worker（推荐 DeepSeek V4 Pro）** | 干活 | 两阶段**抽取** + 运行时**回答** | **是（唯一必需）** |
   | （可选）API judge | 判分 | 仅"自治模式"给 reasoning 题打分 | 可选 |

4. **两种运行模式**：
   - **agent 驱动（默认）**：编排大脑 = coding agent，用工具箱 CLI 逐步驱动、读 JSON 决策。只需 worker 一个 key。
   - **自治（可选）**：配一个 API judge，`BuilderLoop` 无人值守跑到收敛。

---

## 数据流

```
 文档
  │  chunk（结构化切分：噪声过滤→标题分节→~900字→kind标注）
  ▼
 chunks ──► schema 归纳（domain 概念表+别名）
  │
  │  造 ≥100 条分层 gold 测试集 → 人工校验冻结
  ▼
 抽取（DeepSeek 两阶段：先节点→实体消歧→在已知节点间抽关系）──► Neo4j
  ▼
 评测（node/edge 覆盖率，确定性）+ 诊断（漏节点/漏关系/错连/粒度，按类型统计）
  ▼
 修正（据诊断给下一轮抽取加针对性指令 + type 级 few-shot）──┐
  ▲                                                          │
  └───────────────── 未收敛则再抽一轮 ◄──────────────────────┘
  ▼ 收敛（node≥0.85, edge≥0.75, answer≥0.80 且进入平台期）
 知识图谱 + 问答 agent（检索子图→回答）
```

---

## meta 本体（节点 / 关系）

```
节点: Concept 概念 · Claim 论断 · Principle 原则 · Method 方法 · Argument 论证 ·
      Evidence 论据 · Condition 适用条件 · Counterpoint 反例 · Analogy 类比 ·
      MentalModel 框架 · Question 问题 · ReasoningPattern 思考方式
关系: SUPPORTS/REFUTES · USES · EXEMPLIFIES · APPLIES_WHEN · ANSWERS ·
      DEPENDS_ON/PART_OF/REFINES/GENERALIZES · CAUSES/ENABLES · CONTRASTS_WITH ·
      REALIZES · COMPOSED_OF
```

两条问答"脊柱"（共用一套节点，一份图同时服务解释型与应用型问题）：

```
解释型: Question ←ANSWERS← Claim ←SUPPORTS← Argument →USES→ Evidence
应用型: Condition ←APPLIES_WHEN← Method →REALIZES→ Principle
```

完整规范见 [`mindgraph/SPEC.md`](mindgraph/SPEC.md)；完整代码结构见 [`wiki.md`](wiki.md)。

---

## 安装

```bash
git clone <your-repo-url> mindgraph
cd mindgraph
python -m venv .venv && . .venv/Scripts/activate   # Windows；Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
# 或可安装为包（带 mindgraph 命令）：pip install -e .
```

需要一个 **Neo4j 5.x**（本地或远程）。首次使用前执行一次建库脚本：

```bash
cypher-shell -u neo4j -p <password> -f mindgraph/schema/constraints.cypher
```

## 配置

```bash
cp .env.example .env
# 编辑 .env：至少填 KG_WORKER_*（worker 模型）和 NEO4J_*
```

只有 worker 的 key 是必需的。judge（`KG_JUDGE_*`）留空即可——默认 agent 驱动模式不需要它。

---

## 快速开始

### A. agent 驱动模式（默认推荐）

编排大脑就是你的 coding agent（Claude Code/Codex）。它逐步运行工具箱、读 JSON 决策：

```bash
# 1) 切分
python -m mindgraph.cli chunk    --doc doc.txt --doc-id 作者X --out chunks.json

# 2) （agent 读 chunks，写 schema.json: {"concepts":[...],"aliases":{...}}；造并冻结 gold.json）

# 3) 抽取入库（DeepSeek worker）
python -m mindgraph.cli extract  --chunks chunks.json --schema schema.json \
                                  --store neo4j --doc-id 作者X

# 4) 评测 + 诊断（确定性，产出 JSON 供 agent 读）
python -m mindgraph.cli coverage --gold gold.json --store neo4j --doc-id 作者X --out coverage.json
python -m mindgraph.cli diagnose --gold gold.json --store neo4j --doc-id 作者X --out diag.json

# 5) agent 读 coverage.json + diag.json（dominant / missing_*_types / 抽象关系模式）
#    → 决定改 schema 概念表 / 加抽取指令 / 调切分 / 是否再抽一轮 → 回到 3
```

`extract` 也支持 `--store memory --graph-out graph.json` 在没有 Neo4j 时离线验证管线。

### B. 自治模式（可选，需第二个 key）

配好 `KG_JUDGE_*` 后，用 `BuilderLoop` 无人值守跑到收敛——接线见 [`mindgraph/SPEC.md`](mindgraph/SPEC.md) 的「全真接线」。

### 造测试集

```bash
# 用 worker 起草 ≥100 条分层 gold（也可让 agent 直接写），再人工校验冻结
python -m mindgraph.testset.run generate --chunks chunks.json --target 150 --out-dir ./out
python -m mindgraph.testset.run freeze --draft ./out/testset.draft.json --out ./out/gold.json
```

---

## 设计要点（为什么这么做）

- **切分按论证单元、先过滤噪声**：思想文档一个论证常跨多段，定长切块会把论断和论据切散；
  TOC/页码/OCR 占位是检索噪声头号来源，必须先滤掉。
- **抽取分两阶段**：先抽节点、消歧合并，再"在已知节点之间"抽关系——质量和成本都更好。
- **实体消歧**：作者用不同说法指同一概念，用并查集合并到 canonical+aliases，否则子图断裂。
- **防"教到测试集"**：修正器的 few-shot 只用**类型层面**抽象模式，不带入具体 gold 答案。
- **判分与抽取分离**：worker 抽取，judge 判分——避免"同一个模型判自己抽的"。

## 文档

- [`wiki.md`](wiki.md) —— 完整代码结构（每个模块/类/接口/数据流）。
- [`mindgraph/SPEC.md`](mindgraph/SPEC.md) —— 设计规范（schema / 覆盖率指标 / 测试集格式 / 循环编排）。

## 许可证

[MIT](LICENSE) © XiHa0
