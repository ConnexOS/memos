"""F3 系统通知中心 — 单元测试

测试覆盖：
- 通知写入与读取
- 未读计数
- 已读/忽略操作
- 频率限制
- 过期自动清理
- 按类型过滤
- Dashboard API
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from memos.features.notifications import NotificationLogger, get_notification_logger


class TestNotificationLogger:
    """通知日志器核心测试"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "test_notifications.jsonl")
        self.notifier = NotificationLogger(self.log_path)
        # 禁用频率限制以便测试批量写入
        self._rate_patcher = mock.patch("memos.config.config")
        cfg = self._rate_patcher.start()
        cfg.notification.rate_limit_minutes = 0
        cfg.notification.retention_days = 30

    def teardown_method(self):
        self._rate_patcher.stop()
        try:
            os.remove(self.log_path)
        except (FileNotFoundError, OSError):
            pass

    def test_notify_and_list(self):
        """写入通知后可在列表中查到。"""
        nid = self.notifier.notify("extract_complete", "提炼完成", "新增 3 条记忆")
        assert nid is not None
        assert len(nid) == 12

        page, total = self.notifier.list_notifications()
        assert total == 1
        assert page[0]["type"] == "extract_complete"
        assert page[0]["title"] == "提炼完成"
        assert page[0]["read"] is False

    def test_notify_multiple_types(self):
        """不同类型通知独立存储。"""
        self.notifier.notify("extract_complete", "提炼1", "msg1")
        self.notifier.notify("conflict_detected", "冲突1", "msg2")
        self.notifier.notify("expiry_alert", "过期1", "msg3")

        page, total = self.notifier.list_notifications()
        assert total == 3

        # 按类型过滤
        page_e, total_e = self.notifier.list_notifications(type_filter=["extract_complete"])
        assert total_e == 1
        assert page_e[0]["type"] == "extract_complete"

        # 多类型过滤
        page_m, total_m = self.notifier.list_notifications(type_filter=["extract_complete", "conflict_detected"])
        assert total_m == 2

    def test_mark_read(self):
        """标记已读后 unread 计数减少。"""
        self.notifier.notify("extract_complete", "测试", "msg")
        counts = self.notifier.get_unread_counts()
        assert counts["total"] == 1

        page, _ = self.notifier.list_notifications()
        nid = page[0]["id"]
        ok = self.notifier.mark_read(nid)
        assert ok

        counts2 = self.notifier.get_unread_counts()
        assert counts2["total"] == 0

    def test_dismiss(self):
        """忽略后不在列表中显示。"""
        self.notifier.notify("extract_complete", "测试", "msg")
        page, total = self.notifier.list_notifications()
        assert total == 1

        nid = page[0]["id"]
        ok = self.notifier.dismiss(nid)
        assert ok

        page2, total2 = self.notifier.list_notifications()
        assert total2 == 0  # dismissed 通知不出现

    def test_unread_counts_by_type(self):
        """未读计数按类型分组。"""
        self.notifier.notify("extract_complete", "提炼", "msg")
        self.notifier.notify("extract_complete", "提炼2", "msg")
        self.notifier.notify("conflict_detected", "冲突", "msg")

        counts = self.notifier.get_unread_counts()
        assert counts["extract_complete"] == 2
        assert counts["conflict_detected"] == 1
        assert counts.get("expiry_alert", 0) == 0
        assert counts["total"] == 3

    def test_rate_limit(self):
        """同类型通知在 rate_limit_minutes 内不重复。"""
        # 暂时恢复频率限制
        self._rate_patcher.stop()
        cfg_patcher = mock.patch("memos.config.config")
        cfg = cfg_patcher.start()
        cfg.notification.rate_limit_minutes = 60
        cfg.notification.retention_days = 30

        try:
            # 第一条应成功
            nid1 = self.notifier.notify("extract_complete", "第一批", "消息")
            assert nid1 is not None

            # 第二条同类型应立即被频率限制（60分钟窗口）
            nid2 = self.notifier.notify("extract_complete", "第二批", "消息")
            assert nid2 is None  # 被限频

            # 不同类型应通过
            nid3 = self.notifier.notify("conflict_detected", "冲突", "消息")
            assert nid3 is not None
        finally:
            cfg_patcher.stop()
            # 重新禁用频率限制
            self._rate_patcher = mock.patch("memos.config.config")
            cfg2 = self._rate_patcher.start()
            cfg2.notification.rate_limit_minutes = 0
            cfg2.notification.retention_days = 30

    def test_cleanup_expired(self):
        """超过 retention_days 的已读通知自动清理。"""
        # 写入一条"旧"通知（模拟 31 天前）
        old_record = {
            "id": "old_notif_01",
            "timestamp": time.time() - 31 * 86400,
            "type": "extract_complete",
            "title": "旧通知",
            "message": "这条应该被清理",
            "link": "",
            "read": True,
            "dismissed": False,
            "metadata": {},
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_record, ensure_ascii=False) + "\n")

        # 写入一条新通知
        self.notifier.notify("extract_complete", "新通知", "msg")

        # list 时应触发清理
        page, total = self.notifier.list_notifications()
        assert total == 1  # 旧通知被清理
        assert page[0]["id"] != "old_notif_01"

    def test_pagination(self):
        """分页查询正确。"""
        for i in range(25):
            self.notifier.notify("extract_complete", f"通知{i}", f"消息{i}")

        # 第1页
        page1, total = self.notifier.list_notifications(limit=10, offset=0)
        assert total == 25
        assert len(page1) == 10

        # 第3页
        page3, _ = self.notifier.list_notifications(limit=10, offset=20)
        assert len(page3) == 5

    def test_get_recent(self):
        """获取最近 N 条通知。"""
        for i in range(10):
            self.notifier.notify("extract_complete", f"通知{i}", f"消息{i}")

        recent = self.notifier.get_recent(5)
        assert len(recent) == 5
        # 按时间倒序
        ts_list = [r["timestamp"] for r in recent]
        assert ts_list == sorted(ts_list, reverse=True)

    def test_status_filter(self):
        """按状态过滤未读/已读。"""
        self.notifier.notify("extract_complete", "未读", "msg")
        page, _ = self.notifier.list_notifications()
        nid = page[0]["id"]
        self.notifier.mark_read(nid)

        # 未读过滤
        unread_page, unread_total = self.notifier.list_notifications(status="unread")
        assert unread_total == 0

        # 已读过滤
        read_page, read_total = self.notifier.list_notifications(status="read")
        assert read_total == 1

    def test_empty_notifier(self):
        """无通知时返回空。"""
        counts = self.notifier.get_unread_counts()
        assert counts["total"] == 0

        page, total = self.notifier.list_notifications()
        assert total == 0
        assert page == []

        recent = self.notifier.get_recent()
        assert recent == []


class TestDashboardNotificationAPI:
    """Dashboard 通知 API 测试"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from memos.web.app import app

        return TestClient(app)

    def test_unread_count_endpoint(self, client):
        """GET /api/notifications/unread-count 返回正确结构。"""
        resp = client.get("/api/notifications/unread-count")
        assert resp.status_code != 404
        if resp.status_code == 200:
            data = resp.json()
            assert "total" in data
            assert isinstance(data, dict)

    def test_notifications_page_exists(self, client):
        """GET /notifications 页面路由存在。"""
        resp = client.get("/notifications")
        assert resp.status_code != 404

    def test_mark_read_not_found(self, client):
        """标记不存在的通知返回错误。"""
        resp = client.post("/api/notifications/nonexistent123/read")
        assert resp.status_code in (404, 401)  # 401=未认证，404=不存在


class TestNewNotificationTypes:
    """v0.7.2 新通知类型测试（quality_alert / ttl_warning / watchlist_update）"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "test_notifications_v072.jsonl")
        self.notifier = NotificationLogger(self.log_path)
        self._rate_patcher = mock.patch("memos.config.config")
        cfg = self._rate_patcher.start()
        cfg.notification.rate_limit_minutes = 0
        cfg.notification.retention_days = 30

    def teardown_method(self):
        self._rate_patcher.stop()
        try:
            os.remove(self.log_path)
        except (FileNotFoundError, OSError):
            pass

    # --- 存储与读取 ---

    def test_quality_alert_store_and_retrieve(self):
        """quality_alert 类型通知可正常存储和读取。"""
        nid = self.notifier.notify(
            "quality_alert",
            "低质量知识: 测试...",
            "quality_score=0.3，建议审查",
            metadata={"memory_id": "test123", "quality_score": 0.3, "action": "review"},
        )
        assert nid is not None
        page, total = self.notifier.list_notifications(type_filter=["quality_alert"])
        assert total == 1
        assert page[0]["type"] == "quality_alert"
        assert page[0]["metadata"]["quality_score"] == 0.3

    def test_ttl_warning_store_and_retrieve(self):
        """ttl_warning 类型通知可正常存储和读取。"""
        nid = self.notifier.notify(
            "ttl_warning",
            "即将过期: 测试...",
            "距过期还有 3 天",
            metadata={"memory_id": "test456", "expires_at": 9999999999, "action": "renew"},
        )
        assert nid is not None
        page, total = self.notifier.list_notifications(type_filter=["ttl_warning"])
        assert total == 1
        assert page[0]["type"] == "ttl_warning"

    def test_watchlist_update_store_and_retrieve(self):
        """watchlist_update 类型通知可正常存储和读取。"""
        nid = self.notifier.notify(
            "watchlist_update",
            "新增待关注: 测试...",
            "",
            metadata={"watchlist_id": "test789", "action": "view"},
        )
        assert nid is not None
        page, total = self.notifier.list_notifications(type_filter=["watchlist_update"])
        assert total == 1
        assert page[0]["type"] == "watchlist_update"

    # --- 动态未读计数 ---

    def test_dynamic_unread_counts_with_new_types(self):
        """动态未读计数自动聚合新类型。"""
        self.notifier.notify("quality_alert", "质量1", "msg")
        self.notifier.notify("ttl_warning", "过期预警1", "msg")
        self.notifier.notify("watchlist_update", "待关注1", "msg")
        self.notifier.notify("extract_complete", "常规1", "msg")

        counts = self.notifier.get_unread_counts()
        assert counts["quality_alert"] == 1
        assert counts["ttl_warning"] == 1
        assert counts["watchlist_update"] == 1
        assert counts["extract_complete"] == 1
        assert counts["total"] == 4

    def test_dynamic_unread_counts_empty(self):
        """空通知文件返回仅包含 total=0。"""
        counts = self.notifier.get_unread_counts()
        assert counts == {"total": 0}

    # --- 频率限制（验证新类型也受限频保护） ---

    def test_rate_limit_new_types(self):
        """新类型通知同样受频率限制保护。"""
        self._rate_patcher.stop()
        cfg_patcher = mock.patch("memos.config.config")
        cfg = cfg_patcher.start()
        cfg.notification.rate_limit_minutes = 60
        cfg.notification.retention_days = 30

        try:
            nid1 = self.notifier.notify("quality_alert", "低质量1", "msg")
            assert nid1 is not None
            nid2 = self.notifier.notify("quality_alert", "低质量2", "msg")
            assert nid2 is None  # 被限频
            nid3 = self.notifier.notify("watchlist_update", "待关注1", "msg")
            assert nid3 is not None  # 不同类型不受影响
        finally:
            cfg_patcher.stop()
            self._rate_patcher = mock.patch("memos.config.config")
            cfg2 = self._rate_patcher.start()
            cfg2.notification.rate_limit_minutes = 0
            cfg2.notification.retention_days = 30
