"""Dashboard API 请求模型 — 所有 Pydantic BaseModel 子类集中管理 (v0.4.3 架构重整)"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from ...config import config

# --- 记忆管理 ---


class CreateMemoryRequest(BaseModel):
    content: str = Field(min_length=1)
    type: str = "solution"
    project_id: str | None = None


class UpdateMemoryRequest(BaseModel):
    content: str | None = None
    type: str | None = None


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1)


class BatchCreateMemoriesRequest(BaseModel):
    memories: list[CreateMemoryRequest] = Field(min_length=1)


class ImportMemoriesRequest(BaseModel):
    project_id: str | None = None
    strategy: str = "skip"


# --- 检索 ---


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    project_id: str | None = None
    top_k: int = Field(default=5, ge=1)
    days_limit: int | None = None
    type_filter: str | None = None
    decay_lambda: float | None = None
    hybrid: bool = False
    bm25_weight: float | None = None

    def __init__(self, **data):
        super().__init__(**data)
        dc = config.dashboard
        if self.top_k > dc.search_top_k_max:
            self.top_k = dc.search_top_k_max
        if self.decay_lambda is None:
            self.decay_lambda = dc.search_default_decay
        if self.bm25_weight is None:
            self.bm25_weight = dc.search_default_bm25_weight


# --- 对话记录 & 提炼 ---


class ExtractConversationsRequest(BaseModel):
    ids: list[str] = Field(min_length=1)


class ExtractConversationsV2Request(BaseModel):
    ids: list[str] = Field(min_length=1)
    prompt_id: str = "default"
    prompt_version: str | None = None
    llm_endpoint: str | None = None


class CardItem(BaseModel):
    problem: str = ""
    solution: str = ""
    insight: str = ""
    type: str = "process"
    quality_score: float | None = None
    quality_reason: str = ""


class BatchCreateCardsRequest(BaseModel):
    cards: list[CardItem] = Field(min_length=1)
    project_id: str | None = None


# --- 今日回顾 ---


class DailyReviewRequest(BaseModel):
    date: str | None = None
    start_ts: float | None = None  # 本地日期起始 UTC 时间戳（由前端按浏览器时区计算）
    end_ts: float | None = None  # 本地日期结束 UTC 时间戳
    project_id: str | None = None
    llm_endpoint: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    save_as_memory: bool = False


class SaveDailyReviewRequest(BaseModel):
    report: str = Field(min_length=1)
    date: str
    project_id: str | None = Field(default=None, description="当前项目 ID，用于确定保存子目录")
    project_dir: str | None = Field(default=None, description="项目根目录路径，日报保存到此目录的 document/日报/ 下")


# --- 提示词管理 ---


class CreatePromptRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=32)
    name: str = ""
    description: str = ""
    template_type: str = "extract"
    prompt: str | None = None
    system_prompt_text: str | None = None
    user_template: str = "{conversation_text}"
    chat_style: str = "openai"
    parameters: dict = {}


class UpdatePromptRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    system_prompt_text: str | None = None
    parameters: dict | None = None


class SaveDraftRequest(BaseModel):
    system_prompt: str | None = None


class SaveConfigRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    user_template: str | None = None
    chat_style: str | None = None
    parameters: dict | None = None


class UpgradeRequest(BaseModel):
    version: str = Field(min_length=1, max_length=16)
    changelog: str = ""


class RollbackRequest(BaseModel):
    changelog: str = ""


# --- LLM 端点管理 ---


class CreateEndpointRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    api_base: str = Field(min_length=1)
    api_key: str = ""
    model: str = ""


class UpdateEndpointRequest(BaseModel):
    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None
    prompt_templates: dict[str, str] | None = None


class ActivateEndpointRequest(BaseModel):
    name: str = Field(min_length=1)


class TestConnectionRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)


# --- 会话记录检索 ---


class ConversationSearchRequest(BaseModel):
    """会话记录检索请求。"""

    query: str = Field(min_length=1)
    project_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    date_from: float | None = None
    date_to: float | None = None


# --- 配置管理 ---


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str | int | float | bool


class ManualSuggestionCreateRequest(BaseModel):
    """创建用户建议请求体。"""

    content: str = Field(min_length=1, max_length=5000, description="建议内容")
    trigger_keywords: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="触发关键词列表（always 模式可为空）",
    )
    priority: str = Field(default="medium", pattern=r"^(high|medium|low)$")
    trigger_mode: str = Field(default="keyword", pattern=r"^(keyword|always)$")
    cooldown_minutes: int = Field(default=60, ge=0, le=1440)
    validity_minutes: int = Field(
        default=0,
        ge=0,
        le=43200,
        description="有效期（分钟），0=永不过期，超过后自动失效不再匹配",
    )

    @model_validator(mode="after")
    def _validate_keywords_for_mode(self):
        if self.trigger_mode == "keyword" and len(self.trigger_keywords) == 0:
            raise ValueError("keyword 模式下 trigger_keywords 至少需要 1 项")
        return self
