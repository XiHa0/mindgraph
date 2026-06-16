"""[交付2] Builder 循环编排（SPEC §4）。

切分 → schema 归纳 → 造测试 → 抽取 → 评测 → 诊断 → 修正 → 收敛判定。
每步是一个接口（interfaces.py），便于替换真实实现 / 离线 stub。
"""
