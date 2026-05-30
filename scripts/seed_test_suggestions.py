r"""向当前项目注入测试建议数据（用于验收 F3 主动建议面板）

用法：
    cd D:\DevSpace\MEMOS
    .\venv\Scripts\python scripts\seed_test_suggestions.py
"""

import time
import sys
from pathlib import Path

# 确保能找到 memos 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memos.engine.memory import ContextMemory
from memos.config import config


def seed_suggestions(mem, project_id: str):
    """注入 5 条测试建议"""

    suggestions = [
        {
            "content": "检测到您近期频繁修改搜索相关代码，建议提取通用搜索组件以减少重复代码。当前对话记录中有 3 处涉及搜索逻辑的修改。",
            "source_memory_id": "seed-001",
            "similarity": 0.87,
            "query": "搜索重构",
            "source_type": "code_optimize",
        },
        {
            "content": "您之前曾决策使用 Pipeline B（用户直写）保存重要知识。建议将本次对话中的技术方案整理为知识卡片，便于后续回顾。",
            "source_memory_id": "seed-002",
            "similarity": 0.76,
            "query": "知识管理",
            "source_type": "decision",
        },
        {
            "content": "注意到 v0.4.4 验收测试中发现 pageSize 配置不一致的问题。建议在项目 CLAUDE.md 中记录 pageSize 默认为 30 的约定，避免后续版本再次混淆。",
            "source_memory_id": "seed-003",
            "similarity": 0.93,
            "query": "pageSize",
            "source_type": "fact",
        },
        {
            "content": "您的 Hook 对话采集已累计 974 条记录。建议定期运行 memos vacuum 回收 ChromaDB 空间，当前已删除记录 7 条。",
            "source_memory_id": "seed-004",
            "similarity": 0.65,
            "query": "维护",
            "source_type": "preference",
        },
        {
            "content": "检测到 Dashboard 长时间运行（当前会话 > 30 分钟）。如果不再使用，建议关闭以释放系统资源。",
            "source_memory_id": "seed-005",
            "similarity": 0.72,
            "query": "资源管理",
            "source_type": "tech_knowledge",
        },
    ]

    now = time.time()
    added = 0
    skipped = 0

    for sug in suggestions:
        doc_id = f"sug-seed-{sug['source_memory_id']}"

        # 检查是否已存在
        existing = mem.store.get(ids=[doc_id], include=["documents"])
        if existing["ids"]:
            print(f"  [SKIP] 跳过已存在的建议: {doc_id}")
            skipped += 1
            continue

        metadata = {
            "type": "suggestion",
            "project_id": project_id,
            "status": "pending",
            "suggestion_type": "active_push",
            "source_type": sug["source_type"],
            "query": sug["query"],
            "source_memory_id": sug["source_memory_id"],
            "similarity": sug["similarity"],
            "timestamp": now,
            "expires_at": now + 86400 * 7,  # 7 天后过期
            "source_date": time.strftime("%Y-%m-%d", time.localtime(now)),
        }

        # 生成嵌入向量
        mem._ensure_encoder()
        embedding = mem._encoder.encode([sug["content"]]).tolist()

        mem.store.add(
            ids=[doc_id],
            documents=[sug["content"]],
            embeddings=embedding,
            metadatas=[metadata],
        )
        print(f"  [ADD] 添加建议: {sug['query']} (相似度 {sug['similarity']})")
        added += 1

    print(f"\n完成: 新增 {added} 条，跳过 {skipped} 条")


if __name__ == "__main__":
    # 获取当前活动项目 ID
    pid = config.memory.default_project_id
    print(f"当前默认项目 ID: {pid}")
    print(f"数据目录: {config.chroma.path}")

    mem = ContextMemory()
    seed_suggestions(mem, pid)

    # 验证
    count = mem.store.count(
        where={"$and": [{"type": "suggestion"}, {"project_id": pid}, {"status": "pending"}]}
    )
    print(f"当前待处理建议总数: {count}")
