// 收件箱页面 JS：加载数据、渲染列表、轮询、操作处理

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

const INBOX_POLL_INTERVAL = 30000; // 30s

function formatTimeAgo(ts) {
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    return Math.floor(diff / 86400) + '天前';
}

// action→按钮映射表
const ACTION_BUTTONS = {
    'review': [{ text: '查看', cls: 'btn-outline-info', action: 'view' }, { text: '忽略', cls: 'btn-outline-secondary', action: 'dismiss' }],
    'renew': [{ text: '续期', cls: 'btn-outline-success', action: 'renew' }, { text: '忽略', cls: 'btn-outline-secondary', action: 'dismiss' }],
    'view': [{ text: '查看', cls: 'btn-outline-info', action: 'view' }, { text: '忽略', cls: 'btn-outline-secondary', action: 'dismiss' }],
    'retry': [{ text: '重试', cls: 'btn-outline-warning', action: 'retry' }, { text: '忽略', cls: 'btn-outline-secondary', action: 'dismiss' }],
};

// 通知类型→图标
const TYPE_ICONS = {
    quality_alert: '⚠️', conflict_detected: '🔄', ttl_warning: '⏰',
    expiry_alert: '📋', extract_complete: '🔔', watchlist_update: '📌',
    dedup_failed: '❌',
};

async function loadInbox() {
    try {
        const res = await fetch('/api/inbox/items');
        const data = await res.json();

        renderSystemNotifications(data.system_notifications || []);
        renderWatchlist(data.watchlist || []);
        renderPendingReview(data.pending_review || []);
        updateBadges(data);
    } catch (e) {
        console.error('收件箱加载失败:', e);
    }
}

function renderSystemNotifications(items) {
    const container = document.getElementById('inbox-system-list');
    container.innerHTML = items.map(n => {
        const icon = TYPE_ICONS[n.type] || '🔔';
        const buttons = (ACTION_BUTTONS[n.metadata?.action] || ACTION_BUTTONS.view)
            .map(b => `<button class="btn btn-sm ${b.cls} py-0" onclick="inboxAction('${b.action}','${n.id}')">${b.text}</button>`)
            .join(' ');
        return `<div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center">
            <div class="d-flex align-items-center gap-2">
                <span>${icon}</span>
                <div>
                    <div class="${n.read ? '' : 'fw-bold'}">${escapeHtml(n.title)}</div>
                    <small class="text-muted">${n._time_ago}</small>
                </div>
            </div>
            <div class="d-flex gap-1">${buttons}</div>
        </div>`;
    }).join('');
    if (!items.length) {
        container.innerHTML = '<div class="text-center text-muted py-4"><i class="bi bi-inbox" style="font-size:2rem;display:block;margin-bottom:.5rem;"></i><p class="small mb-0">暂无系统通知</p></div>';
    }
}

function renderWatchlist(items) {
    const container = document.getElementById('inbox-watchlist-list');
    container.innerHTML = items.map(w => `
        <div class="list-group-item d-flex justify-content-between align-items-center">
            <div class="flex-grow-1 me-3">
                <div class="small">📌 ${escapeHtml(w.text)}</div>
            </div>
            <div class="d-flex gap-1 flex-wrap">
                <button class="btn btn-sm btn-outline-primary py-0" onclick="watchlistToKnowledge('${w.id}')">转为知识</button>
                <button class="btn btn-sm btn-outline-info py-0" onclick="watchlistNote('${w.id}')">备注</button>
                <button class="btn btn-sm btn-outline-secondary py-0" onclick="watchlistIgnore('${w.id}')">忽略</button>
            </div>
        </div>
    `).join('');
    if (!items.length) {
        container.innerHTML = '<div class="text-center text-muted py-4"><i class="bi bi-eye-slash" style="font-size:2rem;display:block;margin-bottom:.5rem;"></i><p class="small mb-0">暂无待关注项</p></div>';
    }
}

function renderPendingReview(items) {
    // 渲染待修正（仅展示 quality_alert + conflict_detected）
    const container = document.getElementById('inbox-pending-list');
    const filtered = items.filter(n => ['quality_alert', 'conflict_detected'].includes(n.type));
    container.innerHTML = filtered.map(n => `
        <div class="list-group-item d-flex justify-content-between align-items-center">
            <div class="d-flex align-items-center gap-2">
                <span>${n.type === 'quality_alert' ? '⚠️' : '🔄'}</span>
                <div>
                    <div class="${n.read ? '' : 'fw-bold'}">${escapeHtml(n.title)}</div>
                    <small class="text-muted">${n._time_ago}</small>
                </div>
            </div>
            <div class="d-flex gap-1">
                <button class="btn btn-sm btn-outline-info py-0" onclick="viewMemory('${n.metadata?.memory_id || ''}')">查看</button>
                <button class="btn btn-sm btn-outline-secondary py-0" onclick="inboxAction('dismiss','${n.id}')">忽略</button>
            </div>
        </div>
    `).join('');
    if (!filtered.length) {
        container.innerHTML = '<div class="text-center text-muted py-3">暂无待修正项</div>';
    }
}

function updateBadges(data) {
    document.getElementById('badge-system').textContent = (data.system_notifications || []).length;
    document.getElementById('badge-watchlist').textContent = (data.watchlist || []).length;
    document.getElementById('badge-pending').textContent = (data.pending_review || []).filter(
        n => ['quality_alert', 'conflict_detected'].includes(n.type)
    ).length;
}

function findNotificationById(id) {
    // 在已渲染的数据中查找通知（由 loadInbox 缓存到全局）
    const data = window.__inboxData || {};
    for (const key of ['system_notifications', 'pending_review']) {
        for (const n of data[key] || []) {
            if (n.id === id) return n;
        }
    }
    return null;
}

async function inboxAction(action, id) {
    if (action === 'dismiss') {
        await fetch(`/api/inbox/dismiss/${id}`, { method: 'POST' });
    } else if (action === 'view') {
        const n = findNotificationById(id);
        if (n?.metadata?.memory_id) {
            viewMemory(n.metadata.memory_id);
            return; // viewMemory 会跳转页面，无需 reload
        }
        console.log('[inbox] view action: id=%s, no memory_id attached', id);
    } else if (action === 'renew') {
        // renew = 重置过期时间，当前行为等同于忽略
        await fetch(`/api/inbox/dismiss/${id}`, { method: 'POST' });
        console.log('[inbox] renew action: id=%s (dismissed)', id);
    } else if (action === 'retry') {
        // retry = 重试，当前行为等同于忽略
        await fetch(`/api/inbox/dismiss/${id}`, { method: 'POST' });
        console.log('[inbox] retry action: id=%s (dismissed)', id);
    }
    loadInbox(); // 重新加载
}

async function inboxDismissAll() {
    await fetch('/api/inbox/dismiss-all', { method: 'POST' });
    loadInbox();
}

// 记忆高亮：尝试跳转 + 3s 超时降级
function viewMemory(memoryId) {
    if (!memoryId) return;
    // 跳转至 Dashboard 记忆管理面板，携带 hash
    window.location.href = `/?tab=knowledge&highlight=${memoryId}`;
}

function watchlistToKnowledge(id) {
    // 弹出提炼对话框（复用已有 _modals.html 的提炼 modal）
    // 设置 memory_id 和 source=watchlist
    const el = document.getElementById('extract-source');
    if (el) el.value = 'watchlist';
    const idEl = document.getElementById('extract-memory-id');
    if (idEl) idEl.value = id;
    const modal = document.getElementById('extractModal');
    if (modal) {
        new bootstrap.Modal(modal).show();
    } else {
        console.warn('extractModal not found in DOM');
    }
}

function watchlistIgnore(id) {
    if (confirm('确定忽略此项？')) {
        fetch(`/api/v2/watchlist/${id}/ignore`, { method: 'POST' })
            .then(() => loadInbox());
    }
}

function watchlistNote(id) {
    const note = prompt('输入备注：');
    if (note) {
        fetch(`/api/v2/watchlist/${id}/note`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ note }),
        }).then(() => loadInbox());
    }
}

// 启动 30s 轮询：先调轻量接口更新 badge，未读数变化时再全量刷新
let _lastUnreadTotal = -1;
async function pollUnreadCount() {
    try {
        const res = await fetch('/api/inbox/unread-count');
        const data = await res.json();
        const total = data.total || 0;
        if (total !== _lastUnreadTotal) {
            _lastUnreadTotal = total;
            loadInbox(); // 未读数变化 → 全量刷新
        }
    } catch (e) {
        console.error('轮询未读数失败:', e);
    }
}
document.addEventListener('DOMContentLoaded', () => {
    loadInbox();
    setInterval(pollUnreadCount, INBOX_POLL_INTERVAL);
});
