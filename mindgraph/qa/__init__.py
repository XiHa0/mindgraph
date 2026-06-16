"""问答评测链路：检索子图 → 生成回答 → 按 rubric 打分。

JudgeAnswerScorer 实现 CoverageEvaluator 期望的 answer_scorer，使 answer_score 真实化
（SPEC §2 / §3.3 / §3.4）。judge 模型与抽取模型分离。
这一链路同时是最终问答 agent 的雏形——把 retriever+answerer 抽出来即可对外服务。
"""
