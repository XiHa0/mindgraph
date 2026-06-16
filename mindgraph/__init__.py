"""MindGraph：把"针对某份文档调通 schema + 抽取 + 测试集"的过程固化为评测驱动的自动循环。

规范见 SPEC.md。模块布局：
    schema/     meta-layer 本体 + Neo4j 建库脚本（契约，冻结）
    testset/    [交付3] 测试集生成器 + 人工校验冻结流程（循环地基）
    builder/    [交付2] 循环编排
    extraction/ [交付2] 两阶段抽取 + 写图
"""
