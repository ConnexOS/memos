// ====== 主动建议面板 (v0.4.4) ======
// 依赖 dashboard.js (api, escapeHtml, toast, timeAgo, state)

(function() {
'use strict';

let pollingTimer = null;
let shownSet = new Set();
let currentCount = 0;
let currentFilter = 'pending';

// --- 初始化 ---
function init() {
    fetchNoSuggestionsStatus();
    updateBadge();
    const triggerEl = document.getElementById('suggestions-tab');
    if (triggerEl) {
        triggerEl.addEventListener('shown.bs.tab', startPolling);
        triggerEl.addEventListener('hide.bs.tab', stopPolling);
    }
    document.getElementById('sug-pause-toggle')?.addEventListener('click', togglePause);
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) { stopPolling(); } else { maybeStartPolling(); }
    });
    // 统计卡片点击筛选
    document.querySelectorAll('.sug-stat-card').forEach(function(card) {
        card.addEventListener('click', function() { switchFilter(this.getAttribute('data-filter')); });
    });
    document.getElementById('sug-clear-history-btn')?.addEventListener('click', handleClearHistory);
    // 加载各视图计数 + 默认 pending
    loadStatCounts();
    switchFilter('pending');
}

// --- 筛选切换 ---
function switchFilter(filter) {
    currentFilter = filter;
    shownSet = new Set();

    // 更新统计卡片高亮
    document.querySelectorAll('.sug-stat-card').forEach(function(card) {
        card.classList.toggle('active', card.getAttribute('data-filter') === filter);
    });

    // 清空历史按钮仅在历史视图下显示
    var clearBtn = document.getElementById('sug-clear-history-btn');
    if (clearBtn) clearBtn.classList.toggle('d-none', filter !== 'history');

    // stop polling while loading
    stopPolling();

    var container = document.getElementById('sug-list');
    var empty = document.getElementById('sug-empty');
    if (!container) return;
    container.innerHTML = '<div class="text-center text-secondary small py-5"><div class="spinner-border spinner-border-sm me-1"></div>加载中...</div>';

    if (filter === 'manual') {
        loadManualList();
    } else if (filter === 'injection') {
        loadInjectionList();
    } else {
        loadSuggestions(filter);
    }
}

async function loadSuggestions(filter) {
    var statusMap = { pending: 'pending', processed: 'reacted', history: 'dismissed' };
    var statusParam = statusMap[filter] || 'pending';
    // FIX: 历史建议需要包含 manual_trigger（从"我的设定"删除的建议）
    var url = '/api/suggestions?status=' + statusParam + '&limit=100';
    if (filter === 'history') {
        url += '&suggestion_types=active_push,system_alert,manual_trigger';
    }
    try {
        var data = await apiClient.request(url, { method: 'GET' });
        renderSuggestions(data.items || []);
        if (filter === 'pending') {
            fetchCount();
            fetchStats();
        } else {
            fetchStats();
        }
    } catch (e) {
        // 轮询失败时保留已有内容，仅在无任何卡片时显示空状态（避免角标残留但列表变空）
        var sugContainer = document.getElementById('sug-list');
        if (!sugContainer || sugContainer.querySelectorAll('.sug-card').length === 0) {
            showEmpty(filter);
        }
        // 失败时也刷新角标，避免数值残留
        if (filter === 'pending') {
            fetchCount();
        }
    }
}

async function loadManualList() {
    try {
        var data = await apiClient.request('/api/manual-suggestions', { method: 'GET' });
        renderManualList(data.items || []);
    } catch (e) {
        var container = document.getElementById('sug-list');
        if (container) container.innerHTML = '<div class="text-center text-danger small py-3">加载失败</div>';
    }
}

function showEmpty(filter) {
    var container = document.getElementById('sug-list');
    var empty = document.getElementById('sug-empty');
    if (!container) return;
    var labels = { pending: '暂无待处理的建议', processed: '暂无可用的已处理建议', history: '暂无历史建议记录', injection: '暂无注入监控数据' };
    container.innerHTML = '<div class="text-center text-secondary small py-5" id="sug-empty">' +
        '<i class="bi bi-inbox" style="font-size:2rem;display:block;margin-bottom:.5rem;"></i>' +
        '<div class="mt-2">' + (labels[filter] || labels.pending) + '</div>' +
        '</div>';
}

// --- 轮询（仅 pending 视图） ---
function startPolling() {
    if (pollingTimer) return;
    if (currentFilter === 'pending') {
        loadSuggestions('pending');
    }
    pollingTimer = setInterval(function() {
        if (!document.hidden && currentFilter === 'pending') {
            loadSuggestions('pending');
        }
    }, 10000);
}

function stopPolling() {
    if (pollingTimer) {
        clearInterval(pollingTimer);
        pollingTimer = null;
    }
}

function maybeStartPolling() {
    var tab = document.getElementById('suggestions-tab');
    if (tab && tab.classList.contains('active')) {
        startPolling();
    }
}

async function fetchCount() {
    try {
        var data = await apiClient.request('/api/suggestions/count', { method: 'GET' });
        currentCount = data.count || 0;
        updateBadge();
    } catch (e) {}
}

async function fetchStats() {
    try {
        var data = await apiClient.request('/api/suggestions/stats?days=7', { method: 'GET' });
        renderStats(data);
    } catch (e) {}
}

async function loadStatCounts() {
    // 同时获取五个视图的计数
    try {
        var pendingData = await apiClient.request('/api/suggestions?status=pending&limit=1', { method: 'GET' });
        document.getElementById('sug-stat-pending').textContent = pendingData.total || 0;
    } catch(e) {}
    try {
        var processedData = await apiClient.request('/api/suggestions?status=reacted&limit=1', { method: 'GET' });
        document.getElementById('sug-stat-processed').textContent = processedData.total || 0;
    } catch(e) {}
    try {
        var manualData = await apiClient.request('/api/manual-suggestions', { method: 'GET' });
        document.getElementById('sug-stat-manual').textContent = manualData.total || 0;
    } catch(e) {}
    try {
        var historyData = await apiClient.request('/api/suggestions?status=dismissed&limit=1&suggestion_types=active_push,system_alert,manual_trigger', { method: 'GET' });
        document.getElementById('sug-stat-history').textContent = historyData.total || 0;
    } catch(e) {}
    loadInjectionStatCount();
}

async function fetchNoSuggestionsStatus() {
    try {
        var data = await apiClient.request('/api/suggestions/no-suggestions-status', { method: 'GET' });
        var toggle = document.getElementById('sug-pause-toggle');
        if (toggle) toggle.checked = data.enabled === true;
    } catch (e) {}
}

// --- 渲染 ---
function renderSuggestions(items) {
    const container = document.getElementById('sug-list');
    const empty = document.getElementById('sug-empty');
    if (!container) return;

    // 去重：新建议才追加
    const newItems = items.filter(function(item) { return !shownSet.has(item.id); });
    if (newItems.length === 0 && container.querySelectorAll('.sug-card.pending').length === 0 && items.length === 0) {
        if (empty) {
            empty.style.display = '';
        } else {
            // #sug-empty 被 switchFilter 的 innerHTML 覆盖销毁了，重新创建空状态
            showEmpty(currentFilter);
        }
        return;
    }
    if (empty) empty.style.display = 'none';

    newItems.forEach(function(item) {
        shownSet.add(item.id);
        const card = createCard(item);
        card.classList.add('sug-slide-in');
        container.insertBefore(card, container.firstChild);
        // 动画结束后移除 class
        setTimeout(function() { card.classList.remove('sug-slide-in'); }, 500);
    });

    // 清除 switchFilter 留下的加载中提示
    const loadingHint = container.querySelector(':scope > .text-center.py-5');
    if (loadingHint) loadingHint.remove();

    // 更新已有卡片状态（如果后端状态变了）
    items.forEach(function(item) {
        const existing = container.querySelector('[data-sug-id="' + item.id + '"]');
        if (existing && item.status !== 'pending') {
            existing.classList.remove('pending');
            existing.classList.add(item.status === 'dismissed' ? 'dismissed' : 'feedback');
        }
    });
}

function createCard(item) {
    const status = item.status || 'pending';
    const statusClass = status === 'reacted' ? 'feedback' : status;
    const card = document.createElement('div');
    card.className = 'border rounded p-2 mb-1 sug-card ' + statusClass;
    card.setAttribute('data-sug-id', item.id);

    const sugType = item.suggestion_type || 'active_push';
    const sim = (item.similarity * 100).toFixed(0);
    // FIX: "N天前" → "YYYY/M/D hh:mm:ss" 完整时间
    const ts = item.timestamp ? formatTimestamp(item.timestamp) : '';
    const content = escapeHtml((item.content || '').substring(0, 300));

    // 类型标签
    let typeBadge = '';
    let subTagsHtml = '';
    if (sugType === 'active_push') {
        typeBadge = '<span class="badge bg-primary bg-opacity-25 text-primary">知识匹配</span>';
        if (item.source_type) {
            // FIX: source_type 英文 → TYPE_LABELS 中文
            var cnLabel = (typeof TYPE_LABELS !== 'undefined' && TYPE_LABELS[item.source_type]) || escapeHtml(item.source_type);
            subTagsHtml += '<span class="badge bg-info bg-opacity-10 text-info">' + cnLabel + '</span>';
        }
        if (item.query) {
            subTagsHtml += '<span class="small text-secondary">触发: ' + escapeHtml(item.query) + '</span>';
        }
    } else if (sugType === 'system_alert') {
        typeBadge = '<span class="badge bg-warning bg-opacity-25 text-warning">系统提醒</span>';
        if (item.event_type) {
            subTagsHtml += '<span class="badge bg-warning bg-opacity-10 text-warning">' + escapeHtml(item.event_type) + '</span>';
        }
    } else if (sugType === 'manual_trigger') {
        typeBadge = '<span class="badge bg-success bg-opacity-25 text-success">人工建议</span>';
        if (item.trigger_keywords) {
            var keywords = item.trigger_keywords;
            if (typeof keywords === 'string') { try { keywords = JSON.parse(keywords); } catch(e) {} }
            if (Array.isArray(keywords)) {
                keywords.forEach(function(kw) {
                    subTagsHtml += '<span class="badge bg-success bg-opacity-10 text-success me-1">' + escapeHtml(kw) + '</span>';
                });
            }
        }
        if (item.hit_count) {
            subTagsHtml += '<span class="small text-secondary">命中 ' + item.hit_count + ' 次</span>';
        }
    }

    // 根据状态渲染右侧操作区
    var actionsHtml = '';
    if (status === 'pending') {
        actionsHtml =
            (sugType !== 'system_alert' ? '<button class="btn btn-sm btn-outline-success py-0 px-1 sug-feedback-btn" data-feedback="useful" title="有用"><i class="bi bi-hand-thumbs-up"></i></button>' : '') +
            (sugType !== 'system_alert' ? '<button class="btn btn-sm btn-outline-danger py-0 px-1 sug-feedback-btn" data-feedback="not_useful" title="无用"><i class="bi bi-hand-thumbs-down"></i></button>' : '') +
            '<button class="btn btn-sm btn-outline-secondary py-0 px-1 sug-dismiss-btn" title="关闭"><i class="bi bi-x"></i></button>';
    } else if (status === 'reacted') {
        var fbIcon = item.feedback === 'useful' ? 'bi-hand-thumbs-up-fill text-success' : 'bi-hand-thumbs-down-fill text-danger';
        var fbLabel = item.feedback === 'useful' ? '有用' : '无用';
        actionsHtml = '<span class="badge bg-secondary bg-opacity-10 text-secondary d-flex align-items-center gap-1 py-1 px-2"><i class="bi ' + fbIcon + '"></i>' + fbLabel + '</span>' +
            '<button class="btn btn-sm btn-outline-secondary py-0 px-1 sug-dismiss-btn" title="关闭"><i class="bi bi-x"></i></button>';
    } else if (status === 'dismissed') {
        actionsHtml = '<span class="badge bg-secondary bg-opacity-10 text-secondary py-1 px-2">已关闭</span>' +
            '<button class="btn btn-sm btn-outline-primary py-0 px-1 sug-restore-btn" title="恢复"><i class="bi bi-arrow-counterclockwise"></i></button>' +
            '<button class="btn btn-sm btn-outline-danger py-0 px-1 sug-hard-delete-btn" title="删除"><i class="bi bi-trash"></i></button>';
    }

    // v0.4.6: reuse_count 显示
    var reuseHtml = '';
    if (item.reuse_count > 0) {
        reuseHtml = '<span class="small text-secondary"><i class="bi bi-arrow-repeat me-1"></i>已被提升 ' + item.reuse_count + ' 次</span>';
    }

    card.innerHTML =
        '<div class="d-flex justify-content-between align-items-start mb-1">' +
            '<div class="d-flex gap-2 align-items-center flex-wrap">' +
                typeBadge +
                '<span class="small text-secondary">' + ts + '</span>' +
                (sugType === 'active_push' ? '<span class="small text-secondary">相似度 ' + sim + '%</span>' : '') +
            '</div>' +
            '<div class="d-flex gap-1">' +
                actionsHtml +
            '</div>' +
        '</div>' +
        '<div class="small mb-1">' + content + '</div>' +
        '<div class="d-flex gap-2 small text-secondary flex-wrap">' + subTagsHtml + reuseHtml + '</div>';

    // 绑定操作按钮
    if (status === 'pending') {
        card.querySelectorAll('.sug-feedback-btn').forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                const feedback = btn.getAttribute('data-feedback');
                handleFeedback(item.id, feedback, card);
            });
        });
        card.querySelector('.sug-dismiss-btn')?.addEventListener('click', function(e) {
            e.stopPropagation();
            handleDismiss(item.id, card);
        });
    } else if (status === 'reacted') {
        card.querySelector('.sug-dismiss-btn')?.addEventListener('click', function(e) {
            e.stopPropagation();
            handleDismiss(item.id, card);
        });
    } else if (status === 'dismissed') {
        card.querySelector('.sug-restore-btn')?.addEventListener('click', function(e) {
            e.stopPropagation();
            handleRestore(item.id, card);
        });
        card.querySelector('.sug-hard-delete-btn')?.addEventListener('click', function(e) {
            e.stopPropagation();
            handleHardDelete(item.id, card);
        });
    }

    return card;
}

// --- 操作 ---
async function handleFeedback(id, feedback, card) {
    try {
        await apiClient.request('/api/suggestions/' + id + '/feedback', {
            method: 'POST',
            body: JSON.stringify({ feedback: feedback }),
        });
        card.classList.remove('pending');
        card.classList.add('feedback');
        card.style.opacity = '0.5';
        setTimeout(function() {
            card.style.transition = 'all 0.3s ease';
            card.style.transform = 'translateX(100%)';
            card.style.opacity = '0';
            setTimeout(function() { card.remove(); fetchCount(); fetchStats(); loadStatCounts(); }, 300);
        }, 500);
        toast('已提交反馈', 'success');
    } catch (e) {
        toast('反馈失败: ' + e.message, 'danger');
    }
}

async function handleDismiss(id, card) {
    try {
        await apiClient.request('/api/suggestions/' + id + '/dismiss', { method: 'POST' });
        card.style.transition = 'all 0.3s ease';
        card.style.transform = 'translateX(100%)';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); fetchCount(); fetchStats(); loadStatCounts(); }, 300);
    } catch (e) {
        toast('关闭失败: ' + e.message, 'danger');
    }
}

async function handleDismissAll() {
    if (!confirm('确定将所有待处理建议标记为已读？')) return;
    try {
        await apiClient.request('/api/suggestions/dismiss-all', { method: 'POST' });
        document.querySelectorAll('.sug-card.pending').forEach(function(card) {
            card.style.transition = 'all 0.3s ease';
            card.style.transform = 'translateX(100%)';
            card.style.opacity = '0';
        });
        setTimeout(function() {
            document.querySelectorAll('.sug-card').forEach(function(c) { c.remove(); });
            fetchCount();
            fetchStats();
            loadStatCounts();
            document.getElementById('sug-empty').style.display = '';
        }, 300);
        toast('已全部标记为已读', 'success');
    } catch (e) {
        toast('操作失败: ' + e.message, 'danger');
    }
}

async function handleClearHistory() {
    if (!confirm('确定永久删除所有历史建议？删除后不可恢复。')) return;
    try {
        var resp = await apiClient.request('/api/suggestions/history', { method: 'DELETE' });
        var count = resp.deleted || 0;
        document.querySelectorAll('.sug-card.dismissed, .sug-card.history').forEach(function(card) {
            card.style.transition = 'all 0.3s ease';
            card.style.opacity = '0';
        });
        setTimeout(function() {
            document.getElementById('sug-list').querySelectorAll('.sug-card').forEach(function(c) { c.remove(); });
            fetchStats();
            loadStatCounts();
            showEmpty('history');
        }, 300);
        toast('已清空 ' + count + ' 条历史建议', 'success');
    } catch (e) {
        toast('操作失败: ' + e.message, 'danger');
    }
}

async function handleRestore(id, card) {
    try {
        var resp = await apiClient.request('/api/suggestions/' + id + '/restore', { method: 'POST' });
        card.style.transition = 'all 0.3s ease';
        card.style.transform = 'translateX(100%)';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); fetchCount(); fetchStats(); loadStatCounts(); }, 300);
        // FIX: 根据类型显示不同的恢复提示
        if (resp.restore_type === 'manual') {
            toast('已恢复到人工建议', 'success');
        } else {
            toast('已恢复到待处理', 'success');
        }
    } catch (e) {
        toast('恢复失败: ' + (e.message || e), 'danger');
    }
}

async function handleHardDelete(id, card) {
    if (!confirm('确定要永久删除此建议记录？')) return;
    try {
        await apiClient.request('/api/suggestions/' + id, { method: 'DELETE' });
        card.style.transition = 'all 0.3s ease';
        card.style.transform = 'translateX(100%)';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); fetchCount(); fetchStats(); loadStatCounts(); }, 300);
        toast('已永久删除', 'success');
    } catch (e) {
        toast('删除失败: ' + (e.message || e), 'danger');
    }
}

async function togglePause() {
    const toggle = document.getElementById('sug-pause-toggle');
    const wasEnabled = toggle.checked;
    try {
        const data = await apiClient.request('/api/suggestions/toggle-pause', { method: 'POST' });
        toggle.checked = data.enabled === true;
        toast(data.enabled ? '已暂停推送' : '已恢复推送', data.enabled ? 'warning' : 'success');
    } catch (e) {
        // API 调用失败时恢复开关状态
        toggle.checked = wasEnabled;
        toast('操作失败: ' + e.message, 'danger');
    }
}

// --- 渲染手工建议列表（我的设定视图） ---
function formatTimestamp(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    var Y = d.getFullYear();
    var M = ('0' + (d.getMonth() + 1)).slice(-2);
    var D = ('0' + d.getDate()).slice(-2);
    var h = ('0' + d.getHours()).slice(-2);
    var m = ('0' + d.getMinutes()).slice(-2);
    var s = ('0' + d.getSeconds()).slice(-2);
    return Y + '/' + M + '/' + D + ' ' + h + ':' + m + ':' + s;
}

function renderManualList(items) {
    var container = document.getElementById('sug-list');
    if (!container) return;
    if (items.length === 0) {
        container.innerHTML = '<div class="text-center text-secondary small py-5">' +
            '<i class="bi bi-inbox" style="font-size:2rem;display:block;margin-bottom:.5rem;"></i>' +
            '<div class="mt-2">暂无手工建议</div>' +
            '<div class="small text-secondary opacity-75 mt-1">点击 + 按钮创建第一条手工建议</div>' +
            '</div>';
        return;
    }
    container.innerHTML = items.map(function(item) {
        var isDisabled = item.disabled === true;
        var cardOpacity = isDisabled ? 'opacity-50' : '';
        var keywords = item.trigger_keywords || [];
        if (typeof keywords === 'string') { try { keywords = JSON.parse(keywords); } catch(e) { keywords = [keywords]; } }
        var kwTags = Array.isArray(keywords) ? keywords.map(function(k) {
            return '<span class="badge bg-success bg-opacity-10 text-success me-1">' + escapeHtml(k) + '</span>';
        }).join('') : '';
        var modeBadge = item.trigger_mode === 'always'
            ? '<span class="badge bg-secondary bg-opacity-25 text-secondary">始终</span>'
            : '<span class="badge bg-info bg-opacity-25 text-info">关键词</span>';
        // 有效期状态
        var expiryBadge = '';
        if (item.validity_minutes > 0 && item.expires_at > 0) {
            if (Date.now() / 1000 > item.expires_at) {
                expiryBadge = '<span class="badge bg-danger bg-opacity-25 text-danger">已过期</span>';
            }
        }
        var ts = item.timestamp ? formatTimestamp(item.timestamp) : '';
        var safeContent = escapeHtml(item.content || '').substring(0, 100);
        var safeId = item.id;
        var toggleState = isDisabled ? '临时失效' : '启用';
        var toggleColor = isDisabled ? 'text-danger' : 'text-success';
        var toggleChecked = isDisabled ? '' : 'checked';
        return '<div class="border rounded p-2 mb-1 ' + cardOpacity + '">' +
            '<div class="d-flex justify-content-between align-items-start mb-1">' +
                '<div class="d-flex gap-2 align-items-center flex-wrap">' +
                    '<span class="small fw-semibold">' + safeContent + '</span>' +
                '</div>' +
                '<div class="d-flex gap-1 align-items-center">' +
                    '<div class="form-check form-switch d-inline-block mb-0 me-1">' +
                        '<input class="form-check-input" type="checkbox" role="switch" ' + toggleChecked +
                        ' onchange="window.toggleManualSuggestion(\'' + safeId + '\', this)">' +
                    '</div>' +
                    '<span class="small fw-bold ' + toggleColor + ' me-1" style="min-width:3.5em">' + toggleState + '</span>' +
                    '<button class="btn btn-sm btn-outline-secondary py-0 px-1" onclick="window.editManualSuggestion(\'' + safeId + '\')" title="编辑"><i class="bi bi-pencil"></i></button>' +
                    '<button class="btn btn-sm btn-outline-secondary py-0 px-1" onclick="window.deleteManualSuggestion && deleteManualSuggestion(\'' + safeId + '\')" title="关闭"><i class="bi bi-x"></i></button>' +
                '</div>' +
            '</div>' +
            '<div class="d-flex gap-2 align-items-center flex-wrap small">' +
                modeBadge +
                expiryBadge +
                kwTags +
                '<span class="text-secondary">命中 ' + (item.hit_count || 0) + ' 次</span>' +
                '<span class="text-secondary">冷却期 ' + (item.cooldown_minutes || 0) + 'min</span>' +
                '<span class="text-secondary">有效期 ' + (item.validity_minutes || 0) + 'min</span>' +
                '<span class="text-secondary">' + ts + '</span>' +
            '</div>' +
        '</div>';
    }).join('');
}

window.toggleManualSuggestion = async function(id, checkbox) {
    try {
        var result = await apiClient.request('/api/manual-suggestions/' + id + '/toggle-disable', { method: 'PUT' });
        // 刷新列表保持状态一致
        var data = await apiClient.request('/api/manual-suggestions', { method: 'GET' });
        renderManualList(data.items || []);
    } catch (e) {
        toast('切换失败: ' + (e.message || e), 'danger');
        checkbox.checked = !checkbox.checked; // 回滚
    }
};

// --- 标题角标 + 全部已读按钮 + Tab 角标 ---
function updateBadge() {
    const title = document.title;
    if (currentCount > 0) {
        if (!title.startsWith('[' + currentCount + ']')) {
            document.title = '[' + currentCount + '] ' + title.replace(/^\[\d+\]\s*/, '');
        }
    } else {
        document.title = title.replace(/^\[\d+\]\s*/, '');
    }
    // 根据待处理数量控制「全部已读」按钮可见性（CRIT-002）
    const dismissAllBtn = document.getElementById('sug-dismiss-all-btn');
    if (dismissAllBtn) {
        dismissAllBtn.classList.toggle('d-none', currentCount === 0);
    }
}

// --- 统计 ---
function renderStats(data) {
    document.getElementById('stat-sug-total').textContent = data.total || 0;
    document.getElementById('stat-sug-useful').textContent = data.useful || 0;
    document.getElementById('stat-sug-not-useful').textContent = data.not_useful || 0;
    document.getElementById('stat-sug-dismissed').textContent = data.dismissed || 0;
    const rate = data.useful_rate !== null && data.useful_rate !== undefined ? (data.useful_rate * 100).toFixed(0) + '%' : '-';
    document.getElementById('stat-sug-rate').textContent = rate;
}

// --- 注入监控 (S5) ---
function initInjectionMonitor() {
    // 统计卡片点击由 switchFilter 统一处理 (data-filter="injection")
}

let _injectionData = null;

async function loadInjectionList() {
    var container = document.getElementById('sug-list');
    if (!container) return;
    container.innerHTML = '<div class="text-center text-secondary small py-5"><div class="spinner-border spinner-border-sm me-1"></div>加载注入监控...</div>';

    try {
        var data = await apiClient.request('/api/suggestions/injection-stats?window_hours=24', { method: 'GET' });
        _injectionData = data;
        renderInjectionMonitor(container, data);
    } catch (e) {
        container.innerHTML = '<div class="text-center text-danger small py-3">注入监控数据加载失败</div>';
    }
}

async function loadInjectionStatCount() {
    try {
        var data = await apiClient.request('/api/suggestions/injection-stats?window_hours=24', { method: 'GET' });
        var count = (data.recent_injections || []).length;
        var el = document.getElementById('sug-stat-injection');
        if (el) el.textContent = count;
    } catch (e) {}
}

function renderInjectionMonitor(container, data) {
    var pipelines = data.pipelines || {};

    // —— 行1: 最近会话注入记录（原始非去重，时间倒序） ——
    var sessionItems = data.session_injections || [];
    var sessionHtml = '<div class="injection-section mb-2" id="im-session-section">' +
        '<div class="small fw-semibold mb-1 d-flex justify-content-between align-items-center">' +
            '<span>最近会话注入 <span class="text-secondary fw-normal">(' + sessionItems.length + ' 条)</span></span>' +
            '<button class="btn btn-sm btn-outline-secondary py-0" onclick="loadInjectionList()" title="刷新"><i class="bi bi-arrow-clockwise"></i></button>' +
        '</div>';
    if (sessionItems.length === 0) {
        sessionHtml += '<div class="text-secondary small py-1">暂无记录</div>';
    } else {
        sessionItems.forEach(function(item) {
            var typeLabel = item.suggestion_type === 'active_push' ? '知识匹配' :
                            (item.suggestion_type === 'manual_trigger' ? '人工触发' : '系统告警');
            var badgeColor = item.suggestion_type === 'active_push' ? 'primary' :
                             (item.suggestion_type === 'manual_trigger' ? 'success' : 'warning');
            var ts = item.timestamp ? formatTimestamp(item.timestamp) : '';
            var sim = item.similarity ? ' | ' + (item.similarity * 100).toFixed(0) + '%' : '';
            sessionHtml += '<div class="border-bottom py-1 small">' +
                '<div class="d-flex gap-1 align-items-center flex-wrap">' +
                    '<span class="badge bg-' + badgeColor + ' bg-opacity-25 text-' + badgeColor + '">' + typeLabel + '</span>' +
                    '<span class="text-secondary">' + ts + '</span>' +
                    '<span class="text-secondary">' + sim + '</span>' +
                '</div>' +
                '<div class="mt-1">' + escapeHtml((item.content || '').substring(0, 500)) + '</div>' +
            '</div>';
        });
    }
    sessionHtml += '</div>';

    // —— 行2: 近期注入记录列表（去重合并） ——
    var injections = data.recent_injections || [];

    var listHtml = '<div class="injection-section mb-2" id="im-list-section">' +
        '<div class="small fw-semibold mb-1 d-flex justify-content-between align-items-center">' +
            '<span>近期注入记录 <span class="text-secondary fw-normal">(最近24小时 ' + injections.length + ' 条去重)</span></span>' +
            '<button class="btn btn-sm btn-outline-secondary py-0" onclick="loadInjectionList()" title="刷新"><i class="bi bi-arrow-clockwise"></i></button>' +
        '</div>';

    if (injections.length === 0) {
        listHtml += '<div class="text-center text-secondary small py-2">暂无注入记录</div>';
    } else {
        listHtml += '<div class="mb-1 d-flex gap-1 flex-wrap" id="im-filter-group">' +
            '<button class="btn btn-sm btn-outline-secondary py-0 im-filter-btn active" data-im-filter="all">全部</button>' +
            '<button class="btn btn-sm btn-outline-primary py-0 im-filter-btn" data-im-filter="active_push">知识匹配</button>' +
            '<button class="btn btn-sm btn-outline-success py-0 im-filter-btn" data-im-filter="manual_trigger">人工触发</button>' +
        '</div>';

        listHtml += '<div id="im-injection-list">';
        injections.forEach(function(item) {
            listHtml += _buildInjectionItem(item);
        });
        listHtml += '</div>';
    }
    listHtml += '</div>';

    // —— 行3: 注入排行（按注入次数降序，取 Top-5） ——
    var topInjected = injections.slice().sort(function(a, b) {
        return (b.inject_count || 0) - (a.inject_count || 0);
    }).slice(0, 5);
    var rankHtml = '<div class="injection-section" id="im-rank-section">' +
        '<div class="small fw-semibold mb-1 d-flex justify-content-between align-items-center">' +
            '<span>注入排行 <span class="text-secondary fw-normal">(最近24小时注入次数最多的建议)</span></span>' +
            '<button class="btn btn-sm btn-outline-secondary py-0" onclick="loadInjectionList()" title="刷新"><i class="bi bi-arrow-clockwise"></i></button>' +
        '</div>';
    if (topInjected.length === 0) {
        rankHtml += '<div class="text-secondary small py-1">暂无数据</div>';
    } else {
        topInjected.forEach(function(item, idx) {
            rankHtml += '<div class="d-flex align-items-start border-bottom py-1">' +
                '<span class="me-2 text-secondary fw-bold" style="min-width:1.2em">' + (idx + 1) + '.</span>' +
                '<div class="flex-grow-1">' +
                    '<div class="small">' + escapeHtml(item.content || '') + '</div>' +
                    '<div class="small text-secondary">注入 ' + (item.inject_count || 0) + ' 次</div>' +
                '</div>' +
            '</div>';
        });
    }
    rankHtml += '</div>';

    // 合并渲染（最近会话 → 近期去重 → 注入排行）
    container.innerHTML = sessionHtml + listHtml + rankHtml;

    // 绑定 Tab 筛选按钮事件
    var filterGroup = document.getElementById('im-filter-group');
    if (filterGroup) {
        filterGroup.querySelectorAll('.im-filter-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                // 更新按钮活跃状态
                filterGroup.querySelectorAll('.im-filter-btn').forEach(function(b) { b.classList.remove('active'); });
                this.classList.add('active');
                var filter = this.getAttribute('data-im-filter');
                // 筛选记录
                document.querySelectorAll('.im-injection-item').forEach(function(el) {
                    if (filter === 'all' || el.getAttribute('data-im-type') === filter) {
                        el.style.display = '';
                    } else {
                        el.style.display = 'none';
                    }
                });
            });
        });
    }
}
function _buildInjectionItem(item) {
    var typeBadge = '';
    var badgeColor = item.suggestion_type === 'active_push' ? 'primary' :
                     (item.suggestion_type === 'manual_trigger' ? 'success' : 'warning');
    var typeLabel = item.suggestion_type === 'active_push' ? '知识匹配' :
                    (item.suggestion_type === 'manual_trigger' ? '人工触发' : '系统告警');
    typeBadge = '<span class="badge bg-' + badgeColor + ' bg-opacity-25 text-' + badgeColor + '">' + typeLabel + '</span>';

    // 注入次数徽标
    var countBadge = item.inject_count > 1
        ? '<span class="badge bg-secondary bg-opacity-25 text-secondary me-1">×' + item.inject_count + ' 次注入</span>' : '';

    // 反馈
    var fbHtml = '';
    if (item.feedback === 'useful') {
        fbHtml = '<span class="text-success small"><i class="bi bi-hand-thumbs-up-fill"></i> 有用</span>';
    } else if (item.feedback === 'not_useful') {
        fbHtml = '<span class="text-danger small"><i class="bi bi-hand-thumbs-down-fill"></i> 无用</span>';
    }

    // 时间
    var latestTs = item.latest_injected ? formatTimestamp(item.latest_injected) : '';
    var firstTs = item.first_injected ? formatTimestamp(item.first_injected) : '';

    // 来源信息
    var srcInfo = '';
    if (item.source_type) {
        var typeMap = {
            'todo': '待办', 'decision': '决策', 'fact': '事实',
            'preference': '偏好', 'manual_suggestion': '人工建议',
            'bug_fix': 'Bug修复', 'feature_design': '功能设计',
            'code_optimize': '代码优化', 'tech_knowledge': '技术知识',
        };
        var cnLabel = typeMap[item.source_type] || item.source_type;
        srcInfo += '<span class="badge bg-info bg-opacity-10 text-info">' + escapeHtml(cnLabel) + '</span> ';
    }
    if (item.source_date) {
        var sd = item.source_date;
        // Unix 时间戳（数值或纯数字字符串）格式化为完整时间
        if (typeof sd === 'number' || (/^\d+(\.\d+)?$/.test(sd) && parseFloat(sd) > 1e8)) {
            sd = formatTimestamp(parseFloat(sd));
        }
        srcInfo += '<span class="text-secondary">' + escapeHtml(sd) + '</span>';
    }

    // 相似度
    var simHtml = item.best_similarity ? '<span class="small text-secondary">最高 ' + (item.best_similarity * 100).toFixed(0) + '%</span>' : '';

    return '<div class="border rounded p-1 mb-1 im-injection-item" data-im-type="' + item.suggestion_type + '" style="font-size:0.85rem">' +
        '<div class="d-flex justify-content-between align-items-start">' +
            '<div class="d-flex gap-1 align-items-center flex-wrap">' +
                typeBadge + countBadge + simHtml +
            '</div>' +
            '<div class="d-flex gap-1 align-items-center">' +
                fbHtml +
            '</div>' +
        '</div>' +
        '<div class="my-1">' + escapeHtml((item.content || '').substring(0, 500)) + '</div>' +
        '<div class="d-flex gap-2 small text-secondary flex-wrap">' +
            srcInfo +
            (latestTs ? '<span>最近 ' + latestTs + '</span>' : '') +
            (firstTs && item.inject_count > 1 ? '<span>首次 ' + firstTs + '</span>' : '') +
        '</div>' +
    '</div>';
}

// --- 初始化中增加设置、手工建议和注入监控入口 ---
const origInit = init;
init = function() {
    origInit();
    initSettings();
    initManualSuggestion();
    initInjectionMonitor();
    loadInjectionStatCount();
};

// --- 初始化 ---
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// ====== 建议设置面板 (v0.4.4 增强版 Phase 1) ======

let settingsFields = {};
let previewTimer = null;

// --- 设置面板 ---
function initSettings() {
    const settingsBtn = document.getElementById('sug-settings-btn');
    if (!settingsBtn) return;
    settingsBtn.addEventListener('click', openSettingsModal);

    document.getElementById('sug-settings-save-btn')?.addEventListener('click', saveSettings);
    document.getElementById('sug-settings-reset-btn')?.addEventListener('click', resetSettings);

    // Slider 事件绑定（在模态框显示后绑定）
    const modalEl = document.getElementById('sugSettingsModal');
    if (modalEl) {
        modalEl.addEventListener('shown.bs.modal', function() {
            bindSliderEvents();
        });
    }
}

function bindSliderEvents() {
    const thresholdSlider = document.getElementById('sug-setting-threshold');
    const contextSlider = document.getElementById('sug-setting-context-threshold');

    if (thresholdSlider) {
        thresholdSlider.addEventListener('input', function() {
            updateSliderValue(this);
            validateThresholds();
            debouncePreview();
        });
    }
    if (contextSlider) {
        contextSlider.addEventListener('input', function() {
            updateSliderValue(this);
            validateThresholds();
        });
    }
}

function updateSliderValue(slider) {
    const valEl = slider.parentElement.querySelector('.sug-setting-value');
    if (valEl) {
        valEl.textContent = parseFloat(slider.value).toFixed(2);
    }
}

function validateThresholds() {
    const threshold = parseFloat(document.getElementById('sug-setting-threshold').value);
    const context = parseFloat(document.getElementById('sug-setting-context-threshold').value);
    const warning = document.getElementById('sug-setting-threshold-warning');
    if (warning) {
        warning.style.display = threshold < context ? '' : 'none';
    }
}

function validatePending() {
    const maxPerDay = parseInt(document.getElementById('sug-setting-max-per-day').value) || 0;
    const maxPending = parseInt(document.getElementById('sug-setting-max-pending').value) || 0;
    const warning = document.getElementById('sug-setting-pending-warning');
    if (warning) {
        warning.style.display = maxPending < maxPerDay * 2 ? '' : 'none';
    }
}

function debouncePreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(fetchPreview, 300);
}

async function fetchPreview() {
    const threshold = parseFloat(document.getElementById('sug-setting-threshold').value) || 0.6;
    const previewEl = document.getElementById('sug-setting-threshold-preview');
    if (!previewEl) return;
    previewEl.textContent = '计算中...';
    try {
        const data = await apiClient.request('/api/suggestions/preview?threshold=' + threshold, { method: 'GET' });
        previewEl.textContent = '阈值 ' + threshold.toFixed(2) + ' 下，预计 ' + data.above_threshold + ' / ' + data.total_knowledge + ' 条记忆匹配';
    } catch (e) {
        previewEl.textContent = '预览计算失败';
    }
}

async function openSettingsModal() {
    const form = document.getElementById('sug-settings-form');
    const loading = document.getElementById('sug-settings-loading');
    if (form) form.style.display = 'none';
    if (loading) loading.style.display = '';

    const modal = new bootstrap.Modal(document.getElementById('sugSettingsModal'));
    modal.show();

    try {
        const data = await apiClient.request('/api/settings/suggestions', { method: 'GET' });
        settingsFields = data.fields || {};
        populateSettingsForm(settingsFields);
        if (form) form.style.display = '';
        if (loading) loading.style.display = 'none';
    } catch (e) {
        if (loading) loading.textContent = '加载失败: ' + e.message;
        toast('加载设置失败: ' + e.message, 'danger');
    }
}

function populateSettingsForm(fields) {
    // Sliders
    setSliderValue('sug-setting-threshold', fields.active_suggestion_threshold);
    setSliderValue('sug-setting-context-threshold', fields.context_injection_threshold);

    // Number inputs
    setNumberValue('sug-setting-max-per-day', fields.suggestion_max_per_day);
    setNumberValue('sug-setting-max-pending', fields.suggestion_max_pending);
    setNumberValue('sug-setting-display-limit', fields.suggestion_display_limit);
    setNumberValue('sug-setting-manual-daily-limit', fields.suggestion_manual_daily_limit);
    setNumberValue('sug-setting-system-daily-limit', fields.system_suggestion_daily_limit);
    setNumberValue('sug-setting-system-cooldown', fields.system_suggestion_cooldown_hours);

    // Toggle
    const toggle = document.getElementById('sug-setting-system-enabled');
    if (toggle) toggle.checked = fields.system_suggestion_enabled?.value === true;

    // Trigger checkboxes
    const triggers = fields.system_suggestion_triggers?.value || {};
    document.querySelectorAll('.sug-setting-trigger').forEach(function(cb) {
        const key = cb.getAttribute('data-trigger');
        if (key !== null && triggers[key] !== undefined) {
            cb.checked = triggers[key] === true;
        }
    });

    // Bind number input validation
    document.querySelectorAll('.sug-setting-number').forEach(function(input) {
        input.addEventListener('change', function() {
            validatePending();
        });
    });

    // Preview
    bindSliderEvents();
    validateThresholds();
    validatePending();
    fetchPreview();
}

function setSliderValue(id, field) {
    const slider = document.getElementById(id);
    if (!slider || !field) return;
    slider.value = field.value !== undefined ? field.value : field.default;
    updateSliderValue(slider);
}

function setNumberValue(id, field) {
    const input = document.getElementById(id);
    if (!input || !field) return;
    input.value = field.value !== undefined ? field.value : field.default;
}

async function saveSettings() {
    const saveBtn = document.getElementById('sug-settings-save-btn');
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 保存中...';
    }

    // 前端校验
    const threshold = parseFloat(document.getElementById('sug-setting-threshold').value);
    const context = parseFloat(document.getElementById('sug-setting-context-threshold').value);
    if (threshold < context) {
        toast('推送阈值必须 >= 上下文注入阈值', 'danger');
        if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="bi bi-floppy"></i> 保存'; }
        return;
    }

    const maxPerDay = parseInt(document.getElementById('sug-setting-max-per-day').value) || 0;
    const maxPending = parseInt(document.getElementById('sug-setting-max-pending').value) || 0;
    if (maxPending < maxPerDay * 2) {
        toast('最大待处理数必须 >= 每日推送上限 × 2', 'danger');
        if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="bi bi-floppy"></i> 保存'; }
        return;
    }

    const body = {
        active_suggestion_threshold: threshold,
        context_injection_threshold: context,
        suggestion_max_per_day: maxPerDay,
        suggestion_max_pending: maxPending,
        suggestion_display_limit: parseInt(document.getElementById('sug-setting-display-limit').value) || 20,
        suggestion_manual_daily_limit: parseInt(document.getElementById('sug-setting-manual-daily-limit').value) || 5,
        system_suggestion_enabled: document.getElementById('sug-setting-system-enabled').checked,
        system_suggestion_daily_limit: parseInt(document.getElementById('sug-setting-system-daily-limit').value) || 3,
        system_suggestion_cooldown_hours: parseInt(document.getElementById('sug-setting-system-cooldown').value) || 24,
        system_suggestion_triggers: {},
    };

    document.querySelectorAll('.sug-setting-trigger').forEach(function(cb) {
        const key = cb.getAttribute('data-trigger');
        if (key) body.system_suggestion_triggers[key] = cb.checked;
    });

    try {
        const data = await apiClient.request('/api/settings/suggestions', {
            method: 'PUT',
            body: JSON.stringify(body),
        });
        settingsFields = data.fields || {};
        toast('设置已保存', 'success');
    } catch (e) {
        toast('保存失败: ' + (e.message || e), 'danger');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-floppy"></i> 保存';
        }
    }
}

async function resetSettings() {
    if (!confirm('确定恢复建议设置为默认值？')) return;

    const saveBtn = document.getElementById('sug-settings-save-btn');
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 恢复中...';
    }

    try {
        const data = await apiClient.request('/api/settings/suggestions/reset', { method: 'POST' });
        settingsFields = data.fields || {};
        populateSettingsForm(settingsFields);
        toast('已恢复默认设置', 'success');
    } catch (e) {
        toast('恢复失败: ' + e.message, 'danger');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-floppy"></i> 保存';
        }
    }
}

// ====== 手工建议创建 (v0.4.4 增强版 Phase 3) ======

let msKeywords = [];

function initManualSuggestion() {
    // 关键词输入：回车添加
    const keywordInput = document.getElementById('ms-keyword-input');
    if (keywordInput) {
        keywordInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                addMsKeyword(this.value.trim());
                this.value = '';
            }
        });
    }

    document.getElementById('ms-save-btn')?.addEventListener('click', saveManualSuggestion);

    // 触发模式切换 → 条件显隐
    document.getElementById('ms-trigger-mode')?.addEventListener('change', function() {
        toggleMsKeywordsVisibility(this.value);
    });

    // 模态框打开时初始化
    const modalEl = document.getElementById('manualSuggestionModal');
    if (modalEl) {
        modalEl.addEventListener('show.bs.modal', function() {
            const mode = document.getElementById('ms-trigger-mode').value;
            toggleMsKeywordsVisibility(mode);
        });
        modalEl.addEventListener('hidden.bs.modal', resetMsForm);
    }

}

function toggleMsKeywordsVisibility(mode) {
    const section = document.getElementById('ms-keywords-section');
    const hint = document.getElementById('ms-always-hint');
    if (!section || !hint) return;
    if (mode === 'always') {
        section.style.display = 'none';
        hint.style.display = '';
    } else {
        section.style.display = '';
        hint.style.display = 'none';
    }
}

function addMsKeyword(keyword) {
    if (!keyword) return;
    if (keyword.length > 50) {
        document.getElementById('ms-keyword-error').textContent = '关键词不超过 50 字符';
        document.getElementById('ms-keyword-error').style.display = '';
        return;
    }
    if (msKeywords.length >= 10) {
        document.getElementById('ms-keyword-error').textContent = '最多 10 个关键词';
        document.getElementById('ms-keyword-error').style.display = '';
        return;
    }
    if (msKeywords.includes(keyword)) {
        document.getElementById('ms-keyword-error').textContent = '关键词已存在: ' + keyword;
        document.getElementById('ms-keyword-error').style.display = '';
        return;
    }
    document.getElementById('ms-keyword-error').style.display = 'none';
    msKeywords.push(keyword);
    renderMsTags();
}

function removeMsKeyword(keyword) {
    msKeywords = msKeywords.filter(function(k) { return k !== keyword; });
    renderMsTags();
}

function renderMsTags() {
    const container = document.getElementById('ms-keyword-tags');
    if (!container) return;
    if (msKeywords.length === 0) {
        container.innerHTML = '<span class="text-secondary small">暂无关键词</span>';
        return;
    }
    container.innerHTML = msKeywords.map(function(kw) {
        return '<span class="badge bg-info bg-opacity-25 text-info d-inline-flex align-items-center gap-1" data-keyword="' + encodeURIComponent(kw) + '">' +
            escapeHtml(kw) +
            '<i class="bi bi-x" style="cursor:pointer"></i>' +
            '</span>';
    }).join('');
}

// 关键词删除使用事件委托，避免内联 onclick 的 HTML 实体编码问题
document.getElementById('ms-keyword-tags')?.addEventListener('click', function(e) {
    var target = e.target;
    if (target.classList.contains('bi-x')) {
        var span = target.closest('[data-keyword]');
        if (span) {
            var kw = decodeURIComponent(span.getAttribute('data-keyword'));
            removeMsKeyword(kw);
        }
    }
});

function resetMsForm() {
    document.getElementById('ms-edit-id').value = '';
    document.getElementById('ms-content').value = '';
    msKeywords = [];
    renderMsTags();
    document.getElementById('ms-priority').value = 'medium';
    document.getElementById('ms-trigger-mode').value = 'keyword';
    document.getElementById('ms-cooldown').value = 60;
    document.getElementById('ms-validity').value = 0;
    document.getElementById('ms-keyword-error').style.display = 'none';
    toggleMsKeywordsVisibility('keyword');
    document.getElementById('ms-modal-title').innerHTML = '<i class="bi bi-plus-circle me-1"></i>创建手工建议';
}

async function saveManualSuggestion() {
    var content = document.getElementById('ms-content').value.trim();
    if (!content) { toast('请输入建议内容', 'danger'); return; }
    var mode = document.getElementById('ms-trigger-mode').value;
    if (mode === 'keyword' && msKeywords.length === 0) {
        toast('请至少添加一个触发关键词', 'danger');
        return;
    }

    var editId = document.getElementById('ms-edit-id').value;
    var isEdit = !!editId;
    var saveBtn = document.getElementById('ms-save-btn');
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> ' + (isEdit ? '保存中...' : '创建中...');

    var body = {
        content: content,
        trigger_keywords: msKeywords,
        priority: document.getElementById('ms-priority').value,
        trigger_mode: mode,
        cooldown_minutes: parseInt(document.getElementById('ms-cooldown').value) || 60,
        validity_minutes: parseInt(document.getElementById('ms-validity').value) || 0,
    };

    try {
        if (isEdit) {
            await apiClient.request('/api/manual-suggestions/' + editId, { method: 'PUT', body: JSON.stringify(body) });
            toast('已更新手工建议', 'success');
        } else {
            await apiClient.request('/api/manual-suggestions', { method: 'POST', body: JSON.stringify(body) });
            var toastMsg = mode === 'always'
                ? '手工建议已创建，每次对话将自动推送'
                : '手工建议已创建，下次命中关键词时将触发推送';
            toast(toastMsg, 'success');
        }
        bootstrap.Modal.getInstance(document.getElementById('manualSuggestionModal')).hide();
        if (currentFilter === 'manual') loadManualList();
        loadStatCounts();
    } catch (e) {
        toast((isEdit ? '保存' : '创建') + '失败: ' + (e.message || e), 'danger');
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<i class="bi bi-check-lg"></i> 保存';
    }
}

window.editManualSuggestion = function(id) {
    apiClient.request('/api/manual-suggestions', { method: 'GET' }).then(function(data) {
        var items = data.items || [];
        var item = null;
        for (var i = 0; i < items.length; i++) {
            if (items[i].id === id) { item = items[i]; break; }
        }
        if (!item) { toast('找不到该手工建议', 'danger'); return; }

        document.getElementById('ms-edit-id').value = id;
        document.getElementById('ms-content').value = item.content || '';
        document.getElementById('ms-modal-title').innerHTML = '<i class="bi bi-pencil me-1"></i>编辑手工建议';

        var keywords = item.trigger_keywords || [];
        if (typeof keywords === 'string') { try { keywords = JSON.parse(keywords); } catch(e) { keywords = [keywords]; } }
        msKeywords = Array.isArray(keywords) ? keywords.slice() : [];
        renderMsTags();

        document.getElementById('ms-priority').value = item.priority || 'medium';
        document.getElementById('ms-trigger-mode').value = item.trigger_mode || 'keyword';
        document.getElementById('ms-cooldown').value = item.cooldown_minutes || 60;
        document.getElementById('ms-validity').value = item.validity_minutes || 0;
        toggleMsKeywordsVisibility(item.trigger_mode || 'keyword');

        var modal = new bootstrap.Modal(document.getElementById('manualSuggestionModal'));
        modal.show();
    }).catch(function(e) {
        toast('加载建议详情失败: ' + e.message, 'danger');
    });
};

// --- 手工建议管理视图（合并进建议面板） ---

async function deleteManualSuggestion(id) {
    if (!confirm('确定删除此手工建议？')) return;
    try {
        await apiClient.request('/api/manual-suggestions/' + id, { method: 'DELETE' });
        toast('已删除', 'success');
        // 刷新当前视图
        if (currentFilter === 'manual') loadManualList();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

window.deleteManualSuggestion = deleteManualSuggestion;

// --- 项目切换刷新接口（供 dashboard.js 项目切换处理器调用）---
window.refreshSuggestionPanel = function() {
    loadStatCounts();
    fetchStats();
    loadSuggestions(currentFilter);
    if (currentFilter === 'manual') loadManualList();
    if (currentFilter === 'injection') loadInjectionList();
};

// --- 从知识面板提升为建议 ---
window.promoteToSuggestion = function(content) {
    // 确保模态框已初始化
    const modalEl = document.getElementById('manualSuggestionModal');
    if (!modalEl) { toast('页面未完全加载', 'warning'); return; }
    document.getElementById('ms-content').value = '关于记忆的建议：' + (content || '');
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
};

})();
