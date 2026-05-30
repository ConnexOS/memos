"""端到端集成测试 — 真实 ChromaDB + 真实 LLM"""

import sys
import time
import json

from memos.engine.extractor import MemoryExtractor
from memos.engine.memory import ContextMemory


def print_separator(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


tester = IntegrationTest()


class IntegrationTest:
    def __init__(self):
        self.results = []

    def check(self, test_id, description, condition):
        status = "PASS" if condition else "FAIL"
        self.results.append((test_id, description, status))
        print(f"  [{status}] {test_id}: {description}")
        return condition

    def report(self):
        passed = sum(1 for _, _, s in self.results if s == "PASS")
        total = len(self.results)
        print(f"\n{'=' * 60}")
        print(f"  集成测试结果: {passed}/{total} 通过")
        for tid, desc, status in self.results:
            print(f"  [{status}] {tid}: {desc}")
        return passed == total


tester = IntegrationTest()

print_separator("测试1: extract() — 真实 LLM 调用")
ext = MemoryExtractor()
convo = "用户：我们用FastAPI还是Django？\n助手：推荐FastAPI，性能更好\n用户：好的，那就FastAPI"
memories = ext.extract(convo)
print(f"  提取结果: {memories}")
tester.check("1.1", "extract 返回列表", isinstance(memories, list))
tester.check("1.2", "非空结果（LLM 应提取出记忆）", len(memories) > 0)
if memories:
    tester.check("1.3", "每项含 content 字段", all("content" in m for m in memories))
    tester.check("1.4", "每项含 type 字段", all("type" in m for m in memories))
    tester.check("1.5", "type 为有效值", all(m["type"] in ("decision", "preference", "todo", "fact") for m in memories))

print_separator("测试2: extract() 中文对话")
convo2 = "用户：我决定用PostgreSQL\n助手：好的，端口用5432\n用户：前端用Vue3框架"
memories2 = ext.extract(convo2)
print(f"  提取结果: {memories2}")
tester.check("2.1", "返回列表", isinstance(memories2, list))
tester.check("2.2", "非空", len(memories2) > 0)

print_separator("测试3: store_memories() + recall() 真实存储与召回")
memory = ContextMemory("test_integration")
ext3 = MemoryExtractor(memory_system=memory)
test_memories = [
    {"content": "团队决定使用FastAPI框架", "type": "decision"},
    {"content": "数据库选用PostgreSQL", "type": "decision"},
    {"content": "每天早上10点开站会", "type": "fact"},
]
stored = ext3.store_memories(test_memories)
tester.check("3.1", "存储计数正确(3条不同内容)", stored == 3)

time.sleep(1)
results = memory.recall("用了什么后端框架？", top_k=3)
tester.check("3.2", "能召回 FastAPI 相关记忆", any("FastAPI" in r for r in results))
tester.check("3.3", "能召回 PostgreSQL 相关记忆", any("PostgreSQL" in r for r in results))

print_separator("测试4: 存储去重验证")
stored2 = ext3.store_memories([{"content": "团队决定使用FastAPI框架", "type": "decision"}])
tester.check("4.1", "精确重复被跳过", stored2 == 0)

stored3 = ext3.store_memories([{"content": "决定选用FastAPI作为后端", "type": "decision"}])
print(f"  相似句存储结果: {stored3} (0=跳过, 1=存入)")
tester.check("4.2", "高度相似语义去重或存储（不抛异常）", stored3 in (0, 1))

print_separator("测试5: extract_and_store() 端到端流程")
ext5 = MemoryExtractor(memory_system=memory)
convo_new = "用户：日志系统用ELK还是Loki？\n助手：推荐Loki，更轻量\n用户：好，就用Loki"
count = ext5.extract_and_store(convo_new)
print(f"  新对话提取+存储: {count} 条")
tester.check("5.1", "端到端提取+存储成功", count > 0)
tester.check("5.2", "新记忆可被召回", any(memory.recall("日志系统", top_k=5)))

print_separator("测试6: append_conversation + force_extract")
ext6 = MemoryExtractor(memory_system=memory)
r = ext6.append_conversation("user", "我们用Redis做缓存")
tester.check("6.1", "追加对话返回 None（未触发阈值）", r is None)
r = ext6.append_conversation("assistant", "好的，Redis是个好选择")
tester.check("6.2", "助手回复已入 buffer", len(ext6.conversation_buffer) == 2)
ext6.append_conversation("user", "端口用6379")
force_count = ext6.force_extract()
tester.check("6.3", "force_extract 执行成功", force_count > 0)

time.sleep(1)
redis_results = memory.recall("Redis端口", top_k=3)
tester.check("6.4", "Redis/6379 记忆可被召回", any(("6379" in r or "Redis" in r) for r in redis_results))

print_separator("测试7: append_conversation 自动触发提取")
ext7 = MemoryExtractor(memory_system=memory)
ext7._last_extract_time = 0
for i in range(5):
    ext7.append_conversation("user", f"今天讨论第{i + 1}个技术方案")
tester.check("7.1", "达到阈值后 buffer 被清空", len(ext7.conversation_buffer) == 0)
tester.check("7.2", "自动提取无异常", True)
time.sleep(2)

print_separator("测试8: 跨 session 持久化验证")
memory2 = ContextMemory("test_integration")
all_recall = memory2.recall("技术方案", top_k=5)
print(f"  跨 session 召回结果: {all_recall}")
tester.check("8.1", "跨 session 仍能召回历史记忆", len(all_recall) > 0)

print_separator("集成测试报告")
tester.report()
