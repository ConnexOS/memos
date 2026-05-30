"""为每个路由文件添加其需要的导入"""
import re
import os

ROUTES_DIR = 'src/memos/dashboard/routes'

# 每个路由文件需要的额外导入（基于 grep 分析）
EXTRA_IMPORTS = {
    'auth.py': [
        'import asyncio',
        'import re',
        'from pydantic import BaseModel, Field',
        'from .. import _detect_project_id, _get_projects_from_db, _KB_TYPES, _system_status_cache, _status_cache_lock, _projects_cache, _projects_cache_lock',
        'from ..services.helpers import _probe_llm_endpoint',
    ],
    'backups.py': [
        'import re',
        'from datetime import datetime',
        'from ..services.helpers import _format_time_ago, _get_notification_context',
    ],
    'config_routes.py': [
        'import re',
    ],
    'conversations.py': [
        'import re',
        'from datetime import datetime',
        'from pydantic import BaseModel, Field',
        'from .. import _projects_cache',
        'from ..services.helpers import _parse_knowledge_cards, _valid_card, _clean_card, _find_llm_endpoint',
    ],
    'llm.py': [
        'import re',
        'from pydantic import BaseModel, Field',
        'from .. import templates, _system_status_cache, _status_cache_lock',
        'from ..services.helpers import _probe_llm_endpoint, _find_llm_endpoint',
    ],
    'memories.py': [
        'import re',
        'from datetime import datetime',
        'from pydantic import BaseModel, Field',
        'from .. import _detect_project_id, _projects_cache',
        'from ..services.helpers import _template_to_dict',
    ],
    'notifications.py': [
        'import re',
        'from .. import templates',
        'from ..services.helpers import _format_time_ago, _get_notification_context',
    ],
    'pages.py': [
        'import re',
        'from pydantic import BaseModel, Field',
        'from .. import templates',
        'from ..services.helpers import _get_notification_context',
    ],
    'prompts.py': [
        'import re',
        'from datetime import datetime',
        'from pydantic import BaseModel, Field',
        'from .. import templates',
        'from ..services.helpers import _template_to_dict, _find_llm_endpoint',
    ],
    'search.py': [
        'import re',
    ],
    'system.py': [
        'import re',
        'from pydantic import BaseModel',
        'from .. import _detect_project_id, _get_projects_from_db, _KB_TYPES',
        'from ..services.helpers import _calc_db_size',
    ],
}

# 公共导入（每个文件都需要）
COMMON_IMPORTS = [
    'from __future__ import annotations',
    '',
    'import json',
    'import logging',
    'import os',
    'import time',
    'from pathlib import Path',
    '',
    'from fastapi import APIRouter, HTTPException, Query, Request, UploadFile',
    'from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse',
    '',
    'from memos.config import config',
    'from memos.errors import ChromaDBError, MemoError',
    '',
    'logger = logging.getLogger(__name__)',
    '',
    'router = APIRouter()',
    '',
]

for filename, extra_imports in EXTRA_IMPORTS.items():
    filepath = os.path.join(ROUTES_DIR, filename)
    if not os.path.exists(filepath):
        continue

    with open(filepath, encoding='utf-8') as f:
        content = f.read()

    # 替换旧的导入头（从文件开头的 from __future__ 到 router = APIRouter() 之间）
    # 找 'router = APIRouter()' 的位置
    router_pos = content.find('router = APIRouter()')
    if router_pos == -1:
        print(f'WARN: {filename} 中没有找到 router = APIRouter()')
        continue

    # 从 router 定义之后截取
    route_body = content[router_pos + len('router = APIRouter()'):]

    # 构建新的导入头
    new_imports = list(COMMON_IMPORTS)
    if extra_imports:
        new_imports.append('# 本模块特有导入')
        new_imports.extend(sorted(extra_imports))

    new_content = '\n'.join(new_imports) + '\n' + route_body

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f'{filename}: rebuilt ({len(new_content.splitlines())} lines)')

print('\nDone!')
