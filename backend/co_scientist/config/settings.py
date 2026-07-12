"""
============================================================
 配置加载模块(config/settings.py)
============================================================

🎓 教学目标
    本文件教你如何用 pydantic-settings 做类型安全的配置加载。
    为什么要这样做?
      - 直接 os.getenv("KEY") 返回字符串,布尔/数字类型要手动转换,容易出 bug
      - pydantic-settings 让你声明类型 + 默认值,运行时自动校验
      - IDE 能做补全:settings.RELAY_GPT_API_KEY 会有类型提示

📌 设计决策
    1. 所有配置集中在一个 Settings 类,避免散落在各模块
    2. 通过 @lru_cache 缓存 settings() 调用,整个进程只加载一次
    3. 敏感字段(API Key)用 SecretStr,在打印日志时自动脱敏为 **********

🔗 相关文件
    - .env.example:所有可配置项的模板
    - 本文件被所有模块 import:from co_scientist.config import settings

------------------------------------------------------------
"""

from __future__ import annotations  # 让类型注解支持前向引用(Python < 3.10 也能用)

from functools import lru_cache  # 让配置只加载一次(单例模式)
from pathlib import Path  # 处理文件路径,跨平台比 os.path 好用

from pydantic import Field, SecretStr  # Field 加额外约束,SecretStr 脱敏敏感值
from pydantic_settings import BaseSettings, SettingsConfigDict  # 从 .env 自动加载


# ------------------------------------------------------------
# 计算项目根目录:本文件位于 backend/co_scientist/config/settings.py
# Path(__file__).parent = .../config
# .parent.parent         = .../co_scientist
# .parent.parent.parent  = .../backend
# .parent.parent.parent.parent = 项目根目录(agent3/)
# ------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    """
    全局配置类。

    继承 BaseSettings 后,pydantic 会按以下优先级加载值:
      1. 代码里显式传参     Settings(RELAY_GPT_API_KEY="xxx")
      2. 环境变量           export RELAY_GPT_API_KEY=xxx
      3. .env 文件          RELAY_GPT_API_KEY=xxx
      4. 类里定义的默认值   default=...

    这样同一份代码在开发/CI/生产可以用不同的 .env 或环境变量切换。
    """

    # model_config 是 pydantic v2 的类级别配置
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",  # .env 文件位置
        env_file_encoding="utf-8",  # Windows 下中文 .env 需要 utf-8
        case_sensitive=True,  # 环境变量区分大小写(避免踩坑)
        extra="ignore",  # 忽略 .env 里的未知字段(不报错)
    )

    # ==================== LLM API 密钥 ====================
    # SecretStr 的好处:打印对象时自动显示 **********,避免密钥泄露到日志
    # 真正使用时调用 .get_secret_value() 拿到明文
    DEEPSEEK_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="已废弃:不再作为运行时回退密钥使用",
    )
    ANTHROPIC_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="已废弃:不再作为运行时回退密钥使用",
    )

    # ==================== 已废弃 legacy 字段 ====================
    # 下面 DEEPSEEK_* / MODEL_* 字段只为兼容历史导入、旧日志和单元测试保留。
    # 运行时 get_llm() 不读取这些字段,不会切回 legacy provider。
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"

    # ==================== 模型名称(legacy compatibility only) ====================
    MODEL_CHAT: str = "deepseek-chat"  # 日常生成、写作、抽取
    MODEL_REASONER: str = "deepseek-reasoner"  # 推理、评审、决策
    MODEL_CRITICAL: str = "claude-opus-4-7"  # 关键节点,质量决定性
    MODEL_EMBEDDING: str = "deepseek-embedding"  # 文本向量化

    # ==================== 整理版 Phase A-C 开关关系图 ====================
    #
    # 整理版引入了 5 个 USE_* 开关,它们之间的关系如下:
    #
    #     chat/reasoner          ─── 固定走 GPT 中转站
    #     critical               ─── 固定走 Claude 中转站
    #     USE_M0_DISCOVERY       ─── 加 M0 候选课题节点(主图前置)
    #         │ ──> M0_DEFAULT_K, M0_AUTO_SELECT_TOP
    #         ▼
    #     USE_M2_5_ACCESS_STATUS ─── 加 M2.5 节点(M2 与 M3 之间)
    #         │
    #         ▼
    #     USE_M5_5_GATE          ─── 加 M5.5 节点(M5 与 M6 之间)
    #         │ ──> USE_M5_5_LLM(在启发式之上叠加 LLM 综合)
    #
    # 推荐组合:
    #   - 默认             :整理版完整体验(M0/M2.5/M5.5 打开,GPT/Claude 中转打开)
    #   - 兼容老流程       :显式设置 USE_M0_DISCOVERY=false 等开关关闭新节点
    #   - USE_M5_5_LLM 单开:不推荐(M5.5 LLM 依赖 USE_M5_5_GATE 才会被调用)
    #
    # ============================================================================

    # ==================== GPT/Claude 中转站(新架构推荐路径) ====================
    # 整理版架构 §3 决策:把所有模型调用统一收敛到 GPT/Claude 中转站,
    # 业务模块只依赖 chat / reasoner / critical 三个语义角色,不写死模型名。
    #
    # 路由方式:
    #   factory 固定把 chat/reasoner 路由到 GPT 中转站,
    #   critical 路由到 Claude 中转站。USE_RELAY 仅保留为旧 .env 兼容字段,
    #   不再能切回 DeepSeek。
    USE_RELAY: bool = Field(
        default=True,
        description="已废弃兼容字段:运行时始终使用 GPT/Claude 中转站",
    )
    # GPT 中转站(承担 chat/reasoner)
    RELAY_GPT_BASE_URL: str = "https://right.codes/codex/v1"
    RELAY_GPT_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="GPT 中转站密钥;必填,不会回落到 DeepSeek",
    )
    # Claude 中转站(承担 critical / Extended Thinking)
    RELAY_CLAUDE_BASE_URL: str = "https://www.right.codes"
    RELAY_CLAUDE_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="Claude 中转站密钥;必填,不会回落到官方 Anthropic",
    )
    # 中转站对应的模型名(USE_RELAY=true 时由 factory 使用)
    RELAY_MODEL_CHAT: str = "gpt-5.5"
    RELAY_MODEL_REASONER: str = "gpt-5.5"
    RELAY_MODEL_CRITICAL: str = "claude-opus-4-7"
    RELAY_MODEL_EMBEDDING: str = "text-embedding-3-small"

    # ==================== 基础设施 ====================
    # 下面这些服务都是可选的。MVP 阶段不配置也能跑(会自动降级)。
    POSTGRES_URL: str = "postgresql://postgres:postgres@localhost:5432/co_scientist"
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: SecretStr = SecretStr("co_scientist_pwd")
    QDRANT_URL: str = "http://localhost:6333"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ==================== 成本控制 ====================
    MONTHLY_BUDGET_USD: float = 15.0  # 超过 80% 会在日志警告

    # ==================== 运行时 ====================
    LOG_LEVEL: str = "INFO"
    DATA_DIR: Path = PROJECT_ROOT / "data"
    OUTPUT_DIR: Path = PROJECT_ROOT / "data" / "outputs"
    CACHE_DIR: Path = PROJECT_ROOT / "data" / "cache"
    CHECKPOINT_DIR: Path = PROJECT_ROOT / "data" / "checkpoints"

    # 代码执行三档开关(详见模块 6 文档)
    CODE_EXECUTION_MODE: str = Field(
        default="generate_only",
        description="generate_only / dry_run / full_execute",
    )
    CRITIQUE_MAX_TURNS: int = 12  # 模块 4 批判圆桌最大轮数,防死循环
    ENABLE_PROMPT_CACHE: bool = True  # 客户端侧是否缓存 LLM 响应

    # ==================== m4 Orchestrator 开关 ====================
    # 对应 Anthropic 2025.4 多 Agent 研究系统的 Orchestrator-Subagent 范式。
    # True(默认):跑批判圆桌前先让 Orchestrator LLM 动态选 3-5 个 Reviewer,
    #             省 token + 信号更集中。
    # False:走老行为(全量 5 个 Reviewer),用于对比/回归。
    M4_USE_ORCHESTRATOR: bool = True

    # ==================== LangSmith 观测性 ====================
    # LangSmith 是 LangChain 官方的 tracing / 评估平台(langsmith.com)。
    # 开启后,LangGraph 的每个节点调用、LLM 调用、工具调用都会自动上报,
    # 可按 thread_id 回放整条 run,比翻日志强一个量级。
    # 启用方式:在 .env 里设置 LANGSMITH_API_KEY + LANGSMITH_TRACING=true
    LANGSMITH_TRACING: bool = False  # True 才开 tracing
    LANGSMITH_API_KEY: SecretStr = SecretStr("")
    LANGSMITH_PROJECT: str = "co-scientist"  # 分 project 看数据
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    # ==================== Extended Thinking(Claude 推理预算)====================
    # Claude Sonnet 4+ / Opus 4 支持"显式思考预算",让模型在回答前做
    # N 个 token 的推理(类似 o1/o3 的 reasoning tokens)。
    # 对关键节点(Meta 终裁)开大一点,普通节点保持 0(关闭)。
    # 设为 0 表示关闭;建议 Meta 开 4000-8000。
    CLAUDE_THINKING_BUDGET_META: int = 0  # 0 = 关闭;建议 4000+
    CLAUDE_THINKING_BUDGET_DEFAULT: int = 0  # 普通 Claude 调用的预算

    # ==================== Budget Guard(成本护栏)====================
    # Agent 长跑最大的风险:LLM bug 反复调用,一次跑把 API 账户打爆。
    # BudgetGuard 按 run 粒度设上限,超了直接抛异常中断。
    # 设为 0 表示不限(不推荐生产用)。
    RUN_BUDGET_USD: float = 1.0  # 单次 run 的成本上限(美元)

    # ==================== M0 候选课题发现器(整理版 Phase B 新增) ====================
    # USE_M0_DISCOVERY=True 时,主图在 START 后先跑 M0 生成 K 张 TopicCard,
    # 然后让用户/M8 选 1 张,把 candidate_question 注入 M1 的 raw_question。
    # 默认 True:整理版产品形态先展示 M0 候选课题,由用户选择后再继续。
    USE_M0_DISCOVERY: bool = True
    M0_DEFAULT_K: int = 3   # 候选数量,整理版默认 3-5
    M0_AUTO_SELECT_TOP: bool = False  # CLI 可人工选择;API/前端使用两阶段选择接口

    # ==================== Phase C 开关(M2.5/M5.5/DecisionCard) ====================
    # USE_M2_5_ACCESS_STATUS=True 时,m2_retrieve 之后插入 m2_5_access_status 节点;
    # 默认 False 保持老流程,Phase C 启用后 M3/M4 会消费 evidence_access_status。
    USE_M2_5_ACCESS_STATUS: bool = True
    # USE_M5_5_GATE=True 时,m5_experiment 之后插入 m5_5_gate 节点。
    # 输出仅写到 state.metadata.research_gate,不实际跳转(回边由 Phase D 的 M8 实现)。
    USE_M5_5_GATE: bool = True
    # USE_M5_5_LLM=True 时,M5.5 在启发式之上叠加一次 LLM 综合(更贵但更细);
    # 默认 False 走纯规则,够 MVP 用且零成本。
    USE_M5_5_LLM: bool = False

    # ==================== MCP(Model Context Protocol)集成 ====================
    # MCP 是 Anthropic 于 2024 年 11 月发布的工具/上下文互操作标准。
    # 开启后,m2 检索源会走独立的 MCP Server 子进程,而不是本进程直接调用。
    # 好处:
    #   1. 三个检索源可以被任何 MCP 兼容的 Agent 复用(Claude Desktop / Cursor / Zed / 其他团队)
    #   2. 工具层与业务层彻底解耦,工具可以独立部署、独立扩容
    #   3. 进程隔离:某个源崩溃不会拖垮主流程
    # 默认 False(向后兼容),想体验 MCP 模式在 .env 设置 USE_MCP=true
    USE_MCP: bool = False

    # ==================== 初始化后的副作用 ====================
    def ensure_dirs(self) -> None:
        """
        确保所有数据目录存在。
        Python 会在程序启动时调一次,避免后续写文件时目录不存在报错。
        """
        for d in (self.DATA_DIR, self.OUTPUT_DIR, self.CACHE_DIR, self.CHECKPOINT_DIR):
            d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------
# 💡 单例模式:为什么要加 @lru_cache?
# ------------------------------------------------------------
# 每次调用 settings() 都会触发 .env 文件的读取和 pydantic 校验。
# 在一个典型请求链路里,会被调用几十上百次。用 lru_cache(maxsize=1) 缓存,
# 整个进程只会真正加载一次,后续调用几乎零成本。
# 这比写成全局变量 `settings = Settings()` 更好:
#   - 延迟加载:import 时不立即读 .env(方便测试 mock)
#   - 显式调用:读代码时知道"这里在拿配置"
# ------------------------------------------------------------
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置对象(单例)。"""
    s = Settings()
    s.ensure_dirs()
    return s


# 为了方便模块直接 `from co_scientist.config import settings`,
# 我们在这里求值一次,暴露一个"准全局"变量。
# 仍然推荐新代码用 get_settings() 显式调用。
settings = get_settings()
