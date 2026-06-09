"""清理 ChromaDB 中无 project_id 的孤儿记录（test_suite collection）。"""
import os
import sys

# 设置测试环境
os.environ.setdefault("MEMOS_HOME", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MEMOS_TEST_COLLECTION", "test_suite")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("SAFETENSORS_FAST_LOAD", "0")

from memos.storage.chroma import ChromaDBPersistentStore

store = ChromaDBPersistentStore(collection_name="test_suite")

# 查询所有记录
all_ids = store.get()["ids"]
print(f"test_suite 总记录数: {len(all_ids)}")

# 查找 project_id 为空或缺失的记录
results = store.get(include=["metadatas", "documents"])
orphan_ids = []
type_counts = {}

for i, (rid, meta) in enumerate(zip(results["ids"], results.get("metadatas", []))):
    pid = (meta or {}).get("project_id", "")
    rtype = (meta or {}).get("type", "unknown")
    if not pid:  # 空字符串或缺失
        orphan_ids.append(rid)
        type_counts[rtype] = type_counts.get(rtype, 0) + 1

print(f"孤儿记录数 (project_id 为空): {len(orphan_ids)}")
print(f"按类型统计: {type_counts}")

if orphan_ids:
    store.delete(ids=orphan_ids)
    print(f"已删除 {len(orphan_ids)} 条孤儿记录")
else:
    print("没有找到孤儿记录")
