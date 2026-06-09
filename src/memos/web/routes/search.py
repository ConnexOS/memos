from __future__ import annotations

import logging

from fastapi import APIRouter, Request

# 本模块特有导入
from ..models import SearchRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/search")
def search(request: Request, req: SearchRequest):
    mem = request.app.state.context_memory
    logger.info("检索 query=%s top_k=%d project=%s", req.query[:50], req.top_k, req.project_id)
    knowledge_types = [
        "fact",
        "decision",
        "preference",
        "bug_fix",
        "feature_design",
        "code_optimize",
        "tech_knowledge",
    ]
    where = {"type": req.type_filter} if req.type_filter else {"type": {"$in": knowledge_types}}
    results = mem.recall(
        query=req.query,
        top_k=req.top_k,
        where=where,
        days_limit=req.days_limit,
        project_id=req.project_id,
        decay_lambda=req.decay_lambda,
        hybrid=req.hybrid,
        bm25_weight=req.bm25_weight,
        return_scores=True,
    )
    return {
        "results": results,
        "query": req.query,
        "params": {
            "top_k": req.top_k,
            "days_limit": req.days_limit,
            "type_filter": req.type_filter,
            "decay_lambda": req.decay_lambda,
            "hybrid": req.hybrid,
            "bm25_weight": req.bm25_weight,
        },
    }


# --- API: 项目列表 ---
