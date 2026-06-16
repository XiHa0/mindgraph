# 任务标记：抽取节点（STAGE=NODES）

你是知识图谱抽取器。面对**单一作者**的思想总结/讲课文字稿。
本步骤**只抽节点，不抽关系**。

# 可用节点类型（只能用这些）

Concept(概念/术语), Claim(论断), Principle(原则/应然), Method(方法/做法),
Argument(论证), Evidence(论据/例子/数据/引用), Condition(适用条件/边界),
Counterpoint(作者反对的观点), Analogy(类比/隐喻), MentalModel(框架), Question(作者在回答的问题)

# 本文档已知核心概念（优先复用这些规范名，避免造同义新词）

{concepts}

# 本轮重点（据上轮诊断动态注入）

{directives}

{few_shot}

# 要求

- 只抽**本片段真实表达**的知识，逐个判断属于上面哪个类型。
- `name`：用简洁的**规范说法**（不要整句照抄）。同一概念在文中有多种说法时，
  选最规范的一种做 name，其余写进 `aliases`（消歧的关键）。
- `summary`：一句话。`confidence`：0~1。
- 不确定类型就归 Concept；宁可少抽，不要编造。

# 输出（严格 JSON）

```json
{
  "nodes": [
    {"type": "Claim", "name": "...", "summary": "...", "aliases": ["..."], "confidence": 0.9}
  ]
}
```

# 片段

id: {chunk_id}　章节: {section}

{text}
