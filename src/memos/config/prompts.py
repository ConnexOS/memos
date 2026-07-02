"""MEMOS 提示词模板管理 —— PromptTemplate + PromptManager + 版本管理 + 目录持久化。

从 config.py 拆分（v0.4.3 架构重整 Phase 6）。
"""

import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import (
    _DEFAULT_BRIEFING_SYSTEM_PROMPT,
    _DEFAULT_CONFLICT_PROMPT,
    _DEFAULT_DAILY_REVIEW_PROMPT,
    _DEFAULT_PROMPT_FRAME,
    _DEFAULT_SYSTEM_PROMPT,
    _DEFAULT_TODO_EXTRACT_PROMPT,
    get_memos_home,
)

logger = logging.getLogger(__name__)


def _get_default_extract_prompt() -> str:
    """返回手工提炼的默认 system_prompt（分类优先英文版，输出中文）。

    由 PromptManager 管理，不硬编码为模块级常量，支持运行时热更新。
    """
    return (
        "You are a senior technical analyst. Extract valuable knowledge from the conversation "
        "as structured cards, classified into one of four types.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CLASSIFICATION TREE (priority order)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "For each distinct knowledge point, classify using this decision chain:\n"
        "\n"
        '1. solution — "error → fix" pattern\n'
        "   Is there a specific error, bug, or problem with a concrete fix?\n"
        "   → The conversation mentions an error/issue AND how it was resolved.\n"
        "   → Includes: bug fixes, error workarounds, dependency conflicts.\n"
        "   → Use problem/solution/insight as: error description / fix steps / why it works.\n"
        "   → NOT: general feature design or abstract lessons.\n"
        "\n"
        '2. decision — "choice between alternatives"\n'
        "   Was a deliberate technology/architecture/methodology choice made?\n"
        "   → Multiple options discussed, with reasoning for the final pick.\n"
        "   → Includes: library selection, architectural decisions, tool choices.\n"
        '   → "暂定/暂时" → lower confidence; firm conclusion → confident.\n'
        "   → Use problem/solution/insight as: decision context / what was chosen / rationale.\n"
        "   → NOT: routine configuration changes without deliberation.\n"
        "\n"
        '3. process — "repeatable workflow"\n'
        "   Is there a step-by-step procedure or operational norm?\n"
        '   → User explicitly says "记住这个流程" or describes a multi-step workflow.\n'
        "   → Includes: build/release steps, setup procedures, operational checklists.\n"
        "   → Use problem/solution/insight as: scenario / ordered steps / caveats & notes.\n"
        "   → Must be stable enough to follow again verbatim.\n"
        "\n"
        '4. lesson — "generalizable insight"\n'
        "   Is there a takeaway that applies beyond the immediate context?\n"
        "   → Best practices, pitfalls to avoid, patterns that emerged during work.\n"
        "   → Includes: coding patterns, debugging strategies, design principles.\n"
        "   → Use problem/solution/insight as: triggering situation / what was done / the insight.\n"
        "   → NOT: tied to one specific error (→ solution) or one specific choice (→ decision).\n"
        "\n"
        "5. If none of the above clearly match → skip (do not force a type).\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "EXTRACTION RULES\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "1. Do NOT assume a fixed number of cards — 0, 1, or multiple are all valid.\n"
        "2. First identify ALL distinct knowledge points, then decide merge vs split:\n"
        "   - MERGE only if multiple details address the SAME root cause.\n"
        "   - SPLIT if the conversation covers independent topics.\n"
        "3. Pay attention to the start of the conversation — the initial request often describes a feature design from scratch.\n"
        "4. Skip purely conversational turns, greetings, status checks, and already-stored content.\n"
        "5. Each card's fields (problem/solution/insight) must be in Chinese.\n"
        "6. Do NOT merge across different conversation turns if they address separate problems.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "QUALITY SCORING (0.0 ~ 1.0)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "| Dimension     | High (0.8+)                              | Low (< 0.5)                      |\n"
        "|---------------|------------------------------------------|----------------------------------|\n"
        "| Completeness  | problem + solution + insight all present | Missing key fields               |\n"
        "| Specificity   | Has concrete details (code, config, etc) | Vague generalities               |\n"
        "| Reusability   | Likely to help in future similar work    | One-off context only             |\n"
        "\n"
        "Type-specific guidance:\n"
        "- solution: 0.8+ if error pattern + root cause + fix are all clearly stated.\n"
        "- decision:  0.8+ if alternatives compared + selection reason recorded.\n"
        "- process:   0.8+ if steps are concrete, ordered, and repeatable.\n"
        "- lesson:    0.8+ if insight is actionable, not just \"be careful\".\n"
        "\n"
        "quality_reason: One short sentence in Chinese explaining the score.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "OUTPUT FORMAT\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "ONLY a valid JSON array of card objects. No markdown, no extra text, no code fences.\n"
        "\n"
        "Each card:\n"
        "{\n"
        '  "type": "solution" | "decision" | "lesson" | "process",\n'
        '  "problem":  "问题/背景/场景描述（中文）",\n'
        '  "solution": "具体做法/选择/步骤（中文）",\n'
        '  "insight":  "经验总结/最佳实践/注意事项（中文）",\n'
        '  "quality_score": 0.0~1.0,\n'
        '  "quality_reason": "评分理由（中文，一句话）"\n'
        "}\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "EXAMPLES (format reference only)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "[\n"
        "  {\n"
        '    "type": "solution",\n'
        '    "problem": "uvicorn 热重载时 CSS 修改不生效，浏览器始终显示旧版本",\n'
        '    "solution": "在 HTML head 中添加 <meta http-equiv=\\\"Cache-Control\\" content=\\\"no-cache\\">，同时在静态 URL 后附加版本参数 ?v={{timestamp}}",\n'
        '    "insight": "开发阶段应禁用浏览器缓存或使用版本化 URL，避免修改后看不到效果",\n'
        "    \"quality_score\": 0.85,\n"
        '    "quality_reason": "问题清晰、解决方案具体、insight 具有通用性"\n'
        "  },\n"
        "  {\n"
        '    "type": "decision",\n'
        '    "problem": "需要为 Dashboard 选择前端模板引擎",\n'
        '    "solution": "选用 Jinja2（FastAPI 内置）+ Bootstrap 5，不引入 React/Vue",\n'
        '    "insight": "无前端团队的项目应优先选用服务端渲染，降低复杂度",\n'
        "    \"quality_score\": 0.9,\n"
        '    "quality_reason": "有明确选项对比和选择理由"\n'
        "  },\n"
        "  {\n"
        '    "type": "process",\n'
        '    "problem": "发布操作缺少标准化流程，每次手动上线容易遗漏步骤",\n'
        '    "solution": "1) git pull → 2) npm run build → 3) pytest --runslow → 4) git tag → 5) git push origin --tags",\n'
        '    "insight": "标准化的发布流程可以减少人为失误，且方便新人快速上手",\n'
        "    \"quality_score\": 0.75,\n"
        '    "quality_reason": "步骤清晰可重复，但缺乏回滚预案"\n'
        "  },\n"
        "  {\n"
        '    "type": "lesson",\n'
        '    "problem": "ChromaDB 持久化模式下多进程同时写入导致数据损坏",\n'
        '    "solution": "严格限制 MCP Server 和 Dashboard 不能同时对同一项目写入，通过文件锁协调",\n'
        '    "insight": "ChromaDB PersistentClient 不是线程安全的，所有写入必须串行化",\n'
        "    \"quality_score\": 0.88,\n"
        '    "quality_reason": "经验具有跨项目参考价值，根因明确"\n'
        "  }\n"
        "]\n"
        "\n"
        "Now analyze the conversation below and output the JSON array."
    )


def _get_prompts_file() -> Path:
    """旧格式 prompts.json 路径（迁移用）"""
    return get_memos_home() / "etc" / "prompts.json"


def _get_prompts_dir() -> Path:
    return get_memos_home() / "etc" / "prompts"


def _get_prompts_index() -> Path:
    return get_memos_home() / "etc" / "prompts" / "index.json"


def _get_template_dir(template_id: str) -> Path:
    return get_memos_home() / "etc" / "prompts" / template_id


def _get_template_file(template_id: str) -> Path:
    return _get_template_dir(template_id) / "template.json"


def _get_version_file(template_id: str, version: str) -> Path:
    return _get_template_dir(template_id) / "versions" / f"{version}.json"


def _rm_template_dir(template_id: str):
    """删除模板目录及其所有版本文件"""
    tpl_dir = _get_template_dir(template_id)
    if tpl_dir.exists():
        shutil.rmtree(tpl_dir)


class PromptVersion(BaseModel):
    """单个提示词版本 —— 仅记录 system_prompt 快照，其余配置取模板级公共属性"""

    version: str = "1.0.0"
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT
    changelog: str = ""
    created_at: str = ""


class PromptTemplate(BaseModel):
    """提示词模板 —— 一个端点绑定一个模板（id = 端点名），采用「草稿 + 版本」双轨模式"""

    id: str
    name: str = ""
    description: str = ""
    template_type: str = "default"
    user_template: str = "{conversation_text}"
    chat_style: str = "openai"
    parameters: dict = Field(default_factory=dict)
    prompt: str = _DEFAULT_PROMPT_FRAME
    system_prompt_text: str = _DEFAULT_SYSTEM_PROMPT
    created_at: float = 0.0
    updated_at: float = 0.0
    active_version: str = "1.0.0"
    draft: PromptVersion = Field(default_factory=PromptVersion)
    versions: list[PromptVersion] = Field(default_factory=list)

    def _sync_from_legacy(self):
        """若从旧格式加载（prompt 含 ChatML 标记且 versions 为空），推导 chat_style。"""
        if not self.versions and "<|im_start|>" in self.prompt:
            self.chat_style = "chatml"

    def effective_prompt(self) -> PromptVersion:
        """返回当前生效的提示词：draft 始终为当前工作副本"""
        return self.draft

    def get_version(self, version: str):
        """按版本号查找已发布版本"""
        for v in self.versions:
            if v.version == version:
                return v
        return None

    @staticmethod
    def _sanitize_parameters(params: dict) -> dict:
        """剥离敏感字段（api_key 等），不持久化到提示词文件"""
        return {k: v for k, v in params.items() if k not in ("api_key", "authorization")}

    def save_draft(self, **kwargs):
        """保存草稿（仅 system_prompt 写入 draft，公共属性写模板级字段）"""
        if "system_prompt" in kwargs:
            self.draft.system_prompt = kwargs["system_prompt"]
        if "user_template" in kwargs:
            self.user_template = kwargs["user_template"]
        if "chat_style" in kwargs:
            self.chat_style = kwargs["chat_style"]
        if "parameters" in kwargs:
            val = kwargs["parameters"]
            if isinstance(val, dict):
                val = self._sanitize_parameters(val)
            self.parameters = val
        self.system_prompt_text = self.draft.system_prompt
        self.updated_at = time.time()

    def _next_available_version(self, version: str) -> str:
        """若版本号已存在则自动递增，避免重复"""
        candidate = version
        while self.get_version(candidate):
            parts = candidate.split(".")
            if len(parts) == 3:
                try:
                    candidate = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
                except ValueError:
                    candidate = f"{candidate}.1"
            else:
                candidate = f"{candidate}.1"
        return candidate

    def upgrade(self, version: str, changelog: str = "") -> PromptVersion:
        """将当前草稿的 system_prompt 快照为不可变版本。若版本号冲突则自动递增。"""
        version = self._next_available_version(version)
        new_ver = PromptVersion(
            version=version,
            system_prompt=self.draft.system_prompt,
            changelog=changelog,
            created_at=datetime.now().isoformat(),
        )
        self.versions.append(new_ver)
        self.active_version = version
        self.system_prompt_text = new_ver.system_prompt
        self.updated_at = time.time()
        return new_ver

    def rollback_to(self, version: str, changelog: str = "") -> PromptVersion | None:
        """基于历史版本创建新版本（只恢复 system_prompt，公共属性不变）。"""
        target = self.get_version(version)
        if not target:
            return None
        self.draft.system_prompt = target.system_prompt
        parts = version.split(".")
        if len(parts) == 3:
            try:
                base = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
            except ValueError:
                base = f"{version}.1"
        else:
            base = f"{version}.1"
        new_ver = self._next_available_version(base)
        full_changelog = changelog or f"回滚到 {version}"
        return self.upgrade(new_ver, full_changelog)

    def delete_version(self, version: str) -> bool:
        """删除指定版本。不可删除活跃版本和最后一个版本。"""
        if version == self.active_version:
            return False
        if len(self.versions) <= 1:
            return False
        for i, v in enumerate(self.versions):
            if v.version == version:
                self.versions.pop(i)
                return True
        return False

    def sync_to_active(self) -> bool:
        """将草稿的 system_prompt 同步写入当前活跃版本文件（覆盖）。"""
        if not self.active_version:
            return False
        for v in self.versions:
            if v.version == self.active_version:
                v.system_prompt = self.draft.system_prompt
                v.created_at = datetime.now().isoformat()
                self.system_prompt_text = self.draft.system_prompt
                self.updated_at = time.time()
                return True
        return False

    def build_payload(
        self, conversation_text: str, version_override: str | None = None, model_name: str | None = None
    ) -> dict:
        """构建 LLM 请求体。公共属性取自模板级字段，system_prompt 取自 draft 或指定版本。"""
        if version_override:
            ver = self.get_version(version_override)
            system_prompt = ver.system_prompt if ver else self.draft.system_prompt
        else:
            system_prompt = self.draft.system_prompt

        user_content = self.user_template.replace("{conversation_text}", conversation_text)
        if self.chat_style == "chatml":
            prompt_text = _DEFAULT_PROMPT_FRAME.format(
                system_prompt=system_prompt,
                conversation_text=user_content,
            )
            messages = [{"role": "user", "content": prompt_text}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Conversation:\n\n{user_content}"},
            ]

        payload = {"messages": messages}
        reserved = {"messages", "stream"}
        payload.update({k: v for k, v in self.parameters.items() if k not in reserved})
        if model_name:
            payload["model"] = model_name
        if self.chat_style == "chatml":
            payload.setdefault("stop", ["<|im_end|>"])
            payload.setdefault("reasoning_format", "none")
            payload.setdefault("reasoning_in_content", False)
        return payload


class PromptManager(BaseModel):
    """全局提示词管理器（替代 PromptConfig），支持目录存储 + 版本管理"""

    templates: list[PromptTemplate] = Field(default_factory=list)

    def get(self, id: str) -> PromptTemplate | None:
        """按模板 ID（= 端点名）查找"""
        for t in self.templates:
            if t.id == id:
                return t
        return None

    def upsert(self, template: PromptTemplate):
        """按 id（= 端点名）插入或更新模板"""
        for i, t in enumerate(self.templates):
            if t.id == template.id:
                template.updated_at = time.time()
                self.templates[i] = template
                return
        template.created_at = time.time()
        template.updated_at = time.time()
        self.templates.append(template)

    def delete(self, id: str) -> bool:
        """删除模板（不允许删除 fallback/终极回退），同时清理磁盘目录"""
        if id in ("default", "fallback"):
            return False
        for i, t in enumerate(self.templates):
            if t.id == id:
                self.templates.pop(i)
                _rm_template_dir(id)
                return True
        return False

    def get_by_type(self, template_type: str) -> list[PromptTemplate]:
        """按模板类型返回模板列表"""
        return [t for t in self.templates if t.template_type == template_type]

    def get_for_endpoint(self, endpoint_name: str, template_type: str = "extract") -> PromptTemplate | None:
        """获取指定端点绑定的提示词模板。

        查找优先级：
        1. 端点 prompt_templates[type] 显式关联
        2. 命名约定（模板 id = 端点名）
        3. 同类型的 default 模板
        """
        # 延迟导入避免循环
        from memos.config.loader import get_config as _get_cfg

        for ep in _get_cfg().llm.endpoints:
            if ep.name == endpoint_name:
                tid = ep.prompt_templates.get(template_type)
                if tid:
                    t = self.get(tid)
                    if t:
                        return t
                break

        type_variants = {template_type}
        if "_" in template_type:
            type_variants.add(template_type.replace("_", "-"))
        elif "-" in template_type:
            type_variants.add(template_type.replace("-", "_"))
        for tv in type_variants:
            t = self.get(f"{endpoint_name}@{tv}")
            if t:
                return t

        type_part = template_type.replace("_", "-")
        t = self.get(f"default@{type_part}")
        if t:
            return t

        t = self.get(f"fallback@{type_part}")
        if t:
            return t

        return self.get("fallback")

    def get_active_prompt(self, endpoint_name: str | None = None, template_type: str = "extract") -> str:
        """获取指定端点当前生效的 system_prompt 文本，可按模板类型查找"""
        name = endpoint_name
        if name is None:
            try:
                from memos.config.loader import get_config as _get_cfg

                name = _get_cfg().llm.active
            except Exception:
                name = "default"
        t = self.get_for_endpoint(name, template_type=template_type)
        if not t:
            t = self.get("default")
        if t:
            t._sync_from_legacy()
            return t.effective_prompt().system_prompt
        return _DEFAULT_SYSTEM_PROMPT

    def _rename_template(self, old_id: str, new_id: str):
        """Step 1 辅助：将模板从旧 id 重命名为新 id（磁盘目录 + 内存记录）"""
        tpl = self.get(old_id)
        if tpl is None:
            return
        if self.get(new_id) is not None:
            return

        old_dir = _get_template_dir(old_id)
        new_dir = _get_template_dir(new_id)
        if old_dir.exists() and not new_dir.exists():
            old_dir.rename(new_dir)
            tpl_file = new_dir / "template.json"
            if tpl_file.exists():
                with open(tpl_file, "r", encoding="utf-8") as f:
                    tpl_meta = json.load(f)
                tpl_meta["id"] = new_id
                with open(tpl_file, "w", encoding="utf-8") as f:
                    json.dump(tpl_meta, f, indent=2, ensure_ascii=False)
            logger.info("模板目录已迁移: %s → %s", old_id, new_id)

        tpl.id = new_id
        logger.info("模板已重命名: %s → %s", old_id, new_id)

    def _ensure_template(self, tpl_id: str, name: str, tpl_type: str, sys_prompt: str, description: str = ""):
        """创建模板（不覆盖已有），返回是否新创建"""
        if self.get(tpl_id) is not None:
            return False
        tpl = PromptTemplate(
            id=tpl_id,
            name=name,
            description=description or name,
            template_type=tpl_type,
        )
        tpl._sync_from_legacy()
        tpl.draft = PromptVersion(
            version="1.0.0",
            system_prompt=sys_prompt,
            created_at=datetime.now().isoformat(),
        )
        tpl.upgrade("1.0.0", "初始版本")
        self.templates.append(tpl)
        logger.info("创建模板: %s (type=%s)", tpl_id, tpl_type)
        return True

    @staticmethod
    def _is_user_customized(tpl: "PromptTemplate") -> bool:
        """判断用户是否自定义过 extract 模板（draft system_prompt 偏离旧版内置默认值）"""
        return tpl.draft.system_prompt.strip() != _DEFAULT_SYSTEM_PROMPT.strip()

    def ensure_default_template(self):
        """确保必要的默认/缺省模板存在。"""
        self._rename_template("default-extract", "fallback@extract")
        self._rename_template("default-daily-review", "fallback@daily-review")
        self._rename_template("default", "fallback")

        fallbacks = [
            ("fallback", "终极回退", "default", _DEFAULT_SYSTEM_PROMPT),
            ("fallback@extract", "知识提炼 (缺省回退)", "extract", _DEFAULT_SYSTEM_PROMPT),
            ("fallback@daily-review", "今日回顾 (缺省回退)", "daily-review", _DEFAULT_DAILY_REVIEW_PROMPT),
        ]
        for tpl_id, name, tpl_type, sys_prompt in fallbacks:
            self._ensure_template(tpl_id, name, tpl_type, sys_prompt)

        new_defaults = [
            ("default@extract", "知识提炼 (v0.7.2)", "extract", _get_default_extract_prompt()),
            ("default@daily-review", "今日回顾 (默认)", "daily-review", _DEFAULT_DAILY_REVIEW_PROMPT),
            ("default@briefing", "简报生成 (默认)", "briefing", _DEFAULT_BRIEFING_SYSTEM_PROMPT),
            ("default@conflict", "冲突检测", "conflict", _DEFAULT_CONFLICT_PROMPT),
            ("default@todo-extract", "待办提取 (v0.4.5)", "todo-extract", _DEFAULT_TODO_EXTRACT_PROMPT),
        ]
        for tpl_id, name, tpl_type, sys_prompt in new_defaults:
            existing = self.get(tpl_id)
            if existing is None:
                self._ensure_template(tpl_id, name, tpl_type, sys_prompt)
            elif tpl_id == "default@extract" and (
                not self._is_user_customized(existing)
                or "**fact**" in existing.draft.system_prompt
                or "**preference**" in existing.draft.system_prompt
                or "**todo**" in existing.draft.system_prompt
                or "请从以下对话中提取" in existing.draft.system_prompt
            ):
                existing.draft.system_prompt = sys_prompt
                existing.save_draft(system_prompt=sys_prompt)
                self.save()
                logger.info("已升级模板 %s 到 v0.7.2 新版 system_prompt (新 4 类)", tpl_id)
            elif tpl_id == "default@briefing" and "task_status" in existing.draft.system_prompt:
                existing.draft.system_prompt = sys_prompt
                existing.save_draft(system_prompt=sys_prompt)
                self.save()
                logger.info("已升级模板 %s 到 v0.7.1 新版 system_prompt", tpl_id)

        # v0.7.2: 类型专用去重 prompt 模板
        _dedup_templates = {
            "default@dedup_solution": {
                "system_prompt_text": (
                    "你是一个知识去重引擎。类型：solution（问题+解决方案）\n"
                    "旧记忆: {old_text}\n"
                    "新记忆: {new_text}\n"
                    "判断：是否同一错误场景的同一解决方案？若不是，是否互补（不同方案）？\n"
                    '输出 JSON: {"is_same": bool, "is_superseding": bool, "reasoning": "..."}'
                ),
                "template_type": "dedup",
                "description": "solution 类型专用去重判断",
            },
            "default@dedup_decision": {
                "system_prompt_text": (
                    "你是一个知识去重引擎。类型：decision（技术选型/架构决策）\n"
                    "旧记忆: {old_text}\n"
                    "新记忆: {new_text}\n"
                    "判断：是否同一决策主题的更新？决策具有迭代性——新决策应覆盖旧决策。\n"
                    '输出 JSON: {"is_same": bool, "is_superseding": true, "reasoning": "..."}'
                ),
                "template_type": "dedup",
                "description": "decision 类型专用去重判断",
            },
            "default@dedup_lesson": {
                "system_prompt_text": (
                    "你是一个知识去重引擎。类型：lesson（经验教训）\n"
                    "旧记忆: {old_text}\n"
                    "新记忆: {new_text}\n"
                    "判断：是否同一认知角度？教训具有互补性——不同角度应共存。\n"
                    '输出 JSON: {"is_same": bool, "is_superseding": false, "reasoning": "..."}'
                ),
                "template_type": "dedup",
                "description": "lesson 类型专用去重判断",
            },
        }
        for tid, tdata in _dedup_templates.items():
            try:
                self._ensure_template(
                    tpl_id=tid,
                    name=tdata["description"],
                    tpl_type=tdata["template_type"],
                    sys_prompt=tdata["system_prompt_text"],
                    description=tdata["description"],
                )
            except Exception as e:
                logger.warning("注册去重模板失败 (%s): %s", tid, e)

    def save(self):
        """持久化：写 index.json + 各模板目录（含默认模板，修改后重启不丢失）"""
        prompts_dir = get_memos_home() / "etc" / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)

        for t in self.templates:
            tpl_dir = _get_template_dir(t.id)
            tpl_dir.mkdir(parents=True, exist_ok=True)
            (tpl_dir / "versions").mkdir(parents=True, exist_ok=True)

            tpl_meta = {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "template_type": t.template_type,
                "user_template": t.user_template,
                "chat_style": t.chat_style,
                "parameters": t.parameters,
                "active_version": t.active_version,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "draft": {
                    "version": t.draft.version,
                    "system_prompt": t.draft.system_prompt,
                    "changelog": t.draft.changelog,
                    "created_at": t.draft.created_at,
                },
            }
            with open(_get_template_file(t.id), "w", encoding="utf-8") as f:
                json.dump(tpl_meta, f, indent=2, ensure_ascii=False)

            for v in t.versions:
                vf = _get_version_file(t.id, v.version)
                with open(vf, "w", encoding="utf-8") as f:
                    json.dump(v.model_dump(), f, indent=2, ensure_ascii=False)

        index_data = {
            "version": "2.0",
            "templates": {},
        }
        for t in self.templates:
            index_data["templates"][t.id] = {
                "id": t.id,
                "name": t.name,
                "template_type": t.template_type,
                "active_version": t.active_version,
                "version_count": len(t.versions),
                "updated_at": t.updated_at,
            }
        with open(_get_prompts_index(), "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls) -> "PromptManager":
        """从新目录结构加载，若无则尝试从旧 prompts.json 迁移"""
        index_path = _get_prompts_index()
        if index_path.exists():
            return cls._load_from_index(index_path)
        old_file = get_memos_home() / "etc" / "prompts.json"
        if old_file.exists():
            mgr = cls._migrate_from_legacy(old_file)
            mgr.save()
            bak = old_file.with_suffix(".json.bak")
            old_file.rename(bak)
            return mgr
        mgr = cls()
        mgr.ensure_default_template()
        return mgr

    @classmethod
    def _load_from_index(cls, index_path: Path) -> "PromptManager":
        """从 index.json + 各模板目录加载"""
        with open(index_path, encoding="utf-8") as f:
            index_data = json.load(f)

        mgr = cls()
        templates_meta = index_data.get("templates", {})
        for tpl_id, meta in templates_meta.items():
            tpl_file = _get_template_file(tpl_id)
            if not tpl_file.exists():
                continue

            with open(tpl_file, encoding="utf-8") as f:
                tpl_meta = json.load(f)

            draft_data = tpl_meta.get("draft", {})
            draft_kwargs = {
                k: v for k, v in draft_data.items() if k in ("version", "system_prompt", "changelog", "created_at")
            }
            draft = PromptVersion(**draft_kwargs) if draft_kwargs else PromptVersion()

            user_template = tpl_meta.get("user_template", draft_data.get("user_template", "{conversation_text}"))
            chat_style = tpl_meta.get("chat_style", draft_data.get("chat_style", "openai"))
            parameters = tpl_meta.get("parameters", draft_data.get("parameters", {}))

            t = PromptTemplate(
                id=tpl_id,
                name=tpl_meta.get("name", ""),
                description=tpl_meta.get("description", ""),
                template_type=tpl_meta.get("template_type", "extract"),
                user_template=user_template,
                chat_style=chat_style,
                parameters=parameters,
                active_version=tpl_meta.get("active_version", "1.0.0"),
                draft=draft,
                created_at=tpl_meta.get("created_at", 0.0),
                updated_at=tpl_meta.get("updated_at", 0.0),
            )

            t.system_prompt_text = draft.system_prompt

            versions_dir = _get_template_dir(tpl_id) / "versions"
            if versions_dir.exists():
                for vf in sorted(versions_dir.glob("*.json")):
                    with open(vf, encoding="utf-8") as f:
                        vdata = json.load(f)
                    t.versions.append(PromptVersion(**vdata))

            mgr.templates.append(t)

        mgr.ensure_default_template()
        return mgr

    @classmethod
    def _migrate_from_legacy(cls, old_file: Path) -> "PromptManager":
        """从旧 etc/prompts.json 迁移到新目录结构"""
        with open(old_file, encoding="utf-8") as f:
            old_data = json.load(f)

        mgr = cls()
        old_templates = old_data.get("templates", [])
        for ot in old_templates:
            t = PromptTemplate(
                id=ot.get("id", "default"),
                name=ot.get("name", ""),
                description=ot.get("description", ""),
                template_type="extract",
                user_template=ot.get("user_template", "{conversation_text}"),
                chat_style=ot.get("chat_style", "openai"),
                prompt=ot.get("prompt", _DEFAULT_PROMPT_FRAME),
                system_prompt_text=ot.get("system_prompt_text", _DEFAULT_SYSTEM_PROMPT),
                parameters=ot.get("parameters", {}),
                created_at=ot.get("created_at", 0.0),
                updated_at=ot.get("updated_at", 0.0),
            )
            t._sync_from_legacy()
            t.draft = PromptVersion(
                version="1.0.0",
                system_prompt=t.system_prompt_text,
                created_at=datetime.now().isoformat(),
            )
            t.upgrade("1.0.0", "初始版本（从旧格式迁移）")
            mgr.templates.append(t)

        mgr.ensure_default_template()
        return mgr
