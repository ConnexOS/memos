"""MEMOS 配置模型 —— 10 个子配置 + LLM 端点 + 默认提示词常量。

从 config.py 拆分（v0.4.3 架构重整 Phase 6）。
"""

import hashlib
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


def _compute_default_project_id() -> str:
    """以 CWD 的 MD5 前 8 位作为默认项目 ID，彻底消除 'default' 占位符。"""
    return hashlib.md5(os.getcwd().encode()).hexdigest()[:8]


def get_memos_home() -> Path:
    """获取 MEMOS 数据根目录。

    优先级：
    1. $MEMOS_HOME 环境变量（显式覆盖）
    2. 当前目录（若存在 etc/config.json，则为本地开发模式）
    3. ~/.memos/（全局模式默认值）
    """
    env = os.environ.get("MEMOS_HOME")
    if env:
        return Path(env)
    cwd_config = Path.cwd() / "etc" / "config.json"
    if cwd_config.exists():
        return Path.cwd()
    return Path.home() / ".memos"


def ensure_memos_home() -> Path:
    """确保 MEMOS_HOME 目录结构存在并返回根目录路径。"""
    home = get_memos_home()
    (home / "etc").mkdir(parents=True, exist_ok=True)
    (home / "memdb").mkdir(parents=True, exist_ok=True)
    (home / "model").mkdir(parents=True, exist_ok=True)
    (home / "data" / "logs").mkdir(parents=True, exist_ok=True)
    return home


def _default_chroma_path() -> str:
    return str(get_memos_home() / "memdb")


def _default_model_path() -> str:
    name = os.environ.get("MEMOS_MODEL_NAME", "bge-large-zh-v1.5")
    return str(get_memos_home() / "model" / name)


# --- 默认提示词常量 ---

_DEFAULT_SYSTEM_PROMPT = """You are a senior technical analyst. Your task is to extract technical implementation knowledge from the conversation as structured "experience cards". For each significant change, fix, or decision, extract:

- "problem": a concise description of the issue or context (what was wrong or what needed to be done).
- "solution": what exactly was done (code changes, configuration, architecture).
- "insight": the lesson learned, rationale, or defensive practice adopted.
- "type": one of "bug_fix", "feature_design", "code_optimize", "tech_knowledge".

**CRITICAL: CORRECT TYPE CLASSIFICATION RULES**

- **bug_fix**: 故障修复 – 已有功能出错、异常报错、逻辑bug、兼容问题、线上问题、环境故障、解析错误、正则匹配失败等一切**对已有缺陷的修复**。
- **feature_design**: 功能与设计 – **从零开始的新功能开发**、架构设计、业务逻辑规划、方案设计、协议/模型设计。**关键判断标准：对话中描述的是"之前没有，现在需要实现"的新能力，而不是修复已有的错误**。
- **code_optimize**: 代码优化 – 性能优化、代码精简、规范整改、可读性/健壮性优化（但不包括对功能错误的修复）。
- **tech_knowledge**: 技术认知 – 语法用法、库/框架使用、工具技巧、开发常识、知识点积累（如提示词工程最佳实践、LLM调用注意事项等）。

**CRITICAL: EXTRACTION RULES (to avoid missing content)**
1. **Do NOT assume any fixed number of cards** – the conversation may yield 0, 1, 2, 3, or more cards.
2. **First, list all distinct knowledge points** you can identify in the conversation (mentally or in scratchpad):
   - Include both "from scratch feature design" and "fixes to existing features".
   - Include separate technical insights even if they appear in the same message.
3. **Then decide on merging vs. splitting**:
   - Merge only if multiple changes truly fix the **same root cause** (e.g., adding exception handling AND defensive dict access for the same error scenario).
   - Do NOT merge across different conversation turns if they address separate problems.
   - If a single conversation contains multiple independent knowledge points, split them into separate cards.
4. **Pay special attention to the beginning of the conversation** – often the initial user request describes a new feature implementation from scratch. That MUST be extracted as a `feature_design` card (unless the conversation shows the feature already existed).

**Output format**: ONLY a valid JSON array of cards. No markdown, no extra text. Each card must be in **Chinese**.

**Examples (format only – number/length of examples does not imply expected output length)**:

~~~json
[
  {
    "problem": "接口调用时报空指针异常，导致服务偶发崩溃",
    "solution": "对上游传入参数增加非空校验，添加异常捕获与默认返回值",
    "insight": "任何外部输入都不可信，必须做防御性编程处理",
    "type": "bug_fix"
  },
  {
    "problem": "需要从零实现一个用户登录功能，包括前端界面、后端API和token管理",
    "solution": "设计JWT鉴权流程，后端新增/login端点验证密码并返回token，前端存储token并在后续请求中携带",
    "insight": "新功能开发应优先设计整体流程和接口契约，再分模块实现",
    "type": "feature_design"
  },
  {
    "problem": "代码重复率高、命名混乱，性能与可读性较差",
    "solution": "抽取公共方法，统一编码规范，优化循环逻辑提升执行效率",
    "insight": "持续小步优化代码，可显著降低长期维护成本",
    "type": "code_optimize"
  },
  {
        "problem": "不熟悉框架异步任务的正确使用方式",
    "solution": "学习官方文档，编写最小Demo验证用法，封装成通用工具类",
    "insight": "新技术先做最小验证，再集成到业务更安全",
    "type": "tech_knowledge"
  }
]

~~~

Now analyze the conversation below. Follow the extraction rules strictly. Output the JSON array."""


_NEW_EXTRACT_SYSTEM_PROMPT = """You are a senior technical analyst. Your task is to extract structured knowledge from the conversation as "memory cards". Each card captures one distinct piece of technical knowledge.

**TYPE CLASSIFICATION CRITERIA** — Determine the type of each memory:

- **fact**: Objective technical facts confirmed in the conversation (version numbers, API endpoints, configuration parameters, architecture patterns). MUST have direct evidence in the original text.
- **decision**: Technical decision records. Indicators: multiple options were discussed, trade-offs were made, clear rationale for the final choice was stated.
- **preference**: User preferences and project conventions. Signal words: "I prefer", "we usually", "from now on", "convention", "preference", "always/never".
- **todo**: Explicit or implied action items. Include task description. If priority/deadline not mentioned, note "未指定".

**EXTRACTION REQUIREMENTS** — Once type is determined, compose content accordingly:

- **fact**: Quote specific values, endpoints, file names from the conversation. Format: "XX项目使用 YY 技术栈，版本 ZZ"
- **decision**: Include: background (why now), alternatives considered, final choice with rationale, potential risks. Format: "决策：XXX。备选：A/B/C。选择：A。理由：..."
- **preference**: Record the preference content and applicable scope. Format: "用户偏好：XXX（适用于 YY 场景）"
- **todo**: Task description + priority (高/中/低, default "中" if unspecified) + dependencies (omit if none). If no deadline mentioned, note "未指定"

**OUTPUT FORMAT**: ONLY a valid JSON array. No markdown, no extra text. Each card must be in **Chinese**.

Each card:
- "problem": concise description of the issue or context
- "solution": what exactly was done or the specific knowledge content
- "insight": lesson learned, rationale, or best practice
- "type": one of "fact", "decision", "preference", "todo"
- "quality_score": float 0-1, evaluating: ①format completeness (required fields present, types correct) ②information density (specific details vs vague statements) ③relevance (directly from conversation, not fabricated)
- "quality_reason": brief explanation of the score (string)

**Examples** (format only):

~~~json
[
  {
    "problem": "项目后端技术栈选型未确定",
    "solution": "经讨论选择 FastAPI 作为 Web 框架，版本 0.115.6，搭配 SQLAlchemy 2.0 ORM",
    "insight": "FastAPI 异步支持好、生态成熟，适合本项目的 API 密集场景",
    "type": "decision",
    "quality_score": 0.9,
    "quality_reason": "包含具体版本号和选型理由，信息完整且直接来自对话"
  },
  {
    "problem": "用户偏好代码风格约定未记录",
    "solution": "用户偏好：使用双引号、120 字符行宽、ruff 格式化（适用于所有 Python 项目）",
    "insight": "统一代码风格约定应在项目初期明确，避免后续格式化冲突",
    "type": "preference",
    "quality_score": 0.85,
    "quality_reason": "明确记录偏好内容和适用范围，引用了具体工具名"
  },
  {
    "problem": "需要为 MEMOS 项目添加单元测试覆盖",
    "solution": "为 extractor.py 的 store_memories 方法编写单元测试",
    "insight": "优先为核心模块添加测试，保障重构安全",
    "type": "todo",
    "quality_score": 0.7,
    "quality_reason": "任务描述清晰但缺少优先级和截止时间信息"
  }
]

~~~

Now analyze the conversation below. Follow the classification criteria and extraction requirements strictly. Output the JSON array."""

_DEFAULT_CONFLICT_PROMPT = """You are a fact-conflict detector. Given a new piece of information and a list of existing memories, determine if there is any factual contradiction.

A contradiction means two statements cannot both be true at the same time (e.g., "backend uses FastAPI" vs "backend uses Flask"). Mere differences in detail level, wording, or complementary supplementary information are NOT contradictions.

**Output ONLY a valid JSON object** (no markdown, no extra text):

{
  "has_conflict": true or false,
  "conflict_with": "the memory_id of the conflicting existing memory, or null if no conflict",
  "reason": "brief explanation in Chinese, or empty string if no conflict"
}"""

_DEFAULT_PROMPT_FRAME = """<|im_start|>system
{system_prompt}
<|im_end|>
<|im_start|>user
Conversation:

{conversation_text}
<|im_end|>"""

_DEFAULT_TODO_EXTRACT_PROMPT = """You are a professional todo extractor. Your task is to read a daily development report and extract all actionable todos/tasks from it.

For each todo:
- "content": the specific actionable item (清晰的任务描述)
- "context": 1-2 sentences of background/why from the report (从日报中提取导致此待办的背景原因)
- "priority": one of "high", "medium", "low" (based on urgency and impact)

**Rules**:
1. Only extract explicit or clearly implied action items — if the report says "还未开始" or "需要做", extract it.
2. Do NOT extract items that are already completed or described as "done".
3. Each todo should be specific — "补充用户认证模块的单元测试" not "补充测试".
4. If no actionable items found, return an empty array [].
5. Output ONLY a valid JSON array, no markdown, no extra text.

**Output format**:
[
  {"content": "具体的待办事项1", "context": "背景说明", "priority": "high"},
  {"content": "具体的待办事项2", "context": "背景说明", "priority": "medium"}
]

Now analyze the daily report below and extract actionable todos:"""


_DEFAULT_DAILY_REVIEW_PROMPT = """You are a professional development daily report analyst. Your task is to review the conversation records and produce a structured daily development report in **Chinese**. For each conversation session, identify what was accomplished, what decisions were made, what bugs were fixed, and what remains to be done.

**REPORT STRUCTURE** — Output in Markdown, following this exact structure:

# YYYY-MM-DD 开发日报

## 今日概要
(2-3 sentences summarizing the main themes and outcomes of the day's work.)

## 已完成工作
- **Task 1**: what was done, which files were changed, key implementation details.
- **Task 2**: ...
(Group by feature or phase. Be specific — mention file paths, function names, or configuration keys where relevant.)

## 技术决策
- **Decision**: what was decided and why. Include alternatives considered if discussed.
- ...

## Bug 修复
- **Problem**: what broke → **Root cause**: why it broke → **Fix**: what was changed → **Result**: outcome after fix.
- ...

## 文件变更清单
| File | Change |
|------|--------|
| `path/to/file.py` | Added / Modified / Deleted — brief description |
| ...

## 待办事项
- [ ] Task — priority/context from conversation
- [ ] ...

**RULES**:
1. **Strictly based on conversation content** — do not fabricate or assume information not present in the records.
2. **Group related work together** — merge related changes into a single coherent entry rather than listing every minor edit.
3. **Be specific** — mention file paths, function names, error messages, and decisions verbatim from the conversation when possible.
4. **If nothing significant happened in a section, omit it** — do not write "None" or "N/A".
5. **Prioritize substance over ceremony** — a 3-item report that captures the real decisions is better than a padded 10-item list.
6. **Write the report body in Chinese**, but keep code identifiers (file paths, function names, variable names) in their original form.

**Output format**: The Markdown report directly, starting with the title line. No preamble, no JSON wrapper.
"""

# 提示词模板类型枚举
PROMPT_TEMPLATE_TYPES = ("extract", "daily-review", "conflict", "todo-extract", "default")


# --- 配置模型 ---


class ChromaConfig(BaseModel):
    mode: str = "persistent"
    path: str = Field(default_factory=_default_chroma_path)
    host: str = "localhost"
    port: int = 8001
    collection_name: str = "project_memory"
    timeout: int = 30


class ModelConfig(BaseModel):
    path: str = Field(default_factory=_default_model_path)
    name: str = "bge-large-zh-v1.5"
    vector_dim: int = 1024
    download_retries: int = 3
    download_timeout: int = 600
    verify_sha256: bool = False


class LLMEndpoint(BaseModel):
    """单个 LLM 端点配置"""

    name: str = "default"
    api_base: str = "http://localhost:11434/v1"
    api_key: str = ""
    model: str = ""
    prompt_templates: dict[str, str] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    """LLM 配置，支持多端点，通过 name 切换活跃端点"""

    endpoints: list[LLMEndpoint] = Field(default_factory=lambda: [LLMEndpoint(name="default")])
    active: str = "default"
    temperature: float = 0.1
    max_tokens: int = 2048
    request_timeout: int = 600
    max_retries: int = 3
    retry_base_delay: float = 1.0
    stop: list[str] = ["<|im_end|>"]

    @model_validator(mode="before")
    @classmethod
    def _migrate_old_format(cls, data):
        """向后兼容：旧格式 {api_base, api_key, ...} → 新格式 {endpoints: [...], active: ...}"""
        if not isinstance(data, dict):
            return data
        if "api_base" in data:
            ep = {"name": "default"}
            for key in ("api_base", "api_key"):
                if key in data:
                    ep[key] = data.pop(key)
            data["endpoints"] = [ep]
            data.setdefault("active", "default")
        elif "endpoints" not in data:
            data["endpoints"] = [{"name": "default"}]
            data["active"] = "default"
        return data

    @property
    def api_base(self) -> str:
        return self.active_endpoint.api_base

    @api_base.setter
    def api_base(self, value: str):
        self.active_endpoint.api_base = value

    @property
    def api_key(self) -> str:
        return self.active_endpoint.api_key

    @api_key.setter
    def api_key(self, value: str):
        self.active_endpoint.api_key = value

    @property
    def active_endpoint(self) -> LLMEndpoint:
        for ep in self.endpoints:
            if ep.name == self.active:
                return ep
        if self.endpoints:
            return self.endpoints[0]
        return LLMEndpoint(name="default")


class SystemSuggestionTriggers(BaseModel):
    """管道二系统状态型建议的触发事件开关。"""

    first_time_user: bool = Field(default=True, description="新用户首次对话（知识库为空）")
    unrefined_rounds: bool = Field(default=True, description="大量对话未被提炼")
    low_quality_ratio: bool = Field(default=True, description="低质量记忆占比过高")
    no_daily_review: bool = Field(default=True, description="缺少日报/日报过期")
    inactive_project: bool = Field(default=True, description="项目长时间无活动")
    expired_memories: bool = Field(default=True, description="存在过期记忆未处理")


class SystemSuggestionConfig(BaseModel):
    """管道二系统状态型建议配置。"""

    enabled: bool = Field(default=True, description="管道二全局开关")
    daily_limit: int = Field(default=3, ge=0, le=10, description="管道二每日推送上限")
    cooldown_hours: int = Field(default=24, ge=1, le=168, description="同类事件冷却时间（小时）")
    triggers: SystemSuggestionTriggers = Field(default_factory=SystemSuggestionTriggers, description="各事件独立开关")


class SuggestionConfig(BaseModel):
    """主动建议配置（管道一：知识匹配型 + 管道三：手工建议）。

    控制分层检索中 Layer 2（相似度 ≥ active_suggestion_threshold 的建议推送）、
    Layer 1（相似度 ≥ context_injection_threshold 的上下文注入）以及手工建议的行为。
    """

    enable_active_suggestions: bool = Field(default=True, description="主动推送全局开关")
    active_suggestion_threshold: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="Layer 2 推送相似度阈值，相似度≥此值的记忆触发主动建议",
    )
    context_injection_threshold: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Layer 1 上下文注入阈值，相似度≥此值的记忆注入 AI 助手上下文",
    )
    context_max_items: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Layer 1 最大注入条数，超出截断",
    )
    suggestion_cooldown_minutes: int = Field(
        default=30,
        ge=0,
        description="同一记忆的冷却期（分钟），冷却期内不再重复推送",
    )
    suggestion_max_per_day: int = Field(
        default=10,
        ge=0,
        description="管道一每日最大推送数（24h 滑动窗口）",
    )
    suggestion_expiry_days: int = Field(
        default=7,
        ge=0,
        description="建议自动过期天数（0=不过期）",
    )
    suggestion_max_pending: int = Field(
        default=50,
        ge=10,
        le=200,
        description="最大待处理建议数，超出时 FIFO 自动清理",
    )
    suggestion_display_limit: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Dashboard 单次拉取建议数（分页大小）",
    )
    suggestion_manual_daily_limit: int = Field(
        default=5,
        ge=0,
        le=20,
        description="[已废弃] 管道三（手工建议）每日推送上限，由 max_injection_per_round 替代",
    )
    max_injection_per_round: int = Field(
        default=5,
        ge=1,
        le=20,
        description="每轮会话最多注入的记录数（人工建议 + 知识匹配），按优先级排序后截断",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_old_keys(cls, data):
        """向后兼容：suggestion_max_per_session → suggestion_max_per_day"""
        if isinstance(data, dict) and "suggestion_max_per_session" in data:
            data.setdefault("suggestion_max_per_day", data.pop("suggestion_max_per_session"))
        return data

    @model_validator(mode="after")
    def _validate_suggestion_limits(self):
        """跨字段校验：suggestion_max_pending >= suggestion_max_per_day * 2。
        注：仅 Pydantic 构造时触发，运行时属性赋值可绕过。Dashboard API 侧有手工校验兜底。"""
        if self.suggestion_max_pending < self.suggestion_max_per_day * 2:
            raise ValueError(
                f"suggestion_max_pending ({self.suggestion_max_pending}) 必须 >= "
                f"suggestion_max_per_day × 2 ({self.suggestion_max_per_day * 2})"
            )
        return self


class MemoryConfig(BaseModel):
    """核心记忆管理配置（检索、去重、质量、冲突、复用频率加成）。"""

    decay_lambda: float = Field(
        default=0.02,
        ge=0.0,
        description="时间衰减系数，越大越偏向近期记忆。0=不衰减",
    )
    similarity_threshold: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="去重相似度阈值（余弦距离），低于此值判定重复",
    )
    dedup_top_k: int = Field(
        default=1,
        ge=0,
        description="去重检查的候选记忆数，0=不检查",
    )
    default_top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="检索默认返回条数",
    )
    default_type: str = Field(
        default="fact",
        description="新建记忆的默认类型（fact/decision/preference/todo）",
    )
    archive_days: int = Field(
        default=90,
        ge=0,
        description="超过此天数的记忆自动归档（软删除），0=不归档",
    )
    rerank_multiplier: int = Field(
        default=3,
        ge=1,
        description="重排序候选倍数，增大提高质量但降低速度",
    )
    rerank_min_candidates: int = Field(
        default=30,
        ge=1,
        description="重排序最小候选数，低于此值直接返回",
    )
    quality_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="质量评分参考阈值，低于此值标记为低质量",
    )
    conflict_detection_enabled: bool = Field(
        default=True,
        description="新记忆与已有记忆的冲突检测开关",
    )
    conflict_distance_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="冲突检测预过滤阈值，相似度≥此值才检测（v0.4.5 从 0.55 调升至 0.85）",
    )
    conflict_use_llm: bool = Field(
        default=True,
        description="冲突检测是否调用 LLM 判断矛盾；false 时纯向量判断（sim>threshold→冲突，降级模式）",
    )
    daily_todo_time: str = Field(
        default="18:00",
        description="每日待办提醒时间（HH:MM，服务器本地时间）",
    )
    expiry_warn_days: int = Field(
        default=30,
        ge=0,
        description="过期警告提前天数",
    )
    reuse_weight: float = Field(
        default=0.1,
        ge=0.0,
        description="复用频率加成权重（0=禁用）",
    )
    reuse_decay: float = Field(
        default=0.01,
        ge=0.0,
        description="复用频率时间衰减系数",
    )
    reuse_boost_cap: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="复用频率加成上限",
    )
    default_status: str = Field(
        default="active",
        description="新建记忆默认状态（active/archived）",
    )
    daily_review_chunk_tokens: int = Field(
        default=12000,
        ge=2000,
        le=64000,
        description="每日回顾分片最大 token 数（BATCH/PRE_SUMMARIZE 策略）",
    )
    daily_review_chunk_rounds: int = Field(
        default=30,
        ge=5,
        le=100,
        description="每日回顾分片最大轮次数（作为 token 分片的补充上限）",
    )

    @model_validator(mode="after")
    def _validate_conflict_threshold(self):
        """冲突检测阈值必须大于去重阈值（冲突语义是"高度相似才检测矛盾"）。"""
        if self.conflict_distance_threshold <= self.similarity_threshold:
            raise ValueError(
                f"conflict_distance_threshold ({self.conflict_distance_threshold}) "
                f"必须大于 similarity_threshold ({self.similarity_threshold})。"
                f"冲突检测用于高度相似时才检查矛盾，去重用于低度相似即判定重复。"
            )
        return self


class BufferConfig(BaseModel):
    max_tokens: int = 3000
    truncate_target: int = 2500
    trigger_rounds: int = 5
    rate_limit_seconds: int = 30
    token_ratio: float = 0.75
    async_mode: bool = True
    subprocess_timeout: int = 300


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    locale: str = "zh"
    status_cache_ttl: int = 15
    projects_cache_ttl: int = 30
    health_check_timeout: int = 10
    test_connection_timeout: int = 5
    search_default_top_k: int = 5
    search_top_k_max: int = 50
    search_default_decay: float = 0.02
    search_default_bm25_weight: float = 0.7
    list_default_limit: int = 20
    list_limit_max: int = 100


class ServerConfig(BaseModel):
    id_length: int = 8
    mcp_top_k_max: int = 20
    response_truncate_length: int = 100


class AuthConfig(BaseModel):
    """Dashboard 登录认证配置（单用户模式）"""

    disable: bool = False
    token_hash: str = ""
    secret_key: str = ""
    session_ttl: int = 86400


class BackupConfig(BaseModel):
    """备份配置（F2 数据备份与恢复）"""

    target_dir: str = "backups"
    max_backups: int = 10
    remind_after_days: int = 7
    verify_after_backup: bool = True


class NotificationConfig(BaseModel):
    """通知配置（F3 系统通知中心）"""

    retention_days: int = 30
    rate_limit_minutes: int = 60


class AgentConfig(BaseModel):
    """Agent 决策引擎配置 —— 仅数据模型，逻辑由 Agent 阶段实现（v0.4.4 增强版）。"""

    # === Phase 1 字段（本次仅配置占位，不触发逻辑）===
    enabled: bool = Field(default=True, description="Agent 决策引擎全局开关")
    pattern_detection_enabled: bool = Field(default=True, description="模式检测开关（Phase 1）")

    # === Phase 2 预留字段 ===
    daily_briefing_enabled: bool = Field(default=True, description="每日简报开关（Phase 2）")
    daily_briefing_time: str = Field(default="09:00", description="每日简报推送时间（HH:MM）")
    topic_cluster_window_days: int = Field(default=7, ge=1, le=30, description="主题聚类窗口（天）")
    recurrence_threshold: int = Field(default=3, ge=2, le=10, description="重复问题触发阈值")
    bug_match_similarity: float = Field(default=0.70, ge=0.5, le=0.95, description="Bug 匹配相似度阈值")
    max_daily_briefing_items: int = Field(default=3, ge=1, le=10, description="每日简报最大条目数")
    briefing_cooldown_hours: int = Field(default=24, ge=1, le=72, description="简报推送冷却（小时）")

    # === Phase 3 预留字段 ===
    signal_cooldown_hours: int = Field(default=6, ge=1, le=72, description="信号推送冷却（小时）")
    max_active_signals: int = Field(default=5, ge=1, le=20, description="最大活跃信号数")
