"""检查实际 task 文档结构。"""
import json
from fastapi.testclient import TestClient
from memos.web.app import app

client = TestClient(app)
r = client.get("/api/v2/tasks")
data = r.json()

for t in data.get("tasks", []):
    doc = t.get("document", "")
    meta = t.get("metadata", {})
    print("id:", t.get("id", "")[:16])
    print("doc:", doc[:400])
    print("meta:", json.dumps(meta, ensure_ascii=False)[:300])
    # Try to parse as JSON
    try:
        obj = json.loads(doc) if isinstance(doc, str) else doc
        print("parsed goal:", obj.get("goal", "N/A"))
        print("parsed done:", obj.get("done", []))
        print("parsed todo:", obj.get("todo", []))
        print("parsed blocked:", obj.get("blocked", []))
    except Exception as e:
        print("parse error:", e)
    print("---")
