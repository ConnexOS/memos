from __future__ import annotations

import logging

from fastapi import APIRouter, Request

# 本模块特有导入
from ...config import MemoConfig, config
from ...errors import ConfigCorruptedError
from ..models import ConfigUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/config")
def get_config(request: Request):
    sections = config.model_dump()
    # LLM 多端点结构：移除原始端点列表和 active（由端点管理器管理），暴露活跃端点的计算字段
    llm_cfg = sections.get("llm", {})
    llm_cfg.pop("endpoints", None)
    llm_cfg.pop("active", None)
    llm_cfg["api_base"] = config.llm.api_base
    llm_cfg["api_key"] = "******" if config.llm.api_key else ""
    # 将嵌入模型配置合并到记忆管理，减少 tab 数量
    model_fields = sections.pop("model", {})
    sections["memory"] = {**model_fields, **sections.get("memory", {})}
    # stop 保留在配置文件中，不出现在 UI 配置项
    llm_cfg.pop("stop", None)
    # prompt 由独立 Tab（提示词管理）承载，不出现在系统设置中
    sections.pop("prompt", None)
    # suggestion 在 UI 中由独立建议设置面板管理，不在系统设置中展示
    sections.pop("suggestion", None)
    return {
        "sections": sections,
        "flattened": config.flatten(),
    }


def _coerce_value(val: str):
    """将字符串值还原为原始类型"""
    if val.isdigit():
        return int(val)
    try:
        return float(val)
    except ValueError:
        pass
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    return val


@router.put("/api/config")
def update_config(request: Request, req: ConfigUpdateRequest):
    # get_config 将 model section 合并到 memory section 展示，这里反向映射
    ok = config.update_field(req.key, str(req.value))
    effective_key = req.key
    if not ok:
        parts = req.key.split(".", 1)
        if len(parts) == 2 and parts[0] != "model":
            fallback_key = f"model.{parts[1]}"
            ok = config.update_field(fallback_key, str(req.value))
            if ok:
                effective_key = fallback_key
    if not ok:
        raise ConfigCorruptedError(f"无效配置项: {req.key}", detail="请运行 memos config validate 检查配置")
    config.save()
    coerced = _coerce_value(str(req.value))
    logger.info("配置已更新 %s = %s", effective_key, coerced)
    return {"message": f"已更新 {req.key}", "key": effective_key, "value": coerced}


@router.post("/api/config/reload")
def reload_config(request: Request):
    new_cfg = MemoConfig.reload()
    for section_name in ("chroma", "model", "llm", "memory", "buffer", "dashboard", "server"):
        section = getattr(config, section_name)
        new_section = getattr(new_cfg, section_name)
        for field_name in section.model_dump():
            if field_name in type(section).model_computed_fields:
                continue
            setattr(section, field_name, getattr(new_section, field_name))
    request.app.state.config = config
    logger.info("配置已从文件重新加载")
    return {"message": "配置已重新加载", "config": config.flatten()}


# --- API: 对话记录 ---
