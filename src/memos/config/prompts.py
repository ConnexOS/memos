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
    _DEFAULT_CONFLICT_PROMPT,
    _DEFAULT_DAILY_REVIEW_PROMPT,
    _DEFAULT_PROMPT_FRAME,
    _DEFAULT_SYSTEM_PROMPT,
    _DEFAULT_TODO_EXTRACT_PROMPT,
    _NEW_EXTRACT_SYSTEM_PROMPT,
    get_memos_home,
)

logger = logging.getLogger(__name__)


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
        from memos.config.loader import config as _cfg

        for ep in _cfg.llm.endpoints:
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
                from memos.config.loader import config as _cfg

                name = _cfg.llm.active
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
            ("default@extract", "知识提炼 (v0.4.1)", "extract", _NEW_EXTRACT_SYSTEM_PROMPT),
            ("default@daily-review", "今日回顾 (默认)", "daily-review", _DEFAULT_DAILY_REVIEW_PROMPT),
            ("default@conflict", "冲突检测", "conflict", _DEFAULT_CONFLICT_PROMPT),
            ("default@todo-extract", "待办提取 (v0.4.5)", "todo-extract", _DEFAULT_TODO_EXTRACT_PROMPT),
        ]
        for tpl_id, name, tpl_type, sys_prompt in new_defaults:
            existing = self.get(tpl_id)
            if existing is None:
                self._ensure_template(tpl_id, name, tpl_type, sys_prompt)
            elif tpl_id == "default@extract" and not self._is_user_customized(existing):
                existing.draft.system_prompt = sys_prompt
                existing.save_draft(system_prompt=sys_prompt)
                logger.info("已升级模板 %s 到 v0.4.1 新版 system_prompt", tpl_id)

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
