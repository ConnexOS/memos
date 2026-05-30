"""一键拆分 dashboard.py 路由到 dashboard/routes/*.py"""
import re
import os

BASE = 'src/memos/dashboard/routes'

with open('src/memos/dashboard.py', encoding='utf-8') as f:
    content = f.read()
lines = content.split('\n')

# 找到所有 @app 装饰器的行号（1-indexed）
decorator_lines = []
for i, line in enumerate(lines):
    if re.match(r'@app\.(get|post|put|delete|patch)\(', line):
        decorator_lines.append(i + 1)

# 添加文件末尾作为最后一个路由的结束
decorator_lines.append(len(lines) + 1)


def get_group(path):
    if path in ('/', '/login', '/favicon.ico'):
        return 'pages'
    if path.startswith('/api/auth/'):
        return 'auth'
    if path.startswith('/api/backup') or path == '/api/backups/list':
        return 'backups'
    if path.startswith('/api/config'):
        return 'config_routes'
    if path.startswith('/api/conversations/'):
        return 'conversations'
    if path == '/api/conversations':
        return 'conversations'
    if path.startswith('/api/llm/'):
        return 'llm'
    if path.startswith('/api/memories'):
        return 'memories'
    if path in ('/notifications',) or path.startswith('/api/notifications'):
        return 'notifications'
    if path.startswith('/api/prompts'):
        return 'prompts'
    if path == '/api/search':
        return 'search'
    if path in ('/api/projects', '/api/status', '/api/vacuum',
                '/api/stats/usage', '/api/stats/trend',
                '/api/conflicts', '/api/conflicts/count'):
        return 'system'
    if path.startswith('/api/conflicts/'):
        return 'system'
    return None


# 收集每个路由的行范围和所属组
route_entries = []
for idx, start_line in enumerate(decorator_lines[:-1]):
    next_start = decorator_lines[idx + 1]
    dec_line = lines[start_line - 1]
    m = re.match(r"@app\.(get|post|put|delete|patch)\([\"']([^\"']+)", dec_line)
    if not m:
        continue
    end_line = next_start - 1
    path = m.group(2)
    group = get_group(path)
    route_entries.append((start_line, end_line, path, group))

# 按组合并
from collections import defaultdict

group_ranges = defaultdict(list)
for start, end, path, group in route_entries:
    if group is None:
        continue
    group_ranges[group].append((start, end))

# 合并相邻范围
for group in group_ranges:
    merged = []
    for start, end in sorted(group_ranges[group]):
        if merged and start <= merged[-1][1] + 3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    group_ranges[group] = merged

# 生成每个组的路由文件
for group, ranges in sorted(group_ranges.items()):
    all_group_lines = []
    for start, end in ranges:
        all_group_lines.extend(lines[start - 1:end])

    group_text = '\n'.join(all_group_lines)

    # 检测需要的导入
    needed_imports = set()
    import_checks = [
        ('config.', 'from memos.config import config'),
        ('ContextMemory', 'from memos.memory import ContextMemory'),
        ('MemoryExtractor', 'from memos.extractor import MemoryExtractor'),
        ('_extract_llm_content', 'from memos.extractor import _extract_llm_content'),
        ('_strip_think_block', 'from memos.extractor import _strip_think_block'),
        ('format_conversation', 'from memos.extractor import format_conversation'),
        ('MemoConfig', 'from memos.config import MemoConfig'),
        ('PromptTemplate', 'from memos.config import PromptTemplate'),
        ('PromptVersion', 'from memos.config import PromptVersion'),
        ('LLMEndpoint', 'from memos.config import LLMEndpoint'),
        ('_get_version_file', 'from memos.config import _get_version_file'),
        ('ChromaDBError', 'from memos.errors import ChromaDBError'),
        ('MemoError', 'from memos.errors import MemoError'),
        ('LLMUnreachableError', 'from memos.errors import LLMUnreachableError'),
        ('verify_session_token', 'from memos.auth import verify_session_token'),
        ('create_session_token', 'from memos.auth import create_session_token'),
        ('hash_token', 'from memos.auth import hash_token'),
        ('_query_conversations_by_date_range', 'from memos.daily_review import _query_conversations_by_date_range'),
        ('generate_daily_report', 'from memos.daily_review import generate_daily_report'),
        ('write_daily_report', 'from memos.daily_review import write_daily_report'),
        ('usage_logger', 'from memos.usage import usage_logger'),
        ('ConfigCorruptedError', 'from memos.errors import ConfigCorruptedError'),
        ('DiskFullError', 'from memos.errors import DiskFullError'),
        ('PermissionDeniedError', 'from memos.errors import PermissionDeniedError'),
        ('http_status_for', 'from memos.errors import http_status_for'),
    ]
    for pattern, imp in import_checks:
        if pattern in group_text:
            needed_imports.add(imp)

    # 构建导入
    import_lines = [
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
    ]
    import_lines.extend(sorted(needed_imports))
    import_lines.append('')
    import_lines.append('logger = logging.getLogger(__name__)')
    import_lines.append('')
    import_lines.append('router = APIRouter()')
    import_lines.append('')

    # 替换 @app. 为 @router.
    converted = re.sub(r'@app\.(get|post|put|delete|patch)\(', r'@router.\1(', group_text)

    file_content = '\n'.join(import_lines) + '\n' + converted + '\n'

    filepath = os.path.join(BASE, f'{group}.py')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(file_content)

    line_count = len(file_content.split('\n'))
    print(f'{group}.py: {line_count} lines ({len(ranges)} route blocks)')

print("\nDone! All route files created.")
