from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

# 本模块特有导入
from ...config import LLMEndpoint, config
from ..app import _status_cache_lock, _system_status_cache
from ..models import (
    ActivateEndpointRequest,
    CreateEndpointRequest,
    TestConnectionRequest,
    UpdateEndpointRequest,
)
from ..services.helpers import _check_llama_health, _find_llm_endpoint, _probe_llm_endpoint

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/llm/endpoints")
def list_llm_endpoints():
    """列出所有 LLM 端点"""
    items = []
    for ep in config.llm.endpoints:
        items.append(
            {
                "name": ep.name,
                "api_base": ep.api_base,
                "api_key": "******" if ep.api_key else "",
                "model": ep.model,
                "is_active": ep.name == config.llm.active,
                "prompt_templates": ep.prompt_templates,
            }
        )
    return {"endpoints": items, "active": config.llm.active}


@router.post("/api/llm/endpoints", status_code=201)
def create_llm_endpoint(req: CreateEndpointRequest):
    """创建新 LLM 端点"""
    if _find_llm_endpoint(req.name):
        raise HTTPException(409, f"端点 '{req.name}' 已存在")
    new_ep = LLMEndpoint(name=req.name, api_base=req.api_base, api_key=req.api_key, model=req.model)
    config.llm.endpoints.append(new_ep)
    config.save()
    logger.info("LLM 端点已创建 name=%s", req.name)
    return {"name": req.name, "message": "端点已创建"}


@router.put("/api/llm/endpoints/{name}")
def update_llm_endpoint(name: str, req: UpdateEndpointRequest):
    """更新 LLM 端点"""
    ep = _find_llm_endpoint(name)
    if not ep:
        raise HTTPException(404, f"端点 '{name}' 不存在")
    if req.api_base is not None:
        ep.api_base = req.api_base
    if req.api_key is not None:
        ep.api_key = req.api_key
    if req.model is not None:
        ep.model = req.model
    if req.prompt_templates is not None:
        ep.prompt_templates = req.prompt_templates
    config.save()
    logger.info("LLM 端点已更新 name=%s", name)
    return {"name": name, "message": "端点已更新"}


@router.delete("/api/llm/endpoints/{name}")
def delete_llm_endpoint(name: str):
    """删除 LLM 端点"""
    if name == config.llm.active:
        raise HTTPException(400, "不能删除当前活跃端点")
    if name == "default":
        raise HTTPException(400, "不能删除 default 端点")
    if not _find_llm_endpoint(name):
        raise HTTPException(404, f"端点 '{name}' 不存在")
    config.llm.endpoints = [e for e in config.llm.endpoints if e.name != name]
    config.save()
    logger.info("LLM 端点已删除 name=%s", name)
    return {"message": "端点已删除"}


@router.post("/api/llm/activate")
async def activate_llm_endpoint(req: ActivateEndpointRequest):
    """切换活跃 LLM 端点（切换后主动健康检查）"""
    ep = _find_llm_endpoint(req.name)
    if not ep:
        raise HTTPException(404, f"端点 '{req.name}' 不存在")
    config.llm.active = req.name
    config.save()
    # 切换后立即主动检测新端点健康状态
    ok = await _check_llama_health(endpoint=ep)
    # 更新状态缓存，使前端轮询 /api/system 立即看到最新状态
    with _status_cache_lock:
        _system_status_cache["llama_server_ok"] = ok
        _system_status_cache["cached_at"] = time.time()
    status = "online" if ok else "unreachable"
    logger.info("LLM 活跃端点已切换 name=%s status=%s", req.name, status)
    return {"active": req.name, "status": status, "message": f"已切换到端点 '{req.name}'（{status}）"}


@router.post("/api/llm/test-connection")
async def test_llm_connection(req: TestConnectionRequest):
    """测试指定 LLM 端点的连通性，返回详细诊断信息。P2-3: 委托 _probe_llm_endpoint 消除重复。"""
    ep = _find_llm_endpoint(req.endpoint_id)
    if not ep:
        raise HTTPException(404, f"端点 '{req.endpoint_id}' 不存在")

    timeout = config.dashboard.test_connection_timeout  # P1-4: 使用独立超时配置，默认 5s
    ok, method, latency_ms = await _probe_llm_endpoint(ep, timeout)

    if ok:
        return {"status": "ok", "method": method, "latency_ms": latency_ms}

    if "超时" in method or "timeout" in method.lower():
        return {
            "status": "error",
            "reason": f"连接超时 ({timeout}s)",
            "suggestion": "检查 LLM 地址是否可达，网络是否正常",
        }
    return {"status": "error", "reason": method, "suggestion": "检查 LLM 地址和网络连接"}
