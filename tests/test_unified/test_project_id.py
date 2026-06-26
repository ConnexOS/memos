"""测试 project_id 解析逻辑 — .memos-project JSON 单一来源"""

import json
from pathlib import Path

from memos.hook_proxy.project_id import (
    clear_project_id_cache,
    resolve_project_id,
    resolve_project_name,
)


def test_read_from_memos_project_json(tmp_path):
    """读取 .memos-project JSON 文件获取 id 和 name"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": "abc12345", "name": "MyProject"}), encoding="utf-8")
    clear_project_id_cache()
    pid = resolve_project_id(str(tmp_path))
    name = resolve_project_name(str(tmp_path))
    assert pid == "abc12345"
    assert name == "MyProject"


def test_no_memos_project_raises(tmp_path):
    """无 .memos-project 文件时抛出明确错误"""
    clear_project_id_cache()
    try:
        resolve_project_id(str(tmp_path))
        assert False, "Should have raised"
    except FileNotFoundError as e:
        assert ".memos-project" in str(e)


def test_memos_project_cache_hit(tmp_path):
    """缓存命中后不重复读文件"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": "xyz", "name": "X"}), encoding="utf-8")
    clear_project_id_cache()
    pid1 = resolve_project_id(str(tmp_path))
    # 修改文件但缓存未清 → 仍返回旧值
    proj_file.write_text(json.dumps({"id": "abc", "name": "Y"}), encoding="utf-8")
    pid2 = resolve_project_id(str(tmp_path))
    assert pid1 == pid2 == "xyz"


def test_memos_project_bad_json_raises(tmp_path):
    """损坏的 .memos-project JSON 抛出 ValueError"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text("not-json", encoding="utf-8")
    clear_project_id_cache()
    try:
        resolve_project_id(str(tmp_path))
        assert False, "Should have raised"
    except ValueError as e:
        assert "格式错误" in str(e)


def test_find_project_file_in_parent_dir(tmp_path):
    """从子目录向上查找 .memos-project"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": "parent01", "name": "ParentProj"}), encoding="utf-8")
    sub_dir = tmp_path / "sub" / "deep"
    sub_dir.mkdir(parents=True)
    clear_project_id_cache()
    pid = resolve_project_id(str(sub_dir))
    name = resolve_project_name(str(sub_dir))
    assert pid == "parent01"
    assert name == "ParentProj"


def test_clear_cache_affects_only_specified(tmp_path):
    """clear_project_id_cache(cwd) 只清指定缓存"""
    d1 = tmp_path / "proj1"
    d1.mkdir()
    (d1 / ".memos-project").write_text(json.dumps({"id": "11111111", "name": "P1"}), encoding="utf-8")
    d2 = tmp_path / "proj2"
    d2.mkdir()
    (d2 / ".memos-project").write_text(json.dumps({"id": "22222222", "name": "P2"}), encoding="utf-8")
    clear_project_id_cache()
    pid1 = resolve_project_id(str(d1))
    pid2 = resolve_project_id(str(d2))
    clear_project_id_cache(str(d1))
    # d1 缓存被清，应读文件；d2 缓存保留
    assert resolve_project_id(str(d1)) == "11111111"
    assert resolve_project_id(str(d2)) == "22222222"
