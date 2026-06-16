# MindGraph

[English](README.en.md) | 简体中文

![license](https://img.shields.io/badge/license-MIT-blue.svg)
![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![neo4j](https://img.shields.io/badge/Neo4j-5.x-008CC1.svg)
![status](https://img.shields.io/badge/status-v0.1%20early-orange.svg)

把单一作者的大量文字（著作、讲稿、笔记，约 10 万–50 万字）构建成一个知识图谱，
让问答助手能沿着作者本人的论证结构作答，而不是复述检索到的零散段落。

## 背景：要解决的问题

假设你手上有某一个人的大量文字——一位思考者的文集、一门课程的讲稿、一位从业者的笔记，
你想基于它做一个能"按这个人的思路"回答问题的助手。

常见做法是 RAG：把文本切块、向量化、检索 top-k 段落塞进提示词。它在这个场景有几个具体局限：

- 检索回来的是**互相孤立的文本块**——命中的这段，和它前后段落之间的逻辑关系没有被保留。
- 模型只能**复述**命中的段落，难以还原作者完整的推理：一个论断背后的依据、适用条件、反例；
  或一个方法的步骤、何时适用、背后的原则。
- 文本块之间没有显式关系，模型**无法沿着作者的推理链**做多跳。

知识图谱能缓解这些问题——它把"论断""依据""方法""条件""因果"等显式建成节点和关系，
检索时返回一张带结构的子图。但构建一个**质量够用**的知识图谱有现实门槛：

1. 要为具体作者设计本体（schema）；
2. 要写抽取规则、处理同一概念的不同说法；
3. 还要有办法**衡量**抽得全不全、对不对，并据此改进。

这通常是一次性的人工工程，换一个作者就得从头再来。MindGraph 想解决的，
就是把"针对某作者构建知识图谱"这件事，变成一个**可复用、可度量、可迭代**的流程。

## 它做什么

给定一份单作者语料，MindGraph 会：

1. 把文档切成保留论证完整性的片段（而非定长切块）；
2. 归纳出该作者的领域概念表（domain schema）；
3. 生成一套带标准答案（gold）的测试集，用来度量抽取质量；
4. 用大模型分两阶段抽取节点与关系，写入 Neo4j；
5. 计算覆盖率、按类型定位缺漏，据此调整抽取策略，循环到收敛；
6. 提供基于子图的检索与问答。

固定不变的是一套面向"观点/论证"的元本体（论断、依据、方法、条件……）；
随每份文档变化的是作者自己的概念与术语。

### 适用 / 不适用

- **适用**：单一作者的思想性 / 教学性长文本，需要结构化、可追溯的问答。
- **不适用**：多作者百科式知识库、通用领域 KG 抽取、对实时性要求高的场景。
  它也不替你产出内容——你需要先有原始语料。

## 工作原理

<p align="center">
  <img src="docs/process.png" width="760" alt="MindGraph 构建流程：切分 → schema 归纳 → 造测试集 → 两阶段抽取 → 评测/诊断/修正循环 → 收敛">
</p>

**两层 schema**：
- 元层（固定）：所有"观点/讲解"类文本共享的论证结构，决定了回答能"成形"。
- 领域层（每份文档归纳）：该作者特有的概念、术语、别名。

**元本体的节点与关系**：

```
节点：Concept 概念 · Claim 论断 · Principle 原则 · Method 方法 · Argument 论证 ·
      Evidence 论据 · Condition 条件 · Counterpoint 反例 · Analogy 类比 ·
      MentalModel 框架 · Question 问题 · ReasoningPattern 思路
关系：SUPPORTS/REFUTES · USES · EXEMPLIFIES · APPLIES_WHEN · ANSWERS ·
      DEPENDS_ON/PART_OF/REFINES/GENERALIZES · CAUSES/ENABLES · CONTRASTS_WITH ·
      REALIZES · COMPOSED_OF
```

两类问题对应两条检索"主干"，共用同一套节点：

```
解释型：Question ←ANSWERS← Claim ←SUPPORTS← Argument →USES→ Evidence
应用型：Condition ←APPLIES_WHEN← Method →REALIZES→ Principle
```

### 用到几个模型

| 角色 | 说明 | 是否必需 |
|---|---|---|
| 编排者 = coding agent（如 Claude Code / Codex） | 造测试集、定/改 schema、跑流程、读报告决定是否再抽一轮 | 不需要 API key（它本身就是 agent） |
| worker（推荐 DeepSeek V4 Pro） | 抽取 + 回答 | 需要（唯一必需的 key） |
| API judge（可选） | 仅"自治模式"用于给回答打分 | 可选 |

由此有两种运行模式：**agent 驱动**（默认，只需 worker 一个 key，由编排者读 JSON 报告决策）
与**自治**（配一个判分模型后无人值守跑到收敛）。

## 安装

```bash
git clone https://github.com/XiHa0/mindgraph.git
cd mindgraph
python -m venv .venv && . .venv/Scripts/activate   # Windows；Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt                    # 或 pip install -e .（提供 mindgraph 命令）
```

需要一个 Neo4j 5.x（本地或远程）。首次使用前执行一次建库脚本：

```bash
cypher-shell -u neo4j -p <password> -f mindgraph/schema/constraints.cypher
```

## 配置

```bash
cp .env.example .env       # 填入 KG_WORKER_*（worker 模型）和 NEO4J_*
```

只有 worker 的 key 是必需的；judge（`KG_JUDGE_*`）留空即可。

## 使用

agent 驱动模式：工具箱把每一步暴露成命令，各步读写 JSON，由编排者读结果决策。

```bash
# 1) 切分
python -m mindgraph.cli chunk    --doc doc.txt --doc-id authorX --out chunks.json

# 2) 写 schema.json（{"concepts":[...],"aliases":{...}}）；造并冻结 gold.json

# 3) 抽取入库
python -m mindgraph.cli extract  --chunks chunks.json --schema schema.json --store neo4j --doc-id authorX

# 4) 评测 + 诊断（确定性，产出 JSON）
python -m mindgraph.cli coverage --gold gold.json --store neo4j --doc-id authorX --out coverage.json
python -m mindgraph.cli diagnose --gold gold.json --store neo4j --doc-id authorX --out diag.json

# 5) 读 coverage.json / diag.json，决定改 schema / 加抽取指令 / 调切分 / 是否再抽 → 回到 3
```

无 Neo4j 时可用 `extract --store memory --graph-out graph.json` 在本地验证整条管线。
造测试集见 `python -m mindgraph.testset.run generate|freeze`；自治模式接线见
[`mindgraph/SPEC.md`](mindgraph/SPEC.md)。

## 现状与局限

- 这是早期版本（v0.1），接口与默认参数可能调整。
- 入口检索目前用中文字符 bigram 重合做召回；建库脚本已建好全文 / 向量索引，可按需替换为索引召回。
- Neo4j 节点写 `:类型:KGNode` 双标签（类型标签对应约束/索引，`:KGNode` 供通用读查询）。
- worker 模型和 Neo4j 需自备；judge 为可选。
- 设计规范 [`mindgraph/SPEC.md`](mindgraph/SPEC.md) 与代码结构 [`wiki.md`](wiki.md) 目前为中文。

## 文档

- [`wiki.md`](wiki.md) —— 完整代码结构（模块 / 类 / 接口 / 数据流）。
- [`mindgraph/SPEC.md`](mindgraph/SPEC.md) —— 设计规范（schema / 覆盖率指标 / 测试集格式 / 循环编排）。

## 许可证

[MIT](LICENSE) © XiHa0
