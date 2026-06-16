"""[交付3] 测试集生成器 + 人工校验冻结流程。循环的地基。

管线： chunks → quota.plan → generator.generate → validate → review.export
       → (人工校验) → review.import_verified → 冻结
"""
