from __future__ import annotations

import logging

# 本模块特有导入
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from ...config import _DEFAULT_SYSTEM_PROMPT, PromptTemplate, PromptVersion, _get_version_file, config
from ..models import (
    CreatePromptRequest,
    RollbackRequest,
    SaveConfigRequest,
    SaveDraftRequest,
    UpdatePromptRequest,
    UpgradeRequest,
)
from ..services.helpers import _template_to_dict

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_valid_endpoint(name: str) -> bool:
    """检查名称是否为 config.json 中配置的有效端点"""
    return any(ep.name == name for ep in config.llm.endpoints)


@router.get("/api/prompts")
def list_prompts(request: Request):
    """列出所有端点关联的提示词模板（无模板则用虚拟默认条目托底）。"""
    pc = config.prompt
    endpoint_names = {ep.name for ep in config.llm.endpoints}
    known_types = {"extract", "daily-review"}
    items = []
    endpoint_has_template = set()

    # 1. 列出所有属于已配置端点的真实模板
    for t in pc.templates:
        # 从模板 ID 提取端点名：{端点}@{类型} 格式
        ep_name = t.id
        if "@" in t.id:
            parts = t.id.rsplit("@", 1)
            if parts[1] in known_types:
                ep_name = parts[0]
        # 仅展示端点列表中存在的端点
        if ep_name not in endpoint_names:
            continue
        item = _template_to_dict(t)
        item["is_virtual"] = False
        item["has_template"] = True
        item["endpoint_name"] = ep_name
        items.append(item)
        endpoint_has_template.add(ep_name)

    # 2. 对于没有模板的端点，展示虚拟默认条目
    for ep in config.llm.endpoints:
        if ep.name not in endpoint_has_template:
            items.append(
                {
                    "id": ep.name,
                    "name": ep.name,
                    "endpoint_name": ep.name,
                    "template_type": "extract",
                    "is_virtual": True,
                    "has_template": False,
                    "active_version": "-",
                    "system_prompt_text": "",
                    "versions": [],
                    "draft": {},
                    "version_count": 0,
                }
            )

    return {
        "templates": items,
        "endpoints": [{"name": ep.name, "api_base": ep.api_base, "model": ep.model} for ep in config.llm.endpoints],
    }


@router.get("/api/prompts/{id}")
def get_prompt(request: Request, id: str):
    """获取单个提示词模板详情（含版本列表）。无真实模板时若为有效端点则返回默认模板托底"""
    t = config.prompt.get(id)
    if not t:
        if _is_valid_endpoint(id):
            default_t = config.prompt.get("fallback")
            if default_t:
                result = _template_to_dict(default_t)
                result["id"] = id
                result["name"] = id
                result["is_virtual"] = True
                result["has_template"] = False
                return result
        raise HTTPException(404, f"模板 {id} 不存在")
    result = _template_to_dict(t)
    result["is_virtual"] = False
    result["has_template"] = True
    return result


@router.post("/api/prompts", status_code=201)
def create_prompt(request: Request, req: CreatePromptRequest):
    """为指定端点创建专属提示词模板。模板 ID = {端点名}-{类型}，端点+类型为唯一 KEY。
    从对应类型的默认模板 fork system_prompt 作为 v1.0.0 版本。"""
    pc = config.prompt
    template_id = f"{req.endpoint}@{req.template_type}"
    # 检查新格式 ({endpoint}@{type})、旧新格式 ({endpoint}-{type}) 和旧格式 (仅 {endpoint}) 是否已存在
    if pc.get(template_id):
        raise HTTPException(409, f"端点 '{req.endpoint}' 的'{req.template_type}'类型模板已存在")

    # 从对应类型的默认模板 fork system_prompt
    default_type_id = {"extract": "default@extract", "daily-review": "default@daily-review"}.get(
        req.template_type, "fallback"
    )
    default_tpl = pc.get(default_type_id)
    forked_prompt = default_tpl.draft.system_prompt if default_tpl else _DEFAULT_SYSTEM_PROMPT

    t = PromptTemplate(
        id=template_id,
        name=req.name or f"{req.endpoint}@{req.template_type}",
        description=req.description or f"从 {default_type_id} fork",
        template_type=req.template_type,
        user_template=req.user_template,
        chat_style=req.chat_style,
        parameters=req.parameters,
        system_prompt_text=req.system_prompt_text or forked_prompt,
    )
    t.draft = PromptVersion(
        version="1.0.0",
        system_prompt=req.system_prompt_text or forked_prompt,
        created_at=datetime.now().isoformat(),
    )
    t._sync_from_legacy()
    t.upgrade("1.0.0", "初始版本")
    pc.upsert(t)
    pc.save()
    logger.info("提示词模板已创建 id=%s type=%s fork_from=%s", template_id, req.template_type, default_type_id)
    return {"id": t.id, "message": "模板已创建"}


@router.put("/api/prompts/{id}")
def update_prompt(request: Request, id: str, req: UpdatePromptRequest):
    """更新提示词模板元数据"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    if req.name is not None:
        t.name = req.name
    if req.description is not None:
        t.description = req.description
    if req.prompt is not None:
        t.prompt = req.prompt
    if req.system_prompt_text is not None:
        t.system_prompt_text = req.system_prompt_text
    if req.parameters is not None:
        t.parameters = req.parameters
    pc.upsert(t)
    pc.save()
    logger.info("提示词模板已更新 id=%s", id)
    return {"id": id, "message": "模板已更新"}


@router.delete("/api/prompts/{id}")
def delete_prompt(request: Request, id: str):
    """删除提示词模板（不允许删除 default）"""
    pc = config.prompt
    if not pc.delete(id):
        raise HTTPException(400, "无法删除：default 模板不可删除")
    pc.save()
    logger.info("提示词模板已删除 id=%s", id)
    return {"message": "模板已删除"}


# --- 草稿 + 版本管理 ---


@router.post("/api/prompts/{id}/draft")
def save_draft(request: Request, id: str, req: SaveDraftRequest):
    """保存草稿（仅 system_prompt，不创建版本），草稿即时生效于提炼"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    kwargs = {}
    if req.system_prompt is not None:
        kwargs["system_prompt"] = req.system_prompt
    t.save_draft(**kwargs)
    pc.save()
    logger.info("草稿已保存 id=%s", id)
    return {"id": id, "message": "草稿已保存（即时生效）", "draft": t.draft.model_dump()}


@router.put("/api/prompts/{id}/config")
def save_prompt_config(request: Request, id: str, req: SaveConfigRequest):
    """保存模板级公共属性（名称、描述、消息格式、LLM 参数等）"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    kwargs = {}
    if req.name is not None:
        t.name = req.name
    if req.description is not None:
        t.description = req.description
    if req.user_template is not None:
        kwargs["user_template"] = req.user_template
    if req.chat_style is not None:
        kwargs["chat_style"] = req.chat_style
    if req.parameters is not None:
        kwargs["parameters"] = req.parameters
    t.save_draft(**kwargs)
    pc.upsert(t)
    pc.save()
    logger.info("模板配置已保存 id=%s", id)
    return {"id": id, "message": "模板配置已保存"}


@router.post("/api/prompts/{id}/upgrade")
def upgrade_prompt(request: Request, id: str, req: UpgradeRequest):
    """将当前草稿升级为新版本"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    new_ver = t.upgrade(req.version, req.changelog)
    pc.save()
    logger.info("提示词升级 id=%s version=%s", id, req.version)
    return {
        "id": id,
        "version": new_ver.version,
        "changelog": new_ver.changelog,
        "version_count": len(t.versions),
        "message": f"已升级到 {new_ver.version}",
    }


@router.get("/api/prompts/{id}/versions/{version}")
def get_prompt_version(request: Request, id: str, version: str):
    """获取指定版本完整内容"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    v = t.get_version(version)
    if not v:
        raise HTTPException(404, f"版本 {version} 不存在")
    return v.model_dump()


@router.delete("/api/prompts/{id}/versions/{version}")
def delete_prompt_version(request: Request, id: str, version: str):
    """删除指定版本（不可删除活跃版本或最后一个版本）"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    if not t.delete_version(version):
        raise HTTPException(400, "无法删除：不能删除活跃版本或最后一个版本")
    vf = _get_version_file(id, version)
    if vf.exists():
        vf.unlink()
    pc.save()
    logger.info("版本已删除 id=%s version=%s", id, version)
    return {"message": f"版本 {version} 已删除", "version": version}


@router.post("/api/prompts/{id}/sync-to-active")
def sync_draft_to_active(request: Request, id: str):
    """将当前草稿的 system_prompt 同步写入活跃版本（覆盖），用于微小修改无需新建版本号"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    if not t.sync_to_active():
        raise HTTPException(400, "同步失败：无活跃版本或版本不存在")
    pc.save()
    logger.info("草稿已同步至活跃版本 id=%s version=%s", id, t.active_version)
    return {"id": id, "active_version": t.active_version, "message": f"草稿已同步至 v{t.active_version}"}


@router.post("/api/prompts/{id}/activate-version/{version}")
def activate_prompt_version(request: Request, id: str, version: str):
    """切换活跃版本（只更新 system_prompt，公共属性不变）"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    target_ver = t.get_version(version)
    if not target_ver:
        raise HTTPException(404, f"版本 {version} 不存在")
    t.draft.system_prompt = target_ver.system_prompt
    t.active_version = version
    t.system_prompt_text = target_ver.system_prompt
    pc.save()
    logger.info("活跃版本切换 id=%s version=%s", id, version)
    return {"id": id, "active_version": version, "message": f"已切换到版本 {version}"}


@router.post("/api/prompts/{id}/rollback/{version}")
def rollback_prompt(request: Request, id: str, version: str, req: RollbackRequest = None):
    """回滚到指定历史版本（生成新版本）"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    changelog = req.changelog if req else ""
    new_ver = t.rollback_to(version, changelog)
    if not new_ver:
        raise HTTPException(404, f"版本 {version} 不存在")
    pc.save()
    logger.info("提示词回滚 id=%s from=%s to=%s", id, version, new_ver.version)
    return {
        "id": id,
        "version": new_ver.version,
        "changelog": new_ver.changelog,
        "version_count": len(t.versions),
        "message": f"已回滚到 {version}（新版本 {new_ver.version}）",
    }


@router.get("/api/prompts/{id}/diff")
def diff_prompt_versions(request: Request, id: str, v1: str = "", v2: str = ""):
    """对比两个版本的 system_prompt 差异（简易行级 diff）"""
    pc = config.prompt
    t = pc.get(id)
    if not t:
        raise HTTPException(404, f"模板 {id} 不存在")
    t._sync_from_legacy()
    ver1 = t.get_version(v1)
    ver2 = t.get_version(v2)
    if not ver1:
        raise HTTPException(404, f"版本 {v1} 不存在")
    if not ver2:
        raise HTTPException(404, f"版本 {v2} 不存在")
    lines1 = ver1.system_prompt.splitlines(keepends=True)
    lines2 = ver2.system_prompt.splitlines(keepends=True)

    # 简易 LCS diff
    import difflib

    diff_lines = []
    for line in difflib.unified_diff(
        [ln.rstrip("\n") for ln in lines1],
        [ln.rstrip("\n") for ln in lines2],
        fromfile=v1,
        tofile=v2,
        lineterm="",
    ):
        diff_lines.append(line)

    return {
        "id": id,
        "v1": {"version": v1, "created_at": ver1.created_at},
        "v2": {"version": v2, "created_at": ver2.created_at},
        "diff": "\n".join(diff_lines),
    }


@router.get("/api/prompts/for-endpoint/{name}")
def get_prompt_for_endpoint(request: Request, name: str, type: str = "extract"):
    """获取指定端点对应类型的提示词模板（按显式关联 → 命名约定 → 类型默认 fallback）"""
    t = config.prompt.get_for_endpoint(name, template_type=type)
    if not t:
        return {"template": None, "message": f"端点 '{name}' 无专属模板"}
    result = _template_to_dict(t)
    result["is_fallback"] = t.id.startswith("fallback@") or t.id == "fallback"
    return {"template": result}
