# Agent Evals 套件

> 2025-2026 Agent 工程的"标配":对 LLM 驱动的节点做**质量、一致性、回归**评测。
> 这套 evals 就是你在面试里讲 "I have an eval suite" 时拿得出手的那个东西。

## 为什么要做 evals

普通单元测试只能验证"代码不崩",**无法验证 LLM 输出质量**:
- m4 Meta 终裁给出的 rating 今天 7 分、明天 5 分,代码完全没错
- m1 PICO 精炼结果可能字段齐全但完全跑题
- 改了 prompt 之后,是变好了还是变差了?凭感觉?

**evals 把上面这些问题变成可量化的自动化断言**。

## 三层评测覆盖

| 层 | 做什么 | 调 LLM 吗? | 典型用途 |
|---|---|---|---|
| **Schema Eval**(硬)| 字段完整性、数值合法区间 | 否(纯逻辑) | 底线 |
| **Consistency Eval**(半硬)| N 次重跑的标准差 | 是 | 非确定性控制 |
| **Quality Eval**(软)| LLM-as-Judge 按 rubric 打分 | 是(当裁判) | 主观质量 |

## 目录结构

```
tests/evals/
├── conftest.py                     # --run-evals flag + EVAL_MOCK 打桩
├── fixtures.py                     # 种子问题 + 阈值集中管理
├── judges.py                       # LLM-as-Judge + schema 检查工具
├── test_m1_refiner_eval.py         # m1 PICO:schema + 质量 + 底线三类
├── test_m4_consistency_eval.py     # m4 圆桌:一致性方差 + schema
└── README.md                       # 本文件
```

## 怎么跑

### 默认 —— 全部 skip(CI 友好)

```bash
pytest tests/evals/
# 输出:8 skipped in 2s
```

evals 默认 skip,CI 跑全量测试时**不会意外烧钱**。

### Mock 模式 —— 不调真 LLM,验证 eval 代码健康

```bash
EVAL_MOCK=1 pytest tests/evals/ --run-evals -v
# 输出:8 passed in 3s
```

用途:
- 每次改完 eval 代码后先跑一遍,确认逻辑无 bug
- CI 上可以放这个模式做"eval 基础设施回归"
- **任何人 clone 项目都能立刻体验 eval 套件的样子,不需要 API Key**

### 真实模式 —— 调 DeepSeek + Claude,有成本

```bash
pytest tests/evals/ --run-evals -v
```

用途:
- 改完 m4 Reviewer prompt 后,跑一遍看一致性有没有退化
- 发布前的质量体检
- 挑战 THRESHOLDS 里的阈值(做完 A/B 调参)

**预估成本**(默认 3 个 seed + runs=3):
- m1 三次 build_pico + 六次 rubric_judge ≈ $0.02
- m4 三次完整圆桌 ≈ 3 × ($0.02 + Meta Claude Opus) ≈ $0.2
- 一次完整真跑 ≈ **$0.25**

## 阈值调优指南

`fixtures.py::THRESHOLDS` 集中管理所有阈值,改一处影响全部:

| 阈值 | 含义 | 收紧风险 | 放宽风险 |
|---|---|---|---|
| `m1_quality_min_avg` | PICO 均分下限 | CI 频繁红 | 质量退化被漏过 |
| `m4_meta_rating_stdev_max` | Meta 打分方差上限 | 被 LLM 随机性卡住 | Meta 判断不稳没人发现 |
| `m4_reviewer_variance_max` | Reviewer 间分歧上限 | 看不到视角差异 | Reviewer 互相吵架 |
| `m4_consistency_runs` | 一致性测试重跑次数 | 统计不显著 | 成本爆炸 |

## 常见问题

### 为什么 LLM Judge 用 Claude Opus,不用和 Reviewer 同样的 DeepSeek?

**防 self-enhancement bias**。同模型家族自审偏高分(多篇论文证实)。跨家族裁判 + 温度=0,能挤出比较"客观"的分数。代价是每次 judge 贵一点,但评估场景不追求速度,贵一点值得。

### 为什么 `runs=3` 这么少?

统计学上显著需要 n≥10,但 10 次 m4 圆桌 ≈ 30 次 LLM 调用,成本高。教学/demo 用 3 次就够看"方差是否失控",要做发布前体检再开到 10+。

### 为什么不用 LangSmith / Braintrust?

- 本项目刻意零托管依赖,想让任何人 clone 就跑
- 如果你们生产要接 LangSmith,这套评测逻辑直接可以搬过去(LangSmith 的评估接口和 pytest 非常像)

## 面试讲点速查

| 面试问题 | 回答方向 |
|---|---|
| 你怎么保证 Agent 输出质量? | 三层 eval:schema 硬校验 + 一致性方差测试 + LLM-as-Judge |
| LLM Judge 公平吗? | 跨模型家族 + 温度=0 + rubric 精确到 1-5 各分档 |
| 改 Prompt 怎么回归? | 跑 evals,看 quality avg / stdev 有没有退化 |
| 成本可控吗? | 默认 skip,mock 模式零成本,真跑一次 $0.25 |
| 这套和业界比? | 对应 2024-2025 Agent Eval 范式(OpenAI Evals / LangSmith / Inspect 的简化版) |

---

**下一步可以做的**(留给进阶):
1. 接入 pairwise 评估:m4 改版前后拉出来 A/B,让 Judge 两向打分
2. 把 evals 结果写 JSON / Markdown 报告,生成历史曲线图
3. 在 CI 里按 PR 触发 mock 模式,合并主分支触发真实模式(控成本)
4. 加 **red-teaming eval**:故意喂"奇葩输入",看 Agent 崩不崩
