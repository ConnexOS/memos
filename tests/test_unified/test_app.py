import pytest


def test_app_created(unified_app):
    """验证应用可正常创建"""
    assert unified_app.title == "长时记忆系统（Unified）"


def test_app_static_files_mounted(unified_app):
    """验证静态文件路由已挂载"""
    for route in unified_app.routes:
        if "/static" in str(getattr(route, "path", "")):
            return
    pytest.fail("未找到 /static 路由")


def test_no_duplicate_prefix(unified_app):
    """验证无重复路由前缀（评审 IB4：避免 /api/api/xxx）"""
    paths = [str(r.path) for r in unified_app.routes]
    for p in paths:
        if "api" in p:
            assert "/api/api/" not in p, f"发现重复前缀: {p}"
