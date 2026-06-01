# src/memos/web/dependencies.py
from fastapi import Request


async def get_project_id(request: Request) -> str:
    return request.state.project_id
