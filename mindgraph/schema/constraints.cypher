// ============================================================
// MindGraph — Neo4j 建库脚本（meta-layer 冻结结构）
// 幂等：可重复执行。需 Neo4j 5.x（IF NOT EXISTS / 向量索引语法）
// 执行：cypher-shell -u $NEO4J_USER -p $NEO4J_PASSWORD -f constraints.cypher
// 或在 builder 启动时由 schema/setup 调用。
// ============================================================

// ---------- 1. 唯一性约束（id 主键）----------
CREATE CONSTRAINT concept_id        IF NOT EXISTS FOR (n:Concept)         REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT claim_id          IF NOT EXISTS FOR (n:Claim)           REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT principle_id      IF NOT EXISTS FOR (n:Principle)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT method_id         IF NOT EXISTS FOR (n:Method)          REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT argument_id       IF NOT EXISTS FOR (n:Argument)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT evidence_id       IF NOT EXISTS FOR (n:Evidence)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT condition_id      IF NOT EXISTS FOR (n:Condition)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT counterpoint_id   IF NOT EXISTS FOR (n:Counterpoint)    REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT analogy_id        IF NOT EXISTS FOR (n:Analogy)         REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT mentalmodel_id    IF NOT EXISTS FOR (n:MentalModel)     REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT question_id       IF NOT EXISTS FOR (n:Question)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT reasoningpat_id   IF NOT EXISTS FOR (n:ReasoningPattern) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT chunk_id          IF NOT EXISTS FOR (n:Chunk)           REQUIRE n.id IS UNIQUE;

// ---------- 2. 存在性约束（核心属性必填，需企业版；社区版会忽略此段）----------
// 若为社区版，注释掉以下块，改由应用层保证。
// CREATE CONSTRAINT concept_name_exists IF NOT EXISTS FOR (n:Concept) REQUIRE n.name IS NOT NULL;
// CREATE CONSTRAINT claim_name_exists   IF NOT EXISTS FOR (n:Claim)   REQUIRE n.name IS NOT NULL;

// ---------- 3. 检索索引 ----------
// 3.1 名称/别名 —— 消歧与 lookup
CREATE INDEX concept_name   IF NOT EXISTS FOR (n:Concept) ON (n.name);
CREATE INDEX claim_name     IF NOT EXISTS FOR (n:Claim)   ON (n.name);
CREATE INDEX method_name    IF NOT EXISTS FOR (n:Method)  ON (n.name);

// 3.2 章节/顺序 —— 溯源与分层抽样
CREATE INDEX chunk_section  IF NOT EXISTS FOR (n:Chunk) ON (n.section);
CREATE INDEX chunk_order    IF NOT EXISTS FOR (n:Chunk) ON (n.doc_id, n.order);

// 3.2b KGNode 共享标签 —— 支撑通用读查询（has_node/node_names/incident_edges）
//      所有知识节点除类型标签外都带 :KGNode（见 neo4j_store._node_write_queries）
CREATE INDEX kgnode_lookup  IF NOT EXISTS FOR (n:KGNode) ON (n.doc_id, n.name);

// 3.3 全文索引 —— 入口节点召回（问答检索第一跳）
CREATE FULLTEXT INDEX node_fulltext IF NOT EXISTS
  FOR (n:Concept|Claim|Principle|Method|Question|MentalModel)
  ON EACH [n.name, n.summary, n.definition];

// 3.4 向量索引 —— 语义召回（embedding 维度按所用模型调整，示例 1024）
//     若暂不用向量检索，可注释本段。
CREATE VECTOR INDEX node_embedding IF NOT EXISTS
  FOR (n:Concept|Claim|Principle|Method|Question)
  ON (n.embedding)
  OPTIONS { indexConfig: {
    `vector.dimensions`: 1024,
    `vector.similarity_function`: 'cosine'
  }};

// ---------- 4. 验证 ----------
SHOW CONSTRAINTS;
SHOW INDEXES;
