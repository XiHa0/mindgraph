# 任务标记：抽取关系（STAGE=EDGES）

你是知识图谱抽取器。本步骤**只在给定的已知节点之间抽关系**，不得引入新节点。

# 可用关系类型与方向（严格遵守端点类型）

- Argument -SUPPORTS-> Claim ；Argument -REFUTES-> Claim ；Argument -USES-> Evidence
- Evidence -EXEMPLIFIES-> Concept|Claim
- Claim|Method -APPLIES_WHEN-> Condition
- Claim -ANSWERS-> Question
- Concept -DEPENDS_ON|PART_OF|REFINES|GENERALIZES-> Concept
- (任意) -CAUSES|ENABLES|CONTRASTS_WITH-> (任意)
- Method -REALIZES-> Principle
- MentalModel -COMPOSED_OF-> Concept

# 本轮重点（据上轮诊断动态注入）

{directives}

{few_shot}

# 已知节点（from/to 只能取自下表的 name，**一字不差**）

{nodes}

# 要求

- 只就**本片段文本支持**的关系出边；找不到就返回空数组。
- `from`/`to` 必须精确等于上表某个 name；方向遵守上面的端点类型约束。
- 每条给 `confidence`(0~1)。不要臆造文本未表达的关系。

# 输出（严格 JSON）

```json
{
  "edges": [
    {"from": "...", "type": "SUPPORTS", "to": "...", "confidence": 0.9}
  ]
}
```

# 片段

id: {chunk_id}　章节: {section}

{text}
