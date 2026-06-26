"""F11: 性能基线测试。"""

import json


def test_record_and_read_baseline(tmp_path, monkeypatch):
    from memos.features.performance import record_baseline
    from memos.config.models import get_memos_home

    monkeypatch.setattr("memos.features.performance.get_memos_home", lambda: tmp_path)
    record_baseline({"prompt_hook_p50": 0.05})

    path = tmp_path / "etc" / "performance_baseline.json"
    assert path.exists()
    records = json.loads(path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["prompt_hook_p50"] == 0.05


def test_max_records(tmp_path, monkeypatch):
    from memos.features.performance import record_baseline, _MAX_RECORDS
    monkeypatch.setattr("memos.features.performance.get_memos_home", lambda: tmp_path)

    for i in range(_MAX_RECORDS + 5):
        record_baseline({"index": i})

    path = tmp_path / "etc" / "performance_baseline.json"
    records = json.loads(path.read_text(encoding="utf-8"))
    assert len(records) == _MAX_RECORDS
