"""修复 test_integration_all.py 中的 MCP 测试引用"""
import re

with open('tests/test_integration_all.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add _get_memory import
content = content.replace(
    '_get_project_id as mcp_get_project_id,\n)',
    '_get_project_id as mcp_get_project_id,\n    _get_memory as mcp_memory,\n)'
)

# 2. Fix setup_method in TestGroupC_MCP
old_setup = (
    '    def setup_method(self):\n'
    '        from memos.memory import LongTermMemory\n'
    '        self._mem = LongTermMemory(self.COLLECTION)\n'
    '        # \xe6\xb8\x85\xe7\x90\x86\xe6\x97\xa7\xe6\x95\xb0\xe6\x8d\xae\xef\xbc\x88MCP \xe5\x85\xa8\xe5\xb1\x80 store \xe6\x8c\x87\xe5\x90\x91\xe5\x90\x8c\xe4\xb8\x80 collection\xef\xbc\x8c\xe7\x9b\xb4\xe6\x8e\xa5 get/delete\xef\xbc\x89\n'  # Chinese comment
    '        all_ids = self._mem.store.get()["ids"]\n'
    '        if all_ids:\n'
    '            self._mem.store.delete(ids=all_ids)'
)
new_setup = (
    '    def setup_method(self):\n'
    '        # \xe6\xb8\x85\xe7\x90\x86 MCP \xe5\x85\xa8\xe5\xb1\x80\xe9\xbb\x98\xe8\xae\xa4 collection \xe7\x9a\x84\xe6\x95\xb0\xe6\x8d\xae\n'
    '        all_ids = mcp_memory().store.get()["ids"]\n'
    '        if all_ids:\n'
    '            mcp_memory().store.delete(ids=all_ids)'
)
content = content.replace(old_setup, new_setup)

# 3. Fix test_c2
old = (
    '    def test_c2_mcp_recall(self):\n'
    '        from memos.memory import LongTermMemory\n'
    '        m = LongTermMemory(self.COLLECTION)\n'
    '        m.remember("Python使用FastAPI框架", metadata={"project_id": "test_mcp"})'
)
new = (
    '    def test_c2_mcp_recall(self):\n'
    '        mcp_memory().remember("Python使用FastAPI框架", metadata={"project_id": "test_mcp"})'
)
content = content.replace(old, new)

# 4. Fix test_c3
old = (
    '    def test_c3_mcp_recall_hybrid(self):\n'
    '        from memos.memory import LongTermMemory\n'
    '        m = LongTermMemory(self.COLLECTION)\n'
    '        m.remember("使用PostgreSQL数据库", metadata={"project_id": "test_mcp"})'
)
new = (
    '    def test_c3_mcp_recall_hybrid(self):\n'
    '        mcp_memory().remember("使用PostgreSQL数据库", metadata={"project_id": "test_mcp"})'
)
content = content.replace(old, new)

# 5. Fix test_c5
old = (
    '    def test_c5_mcp_list_memories(self):\n'
    '        from memos.memory import LongTermMemory\n'
    '        m = LongTermMemory(self.COLLECTION)\n'
    '        m.remember("列表项A", metadata={"project_id": "test_mcp"})\n'
    '        m.remember("列表项B", metadata={"project_id": "test_mcp"})'
)
new = (
    '    def test_c5_mcp_list_memories(self):\n'
    '        mcp_memory().remember("列表项A", metadata={"project_id": "test_mcp"})\n'
    '        mcp_memory().remember("列表项B", metadata={"project_id": "test_mcp"})'
)
content = content.replace(old, new)

# 6. Fix test_c6
old = (
    '    def test_c6_mcp_update_memory(self):\n'
    '        from memos.memory import LongTermMemory\n'
    '        m = LongTermMemory(self.COLLECTION)\n'
    '        mid = m.remember("旧内容", {"type": "decision", "project_id": "test_mcp"})\n'
    '        m.update_memory(mid, "新内容")\n'
    '        assert m.get_memory(mid)["document"] == "新内容"'
)
new = (
    '    def test_c6_mcp_update_memory(self):\n'
    '        mid = mcp_memory().remember("旧内容", {"type": "decision", "project_id": "test_mcp"})\n'
    '        mcp_memory().update_memory(mid, "新内容")\n'
    '        assert mcp_memory().get_memory(mid)["document"] == "新内容"'
)
content = content.replace(old, new)

# 7. Fix test_c7
old = (
    '    def test_c7_mcp_delete_memory(self):\n'
    '        from memos.memory import LongTermMemory\n'
    '        m = LongTermMemory(self.COLLECTION)\n'
    '        mid = m.remember("待删除", {"project_id": "test_mcp"})\n'
    '        m.delete_memory(mid)\n'
    '        assert m.get_memory(mid) is None'
)
new = (
    '    def test_c7_mcp_delete_memory(self):\n'
    '        mid = mcp_memory().remember("待删除", {"project_id": "test_mcp"})\n'
    '        mcp_memory().delete_memory(mid)\n'
    '        assert mcp_memory().get_memory(mid) is None'
)
content = content.replace(old, new)

# 8. Fix test_d5
old = (
    '    def test_d5_mcp_project_switch(self):\n'
    '        original_pid = mcp_get_project_id()\n'
    '        mcp_set_project_id("isolation_p1")\n'
    '        from memos.memory import LongTermMemory\n'
    '        m = LongTermMemory(self.COLLECTION)\n'
    '        m.remember("p1 \xe7\x9a\x84\xe8\xae\xb0\xe5\xbf\x86", {"project_id": "isolation_p1"})\n'
    '        mcp_set_project_id("isolation_p2")\n'
    '        m.remember("p2 \xe7\x9a\x84\xe8\xae\xb0\xe5\xbf\x86", {"project_id": "isolation_p2"})'
)
new = (
    '    def test_d5_mcp_project_switch(self):\n'
    '        original_pid = mcp_get_project_id()\n'
    '        mcp_set_project_id("isolation_p1")\n'
    '        mcp_memory().remember("p1 \xe7\x9a\x84\xe8\xae\xb0\xe5\xbf\x86", {"project_id": "isolation_p1"})\n'
    '        mcp_set_project_id("isolation_p2")\n'
    '        mcp_memory().remember("p2 \xe7\x9a\x84\xe8\xae\xb0\xe5\xbf\x86", {"project_id": "isolation_p2"})'
)
content = content.replace(old, new)

with open('tests/test_integration_all.py', 'w', encoding='utf-8') as f:
    f.write(content)
print(f'Done, file size={len(content)}')
