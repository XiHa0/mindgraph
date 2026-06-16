"""[step 1] schema 归纳：在冻结的 meta-layer 上长出该文档的 domain-layer（概念/术语/别名）。

LLMSchemaInducer 用 judge/强模型分窗扫全文抽候选概念，复用 extraction.resolve 的实体消歧
做合并，按频次/置信排序取 topN，产出供抽取阶段复用的规范概念表 + 别名表。
"""
