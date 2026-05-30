"""模块二冒烟测试 — 不依赖 ChromaDB"""

from memos.engine.extractor import MemoryExtractor

ext = MemoryExtractor()

print("=" * 50)
print("测试1: 提取记忆")
convo = "用户：我们用FastAPI还是Django？\n助手：推荐FastAPI\n用户：那就FastAPI"
result = ext.extract(convo)
print(f"提取结果: {result}")

print("\n" + "=" * 50)
print("测试2: 缓冲区管理")
ext.conversation_buffer.clear()
r1 = ext.append_conversation("user", "我们决定用PostgreSQL数据库")
print(f"追加1: {r1}")
print(f"缓冲区大小: {len(ext.conversation_buffer)}")

r2 = ext.append_conversation("assistant", "好的，PostgreSQL是个好选择")
print(f"追加2: {r2}")
print(f"缓冲区大小: {len(ext.conversation_buffer)}")

print("\n" + "=" * 50)
print("测试3: 强制提炼")
count = ext.force_extract()
print(f"强制提炼返回: {count}")

print("\n" + "=" * 50)
print("测试4: 空缓冲区强制提炼")
count = ext.force_extract()
print(f"空缓冲区提炼: {count}")

print("\n测试完成!")
