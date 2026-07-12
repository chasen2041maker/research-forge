"""
Agent Evals 套件 —— 评测 m1 / m4 等 LLM 驱动节点的输出质量、一致性、回归。

与 tests/ 下普通单元测试的区别:
  - 单元测试:纯逻辑,确定性,毫秒级,CI 必跑
  - Eval 测试:调真实 LLM,有成本,分钟级,可选跑

详见 evals/README.md 如何运行。
"""
