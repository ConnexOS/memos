// --- 全局状态 ---
const state = {
    // 共享
    currentProject: null,
    projects: [],
    deleteTargetId: null,

    // 知识库
    kb: {
        items: [],
        total: 0,
        page: 1,
        pageSize: 30,
        typeFilter: '',
        sourceFilter: '',  // v0.4.1: 来源过滤 ''/auto/manual/expiring_soon/expired
        sourceDays: '',    // v0.4.1: 关联时间范围（1=today, 7=week）
        includeArchived: false,
    },

    // 对话记录
    conv: {
        items: [],
        total: 0,
        page: 1,
        pageSize: 20,
    },

    // 今日回顾
    dr: {
        report: null,
        reportDate: null,
        projectId: null,     // 生成日报时绑定的项目 ID，保存时用此值而非实时读取
        loadingTimer: null,
        loadingSeconds: 0,
    },
};

// 将 state 暴露到全局，供 api-client.js 等模块读取 currentProject
window.state = state;

// --- 工具函数 ---
function toast(msg, type='success') {
    const text = _safeErrMsg(msg);
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast align-items-center text-bg-${type} border-0 show`;
    el.role = 'alert';
    el.innerHTML = `<div class="d-flex"><div class="toast-body small">${text}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
    container.appendChild(el);
    const bsToast = new bootstrap.Toast(el);
    bsToast.show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
}

function timeAgo(ts) {
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    if (diff < 2592000) return Math.floor(diff / 86400) + '天前';
    return new Date(ts * 1000).toLocaleDateString('zh-CN');
}

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

function scoreBadge(s) {
    // 质量评分 0-1 → 颜色: 绿(>=0.8) 黄(>=0.5) 红(<0.5)
    if (s >= 0.8) return 'bg-success';
    if (s >= 0.5) return 'bg-warning text-dark';
    return 'bg-danger';
}

const TYPE_LABELS = {
    'fact': '事实',
    'decision': '决策',
    'preference': '偏好',
    'bug_fix': '故障修复',
    'feature_design': '功能设计',
    'code_optimize': '代码优化',
    'tech_knowledge': '技术认知',
    'conversation': '对话',
    'user_input': '用户输入',
    'assistant_output': '助手输出',
};
const TYPE_COLORS = {
    'fact': 'bg-primary',
    'decision': 'bg-warning',
    'preference': 'bg-info',
    'bug_fix': 'bg-danger',
    'feature_design': 'bg-purple',
    'code_optimize': 'bg-secondary',
    'tech_knowledge': 'bg-dark',
};

function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

// --- API 调用 ---
function _safeErrMsg(e) {
    if (e === null || e === undefined) return '未知错误';
    if (typeof e === 'string') return e;
    if (e.message && typeof e.message === 'string') return e.message;
    if (typeof e === 'object') return JSON.stringify(e);
    return String(e);
}

async function api(url, options = {}) {
    const {signal, ...fetchOpts} = options;
    const resp = await fetch(url, {
        headers: {'Content-Type': 'application/json'},
        signal,
        ...fetchOpts,
    });
    let data;
    try {
        data = await resp.json();
    } catch (e) {
        // 响应体非 JSON（如 500 时的纯文本错误页）
        const text = await resp.text().catch(() => '');
        const preview = text.substring(0, 200);
        throw new Error(`服务器响应异常 (HTTP ${resp.status}): ${preview}`);
    }
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        // MemoError 格式优先（含 code + message + suggestion）
        if (data.message) {
            msg = data.message;
            if (data.suggestion) msg += '\n→ ' + data.suggestion;
        } else if (data.detail) {
            if (typeof data.detail === 'string') {
                msg = data.detail;
            } else if (Array.isArray(data.detail)) {
                msg = data.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
            } else {
                msg = JSON.stringify(data.detail);
            }
        }
        throw new Error(msg);
    }
    return data;
}

// --- 记忆列表 ---
async function loadMemories() {
    const params = new URLSearchParams();
    params.set('limit', state.kb.pageSize);
    params.set('offset', (state.kb.page - 1) * state.kb.pageSize);
    if (state.kb.typeFilter) {
        params.append('type', state.kb.typeFilter);
    } else {
        ['fact', 'decision', 'preference', 'bug_fix', 'feature_design', 'code_optimize', 'tech_knowledge'].forEach(t => params.append('type', t));
    }
    if (state.kb.sourceFilter) params.set('source', state.kb.sourceFilter);
    if (state.kb.sourceDays) params.set('days', state.kb.sourceDays);
    if (state.kb.includeArchived) params.set('include_archived', 'true');
    const data = await apiClient.request(`/api/memories?${params}`);
    console.log('loadMemories: sourceFilter=' + state.kb.sourceFilter + ' sourceDays=' + state.kb.sourceDays + ' total=' + data.total + ' items=' + (data.memories || []).length);
    state.kb.items = data.memories;
    state.kb.total = data.total || 0;
    renderMemories();
    renderPagination();
    updateKbCountLabel();
    renderSourceFilterBadge();
}

function renderMemories() {
    const tbody = document.getElementById('kb-tbody');
    if (!state.kb.items.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-secondary py-4">暂无记忆</td></tr>';
        updateBatchDeleteBtn();
        updateKbBatchExportBtn();
        return;
    }
    function truncate(text, maxLen) {
        const chars = Array.from(text);
        return chars.length > maxLen ? chars.slice(0, maxLen).join('') + '...' : text;
    }
    tbody.innerHTML = state.kb.items.map((m, i) => {
        const meta = m.metadata || {};
        const isArchived = meta.active === false;
        const displayText = truncate(m.document, 80);
        const typeLabel = TYPE_LABELS[meta.type] || meta.type || '?';
        return `<tr class="${isArchived ? 'opacity-50' : ''}">
            <td><input type="checkbox" class="form-check-input kb-checkbox" value="${m.id}"></td>
            <td class="text-secondary">${(state.kb.page - 1) * state.kb.pageSize + i + 1}</td>
            <td class="text-secondary small" style="white-space:nowrap">${formatTime(meta.timestamp)}</td>
            <td><div class="memory-content" title="${escapeHtml(m.document)}">${escapeHtml(displayText)}</div></td>
            <td><span class="badge bg-info bg-opacity-25 text-info">${escapeHtml(typeLabel)}</span></td>
            <td>
                <button class="btn btn-sm btn-outline-light py-0 px-1" onclick="openDetail('${m.id}')" title="查看/编辑"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-warning py-0 px-1" onclick="toggleArchive('${m.id}', ${isArchived})" title="${isArchived ? '恢复' : '归档'}">
                    <i class="bi ${isArchived ? 'bi-archive-fill' : 'bi-archive'}"></i>
                </button>
                <button class="btn btn-sm btn-outline-info py-0 px-1" onclick="copyMemory('${m.id}')" title="复制"><i class="bi bi-clipboard"></i></button>
                <button class="btn btn-sm btn-outline-success py-0 px-1" onclick="promoteToSuggestion('${escapeHtml(m.document)}')" title="提升为建议"><i class="bi bi-lightbulb"></i></button>
                <button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="confirmDelete('${m.id}')" title="删除"><i class="bi bi-trash"></i></button>
            </td>
        </tr>`;
    }).join('');
    document.getElementById('kb-select-all').checked = false;
    updateBatchDeleteBtn();
    updateKbBatchExportBtn();
}

// --- 知识库分页 ---
function renderPagination() {
    const nav = document.getElementById('kb-pagination-nav');
    if (state.kb.items.length === 0 && state.kb.page === 1) {
        nav.innerHTML = '';
        return;
    }
    const totalPages = Math.ceil(state.kb.total / state.kb.pageSize) || 1;
    let html = `<span class="small text-secondary me-2">第 ${state.kb.page}/${totalPages} 页</span>`;
    html += `<button class="btn btn-sm btn-outline-secondary py-0" onclick="goPage(${state.kb.page - 1})" ${state.kb.page <= 1 ? 'disabled' : ''}><i class="bi bi-chevron-left"></i></button>`;
    const start = Math.max(1, state.kb.page - 2);
    const end = Math.min(totalPages, state.kb.page + 2);
    for (let p = start; p <= end; p++) {
        html += `<button class="btn btn-sm ${p === state.kb.page ? 'btn-primary' : 'btn-outline-secondary'} py-0 ms-1" onclick="goPage(${p})">${p}</button>`;
    }
    html += `<button class="btn btn-sm btn-outline-secondary py-0 ms-1" onclick="goPage(${state.kb.page + 1})" ${state.kb.page >= totalPages ? 'disabled' : ''}><i class="bi bi-chevron-right"></i></button>`;
    nav.innerHTML = html;
}

async function goPage(page) {
    if (page < 1) return;
    state.kb.page = page;
    try {
        await loadMemories();
    } catch (e) {
        toast('加载失败: ' + e.message, 'danger');
    }
}

// --- 详情/编辑 ---
const AUTO_TYPE_OPTIONS = [
    {value: 'fact', label: '事实'},
    {value: 'decision', label: '决策'},
    {value: 'preference', label: '偏好'},
];

const MANUAL_TYPE_OPTIONS = [
    {value: 'bug_fix', label: '故障修复'},
    {value: 'feature_design', label: '功能设计'},
    {value: 'code_optimize', label: '代码优化'},
    {value: 'tech_knowledge', label: '技术认知'},
];

function _getTypeGroup(type) {
    if (['bug_fix', 'feature_design', 'code_optimize', 'tech_knowledge'].includes(type)) {
        return MANUAL_TYPE_OPTIONS;
    }
    return AUTO_TYPE_OPTIONS;
}

async function openDetail(id) {
    try {
        const m = await apiClient.request(`/api/memories/${id}`);
        document.getElementById('edit-id').value = m.id;
        const currentType = m.metadata.type || 'fact';
        const options = _getTypeGroup(currentType);
        const sel = document.getElementById('edit-type');
        sel.innerHTML = options.map(o =>
            `<option value="${o.value}"${o.value === currentType ? ' selected' : ''}>${o.label}</option>`
        ).join('');
        document.getElementById('edit-content').value = m.document;
        // 显示质量评分
        const score = m.metadata.quality_score;
        const reason = m.metadata.quality_reason || '';
        const scoreEl = document.getElementById('detail-score');
        if (score != null) {
            const cls = score >= 0.8 ? 'bg-success' : (score >= 0.5 ? 'bg-warning text-dark' : 'bg-danger');
            scoreEl.innerHTML = `<span class="badge ${cls}" title="${escapeHtml(reason)}">${(score * 100).toFixed(0)}分</span>`;
        } else {
            scoreEl.innerHTML = '';
        }
        new bootstrap.Modal(document.getElementById('detailModal')).show();
    } catch (e) {
        toast('加载详情失败: ' + e.message, 'danger');
    }
}

document.getElementById('save-edit-btn')?.addEventListener('click', async function() {
    const id = document.getElementById('edit-id').value;
    const content = document.getElementById('edit-content').value.trim();
    const type = document.getElementById('edit-type').value;
    if (!content) { toast('内容不能为空', 'warning'); return; }
    try {
        await apiClient.request(`/api/memories/${id}`, {
            method: 'PUT',
            body: JSON.stringify({content, type}),
        });
        toast('已更新');
        bootstrap.Modal.getInstance(document.getElementById('detailModal')).hide();
        await loadMemories();
    } catch (e) {
        toast('更新失败: ' + e.message, 'danger');
    }
});

document.getElementById('delete-detail-btn')?.addEventListener('click', async function() {
    const id = document.getElementById('edit-id').value;
    if (!confirm('确定删除此记忆？此操作不可恢复。')) return;
    try {
        await apiClient.request(`/api/memories/${id}`, {method:'DELETE'});
        toast('已删除');
        bootstrap.Modal.getInstance(document.getElementById('detailModal')).hide();
        await loadMemories();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
});

// --- 添加(支持普通添加和批量提炼保存) ---
let pendingExtracted = null;

document.getElementById('add-save-btn')?.addEventListener('click', async function() {
    const content = document.getElementById('add-content').value.trim();
    const type = document.getElementById('add-type').value;
    if (!content && !pendingExtracted) { toast('内容不能为空', 'warning'); return; }

    try {
        if (pendingExtracted) {
            const memories = pendingExtracted.map(m => ({
                content: m.content,
                type: m.type,
            }));
            const result = await apiClient.request('/api/memories/batch-create', {
                method: 'POST',
                body: JSON.stringify({memories}),
            });
            pendingExtracted = null;
            toast(result.message || `已添加 ${memories.length} 条记忆`);
        } else {
            if (!content) return;
            await apiClient.request('/api/memories', {
                method: 'POST',
                body: JSON.stringify({content, type}),
            });
            toast('记忆已添加');
        }

        bootstrap.Modal.getInstance(document.getElementById('addModal')).hide();
        document.getElementById('add-content').value = '';
        await Promise.all([loadMemories(), loadConversations()]);
        loadConflictCount();
        setTimeout(loadConflictCount, 8000);  // 等异步冲突检测完成
    } catch (e) {
        toast('添加失败: ' + e.message, 'danger');
    }
});

// --- 复制到剪贴板 ---
async function copyToClipboard(text, label) {
    try {
        await navigator.clipboard.writeText(text);
        toast(`已复制${label || ''}`);
    } catch (e) {
        toast('复制失败: ' + e.message, 'danger');
    }
}

function copyMemory(id) {
    const m = state.kb.items.find(x => x.id === id);
    if (m) copyToClipboard(m.document, '记忆');
}

function copyConversation(id) {
    const c = state.conv.items.find(x => x.id === id);
    if (c) copyToClipboard(c.content, '对话');
}

// --- 删除 ---
function confirmDelete(id) {
    state.deleteTargetId = id;
    new bootstrap.Modal(document.getElementById('deleteModal')).show();
}

document.getElementById('confirm-delete-btn')?.addEventListener('click', async function() {
    const id = state.deleteTargetId;
    if (!id) return;
    try {
        await apiClient.request(`/api/memories/${id}`, {method: 'DELETE'});
        toast('已删除');
        bootstrap.Modal.getInstance(document.getElementById('deleteModal')).hide();
        await loadMemories();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
});

// --- 归档/恢复 ---
async function toggleArchive(id, isArchived) {
    try {
        await apiClient.request(`/api/memories/${id}/${isArchived ? 'restore' : 'archive'}`, {method: 'POST'});
        toast(isArchived ? '已恢复' : '已归档');
        await loadMemories();
    } catch (e) {
        toast('操作失败: ' + e.message, 'danger');
    }
}

// --- DOM 构建辅助 ---
function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    for (const child of (children || [])) {
        if (child instanceof Node) node.appendChild(child);
        else node.appendChild(document.createTextNode(String(child)));
    }
    return node;
}

// ====== F1: 知识库搜索 ======

function highlightText(text, query) {
    if (!query || !text) return escapeHtml(text);
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const parts = escapeHtml(text).split(new RegExp(`(${escaped})`, 'gi'));
    return parts.map(p => p.toLowerCase() === query.toLowerCase() ? `<mark>${p}</mark>` : p).join('');
}

function getMatchLabel(r) {
    if (r.metadata && r.metadata._match_method) return r.metadata._match_method;
    if (r.hybrid) return '综合匹配';
    if (r.bm25_score && r.vector_score === undefined) return '关键词匹配';
    if (r.vector_score && r.bm25_score === undefined) return '语义匹配';
    return '综合匹配';
}

function renderKBSearchResults(results, query) {
    const container = document.getElementById('kb-search-results');
    const toolbar = document.getElementById('kb-list-toolbar');
    const table = document.getElementById('knowledge-table');
    container.innerHTML = '';
    if (!results || !results.length) {
        container.innerHTML = `<div class="text-center py-5">
            <i class="bi bi-search text-secondary opacity-50" style="font-size:2rem"></i>
            <div class="text-secondary small mt-2">未找到匹配的知识</div>
            <button class="btn btn-sm btn-outline-secondary mt-2" id="kb-search-clear-btn2">清除筛选</button>
        </div>`;
        table.style.display = 'none';
        toolbar.style.display = 'none';
        document.getElementById('kb-search-clear').classList.remove('d-none');
        document.getElementById('kb-search-clear-btn2')?.addEventListener('click', clearKBSearch);
        return;
    }
    table.style.display = 'none';
    toolbar.style.display = 'none';
    document.getElementById('kb-search-clear').classList.remove('d-none');

    results.forEach((r, idx) => {
        const m = r.metadata || {};
        const label = getMatchLabel(r);
        const card = document.createElement('div');
        card.className = 'card mb-2 kb-result-card';
        card.style.cursor = 'pointer';
        const rid = (r.id || '').substring(0, 16);
        card.innerHTML = `<div class="card-body py-2 px-3">
            <div class="d-flex justify-content-between align-items-start mb-1">
                <div class="d-flex gap-2 align-items-center">
                    <span class="badge ${TYPE_COLORS[m.type] || 'bg-secondary'} bg-opacity-10 ${TYPE_COLORS[m.type] ? TYPE_COLORS[m.type].replace('bg-','text-') : 'text-secondary'}">${TYPE_LABELS[m.type] || m.type || '?'}</span>
                    <span class="small text-secondary">${formatTime(m.timestamp || 0)}</span>
                    <code class="small text-muted">${escapeHtml(rid)}</code>
                    <span class="badge bg-info bg-opacity-10 text-info">${label}</span>
                </div>
                <span class="d-flex gap-1 align-items-center">
                    <span class="small text-secondary me-1">#${idx + 1}</span>
                    <button class="btn btn-sm btn-outline-info py-0 px-1" onclick="showKBSearchDetail('${escapeHtml(r.id)}')" title="查看详情"><i class="bi bi-eye"></i></button>
                    <button class="btn btn-sm btn-outline-secondary py-0 px-1 copy-btn" data-kb-copy="1" title="复制"><i class="bi bi-clipboard"></i></button>
                </span>
            </div>
            <div class="mb-1 small similarity-bar">
                <div class="d-flex justify-content-between"><span class="text-secondary">相似度</span><span>${(r.similarity * 100).toFixed(0)}%</span></div>
                <div class="progress" style="height:4px"><div class="progress-bar" style="width:${(r.similarity * 100).toFixed(0)}%"></div></div>
            </div>
            <div class="kb-result-content">${highlightText(r.document || '', query)}</div>
        </div>`;
        // data 属性存原始文本，避免 onclick 字符串注入
        const copyBtn = card.querySelector('.copy-btn[data-kb-copy]');
        if (copyBtn) {
            copyBtn.dataset.content = r.document || '';
            copyBtn.addEventListener('click', function () {
                copyToClipboard(this.dataset.content || '', '知识');
            });
        }
        container.appendChild(card);
    });
}

function clearKBSearch() {
    document.getElementById('kb-search-query').value = '';
    document.getElementById('kb-search-results').innerHTML = '';
    document.getElementById('knowledge-table').style.display = '';
    document.getElementById('kb-list-toolbar').style.display = '';
    document.getElementById('kb-search-clear').classList.add('d-none');
    // 保存搜索词到 localStorage
    try {
        const key = 'kb_search_suggestions';
        localStorage.removeItem(key);
    } catch(e) {}
}

function showKBSearchDetail(id) {
    // 点击卡片查看详情 — 定位到知识列表并滚动
    toast('知识 ID: ' + id, 'info');
}

function saveKBSearchSuggestion(query) {
    if (!query) return;
    try {
        const key = 'kb_search_suggestions';
        let sugs = JSON.parse(localStorage.getItem(key) || '[]');
        sugs = sugs.filter(s => s !== query);
        sugs.unshift(query);
        if (sugs.length > 10) sugs = sugs.slice(0, 10);
        localStorage.setItem(key, JSON.stringify(sugs));
    } catch(e) {}
}

function showKBSearchSuggestions() {
    const input = document.getElementById('kb-search-query');
    const container = document.getElementById('kb-search-suggestions');
    try {
        const key = 'kb_search_suggestions';
        const sugs = JSON.parse(localStorage.getItem(key) || '[]');
        if (!sugs.length) { container.style.display = 'none'; return; }
        container.innerHTML = sugs.map(s => `<button type="button" class="list-group-item list-group-item-action py-1 small" onclick="document.getElementById('kb-search-query').value='${escapeHtml(s)}';document.getElementById('kb-search-query').focus();container.style.display='none';">${escapeHtml(s)}</button>`).join('');
        container.style.display = '';
    } catch(e) {}
}

// 知识库搜索事件
document.getElementById('kb-search-btn')?.addEventListener('click', doKBSearch);
document.getElementById('kb-search-query')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') doKBSearch();
});
document.getElementById('kb-search-query')?.addEventListener('focus', showKBSearchSuggestions);
document.getElementById('kb-search-query')?.addEventListener('blur', function() {
    setTimeout(() => document.getElementById('kb-search-suggestions').style.display = 'none', 200);
});
document.getElementById('kb-search-clear')?.addEventListener('click', clearKBSearch);
document.getElementById('kb-search-advanced-toggle')?.addEventListener('click', function() {
    const panel = document.getElementById('kb-search-advanced');
    panel.style.display = panel.style.display === 'none' ? '' : 'none';
    // sessionStorage 记忆状态
    try { sessionStorage.setItem('kb_search_advanced', panel.style.display !== 'none' ? '1' : '0'); } catch(e) {}
});
// 恢复高级选项状态
try {
    if (sessionStorage.getItem('kb_search_advanced') === '1') {
        document.getElementById('kb-search-advanced').style.display = '';
    }
} catch(e) {}

async function doKBSearch() {
    const query = document.getElementById('kb-search-query').value.trim();
    if (!query) { toast('请输入搜索内容', 'warning'); return; }
    const btn = document.getElementById('kb-search-btn');
    btn.disabled = true;
    saveKBSearchSuggestion(query);
    try {
        const data = await apiClient.request('/api/search', {
            method: 'POST',
            body: JSON.stringify({
                query,
                top_k: parseInt(document.getElementById('kb-search-topk').value) || 5,
                days_limit: parseInt(document.getElementById('kb-search-days').value) || null,
                type_filter: document.getElementById('kb-search-type').value || null,
                hybrid: document.getElementById('kb-search-hybrid').checked,
                bm25_weight: parseFloat(document.getElementById('kb-search-bm25').value) || 0.7,
            }),
        });
        renderKBSearchResults(data.results, query);
    } catch (e) {
        toast('搜索失败: ' + e.message, 'danger');
    } finally {
        btn.disabled = false;
    }
}

// ====== F2: 会话记录搜索 ======

function renderConvSearchResults(data, query) {
    const container = document.getElementById('conv-search-results');
    const list = document.getElementById('conversation-list');
    const info = document.getElementById('conv-info');
    if (!container) return;
    container.innerHTML = '';
    list.style.display = 'none';
    document.getElementById('conv-search-back').classList.remove('d-none');

    if (!data.results || !data.results.length) {
        container.innerHTML = `<div class="text-center py-5">
            <div class="text-secondary small">未找到匹配的对话记录</div>
            <button class="btn btn-sm btn-outline-secondary mt-2" onclick="clearConvSearch()">返回对话列表</button>
        </div>`;
        _toggleConvInfoBar(false);
        return;
    }
    _toggleConvInfoBar(false);

    data.results.forEach(r => {
        const card = document.createElement('div');
        card.className = 'border rounded p-2 mb-1';
        const ui = r.type === 'paired' ? (r.user_input || {}) : {};
        const ao = r.type === 'paired' ? (r.assistant_output || {}) : {};
        // 时间取用户记录时间，round_id 显示格式与对话记录卡片一致
        const ts = r.type === 'paired' ? ui.timestamp : r.timestamp;
        const roundId = (r.type === 'paired' ? (r.round_id || ui.round_id || '') : (r.round_id || '')).substring(0, 16);

        if (r.type === 'paired') {
            card.innerHTML = `<div class="d-flex justify-content-between align-items-start mb-1">
                <div class="d-flex gap-2 align-items-center small text-secondary">
                    <span class="badge bg-info bg-opacity-10 text-info">对话轮次</span>
                    <span>${formatTime(ts)}</span>
                    <code class="small text-muted">${escapeHtml(roundId)}</code>
                </div>
                <button class="btn btn-sm btn-outline-info py-0 px-1 copy-btn" data-copy-all="1" title="复制对话"><i class="bi bi-clipboard"></i></button>
            </div>
            <div class="mb-1 p-1 rounded bg-primary bg-opacity-10 d-flex justify-content-between align-items-start">
                <div><span class="badge bg-primary bg-opacity-25 text-primary me-1">用户</span>${highlightText(ui.content || '', query)}</div>
                <button class="btn btn-sm btn-outline-info py-0 px-1 flex-shrink-0 ms-1 copy-btn" data-copy-user="1" title="复制用户消息"><i class="bi bi-clipboard"></i></button>
            </div>
            <div class="p-1 rounded bg-success bg-opacity-10 d-flex justify-content-between align-items-start">
                <div><span class="badge bg-success bg-opacity-25 text-success me-1">助手</span>${highlightText(ao.content || '', query)}</div>
                <button class="btn btn-sm btn-outline-info py-0 px-1 flex-shrink-0 ms-1 copy-btn" data-copy-assistant="1" title="复制助手消息"><i class="bi bi-clipboard"></i></button>
            </div>`;
        } else {
            const isInput = r.type === 'user_input';
            const label = isInput ? '用户' : '助手';
            const cls = isInput ? 'primary' : 'success';
            card.innerHTML = `<div class="d-flex justify-content-between align-items-start mb-1">
                <div class="d-flex gap-2 align-items-center small text-secondary">
                    <span class="badge bg-${cls} bg-opacity-10 text-${cls}">${label}</span>
                    <span>${formatTime(ts)}</span>
                    <code class="small text-muted">${escapeHtml(roundId)}</code>
                </div>
                <button class="btn btn-sm btn-outline-info py-0 px-1 copy-btn" data-copy-single="1" title="复制"><i class="bi bi-clipboard"></i></button>
            </div>
            <div>${highlightText(r.content || '', query)}</div>`;
        }
        // 用 data 属性存储原始文本（避免 onclick 字符串注入风险）
        if (r.type === 'paired') {
            card.querySelector('.copy-btn[data-copy-all]').dataset.content = `用户: ${ui.content || ''}\n助手: ${ao.content || ''}`;
            card.querySelector('.copy-btn[data-copy-user]').dataset.content = ui.content || '';
            card.querySelector('.copy-btn[data-copy-assistant]').dataset.content = ao.content || '';
        } else {
            card.querySelector('.copy-btn[data-copy-single]').dataset.content = r.content || '';
        }
        card.querySelectorAll('.copy-btn').forEach(btn => {
            btn.addEventListener('click', function (e) {
                const text = this.dataset.content || '';
                const label = this.dataset.copyAll ? '对话' : this.dataset.copyUser ? '用户消息' : this.dataset.copyAssistant ? '助手消息' : '消息';
                copyToClipboard(text, label);
            });
        });
        container.appendChild(card);
    });
}

function _toggleConvInfoBar(show) {
    // 通过 #conv-info 向上找到信息栏容器
    const info = document.getElementById('conv-info');
    if (info) {
        const bar = info.closest('.d-flex.justify-content-between');
        if (bar) {
            if (show) {
                bar.classList.remove('d-none');
            } else {
                bar.classList.add('d-none');
            }
        }
    }
}

function clearConvSearch() {
    document.getElementById('conv-search-query').value = '';
    document.getElementById('conv-search-results').innerHTML = '';
    document.getElementById('conversation-list').style.display = '';
    _toggleConvInfoBar(true);
    document.getElementById('conv-search-back').classList.add('d-none');
}

document.getElementById('conv-search-btn')?.addEventListener('click', doConvSearch);
document.getElementById('conv-search-query')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') doConvSearch();
});
document.getElementById('conv-search-back')?.addEventListener('click', clearConvSearch);
document.getElementById('conv-quick-range')?.addEventListener('change', function() {
    const days = parseInt(this.value);
    if (!days) return;
    const now = new Date();
    const from = new Date(now);
    from.setDate(from.getDate() - days);
    document.getElementById('conv-search-date-from').value = formatDateInput(from);
    document.getElementById('conv-search-date-to').value = formatDateInput(now);
    // 如有搜索关键词则自动触发搜索
    const query = document.getElementById('conv-search-query').value.trim();
    if (query) doConvSearch();
});

function formatDateInput(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

async function doConvSearch() {
    const query = document.getElementById('conv-search-query').value.trim();
    if (!query) { toast('请输入搜索内容', 'warning'); return; }
    const btn = document.getElementById('conv-search-btn');
    btn.disabled = true;
    try {
        const dateFromStr = document.getElementById('conv-search-date-from').value;
        const dateToStr = document.getElementById('conv-search-date-to').value;
        const dateFrom = dateFromStr ? new Date(dateFromStr).getTime() / 1000 : null;
        const dateTo = dateToStr ? (new Date(dateToStr).getTime() + 86400000) / 1000 : null;
        const data = await apiClient.request('/api/conversations/search', {
            method: 'POST',
            body: JSON.stringify({
                query,
                top_k: 20,
                date_from: dateFrom,
                date_to: dateTo,
            }),
        });
        renderConvSearchResults(data, query);
    } catch (e) {
        toast('搜索失败: ' + e.message, 'danger');
    } finally {
        btn.disabled = false;
    }
}

// --- 项目列表 & 切换 ---
async function loadProjects() {
    try {
        const data = await api('/api/projects');
        state.projects = data.projects || [];
        const sel = document.getElementById('project-selector');
        sel.innerHTML = '<option value="">选择项目...</option>';
        state.projects.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.project_id;
            opt.textContent = `${p.project_name} (${p.project_id})`;
            sel.appendChild(opt);
        });
        if (data.current_project) {
            const matched = [...sel.options].find(o => o.value === data.current_project);
            if (matched) {
                sel.value = data.current_project;
                state.currentProject = data.current_project;
            }
        }
        updateKbCountLabel();
    } catch (e) {
        console.warn('加载项目列表失败:', e);
    }
}

function filterBySource(src, days) {
    var newFilter = (state.kb.sourceFilter === src && state.kb.sourceDays === (days || '')) ? '' : src;
    state.kb.sourceFilter = newFilter;
    state.kb.sourceDays = newFilter ? (days || '') : '';
    state.kb.page = 1;
    // 标记跳过 tab 事件中的重复 loadMemories 调用
    state._skipTabReload = true;
    try {
        var kbTab = document.getElementById('knowledge-tab');
        if (kbTab) { var bsTab = bootstrap.Tab.getInstance(kbTab); if (bsTab) bsTab.show(); }
    } finally {
        state._skipTabReload = false;
    }
    loadMemories();
}

function renderSourceFilterBadge() {
    var container = document.getElementById('kb-source-badge');
    if (!container) return;
    if (state.kb.sourceFilter) {
        container.innerHTML = '<span class=\"text-info small\" style=\"cursor:pointer\" onclick=\"filterBySource(\'' + state.kb.sourceFilter + '\', ' + (state.kb.sourceDays || 0) + ')\">过滤已激活 &times; 清除</span>';
        container.style.display = '';
    } else {
        container.innerHTML = '';
        container.style.display = 'none';
    }
}

function updateKbCountLabel() {
    const label = document.getElementById('kb-count-label');
    const cur = state.projects.find(p => p.project_id === state.currentProject);
    if (cur) {
        label.textContent = `知识库: ${cur.knowledge_count || 0} 条`;
    } else {
        label.textContent = '';
    }
}

// --- 对话记录 ---
async function loadConversations(page) {
    if (page !== undefined) state.conv.page = page;
    const container = document.getElementById('conversation-list');
    const info = document.getElementById('conv-info');
    try {
        const params = new URLSearchParams();
        params.set('limit', state.conv.pageSize);
        params.set('offset', (state.conv.page - 1) * state.conv.pageSize);
        const data = await apiClient.request(`/api/conversations?${params}`);
        state.conv.total = data.total || 0;
        state.conv.items = data.conversations || [];
        info.textContent = `共 ${state.conv.total} 条对话记录`;
        if (!data.conversations || data.conversations.length === 0) {
            container.innerHTML = '<div class="text-secondary small py-2">暂无对话记录。</div>';
            document.getElementById('batch-delete-conv-btn').classList.add('d-none');
            renderConvPagination();
            return;
        }
        container.innerHTML = data.conversations.map(c => {
            const t = formatTime(c.timestamp);
            const id = c.id || '';
            const isInput = c.type === 'user_input';
            const label = isInput ? '用户' : '助手';
            const bgClass = isInput ? 'bg-primary' : 'bg-success';
            const typeBadge = isInput
                ? '<span class="badge bg-primary bg-opacity-10 text-primary me-1" title="用户输入">输入</span>'
                : '<span class="badge bg-success bg-opacity-10 text-success me-1" title="助手输出">输出</span>';
            const rid = c.round_id ? c.round_id.slice(0, 16) : '';
            return `<div class="border rounded p-2 mb-1 d-flex justify-content-between align-items-start">
                <div class="d-flex align-items-start gap-2 flex-grow-1 me-2">
                    <input type="checkbox" class="form-check-input conv-checkbox mt-1" value="${escapeHtml(id)}">
                    <div>
                        <div class="small text-secondary mb-1">${typeBadge}<span class="me-2">${escapeHtml(t)}</span><code class="small text-muted">${escapeHtml(rid)}</code></div>
                        <div class="mb-0"><span class="badge ${bgClass} bg-opacity-10 me-1">${label}</span>${escapeHtml(c.content)}</div>
                    </div>
                </div>
                <button class="btn btn-sm btn-outline-info py-0 px-1 flex-shrink-0" onclick="copyConversation('${id}')" title="复制此对话"><i class="bi bi-clipboard"></i></button>
                <button class="btn btn-sm btn-outline-danger py-0 px-1 flex-shrink-0" onclick="deleteConversation('${id}')" title="删除此对话"><i class="bi bi-trash"></i></button>
            </div>`;
        }).join('');
        updateConvBatchDeleteBtn();
        renderConvPagination();
    } catch (e) {
        container.innerHTML = `<div class="text-danger small py-2">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

function renderConvPagination() {
    const totalPages = Math.ceil(state.conv.total / state.conv.pageSize) || 1;
    const nav = document.getElementById('conv-pagination-nav');
    if (!nav) return;
    if (state.conv.total === 0) { nav.innerHTML = ''; return; }
    let html = `<span class="small text-secondary me-1">${state.conv.page}/${totalPages}</span>`;
    html += `<button class="btn btn-sm btn-outline-secondary py-0" onclick="goConvPage(${state.conv.page - 1})" ${state.conv.page <= 1 ? 'disabled' : ''}><i class="bi bi-chevron-left"></i></button>`;
    const start = Math.max(1, state.conv.page - 2);
    const end = Math.min(totalPages, state.conv.page + 2);
    for (let p = start; p <= end; p++) {
        html += `<button class="btn btn-sm ${p === state.conv.page ? 'btn-primary' : 'btn-outline-secondary'} py-0 ms-1" onclick="goConvPage(${p})">${p}</button>`;
    }
    html += `<button class="btn btn-sm btn-outline-secondary py-0 ms-1" onclick="goConvPage(${state.conv.page + 1})" ${state.conv.page >= totalPages ? 'disabled' : ''}><i class="bi bi-chevron-right"></i></button>`;
    nav.innerHTML = html;
}

async function goConvPage(page) {
    if (page < 1) return;
    state.conv.page = page;
    try {
        await loadConversations();
    } catch (e) {
        toast('加载对话失败: ' + e.message, 'danger');
    }
}

async function deleteConversation(id) {
    if (!id || !confirm('确定要删除这条对话记录吗？此操作不可撤销。')) return;
    try {
        await apiClient.request(`/api/memories/${id}`, {method: 'DELETE'});
        toast('对话已删除');
        await loadConversations();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

async function batchDeleteConversations() {
    const checked = document.querySelectorAll('.conv-checkbox:checked');
    if (checked.length === 0) { toast('请先选择要删除的对话', 'warning'); return; }
    if (!confirm(`确定要删除选中的 ${checked.length} 条对话记录吗？此操作不可撤销。`)) return;
    const ids = Array.from(checked).map(cb => cb.value);
    try {
        await apiClient.request('/api/memories/batch-delete', {
            method: 'POST',
            body: JSON.stringify({ids}),
        });
        toast(`已删除 ${ids.length} 条对话记录`);
        await loadConversations();
        updateConvBatchDeleteBtn();
    } catch (e) {
        toast('批量删除失败: ' + e.message, 'danger');
    }
}

async function batchExportConversations() {
    const checked = document.querySelectorAll('.conv-checkbox:checked');
    if (checked.length === 0) { toast('请先选择要导出的对话', 'warning'); return; }
    const ids = new Set(Array.from(checked).map(cb => cb.value));
    try {
        // P1-6: 改为调用后端 /api/memories/export，无需客户端拼接
        const params = new URLSearchParams();
        params.append('type', 'user_input');
        params.append('type', 'assistant_output');
        // P0-2: export 使用 fetch 处理 blob 流，手动注入 project_id
        if (window.state?.currentProject) params.append('project_id', window.state.currentProject);
        const resp = await fetch('/api/memories/export?' + params.toString());
        if (!resp.ok) throw new Error(`导出失败 (HTTP ${resp.status})`);

        // 流式读取并按 id 过滤选中项
        const text = await resp.text();
        const allLines = text.trim().split('\n').filter(l => l);
        const selectedLines = allLines.filter(line => {
            try {
                const obj = JSON.parse(line);
                return ids.has(obj.id);
            } catch { return false; }
        });

        const blob = new Blob([selectedLines.join('\n') + '\n'], {type: 'application/x-ndjson'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `memos-conversations-export-${new Date().toISOString().slice(0,10)}.jsonl`;
        a.click();
        URL.revokeObjectURL(url);
        toast(`已导出 ${selectedLines.length} 条对话记录`);
    } catch (e) {
        toast('导出失败: ' + e.message, 'danger');
    }
}

function updateConvBatchDeleteBtn() {
    const checked = document.querySelectorAll('.conv-checkbox:checked').length;
    const btn = document.getElementById('batch-delete-conv-btn');
    const count = document.getElementById('batch-delete-conv-count');
    const extractBtn = document.getElementById('extract-memory-btn');
    const extractCount = document.getElementById('extract-memory-count');
    const exportBtn = document.getElementById('conv-batch-export-btn');
    const exportCount = document.getElementById('conv-batch-export-count');
    count.textContent = checked;
    if (extractCount) extractCount.textContent = checked;
    if (exportCount) exportCount.textContent = checked;
    if (checked > 0) {
        btn.classList.remove('d-none');
        if (extractBtn) extractBtn.classList.remove('d-none');
        if (exportBtn) exportBtn.classList.remove('d-none');
    } else {
        btn.classList.add('d-none');
        if (extractBtn) extractBtn.classList.add('d-none');
        if (exportBtn) exportBtn.classList.add('d-none');
    }
}

document.getElementById('project-selector')?.addEventListener('change', function() {
    state.currentProject = this.value || null;
    state.kb.page = 1;
    state.conv.page = 1;
    // 清空今日回顾旧数据
    state.dr.report = null;
    state.dr.reportDate = null;
    state.dr.projectId = null;
    document.getElementById('dr-report-container')?.classList.add('d-none');
    document.getElementById('dr-empty')?.classList.remove('d-none');
    document.getElementById('dr-error')?.classList.add('d-none');
    document.getElementById('dr-info').textContent = '';
    updateKbCountLabel();
    // 刷新主面板数据（apiClient 自动注入新的 project_id）
    Promise.all([
        loadMemories(),
        loadConversations(),
    ]).catch(e => toast('加载失败: ' + e.message, 'danger'));
    // 刷新建议面板（通过 window 接口，因 suggestions.js 在 IIFE 内）
    setTimeout(function() {
        if (typeof window.refreshSuggestionPanel === 'function') window.refreshSuggestionPanel();
    }, 100);
    // 刷新待办面板（loadTodos 是全局函数）
    setTimeout(function() {
        if (typeof loadTodos === 'function') loadTodos();
    }, 200);
    // 刷新统计图表
    setTimeout(loadUsageStats, 300);
    setTimeout(loadConflictCount, 600);
});

// --- 系统状态 ---
async function loadStatus() {
    try {
        const data = await api('/api/status');
        const dot = document.getElementById('status-dot');
        const text = document.getElementById('status-text');
        const epName = data.active_endpoint || 'LLM';
        if (data.llama_server_ok) {
            dot.className = 'status-dot online';
            text.textContent = `${epName} 服务在线`;
        } else {
            dot.className = 'status-dot offline';
            text.textContent = `${epName} 服务离线`;
        }
    } catch (e) {
        document.getElementById('status-dot').className = 'status-dot offline';
        document.getElementById('status-text').textContent = '状态异常';
    }
}

async function checkLLMStatus() {
    try {
        const data = await api('/api/status');
        return data.llama_server_ok === true;
    } catch (e) {
        return false;
    }
}

// --- 备份 ---
async function triggerBackup() {
    const btn = document.getElementById('backup-btn');
    const statusText = document.getElementById('backup-status-text');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>备份中...';
    statusText.style.display = 'none';
    try {
        const resp = await fetch('/api/backup/trigger', {method: 'POST'});
        if (!resp.ok) {
            const errText = await resp.text().catch(() => resp.statusText);
            throw new Error(errText.slice(0, 200));
        }
        await pollBackupProgress(btn, statusText);
        loadBackupList();
    } catch (e) {
        toast('备份失败: ' + e.message, 'danger');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-cloud-arrow-up"></i> 备份';
    }
}

async function pollBackupProgress(btn, statusText) {
    const maxWait = 600; // 最多等 10 分钟
    const interval = 2000; // 每 2 秒轮询
    let waited = 0;
    while (waited < maxWait * 1000) {
        await new Promise(r => setTimeout(r, interval));
        waited += interval;
        try {
            const prog = await api('/api/backup/progress');
            if (!prog.running) {
                const result = prog.result;
                if (result) {
                    if (result.error) {
                        throw new Error(result.error);
                    }
                    toast(`备份完成: ${result.size_mb} MB, ${result.file_count} 文件, 耗时 ${result.elapsed_seconds}s`);
                } else {
                    toast('备份完成（无明细）');
                }
                loadBackupStatus();
                break;
            } else if (prog.progress) {
                btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${prog.progress}`;
            }
        } catch (e) {
            throw e;
        }
    }
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-cloud-arrow-up"></i> 备份';
    if (waited >= maxWait * 1000) {
        toast('备份超时，请检查服务器日志', 'warning');
    }
}

async function loadBackupStatus() {
    const statusText = document.getElementById('backup-status-text');
    try {
        const data = await api('/api/backup/status');
        if (data.latest) {
            const ts = new Date(data.latest.timestamp * 1000);
            const timeStr = ts.toLocaleString('zh-CN', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
            const sizeMb = (data.latest.size_bytes / (1024*1024)).toFixed(1);
            let icon = data.health === 'warning' || data.health === 'partial' ? '⚠' : '✓';
            statusText.style.display = '';
            statusText.innerHTML = `${icon} 上次备份: ${timeStr} (${sizeMb}MB)`;
            if (data.days_since_backup && data.days_since_backup > data.remind_after_days) {
                statusText.className = 'small text-warning';
            } else {
                statusText.className = 'small text-secondary';
            }
        } else {
            statusText.style.display = '';
            statusText.className = 'small text-warning';
            statusText.textContent = '⚠ 暂无备份';
        }
    } catch (e) {
        // 静默失败
    }
}

// --- 备份管理对话框 ---
let _backupListData = null;

async function openBackupManager() {
    const modal = new bootstrap.Modal(document.getElementById('backupModal'));
    modal.show();
    await loadBackupList();
}

async function loadBackupList() {
    const loading = document.getElementById('backup-modal-loading');
    const empty = document.getElementById('backup-modal-empty');
    const list = document.getElementById('backup-modal-list');
    const info = document.getElementById('backup-modal-info');

    loading.style.display = '';
    empty.style.display = 'none';
    list.style.display = 'none';
    list.innerHTML = '';

    try {
        const data = await api('/api/backups/list');
        _backupListData = data;
        loading.style.display = 'none';

        info.textContent = `共 ${data.total} 个备份 (最多 ${data.max_backups} 个)`;

        if (!data.backups || data.backups.length === 0) {
            empty.style.display = '';
            return;
        }

        list.style.display = '';
        list.innerHTML = data.backups.map(function(b, idx) {
            const ts = new Date((b.timestamp || 0) * 1000);
            const dateStr = ts.toLocaleString('zh-CN', {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
            const size = b.size_mb ? b.size_mb.toFixed(1) + ' MB' : '-';
            const files = b.file_count != null ? b.file_count + ' 文件' : '-';
            const statusMap = {ok:'正常', complete:'正常', partial:'部分', missing:'丢失', unknown:'未知'};
            const statusText = statusMap[b.status] || b.status || '未知';
            const statusColor = b.status === 'ok' ? 'success' : b.status === 'partial' ? 'warning' : 'danger';
            const backupId = b.id || '';
            const displayName = backupId || ('备份_' + (b.timestamp || '').toString().slice(-6));
            return '<div class="border rounded p-2 mb-2">'
                + '<div class="d-flex justify-content-between align-items-center">'
                + '<div class="flex-grow-1">'
                + '<div class="fw-semibold small text-light" style="font-family:monospace;">' + escapeHtml(displayName) + '</div>'
                + '<div class="small text-secondary">'
                + dateStr + ' &middot; ' + size + ' &middot; ' + files
                + ' &middot; <span class="text-' + statusColor + '">' + statusText + '</span>'
                + '</div>'
                + '</div>'
                + '<div class="d-flex gap-1 ms-3 flex-shrink-0">'
                + '<button class="btn btn-sm btn-outline-danger py-0 px-1 small" onclick="deleteBackupItem(\'' + backupId.replace(/'/g, "\\'") + '\')" title="删除此备份">删除</button>'
                + '</div>'
                + '</div>'
                + '</div>';
        }).join('');
    } catch (e) {
        loading.innerHTML = '<span class="text-danger small">加载失败: ' + e.message + '</span>';
    }
}

async function deleteBackupItem(backupId) {
    if (!backupId) { toast('无法删除：备份标识未知', 'danger'); return; }
    if (!confirm('确定删除备份 "' + backupId + '"？此操作不可恢复。')) return;
    try {
        await api('/api/backups/' + encodeURIComponent(backupId), {method: 'DELETE'});
        toast('备份 "' + backupId + '" 已删除', 'success');
        await loadBackupList();
        loadBackupStatus();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

function copyText(text) {
    navigator.clipboard.writeText(text).then(() => toast('已复制'), () => {});
}

// 备份管理模态框打开时刷新列表
document.getElementById('backupModal')?.addEventListener('show.bs.modal', function() {
    loadBackupList();
});

// --- 过滤/分页事件绑定 ---
document.getElementById('kb-type-filter')?.addEventListener('change', function() {
    state.kb.typeFilter = this.value;
    state.kb.page = 1;
    loadMemories().catch(e => toast('加载失败: ' + e.message, 'danger'));
});

document.getElementById('kb-show-archived')?.addEventListener('change', function() {
    state.kb.includeArchived = this.checked;
    state.kb.page = 1;
    loadMemories().catch(e => toast('加载失败: ' + e.message, 'danger'));
});


// --- 配置管理 ---
const sectionLabels = {
    chroma: 'ChromaDB 数据库',
    llm: 'LLM 服务',
    memory: '记忆管理',
    buffer: '对话缓冲',
    dashboard: '仪表板',
    server: 'MCP 服务器',
    auth: '登录认证',
    backup: '备份与恢复',
    notification: '通知中心',
    system_suggestion: '系统建议',
    agent: 'Agent 决策引擎',
};

const sectionDescs = {
    chroma: '向量数据库连接与持久化',
    llm: '记忆提炼用大语言模型',
    memory: '嵌入模型、检索、去重与归档',
    buffer: '对话缓冲区与提炼策略',
    dashboard: 'Web 仪表板参数',
    server: 'MCP 协议服务端参数',
    auth: '登录认证参数',
    backup: 'ChromaDB 数据全量备份与恢复',
    notification: '系统通知的保留与限速',
    system_suggestion: '系统状态型建议（管道二）的推送策略',
    agent: 'Agent 决策引擎配置（Phase 1-3：模式检测/每日简报/信号推送）',
};

const fieldHelpText = {
    // ChromaDB
    'chroma.collection_name': 'ChromaDB 集合名，用于按项目隔离数据',
    'chroma.mode': '运行模式: persistent（本地文件）/ http（远程服务）',
    'chroma.path': '本地持久化目录路径，仅 persistent 模式生效',
    'chroma.host': 'ChromaDB 服务端地址，仅 http 模式生效',
    'chroma.port': 'ChromaDB 服务端端口，仅 http 模式生效',
    'chroma.timeout': 'ChromaDB 操作超时时间（秒）',
    // LLM
    'llm.api_base': 'LLM 服务地址，health 和 chat 接口自动拼接后缀',
    'llm.api_key': 'API 密钥（如不需要可留空）',
    'llm.temperature': '生成温度 (0-1)，越高越随机。建议 0.1-0.3',
    'llm.max_tokens': '单次生成的最大 token 数',
    'llm.request_timeout': 'HTTP 请求超时（秒）',
    'llm.max_retries': '请求失败的最大重试次数',
    'llm.retry_base_delay': '重试退避基础延迟（秒），每次递增',
    // Memory（含嵌入模型）
    'memory.path': '嵌入模型路径（本地目录）',
    'memory.vector_dim': '嵌入向量维度，需与模型一致',
    'memory.decay_lambda': '时间衰减系数，越大越偏向近期记忆。0=不衰减',
    'memory.similarity_threshold': '语义相似度阈值（余弦距离），低于此值判定重复',
    'memory.dedup_top_k': '去重检查的候选记忆数',
    'memory.default_type': '新建记忆的默认类型',
    'memory.archive_days': '超过此天数的记忆自动归档（软删除）',
    'memory.rerank_multiplier': '重排序候选倍数，增大提高质量但降低速度',
    'memory.rerank_min_candidates': '重排序最小候选数，低于此值直接返回',
    // Buffer
    'buffer.max_tokens': '对话缓冲最大 token 数，超限从头截断',
    'buffer.truncate_target': '截断目标 token 数',
    'buffer.trigger_rounds': '自动提炼触发的对话轮数',
    'buffer.rate_limit_seconds': 'LLM 提炼冷却时间（秒）',
    'buffer.token_ratio': '字符数 ÷ 此值 ≈ token 数',
    // Dashboard
    'dashboard.status_cache_ttl': '系统状态缓存有效期（秒）',
    'dashboard.projects_cache_ttl': '项目列表缓存有效期（秒）',
    'dashboard.health_check_timeout': 'LLM 健康检查超时（秒）',
    'dashboard.search_default_top_k': '搜索默认返回条数',
    'dashboard.search_top_k_max': '搜索最大返回条数上限',
    'dashboard.search_default_decay': '搜索默认时间衰减系数',
    'dashboard.search_default_bm25_weight': 'BM25 权重: 0=纯向量, 1=纯关键词',
    'dashboard.list_default_limit': '列表每页默认条数',
    'dashboard.list_limit_max': '列表每页条数上限',
    // Server
    'server.id_length': '自动生成的记忆 ID 长度',
    'server.mcp_top_k_max': 'MCP recall 最大返回条数',
    'server.response_truncate_length': 'MCP 响应中长文本截断长度',
    // Auth
    'auth.disable': '是否关闭登录认证（不推荐）',
    'auth.session_ttl': '登录令牌过期时间（分钟）',
    // Backup
    'backup.target_dir': '备份文件输出目录',
    'backup.max_backups': '最大保留备份数，超出自动覆盖最旧备份',
    'backup.remind_after_days': '距上次备份超过此天数时提醒',
    'backup.verify_after_backup': '备份完成后自动校验数据完整性',
    // Notification
    'notification.retention_days': '通知保留天数，过期的自动清理',
    'notification.rate_limit_minutes': '同类通知最小间隔（分钟），超出合并',
    // Memory（补充）
    'memory.name': '嵌入模型名称，用于显示标识',
    'memory.default_top_k': '检索默认返回条数',
    'memory.default_status': '新建记忆默认状态（active/archived）',
    'memory.reuse_weight': '复用频率加成权重（0=禁用）',
    'memory.reuse_decay': '复用频率时间衰减系数',
    'memory.reuse_boost_cap': '复用频率加成上限',
    'memory.quality_threshold': '质量评分参考阈值，低于此值标记为低质量',
    'memory.conflict_detection_enabled': '新记忆与已有记忆的冲突检测开关',
    'memory.conflict_distance_threshold': '冲突检测预过滤相似度阈值',
    'memory.expiry_warn_days': '过期警告提前天数',
    'memory.daily_review_chunk_tokens': '日报分片最大 token 数（BATCH/PRE_SUMMARIZE 策略）',
    'memory.daily_review_chunk_rounds': '日报分片最大轮次数（作为 token 分片的补充上限）',
    // Agent
    'agent.enabled': 'Agent 决策引擎全局开关',
    'agent.pattern_detection_enabled': '模式检测开关（Phase 1），识别重复问题和行为模式',
    'agent.daily_briefing_enabled': '每日简报开关（Phase 2）',
    'agent.daily_briefing_time': '每日简报推送时间（HH:MM 格式）',
    'agent.topic_cluster_window_days': '主题聚类窗口（天），窗口内的话题归为一类',
    'agent.recurrence_threshold': '重复问题触发阈值，相同问题出现超过此次数时触发建议',
    'agent.bug_match_similarity': 'Bug 匹配相似度阈值',
    'agent.max_daily_briefing_items': '每日简报最大条目数',
    'agent.briefing_cooldown_hours': '简报推送冷却时间（小时）',
    'agent.signal_cooldown_hours': '信号推送冷却时间（小时）',
    'agent.max_active_signals': '最大活跃信号数',
    // SystemSuggestion
    'system_suggestion.enabled': '系统状态型建议全局开关（管道二）',
    'system_suggestion.daily_limit': '管道二每日最大推送数',
    'system_suggestion.cooldown_hours': '同类系统事件冷却时间（小时）',
};

const fieldSaveMap = {
    'memory.path': 'model.path',
    'memory.vector_dim': 'model.vector_dim',
};

// 记忆管理子 tab 分组（嵌入模型 / 检索 / 去重与归档）
const memorySubGroups = {
    embedding: {
        label: '嵌入模型',
        desc: '嵌入模型路径、名称与向量维度',
        icon: 'bi-box',
        fields: ['path', 'name', 'vector_dim'],
    },
    retrieval: {
        label: '检索',
        desc: '语义检索参数、时间衰减与复用频率加成',
        icon: 'bi-search',
        fields: [
            'decay_lambda', 'default_top_k', 'rerank_multiplier', 'rerank_min_candidates',
            'reuse_weight', 'reuse_decay', 'reuse_boost_cap',
            'default_type', 'default_status',
            'quality_threshold',
            'daily_review_chunk_tokens', 'daily_review_chunk_rounds',
        ],
    },
    archiving: {
        label: '去重与归档',
        desc: '去重阈值、归档策略、冲突检测与过期警告',
        icon: 'bi-archive',
        fields: [
            'similarity_threshold', 'dedup_top_k', 'archive_days',
            'conflict_detection_enabled', 'conflict_distance_threshold', 'expiry_warn_days',
        ],
    },
};



// 渲染单个配置字段行（label + 控件 + 帮助图标）
function _renderCfgFieldRow(key, fieldName, value) {
    const fullKey = `${key}.${fieldName}`;
    const dataKey = fieldSaveMap[fullKey] || fullKey;
    const inputId = `cfg-input-${fullKey.replace('.', '-')}`;
    const helpText = fieldHelpText[fullKey];

    const chromaShow = (key === 'chroma' && fieldName === 'path') ? 'persistent' :
                       (key === 'chroma' && (fieldName === 'host' || fieldName === 'port')) ? 'http' : '';
    let h = `<div class="cfg-field-row"${chromaShow ? ` data-chroma-show="${chromaShow}"` : ''}>`;
    h += '<div class="row align-items-center">';
    h += `<label class="col-sm-4 col-form-label col-form-label-sm text-secondary text-end" for="${inputId}">${fieldName}</label>`;
    h += '<div class="col-sm-8">';

    if (typeof value === 'boolean') {
        const checkboxLabel = (fullKey === 'auth.disable') ? '关闭登录认证' : '启用';
        h += `<div class="form-check pt-1 d-inline-block">
            <input type="checkbox" class="form-check-input" id="${inputId}" data-key="${dataKey}" ${value ? 'checked' : ''}>
            <label class="form-check-label small" for="${inputId}">${checkboxLabel}</label>
        </div>`;
    } else if (fullKey === 'chroma.mode') {
        h += `<select class="form-select form-select-sm d-inline-block" style="width:auto;min-width:200px;max-width:100%" id="${inputId}" data-key="${dataKey}">
            <option value="persistent" ${value === 'persistent' ? 'selected' : ''}>persistent（本地文件）</option>
            <option value="http" ${value === 'http' ? 'selected' : ''}>http（远程服务）</option>
        </select>`;
    } else {
        h += `<input type="text" class="form-control form-control-sm d-inline-block" style="width:auto;min-width:200px;max-width:100%" id="${inputId}" value="${escapeHtml(String(value))}" data-key="${dataKey}">`;
    }

    if (helpText) {
        h += `<i class="bi bi-info-circle cfg-help-icon" data-bs-toggle="tooltip" title="${escapeHtml(helpText)}"></i>`;
    }

    h += '</div></div></div>';
    return h;
}

async function loadSettings() {
    const body = document.getElementById('settings-body');
    try {
        const data = await api('/api/config');
        const sections = data.sections || {};

        // v0.4.6: 三级折叠布局 — 端点管理(P0) / 基础设置(P1) / 高级设置(P2)
        var html = '';

        // ── 1. 端点管理 ──
        html += '<div class="card mb-2"><div class="card-header p-2 d-flex justify-content-between align-items-center" data-bs-toggle="collapse" data-bs-target="#collapse-endpoint" role="button"><h6 class="mb-0"><i class="bi bi-hdd-network me-1"></i>端点管理</h6><span class="badge bg-danger bg-opacity-25 text-danger">P0 · 必配</span></div><div class="collapse show" id="collapse-endpoint"><div class="card-body p-2">';
        if (sections.llm) {
            html += '<div id="llm-endpoint-manager" class="mb-2"><div class="text-center py-2"><span class="spinner-border spinner-border-sm"></span> 加载端点列表...</div></div>';
            Object.entries(sections.llm).forEach(function(e) {
                if (['active', 'api_base', 'api_key'].includes(e[0]) || Array.isArray(e[1]) || (typeof e[1] === 'object' && e[1] !== null)) return;
                html += _renderCfgFieldRow('llm', e[0], e[1]);
            });
        }
        html += '</div></div></div>';

        // ── 2. 基础设置 ──
        html += '<div class="card mb-2"><div class="card-header p-2 d-flex justify-content-between align-items-center" data-bs-toggle="collapse" data-bs-target="#collapse-basic" role="button"><h6 class="mb-0"><i class="bi bi-sliders me-1"></i>基础设置</h6><span class="badge bg-primary bg-opacity-25 text-primary">P1 · 常用</span></div><div class="collapse show" id="collapse-basic"><div class="card-body p-2">';
        ['dashboard', 'model', 'auth', 'suggestion'].forEach(function(sk) {
            var fields = sections[sk];
            if (!fields) return;
            html += '<div class="cfg-section-desc mb-1">' + (sectionDescs[sk] || sk) + '</div>';
            Object.entries(fields).forEach(function(e) {
                if (Array.isArray(e[1]) || (typeof e[1] === 'object' && e[1] !== null)) return;
                html += _renderCfgFieldRow(sk, e[0], e[1]);
            });
        });
        // memory 常用字段
        if (sections.memory) {
            html += '<div class="cfg-section-desc mb-1">' + (sectionDescs.memory || '记忆') + '</div>';
            ['default_top_k', 'similarity_threshold', 'archive_days'].forEach(function(fn) {
                if (sections.memory[fn] !== undefined) html += _renderCfgFieldRow('memory', fn, sections.memory[fn]);
            });
        }
        html += '</div></div></div>';

        // ── 3. 高级设置 ──
        html += '<div class="card mb-2"><div class="card-header p-2 d-flex justify-content-between align-items-center collapsed" data-bs-toggle="collapse" data-bs-target="#collapse-advanced" role="button"><h6 class="mb-0"><i class="bi bi-tools me-1"></i>高级设置</h6><span class="badge bg-secondary bg-opacity-25 text-secondary">P2</span></div><div class="collapse" id="collapse-advanced"><div class="card-body p-2">';
        var advancedKeys = Object.keys(sections).filter(function(k) { return !['llm', 'dashboard', 'model', 'auth', 'suggestion', 'memory'].includes(k); });
        advancedKeys.forEach(function(key) {
            var fields = sections[key];
            if (!fields) return;
            html += '<div class="cfg-section-desc mb-1">' + (sectionDescs[key] || key) + '</div>';
            if (key === 'memory') {
                // 记忆管理中排除基础设置已显示的字段
                Object.entries(fields).forEach(function(e) {
                    if (['default_top_k', 'similarity_threshold', 'archive_days'].includes(e[0])) return;
                    if (Array.isArray(e[1]) || (typeof e[1] === 'object' && e[1] !== null)) return;
                    html += _renderCfgFieldRow(key, e[0], e[1]);
                });
            } else {
                // 为 system_suggestion 添加简短说明
                if (key === 'system_suggestion') {
                    html += '<div class="small text-secondary mb-2">系统状态型建议配置（原独立 Tab，已整合至此）</div>';
                }
                Object.entries(fields).forEach(function(e) {
                    if (Array.isArray(e[1]) || (typeof e[1] === 'object' && e[1] !== null)) return;
                    html += _renderCfgFieldRow(key, e[0], e[1]);
                });
            }
        });
        html += '</div></div></div>';

        body.innerHTML = html;

        // 初始化 Tooltip
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(el) {
            new bootstrap.Tab(el);
        });
        document.querySelectorAll('.memory-subtabs .nav-link').forEach(el => {
            new bootstrap.Tab(el);
        });
        document.querySelectorAll('.cfg-help-icon').forEach(el => {
            new bootstrap.Tooltip(el);
        });
        // ChromaDB mode 切换：显示/隐藏关联字段
        const chromaModeSelect = document.getElementById('cfg-input-chroma-mode');
        if (chromaModeSelect) {
            const updateChromaFields = () => {
                const mode = chromaModeSelect.value;
                document.querySelectorAll('[data-chroma-show]').forEach(row => {
                    row.style.display = row.getAttribute('data-chroma-show') === mode ? '' : 'none';
                });
            };
            chromaModeSelect.addEventListener('change', updateChromaFields);
            updateChromaFields();
        }
        // 加载 LLM 端点列表
        loadLLMEndpoints();
    } catch (e) {
        body.innerHTML = `<div class="alert alert-danger small py-2 mb-0">加载配置失败: ${escapeHtml(e.message)}</div>`;
    }
}

// --- LLM 端点管理 ---
async function loadLLMEndpoints() {
    const container = document.getElementById('llm-endpoint-manager');
    if (!container) return;
    try {
        const data = await api('/api/llm/endpoints');
        const eps = data.endpoints || [];
        let html = '<div class="fw-semibold small mb-2"><i class="bi bi-cpu me-1"></i>端点管理</div>';
        eps.forEach(ep => {
            const isActive = ep.is_active;
            html += `<div class="endpoint-card ${isActive ? 'active' : ''}">
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <span class="ep-name">${isActive ? '● ' : '○ '}${escapeHtml(ep.name)}</span>
                        ${isActive ? '<span class="badge bg-primary ms-1" style="font-size:.65rem;">当前活跃</span>' : ''}
                        <div class="ep-detail">
                            <div>API: ${escapeHtml(ep.api_base)}</div>
                            <div>Key: ${escapeHtml(ep.api_key) || '<span class="text-muted">(未设置)</span>'}</div>
                            <div>Model: ${escapeHtml(ep.model) || '<span class="text-muted">(默认)</span>'}</div>
                        </div>
                    </div>
                    <div class="btn-group btn-group-sm" style="height:fit-content;">
                        ${isActive ? '' : `<button class="btn btn-outline-primary" onclick="activateLLMEndpoint(this, '${ep.name}')">激活</button>`}
                        <button class="btn btn-outline-secondary" onclick="editLLMEndpoint('${ep.name}')">编辑</button>
                        <button class="btn btn-outline-info" onclick="testLLMEndpoint(this, '${ep.name}')">测试</button>
                        ${ep.name !== 'default' && !isActive ? `<button class="btn btn-outline-danger" onclick="deleteLLMEndpoint('${ep.name}')">删除</button>` : ''}
                    </div>
                </div>
            </div>`;
        });
        html += `<div class="mt-2">
            <button class="btn btn-sm btn-outline-success" onclick="editLLMEndpoint('')">
                <i class="bi bi-plus-circle"></i> 添加端点
            </button>
        </div>`;
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="alert alert-danger small py-2 mb-0">加载端点失败: ${escapeHtml(e.message)}</div>`;
    }
}

async function testLLMEndpoint(btn, name) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span>';
    try {
        const data = await api('/api/llm/test-connection', {
            method: 'POST',
            body: JSON.stringify({endpoint_id: name}),
        });
        if (data.status === 'ok') {
            toast(`端点 '${name}' 连接正常 (${data.method}, ${data.latency_ms}ms)`, 'success');
        } else {
            toast(`端点 '${name}' 不可达: ${data.reason}\n建议: ${data.suggestion}`, 'danger');
        }
    } catch (e) {
        toast(`测试失败: ${e.message}`, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = orig;
    }
}

async function activateLLMEndpoint(btn, name) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>激活中...';
    try {
        const data = await api('/api/llm/activate', {
            method: 'POST',
            body: JSON.stringify({name}),
        });
        toast(data.message || `已切换到端点 '${name}'`, data.status === 'online' ? 'success' : 'warning');
        loadStatus(); // 立即刷新状态指示灯
        await Promise.all([loadSettings(), loadLLMEndpointsForExtract()]);
    } catch (e) {
        btn.disabled = false;
        btn.textContent = '激活';
        toast('切换失败: ' + e.message, 'danger');
    }
}

async function deleteLLMEndpoint(name) {
    if (!confirm(`确定要删除端点 '${name}' 吗？`)) return;
    try {
        await api(`/api/llm/endpoints/${encodeURIComponent(name)}`, {method: 'DELETE'});
        toast(`端点 '${name}' 已删除`, 'success');
        await Promise.all([loadSettings(), loadLLMEndpointsForExtract()]);
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

let _editingEndpointName = null;

function editLLMEndpoint(name) {
    _editingEndpointName = name;
    const title = document.getElementById('endpointModalTitle');
    const nameInput = document.getElementById('ep-name');
    const origInput = document.getElementById('ep-original-name');
    const baseInput = document.getElementById('ep-api-base');
    const keyInput = document.getElementById('ep-api-key');
    const modelInput = document.getElementById('ep-model');

    // 在 settingsModal 之上显示，提升 z-index 避免被 backdrop 遮挡
    document.getElementById('endpointModal').style.setProperty('--bs-modal-zindex', '1060');

    if (name) {
        title.textContent = '编辑端点';
        api('/api/llm/endpoints').then(data => {
            const ep = (data.endpoints || []).find(e => e.name === name);
            if (!ep) { toast('端点不存在', 'warning'); return; }
            nameInput.value = ep.name;
            nameInput.disabled = true;
            origInput.value = ep.name;
            baseInput.value = ep.api_base;
            keyInput.value = ep.api_key || '';
            modelInput.value = ep.model;
            // 初始化 tooltip（Bootstrap 动态生成元素需手动初始化）
            const keyIcon = document.querySelector('#endpointModal .bi-info-circle');
            if (keyIcon && !keyIcon._tooltip) new bootstrap.Tooltip(keyIcon);
            new bootstrap.Modal(document.getElementById('endpointModal')).show();
        });
    } else {
        title.textContent = '添加端点';
        nameInput.value = '';
        nameInput.disabled = false;
        origInput.value = '';
        baseInput.value = '';
        keyInput.value = '';
        modelInput.value = '';
        new bootstrap.Modal(document.getElementById('endpointModal')).show();
    }
}

async function saveLLMEndpoint() {
    const name = document.getElementById('ep-name').value.trim();
    const origName = document.getElementById('ep-original-name').value;
    const apiBase = document.getElementById('ep-api-base').value.trim();
    const apiKey = document.getElementById('ep-api-key').value;
    const model = document.getElementById('ep-model').value.trim();

    if (!name) { toast('请输入端点名称', 'warning'); return; }
    if (!apiBase) { toast('请输入 API Base URL', 'warning'); return; }

    const saveBtn = document.getElementById('ep-save-btn');
    saveBtn.disabled = true;
    try {
        if (origName) {
            // 更新：api_key 为掩码值时视为未修改，不发送
            const body = {api_base: apiBase, model};
            body.api_key = (apiKey === '******' || apiKey === '') ? null : apiKey;
            await api(`/api/llm/endpoints/${encodeURIComponent(origName)}`, {
                method: 'PUT',
                body: JSON.stringify(body),
            });
            toast('端点已更新', 'success');
        } else {
            // 新增
            await api('/api/llm/endpoints', {
                method: 'POST',
                body: JSON.stringify({name, api_base: apiBase, api_key: apiKey === '******' ? '' : apiKey, model}),
            });
            toast('端点已创建', 'success');
        }
        bootstrap.Modal.getInstance(document.getElementById('endpointModal')).hide();
        await Promise.all([loadSettings(), loadLLMEndpointsForExtract()]);
    } catch (e) {
        toast('保存失败: ' + e.message, 'danger');
    } finally {
        saveBtn.disabled = false;
    }
}

async function saveSettings(keys) {
    const changes = [];
    document.querySelectorAll('[data-key]').forEach(el => {
        const key = el.getAttribute('data-key');
        if (keys && !keys.has(key)) return;
        let val;
        if (el.type === 'checkbox') {
            val = el.checked;
        } else {
            val = el.value.trim();
        }
        changes.push({key, value: val});
    });
    if (!changes.length) { toast('没有修改的配置', 'info'); return; }
    let success = 0;
    for (const c of changes) {
        try {
            await api('/api/config', {
                method: 'PUT',
                body: JSON.stringify(c),
            });
            success++;
        } catch (e) {
            toast(`${c.key}: ${e.message}`, 'danger');
        }
    }
    if (success > 0) {
        toast(`已保存 ${success}/${changes.length} 项配置`, 'success');
    }
}

document.getElementById('settings-save-all-btn')?.addEventListener('click', async function() {
    this.disabled = true;
    await saveSettings(null);
    this.disabled = false;
});

document.getElementById('settings-reload-btn')?.addEventListener('click', async function() {
    this.disabled = true;
    try {
        await api('/api/config/reload', {method: 'POST'});
        toast('配置已重新加载', 'success');
        await loadSettings();
    } catch (e) {
        toast('重新加载失败: ' + e.message, 'danger');
    }
    this.disabled = false;
});

document.getElementById('settingsModal')?.addEventListener('show.bs.modal', function() {
    loadSettings();
});

// v0.4.6: 语言切换
document.getElementById('lang-switch')?.addEventListener('click', async function() {
    const currentLang = document.documentElement.lang || 'zh';
    const newLang = currentLang === 'zh' ? 'en' : 'zh';
    try {
        await api('/api/config', { method: 'PUT', body: JSON.stringify({key: 'dashboard.locale', value: newLang}) });
    } catch(e) { /* 静默 */ }
    document.documentElement.lang = newLang;
    location.reload();
});

// --- 多项选择 & 批量删除 (记忆) ---
document.getElementById('kb-select-all')?.addEventListener('change', function() {
    document.querySelectorAll('.kb-checkbox').forEach(cb => cb.checked = this.checked);
    updateBatchDeleteBtn();
    updateKbBatchExportBtn();
});

document.getElementById('kb-batch-delete-btn')?.addEventListener('click', async function() {
    const checked = document.querySelectorAll('.kb-checkbox:checked');
    if (checked.length === 0) return;
    if (!confirm(`确定要删除选中的 ${checked.length} 条记忆吗？此操作不可撤销。`)) return;
    const ids = Array.from(checked).map(cb => cb.value);
    try {
        await apiClient.request('/api/memories/batch-delete', {
            method: 'POST',
            body: JSON.stringify({ids}),
        });
        toast(`已删除 ${ids.length} 条记忆`);
        await loadMemories();
    } catch (e) {
        toast('批量删除失败: ' + e.message, 'danger');
    }
});

function updateBatchDeleteBtn() {
    const checked = document.querySelectorAll('.kb-checkbox:checked').length;
    const btn = document.getElementById('kb-batch-delete-btn');
    const count = document.getElementById('kb-batch-delete-count');
    count.textContent = checked;
    if (checked > 0) {
        btn.classList.remove('d-none');
    } else {
        btn.classList.add('d-none');
        const selectAll = document.getElementById('kb-select-all');
        if (selectAll) selectAll.checked = false;
    }
}

function updateKbBatchExportBtn() {
    const checked = document.querySelectorAll('.kb-checkbox:checked').length;
    const btn = document.getElementById('kb-batch-export-btn');
    const count = document.getElementById('kb-batch-export-count');
    count.textContent = checked;
    btn.classList.toggle('d-none', checked === 0);
}

document.getElementById('kb-import-submit')?.addEventListener('click', async function() {
    const fileInput = document.getElementById('kb-import-file');
    const file = fileInput.files[0];
    if (!file) { toast('请先选择 .jsonl 文件', 'warning'); return; }
    if (!file.name.endsWith('.jsonl')) { toast('仅支持 .jsonl 文件', 'warning'); return; }

    const strategy = document.getElementById('kb-import-strategy').value;
    const resultEl = document.getElementById('kb-import-result');
    const loadingEl = document.getElementById('kb-import-loading');
    resultEl.classList.add('d-none');
    loadingEl.classList.remove('d-none');

    try {
        const form = new FormData();
        form.append('file', file);
        // P0-2: import 使用 FormData，手动注入 project_id
        const importUrl = window.state?.currentProject
            ? `/api/memories/import?strategy=${strategy}&project_id=${encodeURIComponent(window.state.currentProject)}`
            : `/api/memories/import?strategy=${strategy}`;
        const resp = await fetch(importUrl, {
            method: 'POST',
            body: form,
        });
        const text = await resp.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch {
            throw new Error(`服务器错误 (HTTP ${resp.status}): ${text.slice(0, 200)}`);
        }
        if (!resp.ok) throw new Error(data.detail || data.error || '导入失败');

        document.getElementById('kb-import-imported').textContent = `成功: ${data.imported} 条`;
        document.getElementById('kb-import-skipped').textContent = `跳过: ${data.skipped} 条`;
        document.getElementById('kb-import-failed').textContent = `失败: ${data.failed} 条`;
        if (data.errors && data.errors.length > 0) {
            document.getElementById('kb-import-errors').textContent =
                data.errors.slice(0, 5).map(e => `行${e.line}: ${e.error}`).join('; ');
        }
        resultEl.classList.remove('d-none');
        if (data.imported > 0) {
            loadMemories();
            updateKbBatchExportBtn();
            updateBatchDeleteBtn();
        }
        fileInput.value = '';
    } catch (e) {
        toast('导入失败: ' + e.message, 'danger');
    } finally {
        loadingEl.classList.add('d-none');
    }
});

// 知识面板：导出选中 → 直接导出（不弹模态框，不含嵌入向量）
document.getElementById('kb-batch-export-btn')?.addEventListener('click', async function() {
    const checkedBoxes = document.querySelectorAll('.kb-checkbox:checked');
    if (checkedBoxes.length === 0) { toast('请先选择要导出的记忆', 'warning'); return; }

    try {
        const params = new URLSearchParams();
        checkedBoxes.forEach(cb => params.append('memory_ids', cb.value));
        // P0-2: export 使用 fetch 处理 blob 流，手动注入 project_id
        if (window.state?.currentProject) params.append('project_id', window.state.currentProject);
        const resp = await fetch('/api/memories/export?' + params.toString());
        if (!resp.ok) {
            const text = await resp.text();
            throw new Error(`导出失败 (HTTP ${resp.status}): ${text.slice(0, 200)}`);
        }

        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'memos-export-' + new Date().toISOString().slice(0, 10) + '.jsonl';
        a.click();
        URL.revokeObjectURL(a.href);
        toast(`已导出 ${checkedBoxes.length} 条记忆`);
    } catch (e) {
        toast('导出失败: ' + e.message, 'danger');
    }
});

document.addEventListener('change', function(e) {
    if (e.target.classList.contains('kb-checkbox')) {
        updateKbBatchExportBtn();
        updateBatchDeleteBtn();
    }
});
let _extractLoadingModal = null;
let _extractLoadingTimer = null;
let _extractLoadingSeconds = 0;

function showExtractLoading(text) {
    document.getElementById('extractLoadingText').textContent = text || '正在提炼知识卡片...';
    if (!_extractLoadingModal) {
        _extractLoadingModal = new bootstrap.Modal(document.getElementById('extractLoadingModal'));
    }
    _extractLoadingModal.show();
    // 启动计时器
    _extractLoadingSeconds = 0;
    document.getElementById('extractLoadingTimer').textContent = '已等待 0s';
    clearInterval(_extractLoadingTimer);
    _extractLoadingTimer = setInterval(function() {
        _extractLoadingSeconds++;
        var s = _extractLoadingSeconds;
        if (s < 60) {
            document.getElementById('extractLoadingTimer').textContent = '已等待 ' + s + 's';
        } else {
            var m = Math.floor(s / 60);
            var sec = s % 60;
            document.getElementById('extractLoadingTimer').textContent = '已等待 ' + m + 'm' + (sec < 10 ? '0' : '') + sec + 's';
        }
    }, 1000);
}

function hideExtractLoading() {
    clearInterval(_extractLoadingTimer);
    _extractLoadingTimer = null;
    if (_extractLoadingModal) {
        _extractLoadingModal.hide();
    }
}

// --- 多项选择 & 批量删除 (对话) ---
document.addEventListener('change', function(e) {
    if (e.target.classList.contains('conv-checkbox')) {
        updateConvBatchDeleteBtn();
    }
});

document.getElementById('batch-delete-conv-btn')?.addEventListener('click', batchDeleteConversations);
document.getElementById('conv-batch-export-btn')?.addEventListener('click', batchExportConversations);

// --- 提炼知识 (v2) ---
let _extractCards = [];
let _extractConvIds = [];
let _extractReviewModal = null;

function getReviewModal() {
    if (!_extractReviewModal) {
        _extractReviewModal = bootstrap.Modal.getOrCreateInstance(
            document.getElementById('extractReviewModal')
        );
    }
    return _extractReviewModal;
}

async function loadLLMEndpointsForExtract() {
    try {
        const data = await api('/api/llm/endpoints');
        const sel = document.getElementById('extract-endpoint-select');
        sel.innerHTML = '';
        // 添加"使用默认"选项
        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        defaultOpt.textContent = '(使用默认)';
        sel.appendChild(defaultOpt);
        (data.endpoints || []).forEach(ep => {
            const opt = document.createElement('option');
            opt.value = ep.name;
            opt.textContent = `${ep.name}${ep.is_active ? ' ✓' : ''}`;
            if (ep.is_active) opt.selected = true;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.warn('加载 LLM 端点列表失败:', e);
    }
}

let _extractPromptInfo = null;

function renderExtractReview(cards, promptInfo) {
    const container = document.getElementById('extract-review-list');
    const empty = document.getElementById('extract-review-empty');
    const info = document.getElementById('extract-review-info');
    promptInfo = promptInfo || _extractPromptInfo;

    let baseInfo = `已选 ${_extractConvIds.length} 条对话`;
    if (promptInfo && promptInfo.prompt_id) {
        baseInfo += ` | 提示词: ${promptInfo.prompt_id} v${promptInfo.prompt_version || '-'}`;
    }

    if (!cards || cards.length === 0) {
        container.innerHTML = '';
        empty.style.display = 'block';
        info.textContent = baseInfo + ' → 未提取到知识卡片';
        document.getElementById('extract-save-btn').disabled = true;
        document.getElementById('extract-save-count').textContent = '0';
        return;
    }
    empty.style.display = 'none';
    info.textContent = baseInfo + ` → 提取到 ${cards.length} 条知识卡片`;
    document.getElementById('extract-save-btn').disabled = false;
    document.getElementById('extract-save-count').textContent = cards.length;

    const typeBg = {
        'bug_fix': 'danger', 'feature_design': 'primary',
        'code_optimize': 'success', 'tech_knowledge': 'info',
    };

    container.innerHTML = cards.map((card, idx) => {
        const t = card.type || 'tech_knowledge';
        const label = TYPE_LABELS[t] || t;
        const bg = typeBg[t] || 'secondary';
        return `<div class="border rounded p-2 mb-2 extract-card" data-index="${idx}">
            <div class="d-flex align-items-start gap-2 mb-1">
                <input type="checkbox" class="form-check-input extract-card-checkbox mt-1" checked data-index="${idx}">
                <div class="flex-grow-1">
                    <div class="d-flex justify-content-between align-items-start">
                        <span class="badge bg-${bg} bg-opacity-25 text-${bg} mb-1">${label}</span>
                        <div class="d-flex gap-1 align-items-center">
                            ${card.quality_score != null ? `<span class="badge ${scoreBadge(card.quality_score)} me-1" title="${escapeHtml(card.quality_reason || '')}">${(card.quality_score * 100).toFixed(0)}分</span>` : ''}
                            <button class="btn btn-sm btn-outline-primary py-0 px-1" onclick="editExtractCard(${idx})" title="编辑"><i class="bi bi-pencil"></i></button>
                            <button class="btn btn-sm btn-outline-secondary py-0 px-1" onclick="copyExtractCard(${idx})" title="复制"><i class="bi bi-clipboard"></i></button>
                            <button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="toggleExtractCard(${idx})" title="拒绝"><i class="bi bi-x-lg"></i></button>
                        </div>
                    </div>
                    <div class="small mb-1"><strong>问题：</strong>${escapeHtml(card.problem)}</div>
                    <div class="small mb-1"><strong>方案：</strong>${escapeHtml(card.solution)}</div>
                    <div class="small text-secondary"><strong>洞察：</strong>${escapeHtml(card.insight)}</div>
                </div>
            </div>
        </div>`;
    }).join('');

    updateExtractSaveCount();
}

function updateExtractSaveCount() {
    const checked = document.querySelectorAll('.extract-card-checkbox:checked').length;
    document.getElementById('extract-save-count').textContent = checked;
    document.getElementById('extract-copy-selected-btn').disabled = checked === 0;
}

function toggleExtractCard(idx) {
    const cb = document.querySelector(`.extract-card-checkbox[data-index="${idx}"]`);
    if (cb) {
        cb.checked = !cb.checked;
        cb.closest('.extract-card').classList.toggle('opacity-50', !cb.checked);
        updateExtractSaveCount();
    }
}

function copyExtractCard(idx) {
    const card = _extractCards[idx];
    if (!card) return;
    const text = `【${card.type || 'tech_knowledge'}】\n问题：${card.problem || ''}\n方案：${card.solution || ''}\n洞察：${card.insight || ''}`;
    copyToClipboard(text, '知识卡片');
}

function editExtractCard(idx) {
    const card = _extractCards[idx];
    if (!card) return;
    document.getElementById('extract-edit-index').value = idx;
    document.getElementById('extract-edit-type').value = card.type || 'tech_knowledge';
    document.getElementById('extract-edit-problem').value = card.problem || '';
    document.getElementById('extract-edit-solution').value = card.solution || '';
    document.getElementById('extract-edit-insight').value = card.insight || '';
    new bootstrap.Modal(document.getElementById('extractEditModal')).show();
}

document.getElementById('extract-edit-save-btn')?.addEventListener('click', function() {
    const idx = parseInt(document.getElementById('extract-edit-index').value);
    if (isNaN(idx) || !_extractCards[idx]) return;
    _extractCards[idx].type = document.getElementById('extract-edit-type').value;
    _extractCards[idx].problem = document.getElementById('extract-edit-problem').value.trim();
    _extractCards[idx].solution = document.getElementById('extract-edit-solution').value.trim();
    _extractCards[idx].insight = document.getElementById('extract-edit-insight').value.trim();
    renderExtractReview(_extractCards);
    bootstrap.Modal.getInstance(document.getElementById('extractEditModal')).hide();
    toast('卡片已更新', 'success');
});

document.getElementById('extract-select-all-btn')?.addEventListener('click', function() {
    document.querySelectorAll('.extract-card-checkbox').forEach(cb => {
        cb.checked = true;
        cb.closest('.extract-card').classList.remove('opacity-50');
    });
    updateExtractSaveCount();
});

document.getElementById('extract-deselect-all-btn')?.addEventListener('click', function() {
    document.querySelectorAll('.extract-card-checkbox').forEach(cb => {
        cb.checked = false;
        cb.closest('.extract-card').classList.add('opacity-50');
    });
    updateExtractSaveCount();
});

// 复制选中的知识卡片到剪贴板
document.getElementById('extract-copy-selected-btn')?.addEventListener('click', function() {
    const checked = document.querySelectorAll('.extract-card-checkbox:checked');
    if (checked.length === 0) { toast('请至少选择一条知识卡片', 'warning'); return; }
    const parts = [];
    checked.forEach((cb, i) => {
        const idx = parseInt(cb.getAttribute('data-index'));
        const card = _extractCards[idx];
        if (!card) return;
        parts.push([
            `--- 卡片 ${i + 1} ---`,
            `类型：${card.type || 'tech_knowledge'}`,
            `问题：${card.problem || ''}`,
            `方案：${card.solution || ''}`,
            `洞察：${card.insight || ''}`,
        ].join('\n'));
    });
    copyToClipboard(parts.join('\n\n'), `选中的 ${parts.length} 条知识卡片`);
});

document.addEventListener('change', function(e) {
    if (e.target.classList.contains('extract-card-checkbox')) {
        e.target.closest('.extract-card').classList.toggle('opacity-50', !e.target.checked);
        updateExtractSaveCount();
    }
});

document.getElementById('extract-memory-btn')?.addEventListener('click', async function() {
    const checked = document.querySelectorAll('.conv-checkbox:checked');
    if (checked.length === 0) { toast('请先选择要提炼的对话', 'warning'); return; }

    // 检查 LLM 服务状态
    const isLLMOnline = await checkLLMStatus();
    if (!isLLMOnline) {
        toast('LLM 服务当前离线，无法提炼知识。请查看页面右上角状态指示灯确认 LLM 服务是否正常运行。', 'warning');
        return;
    }

    _extractConvIds = Array.from(checked).map(cb => cb.value);

    showExtractLoading('正在提炼知识卡片...');
    try {
        // 加载提示词模板列表和 LLM 端点列表
        await Promise.all([loadPromptTemplates(), loadLLMEndpointsForExtract()]);
        // 两者都就绪后再级联，确保端点选择器已填充
        cascadePromptByEndpoint();

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 600000);
        const promptSel = document.getElementById('extract-prompt-select');
        const promptId = promptSel.value || 'default';
        const promptVersion = promptSel.selectedOptions[0]?.dataset?.version || null;
        const endpointName = document.getElementById('extract-endpoint-select').value || '';
        const extractBody = {ids: _extractConvIds, prompt_id: promptId};
        if (promptVersion) extractBody.prompt_version = promptVersion;
        if (endpointName) extractBody.llm_endpoint = endpointName;
        const data = await apiClient.request('/api/conversations/extract-v2', {
            method: 'POST',
            signal: controller.signal,
            body: JSON.stringify(extractBody),
        });
        clearTimeout(timeoutId);

        _extractCards = data.cards || [];
        _extractPromptInfo = { prompt_id: data.prompt_id, prompt_version: data.prompt_version };
        renderExtractReview(_extractCards, _extractPromptInfo);

        if (!_extractCards.length) {
            toast(data.message || '未提取到知识卡片', 'warning');
        } else {
            toast(data.message, 'success');
        }

        getReviewModal().show();
    } catch (e) {
        if (e.name === 'AbortError') {
            toast('提炼超时，请稍后重试', 'warning');
        } else {
            toast('提炼失败: ' + e.message, 'danger');
        }
    } finally {
        hideExtractLoading();
    }
});

// 重新提炼
document.getElementById('extract-retry-btn')?.addEventListener('click', async function() {
    if (!_extractConvIds.length) {
        toast('请先选择要提炼的对话记录', 'warning');
        return;
    }

    // 检查 LLM 服务状态
    const isLLMOnline = await checkLLMStatus();
    if (!isLLMOnline) {
        toast('LLM 服务当前离线，无法提炼知识。请查看页面右上角状态指示灯确认 LLM 服务是否正常运行。', 'warning');
        return;
    }

    // 关闭审核模态框（若已显示），显示全局等待
    const reviewModal = getReviewModal();
    const modalEl = document.getElementById('extractReviewModal');

    // 若模态框当前可见，先隐藏并等待过渡完成
    if (modalEl.classList.contains('show')) {
        reviewModal.hide();
        await new Promise(resolve => {
            const timeout = setTimeout(resolve, 1000);
            modalEl.addEventListener('hidden.bs.modal', () => { clearTimeout(timeout); resolve(); }, { once: true });
        });
    }

    showExtractLoading('正在重新提炼知识卡片...');
    try {
        const promptSel = document.getElementById('extract-prompt-select');
        const promptId = promptSel.value || 'default';
        const promptVersion = promptSel.selectedOptions[0]?.dataset?.version || '';
        const endpointName = document.getElementById('extract-endpoint-select').value || '';
        const extractBody = {ids: _extractConvIds, prompt_id: promptId};
        if (promptVersion) extractBody.prompt_version = promptVersion;
        if (endpointName) extractBody.llm_endpoint = endpointName;
        console.log('extract-v2 request:', JSON.stringify(extractBody));
        const data = await apiClient.request('/api/conversations/extract-v2', {
            method: 'POST',
            body: JSON.stringify(extractBody),
        });
        _extractCards = data.cards || [];
        _extractPromptInfo = { prompt_id: data.prompt_id, prompt_version: data.prompt_version };
        renderExtractReview(_extractCards, _extractPromptInfo);
        if (!_extractCards.length) {
            toast(data.message || '未提取到知识卡片', 'warning');
        } else {
            toast(data.message, 'success');
        }
        getReviewModal().show();
    } catch (e) {
        const msg = (e && typeof e.message === 'string') ? e.message : (e && typeof e === 'string' ? e : '未知错误');
        toast('提炼失败: ' + msg, 'danger');
    } finally {
        hideExtractLoading();
    }
});

// 复制发送给 LLM 的请求消息
document.getElementById('extract-copy-msg-btn')?.addEventListener('click', async function() {
    if (!_extractConvIds.length) {
        toast('没有可复制的消息', 'warning');
        return;
    }
    const btn = document.getElementById('extract-copy-msg-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>加载中...';
    try {
        const promptSel = document.getElementById('extract-prompt-select');
        const promptId = promptSel.value || 'default';
        const promptVersion = promptSel.selectedOptions[0]?.dataset?.version || '';
        const endpointName = document.getElementById('extract-endpoint-select').value || '';
        const previewBody = {ids: _extractConvIds, prompt_id: promptId};
        if (promptVersion) previewBody.prompt_version = promptVersion;
        if (endpointName) previewBody.llm_endpoint = endpointName;
        const data = await apiClient.request('/api/conversations/extract-preview', {
            method: 'POST',
            body: JSON.stringify(previewBody),
        });
        copyToClipboard(JSON.stringify(data, null, 2), 'API 请求 JSON');
    } catch (e) {
        const msg = (e && typeof e.message === 'string') ? e.message : '未知错误';
        toast('获取消息失败: ' + msg, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-clipboard"></i> 复制消息';
    }
});

// 保存提炼结果
document.getElementById('extract-save-btn')?.addEventListener('click', async function() {
    const checked = document.querySelectorAll('.extract-card-checkbox:checked');
    if (checked.length === 0) { toast('请至少选择一条知识卡片', 'warning'); return; }
    const cards = [];
    checked.forEach(cb => {
        const idx = parseInt(cb.getAttribute('data-index'));
        if (!isNaN(idx) && _extractCards[idx]) {
            cards.push(_extractCards[idx]);
        }
    });
    if (!cards.length) { toast('没有可保存的卡片', 'warning'); return; }

    this.disabled = true;
    const origHtml = this.innerHTML;
    this.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 保存中...';
    try {
        const result = await apiClient.request('/api/memories/batch-create-v2', {
            method: 'POST',
            body: JSON.stringify({
                cards: cards.map(c => ({
                    problem: c.problem || '',
                    solution: c.solution || '',
                    insight: c.insight || '',
                    type: c.type || 'tech_knowledge',
                    quality_score: c.quality_score ?? null,
                    quality_reason: c.quality_reason || '',
                })),
            }),
        });
        const errDetail = result.errors && result.errors.length
            ? '. ' + result.errors.map(e => (typeof e === 'string' ? e : e.reason || JSON.stringify(e).slice(0, 60))).join('; ')
            : '';
        toast((result.message || `已保存 ${cards.length} 条记忆`) + errDetail, result.errors && result.errors.length ? 'warning' : 'success');
        bootstrap.Modal.getInstance(document.getElementById('extractReviewModal')).hide();
        _extractCards = [];
        _extractConvIds = [];
        await Promise.all([loadMemories(), loadConversations()]);
        loadConflictCount();
        setTimeout(loadConflictCount, 8000);  // 等异步冲突检测完成
    } catch (e) {
        toast('保存失败: ' + e.message, 'danger');
    } finally {
        this.disabled = false;
        this.innerHTML = origHtml;
    }
});

document.getElementById('extractReviewModal')?.addEventListener('hidden.bs.modal', function() {
    _extractCards = [];
    // 注意：不在此清空 _extractConvIds，否则重提炼 hide() 时会误清
    // _extractConvIds 仅在用户主动关闭（取消）或保存成功后才清空
});

// --- 初始化 ---
document.getElementById('conversation-tab')?.addEventListener('shown.bs.tab', function() {
    loadConversations().catch(e => toast('加载对话记录失败: ' + e.message, 'danger'));
});

document.getElementById('knowledge-tab')?.addEventListener('shown.bs.tab', function() {
    loadUsageStats();
    if (!state._skipTabReload) {
        loadMemories().catch(e => toast('加载知识失败: ' + e.message, 'danger'));
    }
});

async function init() {
    await Promise.all([loadProjects(), loadStatus(), loadBackupStatus()]);
    await Promise.all([loadMemories(), loadConversations()]);
    setInterval(loadStatus, 15000);

    // 首次访问弹出设置向导
    if (!isWizardCompleted()) {
        setTimeout(function() {
            var modal = new bootstrap.Modal('#setupWizardModal');
            modal.show();
        }, 600);
    }

    // 首次加载对话记录为空时延迟重试（解决偶现的首次加载不显示问题）
    setTimeout(async () => {
        if (state.conv.items.length === 0 && state.conv.total === 0) {
            try {
                await loadConversations();
            } catch (_) { /* ignore retry error */ }
        }
    }, 1500);

    // 从通知中心跳转过来的 URL 参数处理
    const params = new URLSearchParams(window.location.search);
    const tab = params.get('tab');
    const filter = params.get('filter');
    const openConflict = params.get('open_conflict');

    if (tab === 'knowledge') {
        const kbTab = document.getElementById('knowledge-tab');
        if (kbTab) {
            setTimeout(() => {
                bootstrap.Tab.getInstance(kbTab)?.show();
                if (filter === 'expired') {
                    filterBySource('expired', 0);
                }
            }, 300);
        }
    }
    if (openConflict === 'true') {
        setTimeout(() => {
            const modal = new bootstrap.Modal(document.getElementById('conflictModal'));
            modal.show();
        }, 500);
    }
}

// ====== 设置向导 ======

var WIZARD_KEY = 'memos_wizard_completed';
var _wizardStep = 0;

function isWizardCompleted() { return localStorage.getItem(WIZARD_KEY) === '1'; }
function markWizardCompleted() { localStorage.setItem(WIZARD_KEY, '1'); }
function resetWizard() { localStorage.removeItem(WIZARD_KEY); }

function goWizardStep(n) {
    _wizardStep = n;
    // 显示/隐藏步骤面板
    for (var i = 0; i < 3; i++) {
        var pane = document.getElementById('wizard-step-' + i);
        if (pane) pane.classList.toggle('d-none', i !== n);
    }
    // 更新进度点
    var dots = document.getElementById('wizard-dots');
    dots.innerHTML = '';
    for (var j = 0; j < 3; j++) {
        var dot = document.createElement('span');
        dot.className = 'wizard-dot';
        if (j < n) dot.classList.add('done');
        else if (j === n) dot.classList.add('active');
        dots.appendChild(dot);
    }
    // 按钮状态
    var prevBtn = document.getElementById('wiz-prev-btn');
    var nextBtn = document.getElementById('wiz-next-btn');
    var skipBtn = document.getElementById('wiz-skip-btn');
    prevBtn.classList.toggle('d-none', n === 0);
    skipBtn.classList.toggle('d-none', n === 2);
    if (n === 2) {
        nextBtn.innerHTML = '完成 <i class="bi bi-check-lg"></i>';
    } else {
        nextBtn.innerHTML = '下一步 <i class="bi bi-chevron-right"></i>';
    }

    // 进入步骤 0 时加载状态
    if (n === 0) loadWizardStatus();
    // 进入步骤 1 时加载已有端点列表
    if (n === 1) loadWizardEndpoints();
}

async function loadWizardStatus() {
    var container = document.getElementById('wizard-status-cards');
    if (!container) return;
    try {
        var data = await api('/api/status');
        var modelOk = data.model_name && data.model_name !== '未下载';
        var dbOk = data.db_size_mb >= 0;
        var llmOk = data.llama_server_ok;
        container.innerHTML =
            '<div class="row g-2">' +
            '<div class="col-md-4">' +
            '<div class="card bg-dark border-secondary h-100"><div class="card-body text-center py-3">' +
            '<i class="bi ' + (modelOk ? 'bi-check-circle text-success' : 'bi-x-circle text-danger') + '" style="font-size:1.5rem;display:block;margin-bottom:.25rem;"></i>' +
            '<div class="small fw-semibold">嵌入模型</div>' +
            '<div class="small text-secondary">' + (modelOk ? data.model_name : '未下载') + '</div>' +
            '</div></div></div>' +
            '<div class="col-md-4">' +
            '<div class="card bg-dark border-secondary h-100"><div class="card-body text-center py-3">' +
            '<i class="bi ' + (dbOk ? 'bi-check-circle text-success' : 'bi-x-circle text-danger') + '" style="font-size:1.5rem;display:block;margin-bottom:.25rem;"></i>' +
            '<div class="small fw-semibold">向量数据库</div>' +
            '<div class="small text-secondary">' + (dbOk ? 'ChromaDB · ' + data.db_size_mb + ' MB' : '异常') + '</div>' +
            '</div></div></div>' +
            '<div class="col-md-4">' +
            '<div class="card bg-dark border-secondary h-100"><div class="card-body text-center py-3">' +
            '<i class="bi ' + (llmOk ? 'bi-check-circle text-success' : 'bi-x-circle text-danger') + '" style="font-size:1.5rem;display:block;margin-bottom:.25rem;"></i>' +
            '<div class="small fw-semibold">LLM 端点</div>' +
            '<div class="small text-secondary">' + (llmOk ? (data.active_endpoint || '已配置') : '未配置') + '</div>' +
            '</div></div></div></div>';
    } catch (e) {
        container.innerHTML = '<div class="text-danger small">系统状态检查失败: ' + e.message + '</div>';
    }
}

async function loadWizardEndpoints() {
    var container = document.getElementById('wiz-existing-endpoints');
    if (!container) return;
    try {
        var data = await api('/api/llm/endpoints');
        var eps = data.endpoints || [];
        if (!eps.length) {
            container.textContent = '';
            return;
        }
        container.innerHTML = '<div class="fw-semibold small mb-1">已配置的端点:</div>' +
            eps.map(function(ep) {
                return '<div class="small text-secondary"><span class="badge bg-secondary me-1">' +
                    (ep.is_active ? '活跃' : '') + '</span>' + ep.name + ' → ' + (ep.api_base || '(无地址)') + '</div>';
            }).join('');
        // 预填表单（有端点时取第一个的 api_base）
        if (!document.getElementById('wiz-api-base').value && eps[0].api_base) {
            document.getElementById('wiz-api-base').value = eps[0].api_base;
            if (eps[0].model) document.getElementById('wiz-model').value = eps[0].model;
        }
    } catch (e) { /* ignore */ }
}

// 向导导航按钮
document.getElementById('wiz-next-btn')?.addEventListener('click', function() {
    if (_wizardStep < 2) {
        goWizardStep(_wizardStep + 1);
    } else {
        markWizardCompleted();
        cleanWizardTempEndpoint();
        bootstrap.Modal.getInstance('#setupWizardModal').hide();
        toast('设置完成，欢迎使用 MEMOS', 'success');
    }
});

document.getElementById('wiz-prev-btn')?.addEventListener('click', function() {
    if (_wizardStep > 0) goWizardStep(_wizardStep - 1);
});

document.getElementById('wiz-skip-btn')?.addEventListener('click', function() {
    markWizardCompleted();
    cleanWizardTempEndpoint();
    bootstrap.Modal.getInstance('#setupWizardModal').hide();
});

// 清理向导临时端点
async function cleanWizardTempEndpoint() {
    try { await api('/api/llm/endpoints/_wizard_temp', { method: 'DELETE' }); } catch (_) { /* 不存在则忽略 */ }
}

// 向导显示时自动加载步骤 0，并清理上次可能遗留的临时端点
document.getElementById('setupWizardModal')?.addEventListener('show.bs.modal', function() {
    goWizardStep(0);
    cleanWizardTempEndpoint();
});

// 测试连接 — 先保存端点到后端，再由后端探测
document.getElementById('wiz-test-conn-btn')?.addEventListener('click', async function() {
    var apiBase = document.getElementById('wiz-api-base').value.trim();
    if (!apiBase) { toast('请输入 API Base URL', 'warning'); return; }
    var btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>测试中...';
    var resultEl = document.getElementById('wiz-test-result');
    resultEl.textContent = '';
    try {
        // 临时创建/更新端点用于测试
        var tempName = '_wizard_temp';
        var modelVal = document.getElementById('wiz-model').value.trim();
        var keyVal = document.getElementById('wiz-api-key').value.trim();
        try {
            await api('/api/llm/endpoints', {
                method: 'POST',
                body: JSON.stringify({ name: tempName, api_base: apiBase, model: modelVal || null, api_key: keyVal || null }),
            });
        } catch (_) {
            // 端点已存在则更新
            try {
                await api('/api/llm/endpoints/' + tempName, {
                    method: 'PUT',
                    body: JSON.stringify({ api_base: apiBase, model: modelVal || null, api_key: keyVal || null }),
                });
            } catch (__) { /* ignore */ }
        }
        var data = await api('/api/llm/test-connection', {
            method: 'POST',
            body: JSON.stringify({ endpoint_id: tempName }),
        });
        if (data.status === 'ok') {
            resultEl.innerHTML = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>连接成功 · ' + data.latency_ms + 'ms</span>';
            document.getElementById('wiz-api-base').dataset.tested = '1';
        } else {
            resultEl.innerHTML = '<span class="text-danger">' + (data.reason || '连接失败') + '</span>';
        }
        // 立即清理临时端点，避免污染提示词模板列表
        await cleanWizardTempEndpoint();
    } catch (e) {
        resultEl.innerHTML = '<span class="text-danger">连接失败: ' + (e.message || '未知错误') + '</span>';
        await cleanWizardTempEndpoint();
    }
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-lightning-charge me-1"></i>测试连接';
});

// 设置菜单 — 重新运行向导
document.getElementById('settings-wizard-btn')?.addEventListener('click', function() {
    resetWizard();
    bootstrap.Modal.getInstance('#settingsModal').hide();
    setTimeout(function() {
        var modal = new bootstrap.Modal('#setupWizardModal');
        modal.show();
    }, 400);
});

// ====== 提示词管理 ======

let _promptTemplates = [];
let _promptEndpoints = [];
let _currentPromptId = null;

async function loadPromptManagement() {
    try {
        const data = await api('/api/prompts');
        _promptTemplates = data.templates || [];
        _promptEndpoints = data.endpoints || [];
        renderPromptTemplateList();
        // 初次打开时自动选中第一个模板
        if (!_currentPromptId && _promptTemplates.length > 0) {
            selectPromptTemplate(_promptTemplates[0].id);
        }
    } catch (e) {
        console.warn('加载提示词模板失败:', e);
    }
}

function showCreatePromptModal() {
    const epSelect = document.getElementById('new-prompt-endpoint');
    epSelect.innerHTML = '<option value="">-- 选择端点 --</option>';
    _promptEndpoints.forEach(ep => {
        epSelect.innerHTML += `<option value="${escHtml(ep.name)}">${escHtml(ep.name)}</option>`;
    });
    document.getElementById('new-prompt-type').value = 'extract';
    document.getElementById('new-prompt-warning').style.display = 'none';
    document.getElementById('new-prompt-create-btn').disabled = false;
    new bootstrap.Modal(document.getElementById('newPromptModal')).show();
}

function checkEndpointTypeAvailable() {
    const ep = document.getElementById('new-prompt-endpoint').value;
    const tp = document.getElementById('new-prompt-type').value;
    const warn = document.getElementById('new-prompt-warning');
    const btn = document.getElementById('new-prompt-create-btn');
    if (!ep) { btn.disabled = true; warn.style.display = 'none'; return; }

    // 模板 ID = 端点@类型，检查是否已存在真实模板（虚拟条目不阻止新建）
    const templateId = ep + '@' + tp;
    const oldFormatId = ep + '-' + tp;
    const exists = _promptTemplates.some(t => !t.is_virtual && (t.id === templateId || t.id === oldFormatId));
    if (exists) {
        const typeNames = {extract:'提炼知识', 'daily-review':'今日回顾'};
        warn.textContent = `端点 "${ep}" 的"${typeNames[tp]||tp}"模板已存在，不可重复创建`;
        warn.style.display = '';
        btn.disabled = true;
    } else {
        warn.style.display = 'none';
        btn.disabled = false;
    }
}

async function createPromptTemplate() {
    const ep = document.getElementById('new-prompt-endpoint').value;
    const tp = document.getElementById('new-prompt-type').value;
    if (!ep) return;
    try {
        const resp = await api('/api/prompts', {
            method: 'POST',
            body: JSON.stringify({ endpoint: ep, template_type: tp }),
        });
        if (resp.id) {
            bootstrap.Modal.getInstance(document.getElementById('newPromptModal')).hide();
            await loadPromptManagement();
            selectPromptTemplate(resp.id);
        }
    } catch (e) {
        alert('创建失败: ' + (e.message || e));
    }
}

function renderPromptTemplateList() {
    const container = document.getElementById('prompt-template-list');
    if (!_promptTemplates.length) {
        container.innerHTML = '<div class="text-center text-secondary small py-3">暂无端点</div>';
        return;
    }
    const typeFilter = document.getElementById('prompt-type-filter')?.value || '';
    const filtered = typeFilter ? _promptTemplates.filter(t => t.template_type === typeFilter) : _promptTemplates;

    // 类型标签颜色映射
    const typeColors = {extract:'bg-info', 'daily-review':'bg-success', 'default':'bg-secondary'};
    const typeNames = {extract:'提炼', 'daily-review':'日报', 'default':'通用'};

    let html = '';
    filtered.forEach(t => {
        const active = t.id === _currentPromptId ? 'active' : '';
        const isVirtual = t.is_virtual;
        const versionBadge = isVirtual
            ? '<span class="badge bg-secondary" style="font-size:.65rem">默认</span>'
            : `<span class="badge bg-secondary" style="font-size:.65rem">v${escHtml(t.active_version)}</span>`;
        const typeBadge = `<span class="badge ${typeColors[t.template_type] || 'bg-secondary'}" style="font-size:.65rem">${typeNames[t.template_type] || t.template_type}</span>`;
        const deleteBtn = isVirtual ? '' :
            `<button class="btn btn-sm btn-outline-danger py-0 px-1" style="font-size:.6rem"
                onclick="event.stopPropagation();deleteSidebarTemplate('${escHtml(t.id)}')" title="删除模板">
                <i class="bi bi-trash"></i></button>`;
        // 第一行左侧：模板名称，无名称则显示 端点+类型
        const typeName = typeNames[t.template_type] || t.template_type;
        const displayName = t.name || (t.endpoint_name + ' ' + typeName);
        html += `<div class="list-group-item list-group-item-action py-1 px-2 ${active}"
            onclick="selectPromptTemplate('${escHtml(t.id)}')">
            <div class="d-flex justify-content-between align-items-center">
                <span style="font-size:.8rem">${escHtml(displayName)}</span>
                <div class="d-flex align-items-center gap-1">${typeBadge}${versionBadge}${deleteBtn}</div>
            </div>
            <small style="color:#ccc">${escHtml(t.endpoint_name || t.id)}</small>
        </div>`;
    });
    container.innerHTML = html;
    document.getElementById('prompt-count-text').textContent =
        `总数: ${filtered.length}`;
}

function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}
function escHtmlAttr(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function selectPromptTemplate(id) {
    _currentPromptId = id;
    renderPromptTemplateList();
    try {
        const t = await api('/api/prompts/' + id);
        document.getElementById('prompt-editor-empty').style.display = 'none';
        document.getElementById('prompt-editor-panel').style.display = 'block';
        document.getElementById('prompt-edit-name').value = t.name || '';
        document.getElementById('prompt-edit-desc').value = t.description || '';
        document.getElementById('prompt-edit-system').value = t.draft?.system_prompt || t.system_prompt_text || '';
        document.getElementById('prompt-edit-chatstyle').value = t.chat_style || 'openai';

        // 显示左侧配置卡片
        document.getElementById('prompt-config-card').style.display = '';

        // 结构化 LLM 参数
        const params = t.parameters || {};
        document.getElementById('prompt-edit-temperature').value = params.temperature ?? 0.1;
        document.getElementById('prompt-temperature-value').textContent = (params.temperature ?? 0.1).toFixed(2);
        document.getElementById('prompt-edit-max-tokens').value = params.max_tokens ?? 2048;
        document.getElementById('prompt-edit-top-p').value = params.top_p ?? 1.0;
        document.getElementById('prompt-top-p-value').textContent = (params.top_p ?? 1.0).toFixed(2);
        const isVirtual = t.is_virtual;
        const saveDraftBtn = document.getElementById('prompt-save-draft-btn');
        const syncActiveBtn = document.getElementById('prompt-sync-to-active-btn');
        const saveConfigBtn = document.getElementById('prompt-save-config-btn');

        if (isVirtual) {
            document.getElementById('prompt-active-ver').textContent = '默认';
            document.getElementById('prompt-draft-status').textContent = '默认模板（待激活）';
            saveDraftBtn.disabled = true;
            saveDraftBtn.title = '默认模板不可编辑草稿';
            syncActiveBtn.disabled = true;
            syncActiveBtn.title = '默认模板无活跃版本';
            saveConfigBtn.disabled = true;
            saveConfigBtn.title = '默认模板不可编辑配置';
            renderVersionHistory([], null);
            _currentVersions = [];
        } else {
            document.getElementById('prompt-active-ver').textContent = t.active_version || '-';
            saveDraftBtn.disabled = false;
            saveDraftBtn.title = '';
            syncActiveBtn.disabled = false;
            syncActiveBtn.title = '将草稿内容覆盖写入当前活跃版本';
            saveConfigBtn.disabled = false;
            saveConfigBtn.title = '';
            if (t.draft && t.versions && t.versions.length > 0) {
                const draftSame = t.draft.system_prompt === t.system_prompt_text;
                document.getElementById('prompt-draft-status').textContent = draftSame ? '与活跃版本一致' : '已修改(待升级)';
            } else {
                document.getElementById('prompt-draft-status').textContent = '-';
            }
            renderVersionHistory(t.versions || [], t.active_version);
            _currentVersions = t.versions || [];
        }
    } catch (e) {
        console.error('加载模板失败:', e);
        toast('加载模板失败: ' + e.message, 'danger');
    }
}

let _currentVersions = [];

function renderVersionHistory(versions, activeVersion) {
    const tbody = document.querySelector('#prompt-version-table tbody');
    if (!versions.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-secondary">暂无版本</td></tr>';
        return;
    }
    tbody.innerHTML = versions.map(v => {
        const isActive = v.version === activeVersion;
        const activateBtn = isActive
            ? `<button class="btn btn-sm btn-outline-secondary py-0 px-1" style="font-size:.65rem;opacity:0.4" disabled title="已是活跃版本">激活</button>`
            : `<button class="btn btn-sm btn-outline-secondary py-0 px-1" style="font-size:.65rem"
                onclick="activateVersion('${escHtml(v.version)}')" title="激活此版本">激活</button>`;
        return `
        <tr>
            <td><code>v${escHtml(v.version)}</code>${isActive ? ' <span class="badge bg-success" style="font-size:.6rem">活跃</span>' : ''}</td>
            <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(v.changelog || '-')}</td>
            <td style="font-size:.7rem">${(v.created_at || '').substring(0, 19).replace('T', ' ')}</td>
            <td>
                ${activateBtn}
                <button class="btn btn-sm btn-outline-warning py-0 px-1" style="font-size:.65rem"
                    onclick="rollbackVersion('${escHtml(v.version)}')" title="基于此版本新建">新建</button>
            </td>
            <td>
                <button class="btn btn-sm btn-outline-danger py-0 px-1" style="font-size:.65rem"
                    onclick="deleteVersion('${escHtml(v.version)}')" title="删除版本"><i class="bi bi-trash"></i></button>
            </td>
        </tr>`;
    }).join('');
}

// 组装 LLM 参数（结构化字段 + 高级 JSON 合并）
function buildLLMParams() {
    const temp = parseFloat(document.getElementById('prompt-edit-temperature').value);
    const maxTokens = parseInt(document.getElementById('prompt-edit-max-tokens').value);
    const topP = parseFloat(document.getElementById('prompt-edit-top-p').value);
    return {temperature: temp, max_tokens: maxTokens, top_p: topP};
}

// 保存草稿（仅 system_prompt）
document.getElementById('prompt-save-draft-btn')?.addEventListener('click', async function() {
    if (!_currentPromptId) return;
    const btn = this;
    btn.disabled = true;
    try {
        await api('/api/prompts/' + _currentPromptId + '/draft', {
            method: 'POST',
            body: JSON.stringify({
                system_prompt: document.getElementById('prompt-edit-system').value,
            }),
        });
        document.getElementById('prompt-draft-status').textContent = '已修改(待升级)';
        toast('草稿已保存（即时生效）', 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
});

// 同步草稿至当前活跃版本（覆盖写入，无需新建版本号）
document.getElementById('prompt-sync-to-active-btn')?.addEventListener('click', async function() {
    if (!_currentPromptId) return;
    const activeVer = document.getElementById('prompt-active-ver').textContent;
    if (activeVer === '默认' || activeVer === '-') {
        toast('当前无活跃版本可同步', 'danger');
        return;
    }
    if (!confirm(`将当前草稿内容覆盖写入活跃版本 v${activeVer}？\n此操作会直接修改该版本文件，不创建新版本号。`)) return;
    const btn = this;
    btn.disabled = true;
    try {
        // 先保存草稿
        await api('/api/prompts/' + _currentPromptId + '/draft', {
            method: 'POST',
            body: JSON.stringify({
                system_prompt: document.getElementById('prompt-edit-system').value,
            }),
        });
        // 同步至活跃版本
        const result = await api('/api/prompts/' + _currentPromptId + '/sync-to-active', {method: 'POST'});
        toast(result.message, 'success');
        document.getElementById('prompt-draft-status').textContent = '与活跃版本一致';
        await selectPromptTemplate(_currentPromptId);
    } catch (e) {
        toast('同步失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
});

// 保存模板配置（公共属性）
document.getElementById('prompt-save-config-btn')?.addEventListener('click', async function() {
    if (!_currentPromptId) return;
    const btn = this;
    btn.disabled = true;
    const params = buildLLMParams();
    if (params === null) { btn.disabled = false; return; }
    try {
        await api('/api/prompts/' + _currentPromptId + '/config', {
            method: 'PUT',
            body: JSON.stringify({
                name: document.getElementById('prompt-edit-name').value,
                description: document.getElementById('prompt-edit-desc').value,
                chat_style: document.getElementById('prompt-edit-chatstyle').value,
                parameters: params,
            }),
        });
        toast('模板配置已保存', 'success');
        await loadPromptManagement();
    } catch (e) {
        toast('保存失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
});

// 升级按钮 → 弹出升级模态框
document.getElementById('prompt-upgrade-btn')?.addEventListener('click', function() {
    if (!_currentPromptId) return;
    const activeVer = document.getElementById('prompt-active-ver').textContent;
    if (activeVer === '默认') {
        // 虚拟模板 fork：首版本固定 1.0.0
        document.getElementById('upgrade-version').value = '1.0.0';
    } else {
        const parts = (activeVer || '1.0.0').split('.');
        document.getElementById('upgrade-version').value = parts[0] + '.' + parts[1] + '.' + (parseInt(parts[2] || 0) + 1);
    }
    document.getElementById('upgrade-changelog').value = '';
    new bootstrap.Modal(document.getElementById('upgradeModal')).show();
});

// 确认升级（真实模板 / 虚拟模板 fork）
document.getElementById('upgrade-confirm-btn')?.addEventListener('click', async function() {
    const version = document.getElementById('upgrade-version').value.trim();
    const changelog = document.getElementById('upgrade-changelog').value.trim();
    if (!version) { toast('请输入版本号', 'danger'); return; }
    const btn = this;
    btn.disabled = true;
    try {
        const activeVer = document.getElementById('prompt-active-ver').textContent;
        if (activeVer === '默认') {
            // 虚拟模板 → 先创建端点专属模板（含配置），再升级为首版本
            const params = buildLLMParams();
            if (params === null) { btn.disabled = false; return; }
            // 从虚拟 ID ({端点}@{类型}) 提取端点名和类型
            let epName = _currentPromptId;
            let tplType = 'extract';
            if (_currentPromptId.includes('@')) {
                const parts = _currentPromptId.split('@');
                epName = parts[0];
                tplType = parts[1] || 'extract';
            }
            const resp = await api('/api/prompts', {
                method: 'POST',
                body: JSON.stringify({
                    endpoint: epName,
                    template_type: tplType,
                    name: document.getElementById('prompt-edit-name').value || _currentPromptId,
                    description: document.getElementById('prompt-edit-desc').value,
                    system_prompt_text: document.getElementById('prompt-edit-system').value,
                    chat_style: document.getElementById('prompt-edit-chatstyle').value,
                    parameters: params,
                }),
            });
            const newId = resp.id || (epName + '@' + tplType);
            const result = await api('/api/prompts/' + newId + '/upgrade', {
                method: 'POST',
                body: JSON.stringify({ version, changelog }),
            });
            bootstrap.Modal.getInstance(document.getElementById('upgradeModal')).hide();
            toast('已从默认模板创建并升级为 v' + result.version, 'success');
            _currentPromptId = newId;
        } else {
            // 真实模板 → 先保存草稿（system_prompt），再升级
            await api('/api/prompts/' + _currentPromptId + '/draft', {
                method: 'POST',
                body: JSON.stringify({
                    system_prompt: document.getElementById('prompt-edit-system').value,
                }),
            });
            const result = await api('/api/prompts/' + _currentPromptId + '/upgrade', {
                method: 'POST',
                body: JSON.stringify({ version, changelog }),
            });
            bootstrap.Modal.getInstance(document.getElementById('upgradeModal')).hide();
            toast(result.message, 'success');
        }
        await selectPromptTemplate(_currentPromptId);
        await loadPromptManagement();
    } catch (e) {
        toast('升级失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
});

// 激活版本（带草稿变更检测）
async function activateVersion(version) {
    if (!_currentPromptId) return;
    const statusText = document.getElementById('prompt-draft-status').textContent;
    let warnMsg = `激活版本 ${version}？当前草稿内容将被替换为该版本的 system_prompt。`;
    if (statusText.includes('已修改')) {
        warnMsg = `⚠️ 草稿有未保存/未升级的修改！\n\n${warnMsg}\n\n建议先「保存草稿」或「升级为新版本」或「同步至版本」保留修改内容。\n\n确定放弃当前草稿修改？`;
    }
    if (!confirm(warnMsg)) return;
    try {
        await api('/api/prompts/' + _currentPromptId + '/activate-version/' + version, {method: 'POST'});
        toast('已激活版本 ' + version, 'success');
        await loadPromptManagement();  // 刷新侧边栏缓存
        await selectPromptTemplate(_currentPromptId);
    } catch (e) {
        toast('激活失败: ' + e.message, 'danger');
    }
}

// 回滚版本
async function rollbackVersion(version) {
    if (!_currentPromptId) return;
    const changelog = prompt('基于 v' + version + ' 新建版本，变更说明（可选）:', '基于 v' + version + ' 新建');
    if (changelog === null) return;
    try {
        const result = await api('/api/prompts/' + _currentPromptId + '/rollback/' + version, {
            method: 'POST',
            body: JSON.stringify({ changelog: changelog || ('基于 v' + version + ' 新建') }),
        });
        toast(result.message, 'success');
        await selectPromptTemplate(_currentPromptId);
        await loadPromptManagement();
    } catch (e) {
        toast('新建失败: ' + e.message, 'danger');
    }
}

// 版本对比
let _diffData = null;
document.getElementById('prompt-diff-btn')?.addEventListener('click', function() {
    if (!_currentPromptId || _currentVersions.length < 2) {
        toast('至少需要 2 个版本才能对比', 'danger');
        return;
    }
    _diffData = _currentVersions;
    const v1sel = document.getElementById('diff-v1-select');
    const v2sel = document.getElementById('diff-v2-select');
    const opts = _diffData.map(v => `<option value="${escHtml(v.version)}">v${escHtml(v.version)}</option>`).join('');
    v1sel.innerHTML = opts;
    v2sel.innerHTML = opts;
    if (_diffData.length >= 2) {
        v1sel.value = _diffData[_diffData.length - 2].version;
        v2sel.value = _diffData[_diffData.length - 1].version;
    }
    document.getElementById('diff-result').textContent = '';
    new bootstrap.Modal(document.getElementById('diffModal')).show();
});

document.getElementById('diff-run-btn')?.addEventListener('click', async function() {
    const v1 = document.getElementById('diff-v1-select').value;
    const v2 = document.getElementById('diff-v2-select').value;
    if (!v1 || !v2) return;
    try {
        const data = await api(`/api/prompts/${_currentPromptId}/diff?v1=${v1}&v2=${v2}`);
        const resultEl = document.getElementById('diff-result');
        if (data.diff) {
            resultEl.textContent = data.diff;
        } else {
            resultEl.textContent = '(无差异)';
        }
    } catch (e) {
        document.getElementById('diff-result').textContent = '对比失败: ' + e.message;
    }
});

// 侧边栏删除模板
async function deleteSidebarTemplate(id) {
    if (!confirm(`确定删除模板 "${id}"？不可恢复，端点将回到默认模板托底。`)) return;
    try {
        await api('/api/prompts/' + id, {method: 'DELETE'});
        toast('模板已删除', 'success');
        if (_currentPromptId === id) {
            _currentPromptId = null;
            document.getElementById('prompt-editor-panel').style.display = 'none';
            document.getElementById('prompt-editor-empty').style.display = 'block';
            document.getElementById('prompt-config-card').style.display = 'none';
        }
        await loadPromptManagement();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

// 删除版本
async function deleteVersion(version) {
    if (!_currentPromptId) return;
    if (!confirm(`确定删除版本 ${version}？不可恢复。`)) return;
    try {
        await api('/api/prompts/' + _currentPromptId + '/versions/' + version, {method: 'DELETE'});
        toast('版本 ' + version + ' 已删除', 'success');
        await loadPromptManagement();  // 刷新侧边栏缓存
        await selectPromptTemplate(_currentPromptId);
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

// 滑块实时显示值
document.getElementById('prompt-edit-temperature')?.addEventListener('input', function() {
    document.getElementById('prompt-temperature-value').textContent = parseFloat(this.value).toFixed(2);
});
document.getElementById('prompt-edit-top-p')?.addEventListener('input', function() {
    document.getElementById('prompt-top-p-value').textContent = parseFloat(this.value).toFixed(2);
});

// 标签页切换时加载数据
document.getElementById('prompts-tab')?.addEventListener('shown.bs.tab', function() {
    loadPromptManagement();
});

// ====== 提炼界面级联选择器 ======

let _allTemplatesCache = [];

async function loadPromptTemplates() {
    try {
        const data = await api('/api/prompts');
        _allTemplatesCache = data.templates || [];
    } catch (e) {
        console.warn('加载提示词模板失败:', e);
    }
}

// 端点选择器变更 → 严格过滤匹配该端点的提示词
document.getElementById('extract-endpoint-select')?.addEventListener('change', function() {
    cascadePromptByEndpoint();
});

function cascadePromptByEndpoint() {
    const epSel = document.getElementById('extract-endpoint-select');
    const ep = epSel.value || '';
    const sel = document.getElementById('extract-prompt-select');
    sel.innerHTML = '';

    if (!_allTemplatesCache.length) {
        sel.innerHTML = '<option value="">(无模板)</option>';
        return;
    }

    // 按端点名查找模板（id = 端点名 或 端点名@类型），无匹配时 fallback 到 default
    let filtered;
    let isFallback = false;
    if (ep) {
        filtered = _allTemplatesCache.filter(t => t.id === ep + '@extract' || t.id === 'default@extract' || t.id === 'default-extract');
        if (!filtered.length) {
            filtered = _allTemplatesCache.filter(t => t.id === 'default@extract' || t.id === 'default-extract');
            isFallback = filtered.length > 0;
        }
    } else {
        filtered = _allTemplatesCache;
    }

    if (!filtered.length && ep) {
        sel.innerHTML = `<option value="">(端点 ${ep} 无专属模板)</option>`;
        return;
    }

    let activeOpt = null;
    filtered.forEach(t => {
        const versions = t.versions || [];
        const prefix = isFallback ? '[默认] ' : '';
        if (versions.length === 0) {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.dataset.version = '';
            opt.textContent = `${prefix}${t.name || t.id} (草稿)`;
            sel.appendChild(opt);
            return;
        }
        versions.forEach(v => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.dataset.version = v.version;
            const activeMark = v.is_active ? ' ✓ 活跃' : '';
            opt.textContent = `${prefix}${t.name || t.id} v${v.version}${activeMark}`;
            sel.appendChild(opt);
            if (v.is_active) {
                activeOpt = opt;
            }
        });
    });

    if (activeOpt) {
        activeOpt.selected = true;
    } else if (sel.options.length > 0) {
        sel.options[0].selected = true;
    }
}

// 提炼结果中显示版本信息 → 更新 extract-v2 响应处理
const _origExtractV2 = window._extractV2Handler;
window._extractV2Handler = function(data) {
    if (_origExtractV2) _origExtractV2(data);
    if (data.prompt_version) {
        const info = document.getElementById('extract-review-info');
        if (info) {
            info.innerHTML += ` | 提示词: ${data.prompt_id || '-'} v${data.prompt_version}`;
        }
    }
};

// ====== 今日回顾 ======

function renderMarkdown(md) {
    if (!md) return '';

    // 1) 整体 HTML 转义（防 XSS），再还原安全标签
    var html = md
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // 代码块 (```...```) — 最先处理，内容维持转义状态
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, function(_, lang, code) {
        return '<pre class="p-2 mb-2" style="background:#1a1d23;border-radius:4px;overflow-x:auto;">'
            + '<code>' + code.trim() + '</code></pre>';
    });

    // 行内代码 `...`
    html = html.replace(/`([^`]+)`/g, function(_, code) {
        return '<code class="px-1" style="background:#1a1d23;border-radius:3px;">' + code + '</code>';
    });

    // 链接 [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener" class="text-info">$1</a>');

    // 标题 — 注意必须在转义后的文本上匹配，&#xx; 不会含 # 所以安全
    html = html.replace(/^### (.+)$/gm, '<h6 class="mb-2 mt-3 text-info">$1</h6>');
    html = html.replace(/^## (.+)$/gm, '<h5 class="mb-2 mt-3 text-info border-bottom pb-1">$1</h5>');
    html = html.replace(/^# (.+)$/gm, '<h4 class="mb-3 mt-2">$1</h4>');

    // 粗体 / 斜体 / 删除线 — 内容已转义，直接包裹安全标签
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/~~(.+?)~~/g, '<s>$1</s>');

    // 表格
    html = html.replace(/((?:^\|.+\|\n?)+)/gm, function(match) {
        var lines = match.trim().split('\n').filter(function(l) { return l.indexOf('|') >= 0; });
        if (lines.length < 2) return match;
        var rows = [];
        var headerDone = false;
        lines.forEach(function(line) {
            if (line.replace(/\|/g, '').replace(/-/g, '').trim() === '') return;
            var cells = line.split('|').filter(function(c) { return c.trim(); });
            if (!cells.length) return;
            var tag = !headerDone ? 'th' : 'td';
            if (!headerDone) headerDone = true;
            rows.push('<tr>' + cells.map(function(c) {
                return '<' + tag + ' class="px-2 py-1" style="border:1px solid #444;font-size:.8rem">'
                    + c.trim() + '</' + tag + '>';
            }).join('') + '</tr>');
        });
        if (!rows.length) return match;
        return '<table class="table table-dark table-sm mb-2" style="width:auto;font-size:.8rem">'
            + '<tbody>' + rows.join('') + '</tbody></table>';
    });

    // 任务列表 - [ ] / - [x]
    html = html.replace(/^- \[([ x])\] (.+)$/gm, function(_, checked, text) {
        return '<div class="form-check mb-1"><input type="checkbox" class="form-check-input" '
            + (checked === 'x' ? 'checked' : '') + ' disabled> '
            + '<label class="form-check-label small">' + text + '</label></div>';
    });

    // 引用 > text
    html = html.replace(/^&gt; (.+)$/gm,
        '<blockquote class="border-start border-info ps-3 py-1 mb-2 text-secondary small" style="border-left-width:3px!important;">$1</blockquote>');

    // 无序列表 - item
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul class="mb-2 small">$1</ul>');

    // 有序列表 1. item（在无序列表之后处理，避免冲突）
    html = html.replace(/^(\d+)\. (.+)$/gm, function(_, num, text) {
        return '<li data-order="' + num + '" style="list-style-type:decimal;margin-left:1.2em;">' + text + '</li>';
    });
    html = html.replace(/((?:<li data-order=.*<\/li>\n?)+)/g, '<ol class="mb-2 small" style="padding-left:0;">$1</ol>');

    // 水平线
    html = html.replace(/^---$/gm, '<hr class="my-2">');

    // 段落（双换行分段，单换行 <br>）
    html = html.replace(/\n\n/g, '</p><p class="mb-2">');
    html = html.replace(/\n/g, '<br>');

    // 包裹顶层 <p>（仅当尚未被包裹）
    if (html.indexOf('<p') !== 0) {
        html = '<p class="mb-2">' + html + '</p>';
    }
    // 确保末尾闭合（修复双换行替换可能留下的未闭合 <p>）
    if (html.lastIndexOf('<p') > html.lastIndexOf('</p>')) {
        html += '</p>';
    }
    return html;
}

function _escapeHtml(text) {
    var d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

// 初始化日期选择器为今天
(function() {
    var now = new Date();
    var today = now.getFullYear() + '-'
        + String(now.getMonth() + 1).padStart(2, '0') + '-'
        + String(now.getDate()).padStart(2, '0');
    var el = document.getElementById('dr-date');
    if (el) el.value = today;
})();

// 填充端点选择器（项目选择统一使用顶部工具栏）
async function loadDRFilters() {
    // 加载 LLM 端点
    try {
        var data = await api('/api/llm/endpoints');
        var epSel = document.getElementById('dr-endpoint-select');
        epSel.innerHTML = '<option value="">(使用默认端点)</option>';
        (data.endpoints || []).forEach(function(ep) {
            var opt = document.createElement('option');
            opt.value = ep.name;
            opt.textContent = ep.name + (ep.is_active ? ' ✓' : '');
            if (ep.is_active) opt.selected = true;
            epSel.appendChild(opt);
        });
    } catch (e) {
        console.warn('加载 LLM 端点列表失败:', e);
    }
}

// ==== v0.4.5 R1 冲突检测增强（配对数据 + 4 选项解决 + 统计） ====
async function loadConflictList() {
    var list = document.getElementById('conflict-list');
    var countEl = document.getElementById('conflict-modal-count');
    list.innerHTML = '<div class="text-muted small text-center py-3">加载中...</div>';
    try {
        var data = await api('/api/conflicts?limit=100');
        var pairs = data.pairs || [];
        var statEl = document.getElementById('stat-conflict');
        statEl.textContent = pairs.length;
        statEl.style.color = pairs.length > 0 ? '#dc3545' : '';
        if (countEl) countEl.textContent = '(' + pairs.length + ' 条待处理)';
        if (pairs.length === 0) {
            list.innerHTML = '<div class="text-muted small text-center py-3">所有记忆一致无矛盾</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < pairs.length; i++) {
            var p = pairs[i];
            var nm = p.new_memory || {};
            var em = p.existing_memory || {};
            var reason = p.reason || '未知';
            var sim = p.similarity ? (p.similarity).toFixed(2) : '?';
            var pairId = p.id || nm.id || '';
            html += '<div class="card mb-3 border-danger" id="conflict-pair-' + pairId + '">';
            html += '<div class="card-body py-2 px-3">';
            // VS 对比布局
            html += '<div class="row g-2">';
            // 新记忆（左侧）
            html += '<div class="col-5">';
            html += '<div class="small fw-semibold text-success">新记忆</div>';
            html += '<div class="p-2 rounded small bg-opacity-10 bg-success" style="min-height:60px;border:1px solid #19875433">';
            html += '<div class="text-truncate mb-1"><b>' + escHtml(nm.type || '?') + '</b> <span class="text-secondary">' + escHtml(formatTime(nm.created_at)) + '</span></div>';
            html += '<div>' + escHtml((nm.content || '').substring(0, 120)) + '</div>';
            html += '</div></div>';
            // VS 图标（中间）
            html += '<div class="col-2 d-flex align-items-center justify-content-center">';
            html += '<span class="badge bg-secondary" style="font-size:.9rem">VS</span>';
            html += '</div>';
            // 旧记忆（右侧）
            html += '<div class="col-5">';
            html += '<div class="small fw-semibold text-secondary">已有记忆 <span class="text-muted">(相似度 ' + sim + ')</span></div>';
            html += '<div class="p-2 rounded small bg-opacity-10 bg-secondary" style="min-height:60px;border:1px solid #6c757d33">';
            html += '<div class="text-truncate mb-1"><b>' + escHtml(em.type || '?') + '</b> <span class="text-secondary">' + escHtml(formatTime(em.created_at)) + '</span></div>';
            html += '<div>' + escHtml((em.content || '').substring(0, 120)) + '</div>';
            html += '</div></div></div>';
            // LLM 原因
            html += '<div class="mt-2 small"><i class="bi bi-chat-quote text-warning me-1"></i><span class="text-warning">' + escHtml(reason) + '</span></div>';
            // 4 个操作按钮
            html += '<div class="d-flex gap-1 mt-2 flex-wrap">';
            html += '<button class="btn btn-sm btn-outline-success" onclick="resolveConflict(\'' + pairId + '\',\'overwrite\')">覆盖旧记忆</button>';
            html += '<button class="btn btn-sm btn-outline-info" onclick="resolveConflict(\'' + pairId + '\',\'keep_both\')">保留两者</button>';
            html += '<button class="btn btn-sm btn-outline-primary" onclick="editConflictMemory(\'' + pairId + '\',\'' + escHtmlAttr(nm.content || '') + '\')">修改新记忆</button>';
            html += '<button class="btn btn-sm btn-outline-danger" onclick="resolveConflict(\'' + pairId + '\',\'discard\')">放弃新记忆</button>';
            html += '</div></div></div>';
        }
        // 底部统计行
        try {
            var statsData = await api('/api/conflicts/stats');
            var decisions = statsData.decisions || {};
            var totalLogs = statsData.total || 0;
            if (totalLogs > 0) {
                html += '<div class="small text-secondary mt-2 pt-2 border-top text-center">';
                html += '已解决 <b>' + totalLogs + '</b> 条（';
                var parts = [];
                var decisionLabels = {overwrite:'覆盖', keep_both:'保留', edit:'修改', discard:'放弃'};
                for (var d in decisionLabels) {
                    if (decisions[d]) {
                        parts.push(decisionLabels[d] + ' ' + decisions[d]);
                    }
                }
                html += parts.join(' / ');
                html += '）</div>';
            }
        } catch(e) { /* stats not critical */ }
        list.innerHTML = html;
    } catch(e) {
        list.innerHTML = '<div class="text-danger small text-center py-3">加载失败</div>';
        console.error('conflict list error:', e);
    }
}

// 冲突弹窗内是否有操作（关闭时据此刷新知识列表）
var _conflictDirty = false;

async function resolveConflict(pairId, action) {
    try {
        if (action === 'discard') {
            await api('/api/conflicts/' + pairId + '/discard', {method:'POST'});
        } else {
            await api('/api/conflicts/' + pairId + '/resolve?action=' + action, {method:'POST'});
        }
        var card = document.getElementById('conflict-pair-' + pairId);
        if (card) card.remove();
        _conflictDirty = true;
        setTimeout(loadConflictList, 500);
        setTimeout(loadConflictCount, 500);
    } catch(e) { console.error(e); }
}

async function editConflictMemory(pairId, content) {
    // 弹出 prompt 输入框让用户修改内容
    var newContent = prompt('修改新记忆内容：', content);
    if (newContent === null || newContent.trim() === '') return;
    try {
        await api('/api/conflicts/' + pairId + '/resolve?action=edit', {
            method:'POST',
            body: JSON.stringify({content: newContent.trim()}),
            headers: {'Content-Type': 'application/json'}
        });
        var card = document.getElementById('conflict-pair-' + pairId);
        if (card) card.remove();
        _conflictDirty = true;
        setTimeout(loadConflictList, 500);
        setTimeout(loadConflictCount, 500);
    } catch(e) { console.error(e); }
}

// 弹窗关闭时自动刷新知识列表和统计
document.getElementById('conflictModal')?.addEventListener('hidden.bs.modal', function() {
    if (_conflictDirty) {
        _conflictDirty = false;
        loadMemories().catch(function(){});
        loadConflictCount();
        loadUsageStats();
    }
});

// 点击统计数值打开弹窗
document.getElementById('stat-conflict')?.addEventListener('click', function(e) {
    e.preventDefault();
    var modal = new bootstrap.Modal(document.getElementById('conflictModal'));
    modal.show();
});
// 弹窗打开时加载冲突列表
document.getElementById('conflictModal')?.addEventListener('show.bs.modal', loadConflictList);
// 刷新按钮
document.getElementById('conflict-refresh-btn')?.addEventListener('click', loadConflictList);
// 加载冲突统计数（页面加载 + 定时轮询）
async function loadConflictCount() {
    try {
        var data = await api('/api/conflicts/count');
        var count = data.count || 0;
        var statEl = document.getElementById('stat-conflict');
        if (statEl) {
            statEl.textContent = count;
            statEl.style.color = count > 0 ? '#dc3545' : '';
        }
    } catch(e) {}
}
loadConflictCount();
setInterval(loadConflictCount, 30000);  // 每30秒轮询

// ==== v0.4.1 用量统计 ====
async function loadUsageStats() {
    try {
        var today = await apiClient.request('/api/stats/usage?period=today');
        var week = await apiClient.request('/api/stats/usage?period=week');
        var fmtLink = function(label, src, days) {
            var d = days ? '&days=' + days : '';
            return '<a href=\"#\" class=\"v41-stat-link\" onclick=\"filterBySource(\'' + src + '\', ' + (days || 0) + ');return false;\">' + label + '</a>';
        };
        var fmtBreakdown = function(d, days) {
            return fmtLink('手工: ' + (d.manual_cards || 0), 'manual', days) + ' &middot; ' + fmtLink('自动: ' + (d.auto_cards || 0), 'auto', days);
        };
        document.getElementById('stat-today').innerHTML = fmtBreakdown(today, 1);
        document.getElementById('stat-week').innerHTML = fmtBreakdown(week, 7);
        var expEl = document.getElementById('stat-expiring');
        var expCount = today.expiring_soon || 0;
        expEl.innerHTML = expCount > 0 ? fmtLink(expCount, 'expiring_soon') : '0';
        expEl.style.color = expCount > 0 ? '#f0ad4e' : '';
        var expdEl = document.getElementById('stat-expired');
        var expdCount = today.expired || 0;
        expdEl.innerHTML = expdCount > 0 ? fmtLink(expdCount, 'expired') : '0';
        expdEl.style.color = expdCount > 0 ? '#dc3545' : '';
        var totalTokens = week.total_tokens || 0;
        document.getElementById('stat-tokens').textContent = totalTokens > 1000 ? (totalTokens/1000).toFixed(1) + 'K' : totalTokens;
        var rateEl = document.getElementById('stat-rate');
        rateEl.textContent = week.success_rate + '%';
        rateEl.onclick = function() { loadTrendChart(); var m = new bootstrap.Modal(document.getElementById('trendModal')); m.show(); return false; };
    } catch(e) { console.error('stats error:', e); }
}
loadUsageStats();
setInterval(loadUsageStats, 60000);  // 每60秒刷新统计

// ==== v0.4.1 趋势双轴图 ====
async function loadTrendChart() {
    var container = document.getElementById('trend-chart-container');
    try {
        var data = await apiClient.request('/api/stats/trend?days=7');
        var trend = data.trend || [];
        if (!trend.length) { container.innerHTML = '<div class="text-muted small text-center py-5">暂无数据</div>'; return; }

        var maxCount = Math.max.apply(null, trend.map(function(d){return d.count;})) || 1;
        var yMax = Math.ceil(maxCount * 1.1);          // 左轴：7天最大值 * 110%
        if (yMax < 4) yMax = 4;                         // 最少显示 4 格

        // Y 轴标签（顶部→底部 = yMax→0）
        var leftLabels = '', rightLabels = '';
        for (var g = 4; g >= 0; g--) {
            leftLabels += '<span>' + Math.round(yMax * g / 4) + '</span>';
            rightLabels += '<span>' + Math.round(100 * g / 4) + '</span>';
        }

        // 柱状图 + 日期，统一坐标系
        var barsHtml = '<div class="v41-trend-bars">';
        var datesHtml = '<div class="v41-trend-dates">';
        var pts = [];
        for (var i = 0; i < trend.length; i++) {
            var d = trend[i];
            var hPct = yMax > 0 ? Math.round(d.count / yMax * 100) : 0;
            var cls = d.count === 0 ? ' zero' : '';
            barsHtml += '<div class="v41-trend-bar-group">' +
                '<div class="v41-trend-bar' + cls + '" style="height:' + hPct + '%" title="周' + d.weekday + ' ' + d.date + ': ' + d.count + ' 次调用, 成功率 ' + d.success_rate + '%">' +
                '<span class="v41-trend-count">' + d.count + '</span></div></div>';
            datesHtml += '<span>' + d.date + '</span>';

            // SVG 折线坐标（x=每柱中心, y=成功率映射到 0-100 顶部起算）
            var xPct = ((i + 0.5) / trend.length * 100).toFixed(1);
            var yPct = (100 - trend[i].success_rate).toFixed(1);
            pts.push(xPct + '%' + ',' + yPct + '%');
        }
        barsHtml += '</div>';
        datesHtml += '</div>';

        // 虚线网格（5 条 = 4 格，top:0% 到 top:100%）
        var gridHtml = '<div class="v41-trend-grid">';
        for (var k = 0; k <= 4; k++) {
            gridHtml += '<div style="position:absolute;top:' + (k * 25) + '%;left:0;right:0;border-top:1px dashed #dee2e6;"></div>';
        }
        gridHtml += '</div>';

        // SVG 折线（与 chart 等高等宽，% 坐标，无拉伸）
        var svgHtml = '<svg class="v41-trend-line-svg" xmlns="http://www.w3.org/2000/svg">' +
            '<polyline points="' + pts.join(' ') + '" fill="none" stroke="#dc3545" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>';
        for (var k2 = 0; k2 < trend.length; k2++) {
            var cx = ((k2 + 0.5) / trend.length * 100).toFixed(1);
            var cy = (100 - trend[k2].success_rate).toFixed(1);
            svgHtml += '<circle cx="' + cx + '%" cy="' + cy + '%" r="3" fill="#dc3545" vector-effect="non-scaling-stroke"><title>周' + trend[k2].weekday + ' ' + trend[k2].date + ': 成功率 ' + trend[k2].success_rate + '%</title></circle>';
        }
        svgHtml += '</svg>';

        container.innerHTML = '<div class="v41-trend-wrap">' +
            '<div class="v41-trend-yaxis">' + leftLabels + '</div>' +
            '<div class="v41-trend-chart">' + gridHtml + barsHtml + svgHtml + '</div>' +
            '<div class="v41-trend-yaxis">' + rightLabels + '</div>' +
            '</div>' + datesHtml;
    } catch(e) { console.error('trend chart error:', e); container.innerHTML = '<div class="text-danger small text-center py-5">加载失败</div>'; }
}

document.getElementById('daily-review-tab')?.addEventListener('shown.bs.tab', async function() {
    loadDRFilters();
    await loadPromptTemplates();  // 预加载模板缓存供提示词下拉使用
    cascadeDailyReviewPrompt();
});

// 加载计时器
function startDRLoadingTimer() {
    state.dr.loadingSeconds = 0;
    document.getElementById('dr-loading-timer').textContent = '已等待 0s';
    clearInterval(state.dr.loadingTimer);
    state.dr.loadingTimer = setInterval(function() {
        state.dr.loadingSeconds++;
        var s = state.dr.loadingSeconds;
        if (s < 60) {
            document.getElementById('dr-loading-timer').textContent = '已等待 ' + s + 's';
        } else {
            var m = Math.floor(s / 60);
            var sec = s % 60;
            document.getElementById('dr-loading-timer').textContent =
                '已等待 ' + m + 'm' + String(sec).padStart(2, '0') + 's';
        }
    }, 1000);
}

function stopDRLoadingTimer() {
    clearInterval(state.dr.loadingTimer);
    state.dr.loadingTimer = null;
}

// 今日回顾面板端点选择器变更 → 级联提示词下拉
document.getElementById('dr-endpoint-select')?.addEventListener('change', function() {
    cascadeDailyReviewPrompt();
});

function cascadeDailyReviewPrompt() {
    const epSel = document.getElementById('dr-endpoint-select');
    const ep = epSel.value || '';
    const sel = document.getElementById('dr-prompt-select');
    sel.innerHTML = '';
    sel.appendChild(Object.assign(document.createElement('option'), {value: '', textContent: '(自动选择提示词)'}));

    if (!_allTemplatesCache.length) return;

    // 筛选 daily-review 类型且匹配当前端点的模板
    let filtered;
    if (ep) {
        filtered = _allTemplatesCache.filter(t =>
            t.template_type === 'daily-review' && (t.id === ep + '@daily-review' || t.id === 'default@daily-review')
        );
        if (!filtered.length) {
            // fallback: 显示所有 daily-review 类型模板
            filtered = _allTemplatesCache.filter(t => t.template_type === 'daily-review');
        }
    } else {
        filtered = _allTemplatesCache.filter(t => t.template_type === 'daily-review');
    }

    if (!filtered.length && ep) {
        sel.innerHTML = '<option value="">(无可用提示词)</option>';
        return;
    }

    filtered.forEach(t => {
        const versions = t.versions || [];
        if (versions.length === 0) {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.dataset.version = '';
            opt.textContent = (t.name || t.id) + ' (草稿)';
            sel.appendChild(opt);
        } else {
            versions.forEach(v => {
                const opt = document.createElement('option');
                opt.value = t.id;
                opt.dataset.version = v.version;
                opt.textContent = (t.name || t.id) + ' v' + v.version + (v.is_active ? ' ✓' : '');
                sel.appendChild(opt);
            });
        }
    });
}

// 生成日报
document.getElementById('dr-generate-btn')?.addEventListener('click', async function() {
    var date = document.getElementById('dr-date').value;
    if (!date) { toast('请选择日期', 'warning'); return; }

    var isOK = await checkLLMStatus();
    if (!isOK) {
        toast('LLM 服务当前离线，无法生成日报', 'warning');
        return;
    }

    var btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>生成中...';

    var loadingEl = document.getElementById('dr-loading');
    var reportContainer = document.getElementById('dr-report-container');
    var emptyEl = document.getElementById('dr-empty');
    var errorEl = document.getElementById('dr-error');

    loadingEl.classList.remove('d-none');
    reportContainer.classList.add('d-none');
    emptyEl.classList.add('d-none');
    errorEl.classList.add('d-none');
    document.getElementById('dr-info').textContent = '';
    ['dr-preview-btn','dr-copy-btn','dr-save-btn','dr-regenerate-btn','dr-extract-todos-btn'].forEach(function(id){
        document.getElementById(id).disabled = true;
    });

    startDRLoadingTimer();

    try {
        var endpointName = document.getElementById('dr-endpoint-select').value || '';
        var promptSel = document.getElementById('dr-prompt-select');
        var promptId = promptSel.value || '';
        var promptVersion = promptSel.selectedOptions[0]?.dataset?.version || null;

        var body = { date: date, project_id: window.state?.currentProject || null };
        if (endpointName) body.llm_endpoint = endpointName;
        if (promptId) body.prompt_id = promptId;
        if (promptVersion) body.prompt_version = promptVersion;

        var controller = new AbortController();
        var timeoutId = setTimeout(function() { controller.abort(); }, 600000);

        var data = await apiClient.request('/api/conversations/daily-review', {
            method: 'POST',
            signal: controller.signal,
            body: JSON.stringify(body),
        });
        clearTimeout(timeoutId);

        loadingEl.classList.add('d-none');
        stopDRLoadingTimer();

        if (data.report) {
            state.dr.report = data.report;
            state.dr.reportDate = date;
            state.dr.projectId = window.state?.currentProject || null;
            document.getElementById('dr-info').textContent =
                '共 ' + data.conversation_count + ' 条对话记录';
            document.getElementById('dr-report-content').innerHTML = renderMarkdown(data.report);
            reportContainer.classList.remove('d-none');
            ['dr-preview-btn','dr-copy-btn','dr-save-btn','dr-regenerate-btn','dr-extract-todos-btn'].forEach(function(id){
                document.getElementById(id).disabled = false;
            });
        } else {
            if (data.conversation_count === 0) {
                emptyEl.querySelector('p').textContent = date + ' 没有对话记录';
                emptyEl.classList.remove('d-none');
            } else {
                document.getElementById('dr-info').textContent = data.message || '未生成内容';
            }
        }
    } catch (e) {
        loadingEl.classList.add('d-none');
        stopDRLoadingTimer();
        if (e.name === 'AbortError') {
            errorEl.textContent = '生成超时，请稍后重试';
        } else {
            errorEl.textContent = '生成失败: ' + e.message;
        }
        errorEl.classList.remove('d-none');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-magic me-1"></i>生成日报';
    }
});

// 复制日报
document.getElementById('dr-copy-btn')?.addEventListener('click', function() {
    if (state.dr.report) {
        copyToClipboard(state.dr.report, '日报');
    }
});

// 保存日报到文件
document.getElementById('dr-save-btn')?.addEventListener('click', async function() {
    if (!state.dr.report) return;
    var btn = this;
    btn.disabled = true;
    try {
        var data = await apiClient.request('/api/conversations/daily-review/save', {
            method: 'POST',
            body: JSON.stringify({
                report: state.dr.report,
                date: state.dr.reportDate || document.getElementById('dr-date').value,
                project_id: state.dr.projectId || window.state?.currentProject || null,
            }),
        });
        toast(data.message, 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
});

// 重新生成
document.getElementById('dr-regenerate-btn')?.addEventListener('click', function() {
    document.getElementById('dr-generate-btn').click();
});
document.getElementById('dr-extract-todos-btn')?.addEventListener('click', async function() {
    var date = document.getElementById('dr-date').value;
    if (!date) { toast('请先生成日报', 'warning'); return; }
    try {
        this.disabled = true;
        this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>提取中...';
        var resp = await apiClient.request('/api/conversations/extract-todos', {
            method: 'POST',
            body: JSON.stringify({date: date, project_id: window.state?.currentProject || null, report_text: state.dr.report || null}),
            headers: {'Content-Type': 'application/json'}
        });
        var msg = resp.message || '提取完成';
        toast(msg, 'success');
        if (resp.total > 0) {
            // 跳转到待办面板
            var todoTab = document.getElementById('todo-tab');
            if (todoTab) todoTab.click();
        }
    } catch(e) {
        toast('提取失败: ' + (e.message || e), 'danger');
    } finally {
        this.disabled = false;
        this.innerHTML = '<i class="bi bi-list-check me-1"></i>提取待办事项';
    }
});

// 复制请求
document.getElementById('dr-preview-btn')?.addEventListener('click', async function() {
    var date = document.getElementById('dr-date').value;
    if (!date) return;
    var btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>加载中...';
    try {
        var endpointName = document.getElementById('dr-endpoint-select').value || '';
        var promptSel = document.getElementById('dr-prompt-select');
        var promptId = promptSel.value || '';
        var promptVersion = promptSel.selectedOptions[0]?.dataset?.version || null;
        var body = { date: date, project_id: window.state?.currentProject || null };
        if (endpointName) body.llm_endpoint = endpointName;
        if (promptId) body.prompt_id = promptId;
        if (promptVersion) body.prompt_version = promptVersion;

        var data = await apiClient.request('/api/conversations/daily-review/preview', {
            method: 'POST',
            body: JSON.stringify(body),
        });

        copyToClipboard(JSON.stringify(data, null, 2), 'API 请求 JSON');
    } catch (e) {
        toast('获取预览失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-eye me-1"></i>复制请求';
});

init();
