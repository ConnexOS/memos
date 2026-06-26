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

    // F6: 记忆管理
    mm: {
        items: [],
        total: 0,
        page: 1,
        pageSize: 20,
        typeFilter: '',      // '' = 全部
        searchQuery: '',
        statusFilter: 'active',  // active / forgotten
    },

    // v0.7.1 简报聚合视图
    brf: {
        items: [],
        total: 0,
        page: 1,
        pageSize: 20,
        statsCache: null,
        latestDate: null,
        _todayData: null,
        _sseTimer: null,
        _allExpanded: false,
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

// 相对时间格式（所有面板统一使用）
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

// v0.7.1: P10 — 面板加载去重标记，防止事件重复触发
var _panelLoaded = {};

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
// F6: v0.6.0 新 6 类型
const NEW_TYPE_LABELS = {
    'solution': '方案',
    'decision': '决策',
    'lesson': '经验',
    'process': '流程',
    'task': '任务',
    'briefing': '简报',
};
const NEW_TYPE_COLORS = {
    'solution': 'bg-success',
    'decision': 'bg-warning',
    'lesson': 'bg-info',
    'process': 'bg-primary',
    'task': 'bg-secondary',
    'briefing': 'bg-danger',
};

// F6: 旧版 7 类型列表（用于标记 旧版 徽章）
const LEGACY_TYPES = ['fact', 'preference', 'bug_fix', 'feature_design', 'code_optimize', 'tech_knowledge'];

// v0.7.1: Task 和简报类型由专用面板管理，从记忆管理查询中剔除
const NEW_6_TYPES = ['solution', 'decision', 'lesson', 'process'];

// 全量类型列表（用于查询全部）
const ALL_MM_TYPES = [...NEW_6_TYPES, ...LEGACY_TYPES];

function getTypeLabel(type) {
    return NEW_TYPE_LABELS[type] || TYPE_LABELS[type] || type || '?';
}

function getTypeColor(type) {
    return NEW_TYPE_COLORS[type] || TYPE_COLORS[type] || 'bg-secondary';
}

function isLegacyType(type) {
    return LEGACY_TYPES.includes(type);
}

function formatDate(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

// v0.7.1: 骨架屏加载态
function renderSkeleton(type) {
    if (type === 'list') {
        return '<div class="p-3">' +
            '<div class="skeleton skeleton-line"></div>' +
            '<div class="skeleton skeleton-line"></div>' +
            '<div class="skeleton skeleton-line"></div>' +
            '</div>';
    }
    if (type === 'card') {
        return '<div class="p-3">' +
            '<div class="skeleton skeleton-card"></div>' +
            '<div class="skeleton skeleton-card"></div>' +
            '</div>';
    }
    return '<div class="p-3"><div class="skeleton skeleton-line"></div></div>';
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
        ['solution', 'decision', 'lesson', 'process', 'task', 'briefing'].forEach(t => params.append('type', t));
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
    // F6: 旧面板已替换，如果元素不存在则静默跳过
    if (!tbody) return;
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
    if (!nav) return;
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
// v0.7.1: Task 和简报由专用面板管理，从手工添加选项中移除
const TYPE_OPTIONS = [
    {value: 'solution', label: '方案'},
    {value: 'decision', label: '决策'},
    {value: 'lesson', label: '经验教训'},
    {value: 'process', label: '流程'},
];

function _getTypeGroup(type) {
    return TYPE_OPTIONS;
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
        await Promise.all([loadMemories(), loadConversations(), loadMemoryManagement()]);
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
        // 使用本地时区解释日期（new Date("YYYY-MM-DD") 会按 UTC 午夜处理，导致 UTC+8 上午 8 点前的记录被漏掉）
        const dateFrom = dateFromStr ? new Date(dateFromStr + 'T00:00:00').getTime() / 1000 : null;
        const dateTo = dateToStr ? new Date(dateToStr + 'T23:59:59').getTime() / 1000 : null;
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
        // 优先级链: localStorage → CWD → 第一个项目 → 空
        const saved = localStorage.getItem('memos_default_project');
        const candidates = [
            saved,
            data.current_project,
            state.projects[0]?.project_id,
        ];
        let selected = null;
        for (const pid of candidates) {
            if (pid && [...sel.options].some(o => o.value === pid)) {
                selected = pid;
                break;
            }
        }
        if (selected) {
            sel.value = selected;
            state.currentProject = selected;
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
    if (!label) return;
    const cur = state.projects.find(p => p.project_id === state.currentProject);
    if (cur) {
        label.textContent = `知识库: ${cur.knowledge_count || 0} 条`;
    } else {
        label.textContent = '';
    }
}

// ====== F6: 记忆管理 ======

async function loadMemoryManagement() {
    const params = new URLSearchParams();
    params.set('limit', state.mm.pageSize);
    params.set('offset', (state.mm.page - 1) * state.mm.pageSize);
    params.set('status', state.mm.statusFilter);

    // 类型过滤
    if (state.mm.typeFilter) {
        params.append('type', state.mm.typeFilter);
    } else {
        // 全部类型
        ALL_MM_TYPES.forEach(t => params.append('type', t));
    }

    // 搜索 keyword 使用 /api/search 而非 /api/memories
    if (state.mm.searchQuery.trim()) {
        await _searchMemories(state.mm.searchQuery.trim());
        return;
    }

    try {
        const data = await apiClient.request(`/api/memories?${params}`);
        state.mm.items = data.memories || [];
        state.mm.total = data.total || 0;
        renderMemoryCards();
        renderMMPagination();
        updateMMStatusLabel();
        updateMMStats();
    } catch (e) {
        document.getElementById('mm-card-container').innerHTML =
            `<div class="col-12 text-center text-danger small py-4">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

async function _searchMemories(query) {
    try {
        const data = await apiClient.request('/api/search', {
            method: 'POST',
            body: JSON.stringify({
                query,
                top_k: 50,
                hybrid: true,
                bm25_weight: 0.7,
                type_filter: state.mm.typeFilter || null,
            }),
        });
        // 转换搜索结果格式为记忆列表格式
        state.mm.items = (data.results || []).map(r => ({
            id: r.id,
            document: r.document,
            metadata: r.metadata || {},
        }));
        state.mm.total = state.mm.items.length;
        renderMemoryCards();
        renderMMPagination();
        updateMMStatusLabel();
    } catch (e) {
        document.getElementById('mm-card-container').innerHTML =
            `<div class="col-12 text-center text-danger small py-4">搜索失败: ${escapeHtml(e.message)}</div>`;
    }
}

function renderMemoryCards() {
    const container = document.getElementById('mm-card-container');
    const items = state.mm.items;
    if (!items || items.length === 0) {
        container.innerHTML = '<div class="col-12 text-center text-secondary py-5"><i class="bi bi-inbox" style="font-size:2rem;display:block;margin-bottom:.5rem;"></i><span>暂无记忆</span></div>';
        return;
    }

    container.innerHTML = items.map((m, idx) => {
        const meta = m.metadata || {};
        const type = meta.type || 'unknown';
        const typeLabel = getTypeLabel(type);
        const typeColor = getTypeColor(type);
        const isLegacy = isLegacyType(type);
        const ts = meta.timestamp;

        // 全文显示（不截断）
        const displayText = m.document || '';

        // 计算距离遗忘自动归档天数
        let countdownHtml = '';
        if (state.mm.statusFilter === 'forgotten' && meta.forgotten_at) {
            const daysSinceForgotten = (Date.now() / 1000 - meta.forgotten_at) / 86400;
            const archiveDays = 25;
            const daysLeft = Math.max(0, Math.ceil(archiveDays - daysSinceForgotten));
            if (daysLeft > 0) {
                countdownHtml = `<span class="badge bg-warning text-dark ms-1">${daysLeft} 天后自动归档</span>`;
            } else {
                countdownHtml = `<span class="badge bg-danger ms-1">即将自动归档</span>`;
            }
        }

        // 动作按钮
        let actionBtns = '';
        if (state.mm.statusFilter === 'forgotten') {
            actionBtns = `
                <button class="btn btn-sm btn-outline-success mm-restore-btn py-0 px-1" data-id="${escapeHtml(m.id)}" title="恢复"><i class="bi bi-arrow-counterclockwise"></i></button>
                <button class="btn btn-sm btn-outline-danger mm-delete-btn py-0 px-1" data-id="${escapeHtml(m.id)}" title="永久删除"><i class="bi bi-trash"></i></button>
                <button class="btn btn-sm btn-outline-warning mm-archive-btn py-0 px-1" data-id="${escapeHtml(m.id)}" title="永久归档"><i class="bi bi-archive"></i></button>
            `;
        } else {
            actionBtns = `
                <button class="btn btn-sm btn-outline-info mm-detail-btn py-0 px-1" data-id="${escapeHtml(m.id)}" title="查看/编辑"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-warning mm-forget-btn py-0 px-1" data-id="${escapeHtml(m.id)}" title="遗忘"><i class="bi bi-eye-slash"></i></button>
                <button class="btn btn-sm btn-outline-danger mm-delete-btn py-0 px-1" data-id="${escapeHtml(m.id)}" title="永久删除"><i class="bi bi-trash"></i></button>
            `;
        }

        return `<div class="col-12 mb-2">
            <div class="card h-100 mm-card" data-id="${escapeHtml(m.id)}">
                <div class="card-body py-2 px-3">
                    <div class="d-flex justify-content-between align-items-start mb-1">
                        <div class="d-flex gap-1 align-items-center flex-wrap">
                            <span class="badge ${typeColor} bg-opacity-25 ${typeColor.replace('bg-', 'text-')}">${escapeHtml(typeLabel)}</span>
                            ${isLegacy ? '<span class="badge bg-secondary bg-opacity-25 text-secondary" style="font-size:.65rem;">旧版</span>' : ''}
                            ${countdownHtml}
                            <span class="small text-secondary" style="font-size:.75rem;">${formatTime(ts)}</span>
                        </div>
                        <div class="d-flex gap-1 flex-shrink-0">
                            ${actionBtns}
                        </div>
                    </div>
                    <div class="mm-card-content small" style="word-break:break-word;">${escapeHtml(displayText)}</div>
                </div>
            </div>
        </div>`;
    }).join('');
}

// --- 分页 ---
function renderMMPagination() {
    const nav = document.getElementById('mm-pagination-nav');
    if (!nav) return;
    if (state.mm.total === 0 && state.mm.page === 1) {
        nav.innerHTML = '';
        return;
    }
    const totalPages = Math.ceil(state.mm.total / state.mm.pageSize) || 1;
    let html = `<span class="small text-secondary me-2">${state.mm.page}/${totalPages}</span>`;
    html += `<button class="btn btn-sm btn-outline-secondary py-0" onclick="goMMPage(${state.mm.page - 1})" ${state.mm.page <= 1 ? 'disabled' : ''}><i class="bi bi-chevron-left"></i></button>`;
    const start = Math.max(1, state.mm.page - 2);
    const end = Math.min(totalPages, state.mm.page + 2);
    for (let p = start; p <= end; p++) {
        html += `<button class="btn btn-sm ${p === state.mm.page ? 'btn-primary' : 'btn-outline-secondary'} py-0 ms-1" onclick="goMMPage(${p})">${p}</button>`;
    }
    html += `<button class="btn btn-sm btn-outline-secondary py-0 ms-1" onclick="goMMPage(${state.mm.page + 1})" ${state.mm.page >= totalPages ? 'disabled' : ''}><i class="bi bi-chevron-right"></i></button>`;
    nav.innerHTML = html;
}

async function goMMPage(page) {
    if (page < 1) return;
    state.mm.page = page;
    try {
        await loadMemoryManagement();
    } catch (e) {
        toast('加载失败: ' + e.message, 'danger');
    }
}

function updateMMStatusLabel() {
    const label = document.getElementById('mm-status-label');
    if (!label) return;
    const total = state.mm.total;
    const showing = state.mm.items.length;
    const filterLabel = state.mm.typeFilter ? getTypeLabel(state.mm.typeFilter) : '全部';
    const statusLabel = state.mm.statusFilter === 'forgotten' ? '已遗忘' : '活跃';
    const searchSuffix = state.mm.searchQuery.trim() ? ` · 搜索: "${escapeHtml(state.mm.searchQuery.trim())}"` : '';
    label.textContent = `${statusLabel}记忆 · ${filterLabel} · 共 ${total} 条 (显示 ${showing} 条)${searchSuffix}`;
}

async function updateMMStats() {
    try {
        function statsUrl(status) {
            const params = new URLSearchParams();
            params.set('limit', status === 'forgotten' ? '100' : '1');
            params.set('status', status);
            ALL_MM_TYPES.forEach(t => params.append('type', t));
            return '/api/memories?' + params.toString();
        }
        const [activeData, forgottenResp, archivedData] = await Promise.all([
            apiClient.request(statsUrl('active')),
            apiClient.request(statsUrl('forgotten')),
            apiClient.request(statsUrl('archived')),
        ]);
        const activeTotal = activeData.total || 0;
        const forgottenTotal = forgottenResp.total || 0;
        const archivedTotal = archivedData.total || 0;

        // 待归档：forgotten_at + 25d < now 的条数
        const forgottenItems = forgottenResp.memories || [];
        const now = Date.now() / 1000;
        const expiringCount = forgottenItems.filter(m => {
            const fa = (m.metadata || {}).forgotten_at || 0;
            return fa > 0 && (now - fa) > 25 * 86400;
        }).length;

        const curStatus = state.mm.statusFilter;
        document.getElementById('mm-stat-total').innerHTML =
            `<a href="#" onclick="switchMMStatus('active');return false;" class="text-decoration-none ${curStatus === 'active' ? 'text-primary fw-bold' : 'text-light'}">${activeTotal}</a>`;
        document.getElementById('mm-stat-forgotten').innerHTML =
            `<a href="#" onclick="switchMMStatus('forgotten');return false;" class="text-decoration-none ${curStatus === 'forgotten' ? 'text-primary fw-bold' : 'text-light'}">${forgottenTotal}</a>`;
        document.getElementById('mm-stat-archived').innerHTML =
            `<a href="#" onclick="switchMMStatus('archived');return false;" class="text-decoration-none ${curStatus === 'archived' ? 'text-primary fw-bold' : 'text-light'}">${archivedTotal}</a>`;
        document.getElementById('mm-stat-expiring').innerHTML =
            `<a href="#" onclick="switchMMStatus('forgotten');return false;" class="text-decoration-none ${curStatus === 'forgotten' ? 'text-primary fw-bold' : 'text-light'}">${expiringCount}</a>`;
    } catch (e) {
        console.warn('更新统计失败:', e);
    }
}

// --- 状态筛选切换 ---
function switchMMStatus(status) {
    state.mm.statusFilter = status;
    state.mm.page = 1;
    loadMemoryManagement().catch(e => toast('加载失败: ' + e.message, 'danger'));
}

// --- 查看详情 ---
async function showMemoryDetail(id) {
    try {
        const m = await apiClient.request(`/api/memories/${id}`);
        const meta = m.metadata || {};
        const type = meta.type || 'unknown';
        const typeLabel = getTypeLabel(type);
        const typeColor = getTypeColor(type);
        const isLegacy = isLegacyType(type);

        // 构建详情HTML
        const detailHtml = `
            <div class="mb-3">
                <div class="d-flex gap-2 align-items-center mb-2 flex-wrap">
                    <span class="badge ${typeColor} bg-opacity-25 ${typeColor.replace('bg-', 'text-')}" style="font-size:.9rem;">${escapeHtml(typeLabel)}</span>
                    ${isLegacy ? '<span class="badge bg-secondary">旧版</span>' : ''}
                    <span class="small text-secondary">${formatTime(meta.updated_at || meta.timestamp)}</span>
                    <span class="badge ${meta.status === 'active' ? 'bg-success' : meta.status === 'forgotten' ? 'bg-warning text-dark' : 'bg-secondary'}">${meta.status || 'active'}</span>
                </div>
            <div class="mb-2">
                <div class="small fw-semibold mb-1">元数据</div>
                <table class="table table-sm table-borderless small mb-0">
                    <tr><td class="text-secondary" style="width:120px;">类型</td><td>${escapeHtml(typeLabel)} (${escapeHtml(type)})</td></tr>
                    ${meta.quality_score != null ? `<tr><td class="text-secondary">质量评分</td><td>${(meta.quality_score * 100).toFixed(0)}分 ${meta.quality_reason ? '(' + escapeHtml(meta.quality_reason) + ')' : ''}</td></tr>` : ''}
                    ${meta.source ? `<tr><td class="text-secondary">来源</td><td>${escapeHtml(meta.source)}</td></tr>` : ''}
                    ${meta.reuse_count != null ? `<tr><td class="text-secondary">复用次数</td><td>${meta.reuse_count}</td></tr>` : ''}
                    ${meta.timestamp ? `<tr><td class="text-secondary">创建时间</td><td>${formatTime(meta.timestamp)}</td></tr>` : ''}
                    ${meta.forgotten_at ? `<tr><td class="text-secondary">遗忘时间</td><td>${formatTime(meta.forgotten_at)}</td></tr>` : ''}
                    ${meta.inactive_reason ? `<tr><td class="text-secondary">状态原因</td><td>${escapeHtml(meta.inactive_reason)}</td></tr>` : ''}
                    ${meta.linked_error_pattern ? `<tr><td class="text-secondary">关联错误模式</td><td>${escapeHtml(meta.linked_error_pattern)}</td></tr>` : ''}
                    ${meta.briefing_date ? `<tr><td class="text-secondary">简报日期</td><td>${escapeHtml(meta.briefing_date)}</td></tr>` : ''}
                    ${meta.project_id ? `<tr><td class="text-secondary">项目ID</td><td><code>${escapeHtml(meta.project_id)}</code></td></tr>` : ''}
                </table>
            </div>
            <div class="mb-2">
                <label class="form-label small fw-bold">编辑内容</label>
                <textarea class="form-control form-control-sm" id="mm-edit-content" rows="6">${escapeHtml(m.document || '')}</textarea>
            </div>
            <div class="mb-2">
                <label class="form-label small fw-bold">编辑类型</label>
                <select class="form-select form-select-sm" id="mm-edit-type" ${type === 'task' ? 'disabled' : ''}>
                    ${NEW_6_TYPES.map(t =>
                        `<option value="${t}"${t === type ? ' selected' : ''}>${escapeHtml(getTypeLabel(t))} (${t})</option>`
                    ).join('')}
                </select>
                ${type === 'task' ? '<div class="small text-secondary mt-1">Task 类型不可更改</div>' : ''}
            </div>
        `;

        const modal = document.getElementById('mm-detail-modal');
        if (!modal) {
            // 创建详情模态框
            _createDetailModal(detailHtml, id, meta);
        } else {
            document.getElementById('mm-detail-body').innerHTML = detailHtml;
            modal.dataset.memoryId = id;
            new bootstrap.Modal(modal).show();
        }
    } catch (e) {
        toast('加载详情失败: ' + e.message, 'danger');
    }
}

function _createDetailModal(html, id, meta) {
    const modalHtml = `
    <div class="modal fade" id="mm-detail-modal" tabindex="-1">
        <div class="modal-dialog modal-lg modal-dialog-scrollable">
            <div class="modal-content">
                <div class="modal-header">
                    <h6 class="modal-title"><i class="bi bi-info-circle me-1"></i>记忆详情</h6>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body" id="mm-detail-body">${html}</div>
                <div class="modal-footer d-flex justify-content-between">
                    <div class="d-flex gap-1">
                        <button type="button" class="btn btn-sm btn-outline-success mm-restore-from-detail" data-id="${escapeHtml(id)}">恢复</button>
                        <button type="button" class="btn btn-sm btn-outline-warning mm-archive-from-detail" data-id="${escapeHtml(id)}">归档</button>
                        <button type="button" class="btn btn-sm btn-outline-danger mm-delete-from-detail" data-id="${escapeHtml(id)}">永久删除</button>
                    </div>
                    <div class="d-flex gap-1">
                        <button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>
                        <button type="button" class="btn btn-sm btn-primary" id="mm-save-edit-btn" data-id="${escapeHtml(id)}">保存</button>
                    </div>
                </div>
            </div>
        </div>
    </div>`;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = modalHtml;
    document.body.appendChild(wrapper.firstElementChild);
    const modal = document.getElementById('mm-detail-modal');
    modal.dataset.memoryId = id;
    new bootstrap.Modal(modal).show();
}

// --- 保存编辑 ---
document.addEventListener('click', function(e) {
    const saveBtn = e.target.closest('#mm-save-edit-btn');
    if (!saveBtn) return;
    const id = saveBtn.dataset.id || document.getElementById('mm-detail-modal')?.dataset?.memoryId;
    if (!id) { toast('无法获取记忆ID', 'danger'); return; }
    const content = document.getElementById('mm-edit-content')?.value?.trim();
    const type = document.getElementById('mm-edit-type')?.value;
    if (!content) { toast('内容不能为空', 'warning'); return; }
    _saveMemoryEdit(id, content, type);
});

async function _saveMemoryEdit(id, content, type) {
    try {
        await apiClient.request(`/api/memories/${id}`, {
            method: 'PUT',
            body: JSON.stringify({content, type}),
        });
        toast('记忆已更新');
        bootstrap.Modal.getInstance(document.getElementById('mm-detail-modal'))?.hide();
        await loadMemoryManagement();
    } catch (e) {
        toast('更新失败: ' + e.message, 'danger');
    }
}

// --- 遗忘 ---
async function forgetMemory(id) {
    if (!confirm('确定要将此记忆标记为"遗忘"吗？可在已遗忘列表中恢复。')) return;
    try {
        await apiClient.request(`/api/memories/${id}/forget`, {method: 'POST'});
        toast('记忆已标记为遗忘');
        await loadMemoryManagement();
    } catch (e) {
        toast('操作失败: ' + e.message, 'danger');
    }
}

// --- 恢复 ---
async function restoreMemory(id) {
    try {
        await apiClient.request(`/api/memories/${id}/restore`, {method: 'POST'});
        toast('记忆已恢复');
        await loadMemoryManagement();
    } catch (e) {
        toast('恢复失败: ' + e.message, 'danger');
    }
}

// --- 永久归档 ---
async function archiveMemory(id) {
    if (!confirm('确定要将此记忆永久归档吗？归档后不再出现在活跃列表中。')) return;
    try {
        await apiClient.request(`/api/memories/${id}/archive`, {method: 'POST'});
        toast('记忆已永久归档');
        await loadMemoryManagement();
    } catch (e) {
        toast('归档失败: ' + e.message, 'danger');
    }
}

// --- 硬删除（需确认） ---
async function deleteMemory(id) {
    if (!confirm('确定要永久删除此记忆吗？此操作不可恢复！')) return;
    try {
        await apiClient.request(`/api/memories/${id}`, {method: 'DELETE'});
        toast('记忆已永久删除');
        await loadMemoryManagement();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

// --- 事件绑定：类型过滤 pills ---
document.addEventListener('click', function(e) {
    const pill = e.target.closest('.mm-type-pill');
    if (!pill) return;
    const type = pill.dataset.type || '';
    // 更新 pills 样式
    document.querySelectorAll('.mm-type-pill').forEach(p => {
        p.classList.remove('btn-primary');
        p.classList.add('btn-outline-secondary');
    });
    pill.classList.remove('btn-outline-secondary');
    pill.classList.add('btn-primary');
    state.mm.typeFilter = type;
    state.mm.page = 1;
    loadMemoryManagement().catch(e => toast('加载失败: ' + e.message, 'danger'));
});

// --- 搜索事件 ---
document.getElementById('mm-search-query')?.addEventListener('input', function() {
    // 用户输入后延迟搜索 (debounce 300ms)
    clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => {
        state.mm.searchQuery = this.value;
        state.mm.page = 1;
        loadMemoryManagement().catch(e => toast('搜索失败: ' + e.message, 'danger'));
    }, 300);
});

document.getElementById('mm-search-clear')?.addEventListener('click', function() {
    document.getElementById('mm-search-query').value = '';
    state.mm.searchQuery = '';
    state.mm.page = 1;
    loadMemoryManagement().catch(e => toast('加载失败: ' + e.message, 'danger'));
});

// --- 事件绑定：记忆操作按钮（委托） ---
document.addEventListener('click', function(e) {
    // 查看详情
    const detailBtn = e.target.closest('.mm-detail-btn');
    if (detailBtn) {
        const id = detailBtn.dataset.id;
        showMemoryDetail(id);
        return;
    }

    // 遗忘
    const forgetBtn = e.target.closest('.mm-forget-btn');
    if (forgetBtn) {
        const id = forgetBtn.dataset.id;
        forgetMemory(id);
        return;
    }

    // 恢复（卡片）
    const restoreBtn = e.target.closest('.mm-restore-btn');
    if (restoreBtn) {
        const id = restoreBtn.dataset.id;
        restoreMemory(id);
        return;
    }

    // 归档（卡片）
    const archiveBtn = e.target.closest('.mm-archive-btn');
    if (archiveBtn) {
        const id = archiveBtn.dataset.id;
        archiveMemory(id);
        return;
    }

    // 删除（卡片）
    const deleteBtn = e.target.closest('.mm-delete-btn');
    if (deleteBtn) {
        const id = deleteBtn.dataset.id;
        deleteMemory(id);
        return;
    }
});

// --- 详情模态框的恢复/归档/删除按钮委托 ---
document.addEventListener('click', function(e) {
    const restoreBtn = e.target.closest('.mm-restore-from-detail');
    if (restoreBtn) {
        const id = restoreBtn.dataset.id;
        bootstrap.Modal.getInstance(document.getElementById('mm-detail-modal'))?.hide();
        restoreMemory(id);
        return;
    }
    const archiveBtn = e.target.closest('.mm-archive-from-detail');
    if (archiveBtn) {
        const id = archiveBtn.dataset.id;
        bootstrap.Modal.getInstance(document.getElementById('mm-detail-modal'))?.hide();
        archiveMemory(id);
        return;
    }
    const deleteBtn = e.target.closest('.mm-delete-from-detail');
    if (deleteBtn) {
        const id = deleteBtn.dataset.id;
        bootstrap.Modal.getInstance(document.getElementById('mm-detail-modal'))?.hide();
        deleteMemory(id);
        return;
    }
});

// --- 激活记忆管理面板时刷新 ---
document.addEventListener('shown.bs.tab', function(e) {
    const target = e.target?.getAttribute?.('data-bs-target');
    if (target === '#knowledge-panel') {
        state.mm.page = 1;
        state.mm.searchQuery = '';
        state.mm.statusFilter = 'active';
        document.getElementById('mm-search-query').value = '';
        loadMemoryManagement().catch(() => {});
    }
});

// ====== F6: 手工提炼面板 ======
const mmExtractState = {
    items: [],
    total: 0,
    page: 1,
    pageSize: 20,
};

async function loadExtractionConversations(page) {
    if (page !== undefined) mmExtractState.page = page;
    const container = document.getElementById('mm-extract-conv-list');
    const info = document.getElementById('mm-extract-info');
    try {
        const params = new URLSearchParams();
        params.set('limit', mmExtractState.pageSize);
        params.set('offset', (mmExtractState.page - 1) * mmExtractState.pageSize);
        const data = await apiClient.request(`/api/conversations?${params}`);
        mmExtractState.total = data.total || 0;
        mmExtractState.items = data.conversations || [];
        if (info) info.textContent = `选择对话记录进行手工提炼（共 ${mmExtractState.total} 条）`;

        if (!data.conversations || data.conversations.length === 0) {
            container.innerHTML = '<div class="text-center text-secondary small py-4">暂无对话记录。</div>';
            renderMMExtractPagination();
            return;
        }

        container.innerHTML = data.conversations.map(c => {
            const t = formatTime(c.timestamp);
            const id = c.id || '';
            const isInput = c.type === 'user_input';
            const label = isInput ? '用户' : '助手';
            const bgClass = isInput ? 'bg-primary' : 'bg-success';
            const typeBadge = isInput
                ? '<span class="badge bg-primary bg-opacity-10 text-primary me-1">输入</span>'
                : '<span class="badge bg-success bg-opacity-10 text-success me-1">输出</span>';
            const rid = c.round_id ? c.round_id.slice(0, 16) : '';
            return `<div class="border rounded p-2 mb-1 d-flex justify-content-between align-items-start">
                <div class="d-flex align-items-start gap-2 flex-grow-1 me-2">
                    <input type="checkbox" class="form-check-input mm-extract-checkbox mt-1" value="${escapeHtml(id)}">
                    <div>
                        <div class="small text-secondary mb-1">${typeBadge}<span class="me-2">${escapeHtml(t)}</span><code class="small text-muted">${escapeHtml(rid)}</code></div>
                        <div class="mb-0"><span class="badge ${bgClass} bg-opacity-10 me-1">${label}</span>${escapeHtml(c.content)}</div>
                    </div>
                </div>
            </div>`;
        }).join('');
        updateMMExtractButtons();
        renderMMExtractPagination();
    } catch (e) {
        container.innerHTML = `<div class="text-danger small py-2">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

function renderMMExtractPagination() {
    const nav = document.getElementById('mm-extract-pagination-nav');
    if (!nav) return;
    const totalPages = Math.ceil(mmExtractState.total / mmExtractState.pageSize) || 1;
    if (mmExtractState.total === 0) { nav.innerHTML = ''; return; }
    let html = `<span class="small text-secondary me-1">${mmExtractState.page}/${totalPages}</span>`;
    html += `<button class="btn btn-sm btn-outline-secondary py-0" onclick="goMMExtractPage(${mmExtractState.page - 1})" ${mmExtractState.page <= 1 ? 'disabled' : ''}><i class="bi bi-chevron-left"></i></button>`;
    const start = Math.max(1, mmExtractState.page - 2);
    const end = Math.min(totalPages, mmExtractState.page + 2);
    for (let p = start; p <= end; p++) {
        html += `<button class="btn btn-sm ${p === mmExtractState.page ? 'btn-primary' : 'btn-outline-secondary'} py-0 ms-1" onclick="goMMExtractPage(${p})">${p}</button>`;
    }
    html += `<button class="btn btn-sm btn-outline-secondary py-0 ms-1" onclick="goMMExtractPage(${mmExtractState.page + 1})" ${mmExtractState.page >= totalPages ? 'disabled' : ''}><i class="bi bi-chevron-right"></i></button>`;
    nav.innerHTML = html;
}

async function goMMExtractPage(page) {
    if (page < 1) return;
    mmExtractState.page = page;
    try {
        await loadExtractionConversations();
    } catch (e) {
        toast('加载失败: ' + e.message, 'danger');
    }
}

function updateMMExtractButtons() {
    const checked = document.querySelectorAll('.mm-extract-checkbox:checked').length;
    const deleteBtn = document.getElementById('mm-extract-batch-delete-btn');
    const exportBtn = document.getElementById('mm-extract-export-btn');
    const extractBtn = document.getElementById('mm-extract-memory-btn');
    document.getElementById('mm-extract-batch-delete-count').textContent = checked;
    document.getElementById('mm-extract-export-count').textContent = checked;
    document.getElementById('mm-extract-memory-count').textContent = checked;
    deleteBtn.classList.toggle('d-none', checked === 0);
    exportBtn.classList.toggle('d-none', checked === 0);
    extractBtn.classList.toggle('d-none', checked === 0);
}

// --- 提炼按钮事件 ---
document.getElementById('mm-extract-memory-btn')?.addEventListener('click', async function() {
    const checked = document.querySelectorAll('.mm-extract-checkbox:checked');
    if (checked.length === 0) { toast('请先选择要提炼的对话', 'warning'); return; }
    const isLLMOnline = await checkLLMStatus();
    if (!isLLMOnline) {
        toast('LLM 服务当前离线，无法提炼知识。', 'warning');
        return;
    }
    _extractConvIds = Array.from(checked).map(cb => cb.value);
    showExtractLoading('正在提炼知识卡片...');
    try {
        await Promise.all([loadPromptTemplates(), loadLLMEndpointsForExtract()]);
        cascadePromptByEndpoint();
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 600000);
        const promptSel = document.getElementById('extract-prompt-select');
        const promptId = promptSel?.value || 'default';
        const promptVersion = promptSel?.selectedOptions[0]?.dataset?.version || null;
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

// --- 导出选中 ---
document.getElementById('mm-extract-export-btn')?.addEventListener('click', async function() {
    const checked = document.querySelectorAll('.mm-extract-checkbox:checked');
    if (checked.length === 0) { toast('请先选择要导出的对话', 'warning'); return; }
    const ids = new Set(Array.from(checked).map(cb => cb.value));
    try {
        const params = new URLSearchParams();
        params.append('type', 'user_input');
        params.append('type', 'assistant_output');
        if (window.state?.currentProject) params.append('project_id', window.state.currentProject);
        const resp = await fetch('/api/memories/export?' + params.toString());
        if (!resp.ok) throw new Error(`导出失败 (HTTP ${resp.status})`);
        const text = await resp.text();
        const allLines = text.trim().split('\n').filter(l => l);
        const selectedLines = allLines.filter(line => {
            try { const obj = JSON.parse(line); return ids.has(obj.id); } catch { return false; }
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
});

// --- 批量删除 ---
document.getElementById('mm-extract-batch-delete-btn')?.addEventListener('click', async function() {
    const checked = document.querySelectorAll('.mm-extract-checkbox:checked');
    if (checked.length === 0) { toast('请先选择要删除的对话', 'warning'); return; }
    if (!confirm(`确定要删除选中的 ${checked.length} 条对话记录吗？此操作不可撤销。`)) return;
    const ids = Array.from(checked).map(cb => cb.value);
    try {
        await apiClient.request('/api/memories/batch-delete', {
            method: 'POST',
            body: JSON.stringify({ids}),
        });
        toast(`已删除 ${ids.length} 条对话记录`);
        await loadExtractionConversations();
        updateMMExtractButtons();
    } catch (e) {
        toast('批量删除失败: ' + e.message, 'danger');
    }
});

// 监听 checkbox 变化
document.addEventListener('change', function(e) {
    if (e.target.classList.contains('mm-extract-checkbox')) {
        updateMMExtractButtons();
    }
});

// 激活手工提炼面板时加载
document.addEventListener('shown.bs.tab', function(e) {
    const target = e.target?.getAttribute?.('data-bs-target');
    if (target === '#manual-extraction-panel') {
        mmExtractState.page = 1;
        loadExtractionConversations().catch(() => {});
    }
});

// --- 对话记录 ---
async function loadConversations(page) {
    if (page !== undefined) state.conv.page = page;
    const container = document.getElementById('conversation-list');
    const info = document.getElementById('conv-info');
    // 不在对话面板时静默跳过
    if (!container || !info) return;
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
    // 持久化默认项目
    if (this.value) {
        localStorage.setItem('memos_default_project', this.value);
    } else {
        localStorage.removeItem('memos_default_project');
    }
    state.kb.page = 1;
    state.conv.page = 1;
    state.mm.page = 1;
    state.mm.searchQuery = '';
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
        loadMemoryManagement(),
    ]).catch(e => toast('加载失败: ' + e.message, 'danger'));
    // 刷新待办面板（loadTodos 是全局函数）
    setTimeout(function() {
        if (typeof loadTodos === 'function') loadTodos();
    }, 200);
    // 刷新总览子面板：项目简报
    state.brf.statsCache = null;
    setTimeout(function() { loadBriefingPanel(); }, 150);
    // 刷新总览子面板：任务看板
    setTimeout(function() { loadTaskProgress(); loadTaskSequence(); }, 150);
    // 刷新今日回顾过滤器（端点列表按项目重新加载）
    setTimeout(function() { if (typeof loadDRFilters === 'function') loadDRFilters(); }, 150);
    // 刷新统计图表
    setTimeout(loadUsageStats, 300);
    setTimeout(loadConflictCount, 600);
    // 刷新对话组子面板：事件看板 + 监控面板
    setTimeout(function() { loadMemoryStream(); }, 150);
    setTimeout(function() { loadMonitorPanel(); }, 150);
});

// --- 系统状态 ---

async function checkLLMStatus() {
    try {
        const data = await api('/api/status');
        return data.llama_server_ok === true;
    } catch (e) {
        return false;
    }
}

// 顶栏状态指示灯
async function loadTopBarStatus() {
    var sysEl = document.getElementById('sys-status-indicator');
    var llmEl = document.getElementById('llm-status-indicator');
    if (!sysEl && !llmEl) return;
    try {
        var [health, llm] = await Promise.all([
            api('/api/health').catch(function() { return null; }),
            api('/api/v2/llm/ping').catch(function() { return null; }),
        ]);

        // 系统运行状态
        if (sysEl) {
            var icon = sysEl.querySelector('i');
            if (health && health.status === 'ok') {
                icon.className = 'bi bi-cpu text-success';
                sysEl.title = '系统 (' + (health.version || '-') + ') 运行正常';
            } else {
                icon.className = 'bi bi-cpu text-danger';
                sysEl.title = '系统异常';
            }
        }

        // LLM 服务状态
        if (llmEl) {
            var icon = llmEl.querySelector('i');
            if (llm && llm.llama_server_ok) {
                icon.className = 'bi bi-robot text-success';
                llmEl.title = (llm.active_endpoint || 'LLM') + ' 服务在线';
            } else {
                icon.className = 'bi bi-robot text-danger';
                llmEl.title = 'LLM 服务离线';
            }
        }
    } catch (e) {
        // 失败保持灰色
    }
}

// --- 备份 ---
async function triggerBackup() {
    const btn = document.getElementById('backup-btn');
    const statusText = document.getElementById('backup-status-text');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>';
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
        btn.innerHTML = '<i class="bi bi-cloud-arrow-up"></i>';
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
    btn.innerHTML = '<i class="bi bi-cloud-arrow-up"></i>';
    if (waited >= maxWait * 1000) {
        toast('备份超时，请检查服务器日志', 'warning');
    }
}

async function loadBackupStatus() {
    const statusText = document.getElementById('backup-status-text');
    if (!statusText) return;
    try {
        const data = await api('/api/backup/status');
        if (data.latest) {
            const ts = new Date(data.latest.timestamp * 1000);
            const timeStr = ts.toLocaleString('zh-CN', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
            const sizeMb = (data.latest.size_bytes / (1024*1024)).toFixed(1);
            let icon = data.health === 'warning' || data.health === 'partial' ? '⚠' : '✓';
            statusText.style.display = '';
            statusText.innerHTML = `${icon} 上次备份: ${timeStr}`;
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

let _bdId = null;

async function deleteBackupItem(backupId) {
    if (!backupId) { toast('无法删除：备份标识未知', 'danger'); return; }
    _bdId = backupId;
    document.getElementById('bd-name').textContent = backupId;
    const btn = document.getElementById('bd-execute');
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-trash me-1"></i>删除';
    // 隐藏备份管理对话框，弹出确认删除对话框
    bootstrap.Modal.getInstance(document.getElementById('backupModal'))?.hide();
    new bootstrap.Modal(document.getElementById('backupDeleteModal')).show();
}

document.getElementById('bd-execute')?.addEventListener('click', async function() {
    if (!_bdId) return;
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>删除中...';
    try {
        await api('/api/backups/' + encodeURIComponent(_bdId), {method: 'DELETE'});
        bootstrap.Modal.getInstance(document.getElementById('backupDeleteModal'))?.hide();
        toast('备份已删除', 'success');
        // 重新打开备份管理并刷新列表
        new bootstrap.Modal(document.getElementById('backupModal')).show();
        await loadBackupList();
        loadBackupStatus();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-trash me-1"></i>删除';
    }
});

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
    backup: '备份与恢复',
    notification: '通知中心',
    system_suggestion: '系统建议',
    agent: 'Agent 决策引擎',
    hook_proxy: 'Hook 代理',
};

const sectionDescs = {
    chroma: '向量数据库连接与持久化',
    llm: '记忆提炼用大语言模型',
    memory: '嵌入模型、检索、去重与归档',
    buffer: '对话缓冲区与提炼策略',
    dashboard: 'Web 仪表板参数',
    server: 'MCP 协议服务端参数',
    backup: 'ChromaDB 数据全量备份与恢复',
    notification: '系统通知的保留与限速',
    system_suggestion: '系统状态型建议（管道二）的推送策略',
    agent: 'Agent 决策引擎配置（Phase 1-3：模式检测/每日简报/信号推送）',
    hook_proxy: 'Hook 代理（server_url 已从 server.port 自动派生）',
};

const fieldHelpText = {
    // ChromaDB
    'chroma.collection_name': 'ChromaDB 集合名，用于按项目隔离数据',
    'chroma.path': '本地持久化目录路径',
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
    // HookProxy
    'hook_proxy.timeout': 'Hook 请求超时秒数（server_url 从 server.port 自动派生）',
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
        const checkboxLabel = '启用';
        h += `<div class="form-check pt-1 d-inline-block">
            <input type="checkbox" class="form-check-input" id="${inputId}" data-key="${dataKey}" ${value ? 'checked' : ''}>
            <label class="form-check-label small" for="${inputId}">${checkboxLabel}</label>
        </div>`;
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
        ['dashboard', 'model', 'suggestion'].forEach(function(sk) {
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
        var advancedKeys = Object.keys(sections).filter(function(k) { return !['llm', 'dashboard', 'model', 'suggestion', 'memory'].includes(k); });
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
    const text = `【${card.type || 'solution'}】\n问题：${card.problem || ''}\n方案：${card.solution || ''}\n洞察：${card.insight || ''}`;
    copyToClipboard(text, '知识卡片');
}

function editExtractCard(idx) {
    const card = _extractCards[idx];
    if (!card) return;
    document.getElementById('extract-edit-index').value = idx;
    document.getElementById('extract-edit-type').value = card.type || 'solution';
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
    await Promise.all([loadProjects(), loadBackupStatus()]);
    await Promise.all([loadMemories(), loadConversations()]);

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

    // v0.7.1: 应用 URL hash 定位二级面板
    applyHash();

    // v0.7.1: P10 — 首次加载时直接激活默认面板
    var firstSub = document.querySelector('#group-overview .subpanel-pills .nav-link.active');
    if (firstSub) {
        // 直接派发 shown.bs.tab 事件确保面板数据加载
        firstSub.dispatchEvent(new Event('shown.bs.tab', {bubbles: true}));
    }

    // 加载顶栏状态指示灯
    loadTopBarStatus();
    setInterval(loadTopBarStatus, 60000);
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
        const typeNames = {extract:'提炼知识', 'daily-review':'今日回顾', briefing:'简报生成'};
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
    var brfEl = document.getElementById('brf-date');
    if (brfEl) {
        brfEl.value = today;
        // 注意：时间选择器只控制历史简报列表 + 立即生成，不影响当前简报区域
    }
    var taskDateEl = document.getElementById('task-sequence-date');
    if (taskDateEl) {
        taskDateEl.value = today;
        taskDateEl.addEventListener('change', function() {
            loadTaskSequence(this.value);
        });
    }
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
        var data = await apiClient.request('/api/conflicts?limit=100');
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
            html += '<button class="btn btn-sm btn-outline-primary" data-edit-content="' + escHtmlAttr(nm.content || '') + '" onclick="editConflictMemory(\'' + pairId + '\',this)">修改新记忆</button>';
            html += '<button class="btn btn-sm btn-outline-danger" onclick="resolveConflict(\'' + pairId + '\',\'discard\')">放弃新记忆</button>';
            html += '</div></div></div>';
        }
        // 底部统计行
        try {
            var statsData = await apiClient.request('/api/conflicts/stats');
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

var _editConflictPairId = '';

function editConflictMemory(pairId, btn) {
    _editConflictPairId = pairId;
    document.getElementById('edit-conflict-content').value = btn.getAttribute('data-edit-content') || '';
    var modal = new bootstrap.Modal(document.getElementById('editConflictModal'));
    modal.show();
}

document.getElementById('edit-confirm-btn')?.addEventListener('click', async function() {
    var content = document.getElementById('edit-conflict-content').value.trim();
    var pairId = _editConflictPairId;
    if (!content) return;
    try {
        await api('/api/conflicts/' + pairId + '/resolve?action=edit', {
            method:'POST',
            body: JSON.stringify({content: content}),
            headers: {'Content-Type': 'application/json'}
        });
        bootstrap.Modal.getInstance(document.getElementById('editConflictModal')).hide();
        var card = document.getElementById('conflict-pair-' + pairId);
        if (card) card.remove();
        _conflictDirty = true;
        setTimeout(loadConflictList, 500);
        setTimeout(loadConflictCount, 500);
    } catch(e) { console.error(e); }
});

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
        var data = await apiClient.request('/api/conflicts/count');
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
        // 不在总览面板时跳过
        if (!document.getElementById('stat-today')) return;
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

// 将日期字符串（YYYY-MM-DD）转为本地时区的 UTC 时间戳范围
function dateToTsRange(dateStr) {
    return {
        start_ts: new Date(dateStr + 'T00:00:00').getTime() / 1000,
        end_ts: new Date(dateStr + 'T23:59:59').getTime() / 1000,
    };
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

        var tsRange = dateToTsRange(date);
        var body = { date: date, start_ts: tsRange.start_ts, end_ts: tsRange.end_ts, project_id: window.state?.currentProject || null };
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
        var tsRange = dateToTsRange(date);
        var body = { date: date, start_ts: tsRange.start_ts, end_ts: tsRange.end_ts, project_id: window.state?.currentProject || null };
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

// --- 项目管理 ---
let _pmDeletePid = null;
let _pmDeleteName = '';

async function _loadProjectManagerData() {
    document.getElementById('pm-loading').style.display = '';
    document.getElementById('pm-content').style.display = 'none';

    try {
        const data = await api('/api/projects');
        const projects = data.projects || [];
        const tbody = document.getElementById('pm-table-body');
        tbody.innerHTML = '';

        // Sort: current project first, then by latest_time
        projects.sort((a, b) => {
            if (a.project_id === data.current_project) return -1;
            if (b.project_id === data.current_project) return 1;
            return (b.latest_time || 0) - (a.latest_time || 0);
        });

        for (const p of projects) {
            const tr = document.createElement('tr');

            // Name
            const nameTd = document.createElement('td');
            nameTd.textContent = p.project_name || p.project_id;
            if (p.project_id === data.current_project) {
                const badge = document.createElement('span');
                badge.className = 'badge bg-info ms-1';
                badge.textContent = '当前';
                nameTd.appendChild(badge);
            }
            tr.appendChild(nameTd);

            // ID
            const idTd = document.createElement('td');
            idTd.className = 'text-muted small font-monospace';
            idTd.textContent = p.project_id;
            tr.appendChild(idTd);

            // Type distribution
            const typeTd = document.createElement('td');
            typeTd.className = 'small';
            const byType = p.by_type || {};
            const typeEntries = Object.entries(byType).sort((a, b) => b[1] - a[1]);
            if (typeEntries.length > 0) {
                typeTd.innerHTML = typeEntries
                    .map(([k, v]) => `<span class="badge bg-secondary bg-opacity-10 text-secondary me-1">${k}: ${v}</span>`)
                    .join(' ');
            } else {
                typeTd.innerHTML = '<span class="text-muted">（空）</span>';
            }
            tr.appendChild(typeTd);

            // Actions
            const actionTd = document.createElement('td');
            const delBtn = document.createElement('button');
            delBtn.className = 'btn btn-sm btn-outline-danger py-0';
            delBtn.innerHTML = '<i class="bi bi-trash"></i>';
            delBtn.title = '删除项目';
            delBtn.onclick = () => confirmPmDelete(p.project_id, p.project_name || p.project_id);
            actionTd.appendChild(delBtn);
            tr.appendChild(actionTd);

            tbody.appendChild(tr);
        }
    } catch (e) {
        document.getElementById('pm-table-body').innerHTML =
            '<tr><td colspan="4" class="text-danger text-center">加载失败: ' + e.message + '</td></tr>';
    } finally {
        document.getElementById('pm-loading').style.display = 'none';
        document.getElementById('pm-content').style.display = '';
    }
}

async function openProjectManager() {
    const modal = new bootstrap.Modal(document.getElementById('projectMgmtModal'));
    modal.show();
    await _loadProjectManagerData();
}

async function refreshProjectManager() {
    await Promise.all([_loadProjectManagerData(), loadProjects()]);
}

// --- 项目管理：删除确认 ---
function confirmPmDelete(pid, name) {
    _pmDeletePid = pid;
    _pmDeleteName = name;

    document.getElementById('pm-del-name').textContent = name;
    document.getElementById('pm-del-id').textContent = pid;
    document.getElementById('pm-del-stats').textContent = '加载中...';
    document.getElementById('pm-del-confirm').value = '';
    document.getElementById('pm-del-execute').disabled = true;

    // 加载统计数据
    api(`/api/projects/${pid}/stats`).then(stats => {
        if (stats.total > 0) {
            const parts = Object.entries(stats.by_type).map(([k, v]) => `${k}: ${v}`);
            document.getElementById('pm-del-stats').textContent = `共 ${stats.total} 条 — ` + parts.join(' | ');
        } else {
            document.getElementById('pm-del-stats').textContent = '（空项目，无数据）';
        }
    }).catch(() => {
        document.getElementById('pm-del-stats').textContent = '加载失败';
    });

    // 关闭项目管理大对话框，打开确认删除小对话框
    bootstrap.Modal.getInstance(document.getElementById('projectMgmtModal'))?.hide();
    const confirmModal = new bootstrap.Modal(document.getElementById('pmDeleteConfirmModal'));
    confirmModal.show();
}

// 输入项目名匹配后启用删除按钮
document.getElementById('pm-del-confirm')?.addEventListener('input', function() {
    document.getElementById('pm-del-execute').disabled = this.value !== _pmDeleteName;
});

// 确认删除执行
document.getElementById('pm-del-execute')?.addEventListener('click', async function() {
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>删除中...';

    try {
        await api(`/api/projects/${_pmDeletePid}`, { method: 'DELETE' });
        // 如果删除的是当前选中项目，重置
        if (state.currentProject === _pmDeletePid) {
            state.currentProject = null;
            const saved = localStorage.getItem('memos_default_project');
            if (saved === _pmDeletePid) localStorage.removeItem('memos_default_project');
        }
        // 关闭确认弹窗
        bootstrap.Modal.getInstance(document.getElementById('pmDeleteConfirmModal'))?.hide();
        // 刷新项目列表
        await loadProjects();
        // 刷新各面板
        Promise.all([loadMemories(), loadConversations()]);
        toast('项目已删除', 'success');
        // 重新打开项目管理对话框
        openProjectManager();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-trash me-1"></i>确认删除';
    }
});

// ====== F2b: 新面板 JS 处理 ======

// === 1. Watchlist (跟进组 → 待关注) ===
// GET /api/v2/watchlist?page=1&page_size=20
// POST /api/v2/watchlist/{id}/to-knowledge → 转为知识
// POST /api/v2/watchlist/{id}/ignore → 忽略
// POST /api/v2/watchlist/{id}/note → 备注

async function loadWatchlist() {
    var container = document.getElementById('watchlist-container');
    if (!container) return;
    container.innerHTML = renderSkeleton('list');
    try {
        var data = await apiClient.request('/api/v2/watchlist?page=1&page_size=20');
        var items = data.items || [];
        if (!items.length) {
            container.innerHTML = '<div class="empty-state">' +
                '<i class="bi bi-eye"></i>' +
                '<p>暂无待关注内容</p>' +
                '<p class="hint">使用 remember() 标记待关注的信息会出现在这里</p>' +
                '</div>';
            return;
        }
        var html = '';
        items.forEach(function(item) {
            var meta = item.metadata || {};
            var ts = meta.timestamp || 0;
            html += '<div class="card mb-2">';
            html += '<div class="card-body py-2 px-3">';
            html += '<div class="small text-secondary mb-1">' + timeAgo(ts) + '</div>';
            html += '<div class="mb-2">' + escapeHtml(item.document) + '</div>';
            html += '<div class="d-flex gap-1">';
            html += '<button class="btn btn-sm btn-outline-success py-0 px-1" onclick="watchlistToKnowledge(\'' + item.id + '\')">转为知识</button>';
            html += '<button class="btn btn-sm btn-outline-warning py-0 px-1" onclick="watchlistIgnore(\'' + item.id + '\')">忽略</button>';
            html += '<button class="btn btn-sm btn-outline-info py-0 px-1" onclick="watchlistNote(\'' + item.id + '\')">备注</button>';
            html += '</div></div></div>';
        });
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '<div class="text-danger small text-center py-3">加载失败: ' + escapeHtml(e.message) + '</div>';
    }
}

async function watchlistToKnowledge(id) {
    var type = prompt('选择目标知识类型: solution/decision/lesson/process', 'solution');
    if (!type) return;
    type = type.trim().toLowerCase();
    if (['solution', 'decision', 'lesson', 'process'].indexOf(type) === -1) {
        toast('无效类型，可选: solution/decision/lesson/process', 'warning');
        return;
    }
    try {
        await apiClient.request('/api/v2/watchlist/' + id + '/to-knowledge', {
            method: 'POST',
            body: JSON.stringify({type: type}),
        });
        toast('已转为知识', 'success');
        loadWatchlist();
    } catch (e) {
        toast('转换失败: ' + e.message, 'danger');
    }
}

async function watchlistIgnore(id) {
    try {
        await apiClient.request('/api/v2/watchlist/' + id + '/ignore', {method: 'POST'});
        toast('已忽略', 'success');
        loadWatchlist();
    } catch (e) {
        toast('忽略失败: ' + e.message, 'danger');
    }
}

async function watchlistNote(id) {
    var note = prompt('输入备注:');
    if (note === null) return;
    try {
        await apiClient.request('/api/v2/watchlist/' + id + '/note', {
            method: 'POST',
            body: JSON.stringify({note: note}),
        });
        toast('备注已保存', 'success');
        loadWatchlist();
    } catch (e) {
        toast('保存备注失败: ' + e.message, 'danger');
    }
}

// === 2. 事件看板（活动日志时间线） ===
// GET /api/v2/activity-log?page=1&page_size=30
// 差异化展示每种事件类型的独有字段

var _EVENT_LABELS = {
    recall:            {icon: 'bi-search',      label: '语义检索'},
    knowledge_write:   {icon: 'bi-pencil',      label: '知识写入'},
    context_injection: {icon: 'bi-send',        label: '上下文注入'},
    manual_injection:  {icon: 'bi-hand-index',  label: '手工注入'},
    ai_reference:      {icon: 'bi-robot',       label: 'AI 引用'},
};
var _EVENT_COLORS = {
    recall:            'info',
    knowledge_write:   'success',
    context_injection: 'warning',
    manual_injection:  'primary',
    ai_reference:      'secondary',
};

/** 渲染单条事件卡片 */
function renderEventCard(item) {
    var ev = item.event || 'unknown';
    var info = _EVENT_LABELS[ev] || {icon: 'bi-question-circle', label: ev};
    var color = _EVENT_COLORS[ev] || 'secondary';
    var ts = item.timestamp ? '<span class="small text-secondary">' + timeAgo(item.timestamp) + '</span>' : '';

    var detailHtml = '';

    switch (ev) {
        case 'recall':
            // 字段: query, result_count, match_types
            detailHtml += '<div class="d-flex align-items-center gap-2 mt-1">';
            detailHtml += '<span class="small text-break flex-grow-1"><strong>查询：</strong>' + escapeHtml(item.query || '') + '</span>';
            detailHtml += '<span class="badge bg-info bg-opacity-10 text-info flex-shrink-0">' + (item.result_count || 0) + ' 条命中</span>';
            detailHtml += '</div>';
            if (item.match_types && item.match_types.length) {
                detailHtml += '<div class="small text-secondary mt-1">匹配类型：' + escapeHtml(item.match_types.join(', ')) + '</div>';
            }
            break;

        case 'knowledge_write':
            // 字段: type, summary, source
            var typeLabel = item.type || 'unknown';
            detailHtml += '<div class="d-flex align-items-center gap-2 mt-1">';
            detailHtml += '<span class="badge bg-success bg-opacity-10 text-success flex-shrink-0">' + escapeHtml(typeLabel) + '</span>';
            detailHtml += '<span class="small text-secondary flex-shrink-0">来源：' + escapeHtml(item.source || '') + '</span>';
            detailHtml += '</div>';
            if (item.summary) {
                detailHtml += '<div class="small text-break mt-1" style="border-left:2px solid var(--bs-gray-600,#6c757d);padding-left:8px">' + escapeHtml(item.summary) + '</div>';
            }
            break;

        case 'context_injection':
            // 字段: memory_ids, types, injection_type
            var injType = item.injection_type || 'knowledge';
            var injLabel = injType === 'manual' ? '用户建议' : '知识注入';
            var count = (item.memory_ids && item.memory_ids.length) || 0;
            detailHtml += '<div class="d-flex align-items-center gap-2 mt-1">';
            detailHtml += '<span class="badge bg-warning bg-opacity-10 text-warning flex-shrink-0">' + injLabel + '</span>';
            detailHtml += '<span class="small text-secondary">' + count + ' 条记忆</span>';
            detailHtml += '</div>';
            if (item.types && item.types.length) {
                detailHtml += '<div class="small text-secondary mt-1">类型：' + escapeHtml(item.types.join(', ')) + '</div>';
            }
            break;

        case 'manual_injection':
            // 字段: count, summary
            detailHtml += '<div class="d-flex align-items-center gap-2 mt-1">';
            detailHtml += '<span class="small"><strong>' + (item.count || 0) + ' 条</strong> 用户建议已注入</span>';
            detailHtml += '</div>';
            if (item.summary) {
                detailHtml += '<div class="small text-secondary mt-1">' + escapeHtml(item.summary) + '</div>';
            }
            break;

        case 'ai_reference':
            // 字段: memory_id, content_snippet, referenced
            var refStatus = item.referenced ? '已引用' : '未匹配';
            var refColor = item.referenced ? 'success' : 'secondary';
            detailHtml += '<div class="d-flex align-items-center gap-2 mt-1">';
            detailHtml += '<span class="badge bg-' + refColor + ' bg-opacity-10 text-' + refColor + ' flex-shrink-0">' + refStatus + '</span>';
            detailHtml += '<span class="small text-secondary text-truncate" title="' + escapeHtml(item.memory_id || '') + '">ID: ' + escapeHtml((item.memory_id || '').slice(0, 20)) + '</span>';
            detailHtml += '</div>';
            if (item.content_snippet) {
                detailHtml += '<div class="small text-break mt-1">' + escapeHtml(item.content_snippet) + '</div>';
            }
            break;

        default:
            // 未知事件类型 — 兜底显示全部字段
            var fallback = item.query || item.type || item.event || item.summary || '';
            detailHtml += '<div class="small mt-1">' + escapeHtml(fallback) + '</div>';
            break;
    }

    return '<div class="d-flex align-items-start gap-2 mb-2 p-2 border rounded">' +
        '<i class="bi ' + info.icon + ' text-' + color + '" style="font-size:1.1rem;margin-top:2px"></i>' +
        '<div class="flex-grow-1 min-w-0">' +
        '<div class="d-flex justify-content-between align-items-center">' +
        '<span class="badge bg-' + color + ' bg-opacity-10 text-' + color + ' flex-shrink-0">' + escapeHtml(info.label) + '</span>' +
        ts +
        '</div>' +
        detailHtml +
        '</div></div>';
}

async function loadMemoryStream() {
    var container = document.getElementById('memory-stream-container');
    if (!container) return;
    container.innerHTML = renderSkeleton('list');
    try {
        var data = await apiClient.request('/api/v2/activity-log?page=1&page_size=30');
        var items = data.items || [];
        if (!items.length) {
            container.innerHTML = '<div class="empty-state">' +
                '<i class="bi bi-water"></i>' +
                '<p>暂无事件记录</p>' +
                '<p class="hint">当有记忆写入、检索或注入时会显示在这里</p>' +
                '</div>';
            return;
        }
        container.innerHTML = items.map(renderEventCard).join('');
    } catch (e) {
        container.innerHTML = '<div class="text-danger small text-center py-3">加载失败: ' + escapeHtml(e.message) + '</div>';
    }
}

// === 3. Task Progress (总览组 → Task 进度) ===
// GET /api/v2/tasks

/* 解析 task document（可能为 TASK_EVAL JSON 结构体） */
function parseTaskEval(task) {
    var doc = (task && task.document) || '';
    if (!doc) return { goal: '(无描述)', done: [], todo: [], blocked: [] };
    try {
        var obj = typeof doc === 'string' ? JSON.parse(doc) : doc;
        /* done/todo/blocked 可能嵌套在 obj.progress 里，也可能在顶层 */
        var progress = obj.progress || obj;
        return {
            goal: obj.goal || obj.title || obj.summary || doc,
            done: progress.done || obj.done || [],
            todo: progress.todo || obj.todo || [],
            blocked: progress.blocked || obj.blocked || [],
        };
    } catch (e) {
        return { goal: doc, done: [], todo: [], blocked: [] };
    }
}

/* 渲染单条 task 卡片 */
function renderTaskCard(task, isActive) {
    var meta = task.metadata || {};
    var ts = meta.updated_at || meta.timestamp || 0;
    var parsed = parseTaskEval(task);
    var goal = parsed.goal;
    var taskId = task.id || '';

    if (isActive) {
        // 活跃 task — 大卡片，含进度摘要 + 操作按钮
        var html = '<div class="card border-success border-opacity-25 mb-3" data-task-id="' + escapeHtml(taskId) + '">';
        html += '<div class="card-header py-2 px-3 d-flex justify-content-between align-items-center bg-success bg-opacity-10">';
        html += '<span><span class="badge bg-success me-2">进行中</span><strong>' + escapeHtml(goal) + '</strong></span>';
        html += '<span class="small text-secondary">' + (ts ? timeAgo(ts) : '') + '</span>';
        html += '</div>';

        // G2: 进度详情（done/todo/blocked 列表）
        var hasProgress = parsed.done.length > 0 || parsed.todo.length > 0 || parsed.blocked.length > 0;
        if (hasProgress || meta.context) {
            html += '<div class="card-body py-2 px-3">';
            // 进度详情
            if (hasProgress) {
                if (parsed.done.length > 0) {
                    html += '<div class="small mb-1"><span class="text-success fw-semibold">✅ 已完成</span></div>';
                    html += '<ul class="list-unstyled small mb-2" style="padding-left:1.2rem;">';
                    parsed.done.forEach(function(item) {
                        html += '<li class="text-success mb-1" style="list-style:none;">' + escapeHtml(item) + '</li>';
                    });
                    html += '</ul>';
                }
                if (parsed.todo.length > 0) {
                    html += '<div class="small mb-1"><span class="text-warning fw-semibold">📋 待做</span></div>';
                    html += '<ul class="list-unstyled small mb-2" style="padding-left:1.2rem;">';
                    parsed.todo.forEach(function(item) {
                        html += '<li class="text-warning-emphasis mb-1" style="list-style:none;">☐ ' + escapeHtml(item) + '</li>';
                    });
                    html += '</ul>';
                }
                if (parsed.blocked.length > 0) {
                    html += '<div class="small mb-1"><span class="text-danger fw-semibold">🚫 阻塞</span></div>';
                    html += '<ul class="list-unstyled small mb-2" style="padding-left:1.2rem;">';
                    parsed.blocked.forEach(function(item) {
                        html += '<li class="text-danger mb-1" style="list-style:none;">✗ ' + escapeHtml(item) + '</li>';
                    });
                    html += '</ul>';
                }
            }
            // 上下文
            if (meta.context) {
                if (hasProgress) html += '<hr class="my-1 border-secondary">';
                html += '<div class="small text-secondary" style="border-left:2px solid var(--bs-gray-600,#6c757d);padding-left:8px">' + escapeHtml(meta.context) + '</div>';
            }
            html += '</div>';
        }

        // G3: 操作按钮 — 主次区分布局
        html += '<div class="card-footer py-1 px-3 bg-transparent">';
        html += '<div class="d-flex justify-content-between align-items-center">';
        // 主操作区（左）：实心按钮 + 文字标签
        html += '<div class="d-flex gap-1">';
        html += '<button class="btn btn-sm btn-success py-0 px-2" onclick="completeTask(\'' + escapeHtml(taskId) + '\')">';
        html += '<i class="bi bi-check-lg"></i> 完成</button>';
        html += '<button class="btn btn-sm btn-outline-warning py-0 px-2" onclick="pauseTask(\'' + escapeHtml(taskId) + '\')">';
        html += '<i class="bi bi-pause-fill"></i> 暂停</button>';
        html += '</div>';
        // 次级操作区（右）：纯图标
        html += '<div class="d-flex gap-1">';
        html += '<button class="btn btn-sm btn-outline-info py-0 px-2" onclick="showMemoryDetail(\'' + escapeHtml(taskId) + '\')" title="详情/编辑">';
        html += '<i class="bi bi-info-circle"></i></button>';
        html += '<button class="btn btn-sm btn-outline-secondary py-0 px-2" onclick="archiveTask(\'' + escapeHtml(taskId) + '\')" title="归档">';
        html += '<i class="bi bi-archive"></i></button>';
        html += '<button class="btn btn-sm btn-outline-danger py-0 px-2 ms-2" onclick="deleteTask(\'' + escapeHtml(taskId) + '\')" title="删除">';
        html += '<i class="bi bi-trash"></i></button>';
        html += '</div></div></div></div>';
        return html;
    }

    // G1: 历史 task — 按状态区分紧凑行 + 操作按钮
    var statusInfo = {
        completed: {label: '已完成', color: 'success'},
        paused: {label: '已暂停', color: 'warning'},
        archived: {label: '已归档', color: 'secondary'},
    };
    var info = statusInfo[meta.status] || {label: meta.status || '未知', color: 'secondary'};
    var label = meta.status === 'paused' ? '已暂停' : (meta.status === 'completed' ? '已完成' : '已归档');
    var color = meta.status === 'paused' ? 'warning' : (meta.status === 'completed' ? 'success' : 'secondary');

    var html = '<div class="d-flex justify-content-between align-items-center py-1 px-2 border-bottom border-secondary border-opacity-25">';
    html += '<div><span class="badge bg-' + color + ' bg-opacity-10 text-' + color + ' me-2" style="min-width:3rem;">' + label + '</span>';
    html += '<span class="small">' + escapeHtml(goal) + '</span></div>';
    html += '<div class="d-flex gap-1 align-items-center">';
    html += '<span class="small text-secondary me-2">' + (ts ? timeAgo(ts) : '') + '</span>';

    if (meta.status === 'completed') {
        html += '<button class="btn btn-sm btn-outline-primary py-0 px-1" onclick="resumeTask(\'' + escapeHtml(taskId) + '\')" title="重新打开"><i class="bi bi-arrow-counterclockwise"></i></button>';
        html += '<button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="deleteTask(\'' + escapeHtml(taskId) + '\')" title="删除"><i class="bi bi-trash"></i></button>';
    } else if (meta.status === 'paused' || meta.paused) {
        html += '<button class="btn btn-sm btn-outline-success py-0 px-1" onclick="resumeTask(\'' + escapeHtml(taskId) + '\')" title="恢复"><i class="bi bi-play-fill"></i></button>';
        html += '<button class="btn btn-sm btn-outline-secondary py-0 px-1" onclick="archiveTask(\'' + escapeHtml(taskId) + '\')" title="归档"><i class="bi bi-archive"></i></button>';
        html += '<button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="deleteTask(\'' + escapeHtml(taskId) + '\')" title="删除"><i class="bi bi-trash"></i></button>';
    } else {
        // archived / forgotten / other
        html += '<button class="btn btn-sm btn-outline-primary py-0 px-1" onclick="resumeTask(\'' + escapeHtml(taskId) + '\')" title="恢复"><i class="bi bi-arrow-counterclockwise"></i></button>';
        html += '<button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="deleteTask(\'' + escapeHtml(taskId) + '\')" title="删除"><i class="bi bi-trash"></i></button>';
    }

    html += '</div></div>';
    return html;
}

async function loadTaskProgress() {
    var container = document.getElementById('task-progress-container');
    if (!container) return;
    if (_taskLoading) return;
    _taskLoading = true;
    container.innerHTML = renderSkeleton('list');
    try {
        var data = await apiClient.request('/api/v2/tasks?limit=30');
        var tasks = data.tasks || [];
        var counts = data.counts || {};

        if (tasks.length === 0) {
            container.innerHTML = '<div class="empty-state">' +
                '<i class="bi bi-list-task"></i>' +
                '<p>暂无 task 记录</p>' +
                '<p class="hint">Task 会在每次会话开始时自动创建</p>' +
                '</div>';
            _taskLoading = false;
            return;
        }

        var html = '';

        // 活跃 task
        var activeTasks = tasks.filter(function(t) {
            var m = t.metadata || {};
            return m.status === 'active' && !m.paused;
        });
        if (activeTasks.length > 0) {
            html += '<h6 class="mb-2"><span class="badge bg-success me-1">' + counts.active + '</span>活跃任务</h6>';
            activeTasks.forEach(function(t) { html += renderTaskCard(t, true); });
        }

        // 已暂停/归档（默认折叠）
        var pausedTasks = tasks.filter(function(t) {
            var m = t.metadata || {};
            return (m.status === 'paused' || m.paused || m.status === 'archived' || m.status === 'forgotten')
                && m.status !== 'completed';
        });
        if (pausedTasks.length > 0) {
            html += '<h6 class="mt-3 mb-2">';
            html += '<a class="text-decoration-none text-secondary small" data-bs-toggle="collapse" href="#paused-task-list" role="button">';
            html += '⏸️ 已暂停/归档 <span class="badge bg-secondary">' + counts.paused_archived + '</span>';
            html += '</a></h6>';
            html += '<div class="collapse" id="paused-task-list"><div class="card"><div class="card-body py-1 px-0">';
            pausedTasks.forEach(function(t) { html += renderTaskCard(t, false); });
            html += '</div></div></div>';
        }

        container.innerHTML = html;
        // 加载任务模式指示器
        loadTaskMode();
        _taskLoading = false;
    } catch (e) {
        _taskLoading = false;
        container.innerHTML = '<div class="text-danger small text-center py-3">加载失败: ' + escapeHtml(e.message) + '</div>';
    }
}

// --- Task 管理操作 ---

async function pauseTask(id) {
    if (_taskLoading) return;
    if (!confirm("暂停此 Task？")) return;
    _taskLoading = true;
    try {
        await apiClient.request('/api/v2/tasks/' + id + '/pause', {method: 'POST'});
        toast('Task 已暂停', 'warning');
        _taskLoading = false;
        loadTaskProgress();
        loadTaskSequence();
    } catch (e) {
        _taskLoading = false;
        toast('操作失败: ' + e.message, 'danger');
    }
}

/* resumeTask 同时承担 reopen 职责（completed→active） */
async function resumeTask(id) {
    if (_taskLoading) return;
    _taskLoading = true;
    try {
        await apiClient.request('/api/v2/tasks/' + id + '/resume', {method: 'POST'});
        toast('Task 已恢复', 'success');
        _taskLoading = false;
        loadTaskProgress();
        loadTaskSequence();
    } catch (e) {
        _taskLoading = false;
        toast('操作失败: ' + e.message, 'danger');
    }
}

async function completeTask(id) {
    if (_taskLoading) return;
    if (!confirm("标记此 Task 为已完成？")) return;
    _taskLoading = true;
    try {
        await apiClient.request('/api/v2/tasks/' + id + '/complete', {method: 'POST'});
        toast('Task 已完成', 'success');
        // 就地更新该行状态，避免整表重渲染闪烁
        if (_taskSeqCache && _taskSeqCache[id]) {
            _taskSeqCache[id].metadata = _taskSeqCache[id].metadata || {};
            _taskSeqCache[id].metadata.status = 'completed';
        }
        var row = document.querySelector('[data-task-id="' + CSS.escape(id) + '"]');
        if (row && _taskSeqCache && _taskSeqCache[id]) {
            row.outerHTML = renderTaskRow(_taskSeqCache[id]);
        }
        _taskLoading = false;
        loadTaskProgress();
    } catch (e) {
        _taskLoading = false;
        toast('操作失败: ' + e.message, 'danger');
    }
}

async function archiveTask(id) {
    if (_taskLoading) return;
    if (!confirm("归档此 Task？可在记忆管理中恢复。")) return;
    _taskLoading = true;
    try {
        await apiClient.request('/api/v2/tasks/' + id + '/archive', {method: 'POST'});
        toast('Task 已归档', 'secondary');
        _taskLoading = false;
        loadTaskProgress();
        loadTaskSequence();
    } catch (e) {
        _taskLoading = false;
        toast('操作失败: ' + e.message, 'danger');
    }
}

async function deleteTask(id) {
    if (_taskLoading) return;
    if (!confirm("确定永久删除此 Task？不可恢复！")) return;
    _taskLoading = true;
    try {
        await apiClient.request('/api/v2/tasks/' + id, {method: 'DELETE'});
        toast('Task 已删除', 'danger');
        _taskLoading = false;
        loadTaskProgress();
        loadTaskSequence();
    } catch (e) {
        _taskLoading = false;
        toast('操作失败: ' + e.message, 'danger');
    }
}

/* 内联编辑，直接调用 task 专用端点（修复 C1） */
async function editTask(id) {
    if (_taskLoading) return;
    var newContent = prompt("编辑 Task 内容：");
    if (!newContent) return;
    _taskLoading = true;
    try {
        await apiClient.request('/api/v2/tasks/' + id, {
            method: 'PUT',
            body: JSON.stringify({document: newContent})
        });
        toast('Task 已更新', 'success');
        _taskLoading = false;
        loadTaskProgress();
    } catch (e) {
        _taskLoading = false;
        toast('操作失败: ' + e.message, 'danger');
    }
}

// === v0.7.1: Task 序列（下区）功能 ===

/* 解析 task document（可能为 TASK_EVAL JSON 结构体） */
function parseTaskEval(task) {
    var doc = task.document || '';
    if (!doc) return { goal: '(无描述)', done: [], todo: [], blocked: [] };
    try {
        var obj = typeof doc === 'string' ? JSON.parse(doc) : doc;
        var progress = obj.progress || obj;
        return {
            goal: obj.goal || obj.title || obj.summary || doc,
            done: progress.done || obj.done || [],
            todo: progress.todo || obj.todo || [],
            blocked: progress.blocked || obj.blocked || [],
        };
    } catch (e) {
        return { goal: doc || '(无描述)', done: [], todo: [], blocked: [] };
    }
}

/* 加载 Task 序列（下区），支持日期筛选 */
async function loadTaskSequence(date) {
    // 不传 date 时从日期选择框读取
    if (date === undefined) {
        var dateInput = document.getElementById('task-sequence-date');
        date = dateInput ? dateInput.value : '';
    }
    var container = document.getElementById('task-sequence-container');
    if (!container) { return; }
    container.style.display = '';
    var list = document.getElementById('task-sequence-list');
    if (!list) return;
    list.innerHTML = renderSkeleton('list');
    try {
        var data = await apiClient.request('/api/v2/tasks?limit=50');
        var tasks = data.tasks || [];
        if (tasks.length === 0) {
            list.innerHTML = '<div class="text-center text-secondary small py-3">暂无记录</div>';
            return;
        }
        // 前端日期过滤（API 不支持 date 参数）
        var filtered = tasks;
        if (date) {
            filtered = tasks.filter(function(t) {
                var meta = t.metadata || {};
                var ts = meta.updated_at || meta.timestamp || 0;
                var taskDate = new Date(ts * 1000).toISOString().split('T')[0];
                return taskDate === date;
            });
            if (filtered.length === 0) {
                list.innerHTML = '<div class="text-center text-secondary small py-3">该日期无 task 记录</div>';
                return;
            }
        }
        // 写入缓存（供 toggleTaskRow 展开详情用）
        _taskSeqCache = {};
        filtered.forEach(function(t) { _taskSeqCache[t.id] = t; });
        var html = '';
        filtered.forEach(function(t) { html += renderTaskRow(t); });
        list.innerHTML = html;
        _taskSeqOffset = 50;
        // 更新标题统计（与活跃任务 样式一致）
        var heading = document.getElementById('task-sequence-heading');
        if (heading) heading.innerHTML = '<span class="badge bg-secondary me-1">' + filtered.length + '</span>任务序列';
    } catch (e) {
        list.innerHTML = '<div class="text-danger small text-center py-3">加载失败: ' + escapeHtml(e.message) + '</div>';
    }
}

/* 分页加载更多 task 记录 */
async function loadMoreTasks() {
    var list = document.getElementById('task-sequence-list');
    try {
        var data = await apiClient.request('/api/v2/tasks?limit=50&offset=' + _taskSeqOffset);
        var tasks = data.tasks || [];
        if (tasks.length === 0) {
            toast('已加载全部记录', 'secondary');
            return;
        }
        var html = '';
        tasks.forEach(function(t) { html += renderTaskRow(t); });
        list.insertAdjacentHTML('beforeend', html);
        _taskSeqOffset += tasks.length;
        tasks.forEach(function(t) { _taskSeqCache[t.id] = t; });
    } catch (e) {
        toast('加载失败: ' + e.message, 'danger');
    }
}

/* 渲染单条 task 序列行（状态徽章 + goal + 时间 + 操作按钮） */
function renderTaskRow(task) {
    var meta = task.metadata || {};
    var status = meta.status || 'unknown';
    var labelMap = {pending: '待定', active: '活跃', completed: '已完成', archived: '已归档'};
    var colorMap = {pending: 'secondary', active: 'success', completed: 'primary', archived: 'secondary'};
    var label = labelMap[status] || status;
    var color = colorMap[status] || 'secondary';
    var parsed = parseTaskEval(task);
    var goal = parsed.goal;
    var ts = meta.created_at || 0;

    var html = '<div class="d-flex justify-content-between align-items-center py-1 px-2 border-bottom border-secondary border-opacity-25" data-task-id="' + escapeHtml(task.id) + '" onclick="toggleTaskRow(this, \'' + escapeHtml(task.id) + '\')">';
    html += '<div><span class="badge bg-' + color + ' bg-opacity-10 text-' + color + ' me-2" style="min-width:3rem;">' + label + '</span>';
    html += '<span class="small">' + escapeHtml(goal) + '</span>';
    html += '<span class="small text-secondary ms-2">' + (ts ? timeAgo(ts) : '') + '</span></div>';
    html += '<div class="d-flex gap-1 align-items-center">';

    if (status === 'pending') {
        html += '<button class="btn btn-sm btn-outline-success py-0 px-1" onclick="event.stopPropagation();completeTask(\'' + escapeHtml(task.id) + '\')" title="完成"><i class="bi bi-check-lg"></i></button>';
    }
    if (status !== 'active') {
        html += '<button class="btn btn-sm btn-outline-success py-0 px-1" onclick="event.stopPropagation();activateTask(\'' + escapeHtml(task.id) + '\')" title="激活"><i class="bi bi-arrow-counterclockwise"></i></button>';
    }
    if (status !== 'archived' && status !== 'active') {
        html += '<button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="event.stopPropagation();deleteTask(\'' + escapeHtml(task.id) + '\')" title="删除"><i class="bi bi-trash"></i></button>';
    }

    html += '</div></div>';
    return html;
}

/* 展开/折叠行：显示完整 done/todo/blocked */
function toggleTaskRow(el, taskId) {
    var detail = document.getElementById('task-detail-' + taskId);
    if (detail) { detail.remove(); return; }
    var task = _taskSeqCache[taskId];
    if (!task) { toast('未找到 task 详情', 'warning'); return; }
    var parsed = parseTaskEval(task);
    var html = '<div id="task-detail-' + taskId + '" class="px-3 py-2 bg-dark bg-opacity-25 small">';
    if (parsed.done.length > 0) {
        html += '<div class="text-success mb-1">✅ 已完成</div><ul class="list-unstyled mb-2" style="padding-left:1.2rem;">';
        parsed.done.forEach(function(i) { html += '<li class="text-success-emphasis">' + escapeHtml(i) + '</li>'; });
        html += '</ul>';
    }
    if (parsed.todo.length > 0) {
        html += '<div class="text-warning mb-1">📋 待做</div><ul class="list-unstyled mb-2" style="padding-left:1.2rem;">';
        parsed.todo.forEach(function(i) { html += '<li class="text-warning-emphasis">☐ ' + escapeHtml(i) + '</li>'; });
        html += '</ul>';
    }
    if (parsed.blocked.length > 0) {
        html += '<div class="text-danger mb-1">🚫 阻塞</div><ul class="list-unstyled mb-2" style="padding-left:1.2rem;">';
        parsed.blocked.forEach(function(i) { html += '<li class="text-danger">✗ ' + escapeHtml(i) + '</li>'; });
        html += '</ul>';
    }
    html += '</div>';
    el.insertAdjacentHTML('afterend', html);
}

/* 展开/收起全部任务详情 */
var _taskSeqAllExpanded = false;

function toggleAllTaskDetails() {
    _taskSeqAllExpanded = !_taskSeqAllExpanded;
    var btn = document.getElementById('btn-expand-all');
    var list = document.getElementById('task-sequence-list');
    if (!list) return;

    if (_taskSeqAllExpanded) {
        // 展开全部：遍历序列行，逐行展开详情
        list.querySelectorAll('[data-task-id]').forEach(function(row) {
            var taskId = row.getAttribute('data-task-id');
            var detail = document.getElementById('task-detail-' + taskId);
            if (!detail && _taskSeqCache[taskId]) {
                var parsed = parseTaskEval(_taskSeqCache[taskId]);
                var html = '<div id="task-detail-' + taskId + '" class="px-3 py-2 bg-dark bg-opacity-25 small">';
                if (parsed.done.length > 0) {
                    html += '<div class="text-success mb-1">✅ 已完成</div><ul class="list-unstyled mb-2" style="padding-left:1.2rem;">';
                    parsed.done.forEach(function(i) { html += '<li class="text-success-emphasis">' + escapeHtml(i) + '</li>'; });
                    html += '</ul>';
                }
                if (parsed.todo.length > 0) {
                    html += '<div class="text-warning mb-1">📋 待做</div><ul class="list-unstyled mb-2" style="padding-left:1.2rem;">';
                    parsed.todo.forEach(function(i) { html += '<li class="text-warning-emphasis">☐ ' + escapeHtml(i) + '</li>'; });
                    html += '</ul>';
                }
                if (parsed.blocked.length > 0) {
                    html += '<div class="text-danger mb-1">🚫 阻塞</div><ul class="list-unstyled mb-2" style="padding-left:1.2rem;">';
                    parsed.blocked.forEach(function(i) { html += '<li class="text-danger">✗ ' + escapeHtml(i) + '</li>'; });
                    html += '</ul>';
                }
                html += '</div>';
                row.insertAdjacentHTML('afterend', html);
            }
        });
        if (btn) btn.innerHTML = '<i class="bi bi-arrows-collapse"></i>';
    } else {
        // 收起全部
        list.querySelectorAll('[id^="task-detail-"]').forEach(function(d) { d.remove(); });
        if (btn) btn.innerHTML = '<i class="bi bi-arrows-expand"></i>';
    }
}

/* 激活 Task */
async function activateTask(id) {
    if (_taskLoading) return;
    if (!confirm("激活此 Task？原活跃任务 将自动标记为已完成。")) return;
    _taskLoading = true;
    try {
        await apiClient.request('/api/v2/tasks/' + id + '/activate', {method: 'POST'});
        toast('Task 已激活', 'success');
        // 就地更新行状态，避免整表重渲染闪烁
        if (_taskSeqCache && _taskSeqCache[id]) {
            _taskSeqCache[id].metadata = _taskSeqCache[id].metadata || {};
            _taskSeqCache[id].metadata.status = 'active';
        }
        var row = document.querySelector('[data-task-id="' + CSS.escape(id) + '"]');
        if (row && _taskSeqCache && _taskSeqCache[id]) {
            row.outerHTML = renderTaskRow(_taskSeqCache[id]);
        }
        _taskLoading = false;
        loadTaskProgress();
    } catch (e) {
        _taskLoading = false;
        toast('操作失败: ' + e.message, 'danger');
    }
}

/* 任务模式：加载当前模式并更新 UI 指示器 */
async function loadTaskMode() {
    var btn = document.getElementById('btn-task-mode');
    var label = document.getElementById('task-mode-label');
    if (!btn || !label) return;
    try {
        var data = await apiClient.request('/api/v2/tasks/mode');
        var isAuto = data.mode === 'auto';
        btn.innerHTML = isAuto
            ? '<i class="bi bi-toggle-on text-info"></i> <span id="task-mode-label">自动</span>'
            : '<i class="bi bi-toggle-off"></i> <span id="task-mode-label">手动</span>';
        btn.title = isAuto
            ? '当前: 自动模式 (新 TASK_EVAL 自动替换旧任务)'
            : '当前: 手动模式 (新 TASK_EVAL 进入待定队列)';
        btn.className = 'btn btn-sm py-0 px-1 ' + (isAuto ? 'btn-outline-info' : 'btn-outline-secondary');
    } catch (e) {
        console.warn('加载任务模式失败:', e.message);
    }
}

/* 任务模式：切换 auto ↔ manual */
async function toggleTaskMode() {
    try {
        var cur = await apiClient.request('/api/v2/tasks/mode');
        var newMode = cur.mode === 'auto' ? 'manual' : 'auto';
        var result = await apiClient.request('/api/v2/tasks/mode', {
            method: 'POST',
            body: JSON.stringify({mode: newMode}),
        });
        if (result.ok) {
            toast('任务模式已切换为: ' + (newMode === 'auto' ? '🔄 自动' : '🖐️ 手动'), 'info');
            await loadTaskMode();
        }
    } catch (e) {
        toast('切换失败: ' + e.message, 'danger');
    }
}

/* 任务审计入口（延后至 v0.7.2，当前复用日期筛选） */
function openTaskAudit() {
    var date = document.getElementById('task-sequence-date').value;
    loadTaskSequence(date);
    toast('任务审计功能将在 v0.7.2 中提供', 'secondary');
}

async function triggerBriefing() {
    var btn = document.getElementById('brf-generate-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>生成中...';
    updateBrfBadge('generating');
    try {
        var body = {};
        var dateEl = document.getElementById('brf-date');
        if (dateEl && dateEl.value) body.date = dateEl.value;
        var epSel = document.getElementById('brf-endpoint-select');
        if (epSel && epSel.value) body.llm_endpoint = epSel.value;
        var promptSel = document.getElementById('brf-prompt-select');
        if (promptSel && promptSel.value) body.prompt_id = promptSel.value;
        await apiClient.request('/api/v2/briefing/generate', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        toast('简报生成完成', 'success');
        state.brf.statsCache = null;
        await loadBriefingPanel();
    } catch (e) {
        toast('生成失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>立即生成';
}

// === 5. Manual Suggestions (配置组 → 用户建议) ===
// GET /api/manual-suggestions
// DELETE /api/manual-suggestions/{id}
// PUT /api/manual-suggestions/{id}/toggle-disable

async function loadManualSuggestions() {
    var container = document.getElementById('manual-suggestions-container');
    if (!container) return;
    container.innerHTML = renderSkeleton('list');
    try {
        var data = await apiClient.request('/api/manual-suggestions');
        var items = data.items || [];
        // 更新建议总数
        var countEl = document.getElementById('suggestion-total-count');
        if (countEl) countEl.textContent = items.length;

        if (!items.length) {
            container.innerHTML = '<div class="empty-state">' +
                '<i class="bi bi-hand-index-thumb"></i>' +
                '<p>暂无用户建议</p>' +
                '<p class="hint">配置用户建议后，AI 会在适当时机主动推送有价值的信息</p>' +
                '</div>';
            return;
        }

        container.innerHTML = items.map(function(item) {
            var disabled = item.disabled === true;
            var opacity = disabled ? 'opacity-50' : '';
            var keywords = item.trigger_keywords || [];
            var kwTags = Array.isArray(keywords) ? keywords.map(function(k) {
                return '<span class="badge bg-success bg-opacity-10 text-success me-1">' + escapeHtml(k) + '</span>';
            }).join('') : '';
            var modeBadge = item.trigger_mode === 'always'
                ? '<span class="badge bg-secondary bg-opacity-25 text-secondary">始终</span>'
                : '<span class="badge bg-info bg-opacity-25 text-info">关键词</span>';
            var expiryBadge = '';
            if (item.validity_minutes > 0 && item.expires_at > 0) {
                if (Date.now() / 1000 > item.expires_at) {
                    expiryBadge = '<span class="badge bg-danger bg-opacity-25 text-danger">已过期</span>';
                }
            }
            var prioColor = {high: 'danger', medium: 'warning', low: 'success'}[item.priority] || 'secondary';
            var ts = item.timestamp ? timeAgo(item.timestamp) : '';
            var safeContent = escapeHtml(item.content || '').substring(0, 100);
            var safeId = escapeHtml(item.id);
            var toggleChecked = disabled ? '' : 'checked';
            return '<div class="border rounded p-2 mb-1 bg-black bg-opacity-25 ' + opacity + '">' +
                // 第一行：内容 + 开关 + 操作
                '<div class="d-flex justify-content-between align-items-start mb-1">' +
                    '<div class="d-flex gap-2 align-items-center flex-wrap flex-grow-1 me-2">' +
                        '<span class="badge bg-' + prioColor + ' bg-opacity-10 text-' + prioColor + '" style="font-size:.6rem">' + escapeHtml(item.priority) + '</span>' +
                        '<span class="small fw-semibold">' + safeContent + '</span>' +
                    '</div>' +
                    '<div class="d-flex gap-1 align-items-center flex-shrink-0">' +
                        '<div class="form-check form-switch d-inline-block m-0 p-0" style="min-height:auto">' +
                            '<input class="form-check-input" type="checkbox" role="switch" ' + toggleChecked +
                            ' style="float:none;cursor:pointer;margin:0" onclick="toggleManualSuggestion(\'' + safeId + '\', this)">' +
                        '</div>' +
                        '<span class="small fw-bold ' + (disabled ? 'text-danger' : 'text-success') + '" style="min-width:3em">' + (disabled ? '已停用' : '启用') + '</span>' +
                        '<button class="btn btn-sm btn-outline-secondary py-0 px-1" onclick="window.editManualSuggestion(\'' + safeId + '\')" title="详情/编辑"><i class="bi bi-pencil"></i></button>' +
                        '<button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="deleteManualSuggestion(\'' + safeId + '\')" title="删除"><i class="bi bi-x"></i></button>' +
                    '</div>' +
                '</div>' +
                // 第二行：标签 + 元数据
                '<div class="d-flex gap-2 align-items-center flex-wrap small">' +
                    modeBadge +
                    expiryBadge +
                    kwTags +
                    '<span class="text-secondary">命中 ' + (item.hit_count || 0) + ' 次</span>' +
                    '<span class="text-secondary">冷却 ' + (item.cooldown_minutes || 0) + 'min</span>' +
                    '<span class="text-secondary">有效 ' + (item.validity_minutes || 0) + 'min</span>' +
                    '<span class="text-secondary">' + ts + '</span>' +
                '</div>' +
            '</div>';
        }).join('');
    } catch (e) {
        container.innerHTML = '<div class="text-danger small text-center py-3">加载失败: ' + escapeHtml(e.message) + '</div>';
    }
}

async function toggleManualSuggestion(id, checkbox) {
    try {
        var r = await apiClient.request('/api/manual-suggestions/' + id + '/toggle-disable', {method: 'PUT'});
        var isDisabled = r.disabled;
        toast(isDisabled ? '已停用' : '已启用', 'success');
        // 刷新列表保持显示一致
        loadManualSuggestions();
    } catch (e) {
        toast('操作失败: ' + e.message, 'danger');
        checkbox.checked = !checkbox.checked;  // 还原开关状态
    }
}

async function deleteManualSuggestion(id) {
    if (!confirm('确定删除此用户建议？')) return;
    try {
        await apiClient.request('/api/manual-suggestions/' + id, {method: 'DELETE'});
        toast('已删除', 'success');
        loadManualSuggestions();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

// --- 暂停推送开关（配合 #user-suggestions-panel） ---
async function fetchNoSuggestionsStatus() {
    try {
        var data = await apiClient.request('/api/suggestions/no-suggestions-status', { method: 'GET' });
        var toggle = document.getElementById('sug-pause-toggle');
        if (toggle) toggle.checked = data.enabled === true;
    } catch (e) {}
}

async function togglePause() {
    var toggle = document.getElementById('sug-pause-toggle');
    var wasEnabled = toggle ? toggle.checked : false;
    try {
        var data = await apiClient.request('/api/suggestions/toggle-pause', { method: 'POST' });
        if (toggle) toggle.checked = data.enabled === true;
        toast(data.enabled ? '已暂停推送' : '已恢复推送', data.enabled ? 'warning' : 'success');
    } catch (e) {
        if (toggle) toggle.checked = wasEnabled;
        toast('操作失败: ' + e.message, 'danger');
    }
}

// --- 用户建议 Modal 管理 ---

var msKeywords = [];

function initManualSuggestionModal() {
    var keywordInput = document.getElementById('ms-keyword-input');
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

    document.getElementById('ms-trigger-mode')?.addEventListener('change', function() {
        toggleMsKeywordsVisibility(this.value);
    });

    var modalEl = document.getElementById('manualSuggestionModal');
    if (modalEl) {
        modalEl.addEventListener('show.bs.modal', function() {
            var mode = document.getElementById('ms-trigger-mode').value;
            toggleMsKeywordsVisibility(mode);
        });
        modalEl.addEventListener('hidden.bs.modal', resetMsForm);
    }

    // 关键词标签点击删除（事件委托）
    document.getElementById('ms-keyword-tags')?.addEventListener('click', function(e) {
        if (e.target.classList.contains('bi-x')) {
            var span = e.target.closest('[data-keyword]');
            if (span) {
                removeMsKeyword(decodeURIComponent(span.getAttribute('data-keyword')));
            }
        }
    });
}

function toggleMsKeywordsVisibility(mode) {
    var section = document.getElementById('ms-keywords-section');
    var hint = document.getElementById('ms-always-hint');
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
    var errEl = document.getElementById('ms-keyword-error');
    if (keyword.length > 50) {
        if (errEl) { errEl.textContent = '关键词不超过 50 字符'; errEl.style.display = ''; }
        return;
    }
    if (msKeywords.length >= 10) {
        if (errEl) { errEl.textContent = '最多 10 个关键词'; errEl.style.display = ''; }
        return;
    }
    if (msKeywords.includes(keyword)) {
        if (errEl) { errEl.textContent = '关键词已存在: ' + keyword; errEl.style.display = ''; }
        return;
    }
    if (errEl) errEl.style.display = 'none';
    msKeywords.push(keyword);
    renderMsTags();
}

function removeMsKeyword(keyword) {
    msKeywords = msKeywords.filter(function(k) { return k !== keyword; });
    renderMsTags();
}

function renderMsTags() {
    var container = document.getElementById('ms-keyword-tags');
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

function resetMsForm() {
    document.getElementById('ms-edit-id').value = '';
    document.getElementById('ms-content').value = '';
    msKeywords = [];
    renderMsTags();
    document.getElementById('ms-priority').value = 'medium';
    document.getElementById('ms-trigger-mode').value = 'keyword';
    document.getElementById('ms-cooldown').value = 60;
    document.getElementById('ms-validity').value = 0;
    var errEl = document.getElementById('ms-keyword-error');
    if (errEl) errEl.style.display = 'none';
    toggleMsKeywordsVisibility('keyword');
    document.getElementById('ms-modal-title').innerHTML = '<i class="bi bi-plus-circle me-1"></i>创建用户建议';
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
            toast('已更新用户建议', 'success');
        } else {
            await apiClient.request('/api/manual-suggestions', { method: 'POST', body: JSON.stringify(body) });
            toast(mode === 'always' ? '用户建议已创建，每次对话将自动推送' : '用户建议已创建，下次命中关键词时将触发推送', 'success');
        }
        bootstrap.Modal.getInstance(document.getElementById('manualSuggestionModal')).hide();
        loadManualSuggestions();
    } catch (e) {
        toast((isEdit ? '保存' : '创建') + '失败: ' + (e.message || e), 'danger');
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<i class="bi bi-check-lg"></i> 保存';
    }
}

// --- 编辑用户建议（供用户建议面板按钮调用） ---
window.editManualSuggestion = function(id) {
    apiClient.request('/api/manual-suggestions', { method: 'GET' }).then(function(data) {
        var items = data.items || [];
        var item = null;
        for (var i = 0; i < items.length; i++) {
            if (items[i].id === id) { item = items[i]; break; }
        }
        if (!item) { toast('找不到该用户建议', 'danger'); return; }

        document.getElementById('ms-edit-id').value = id;
        document.getElementById('ms-content').value = item.content || '';
        document.getElementById('ms-modal-title').innerHTML = '<i class="bi bi-pencil me-1"></i>编辑用户建议';

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

// --- 提升为建议（供知识管理面板按钮调用） ---
window.promoteToSuggestion = function(content) {
    resetMsForm();
    var modalEl = document.getElementById('manualSuggestionModal');
    if (!modalEl) { toast('页面未完全加载', 'warning'); return; }
    document.getElementById('ms-content').value = '关于记忆的建议：' + (content || '');
    var modal = new bootstrap.Modal(modalEl);
    modal.show();
};

async function loadInjectionMonitor() {
    var knowledgeList = document.getElementById('inject-knowledge-list');
    var manualList = document.getElementById('inject-manual-list');
    if (!knowledgeList || !manualList) return;
    try {
        var data = await apiClient.request('/api/v2/activity-log?page=1&page_size=50');
        var items = data.items || [];
        var knowledgeItems = items.filter(function(i) { return i.injection_type === 'knowledge'; });
        var manualItems = items.filter(function(i) { return i.injection_type === 'manual'; });

        if (knowledgeItems.length === 0) {
            knowledgeList.innerHTML = '<div class="text-center text-secondary py-3">暂无知识注入记录</div>';
        } else {
            var html = '<div class="list-group list-group-flush">';
            knowledgeItems.forEach(function(i) {
                var ts = i.timestamp ? timeAgo(i.timestamp) : '';
                var types = (i.types || []).join(', ') || '?';
                var ids = (i.memory_ids || []).slice(0, 3).map(function(id) { return id.slice(0, 8); }).join(', ');
                html += '<div class="list-group-item bg-transparent border-secondary"><div class="d-flex justify-content-between"><span><span class="badge bg-info bg-opacity-25 text-info me-1">知识</span>类型: ' + escapeHtml(types) + '</span><small class="text-secondary">' + ts + '</small></div><div class="small text-secondary mt-1">' + (ids ? 'ID: ' + ids : '') + '</div></div>';
            });
            html += '</div>';
            knowledgeList.innerHTML = html;
        }

        if (manualItems.length === 0) {
            manualList.innerHTML = '<div class="text-center text-secondary py-3">暂无用户建议注入记录</div>';
        } else {
            var html = '<div class="list-group list-group-flush">';
            manualItems.forEach(function(i) {
                var ts = i.timestamp ? timeAgo(i.timestamp) : '';
                var count = i.count || (i.memory_ids || []).length || '?';
                html += '<div class="list-group-item bg-transparent border-secondary"><div class="d-flex justify-content-between"><span><span class="badge bg-warning bg-opacity-25 text-warning me-1">手工</span>注入 ' + count + ' 条</span><small class="text-secondary">' + ts + '</small></div></div>';
            });
            html += '</div>';
            manualList.innerHTML = html;
        }
    } catch(e) {
        knowledgeList.innerHTML = '<div class="text-center text-danger py-3">加载失败</div>';
        manualList.innerHTML = '<div class="text-center text-danger py-3">加载失败</div>';
    }
}

// ==== v0.7.1: 监控面板 ====

// 简报注入开关
function toggleBriefingInjection(briefingId, enabled) {
    if (!briefingId) return;
    apiClient.request('/api/v2/briefing/toggle-injection', {
        method: 'POST',
        body: JSON.stringify({briefing_id: briefingId, enabled: enabled}),
        headers: {'Content-Type': 'application/json'},
    }).then(function(res) {
        if (res.ok) {
            toast('简报注入' + (enabled ? '已开启' : '已关闭'), 'success');
        } else {
            toast('操作失败: ' + (res.error || '未知错误'), 'danger');
            // 回滚开关状态
            loadMonitorPanel();
            loadBriefingToggle();
        }
    }).catch(function(e) {
        toast('请求失败: ' + e.message, 'danger');
        loadMonitorPanel();
        loadBriefingToggle();
    });
}

function loadMonitorPanel() {
    var container = document.getElementById('monitor-container');
    if (!container) return;
    container.innerHTML = renderSkeleton('card');
    apiClient.request('/api/v2/monitor/overview').then(function(data) {
        var html = '';

        // 4 卡片行
        html += '<div class="row g-2 mb-3">';
        var cards = [
            {key: 'task', icon: 'bi-list-task', color: 'primary'},
            {key: 'briefing', icon: 'bi-journal-text', color: 'info'},
            {key: 'knowledge', icon: 'bi-database', color: 'success'},
            {key: 'suggestion', icon: 'bi-hand-index-thumb', color: 'warning'},
        ];
        cards.forEach(function(c) {
            var card = data.cards[c.key] || {status: 'none', label: '无'};
            // 四卡片统一格式：名称主标签 + 状态/数量小字
            var mainLabel = card.label;
            var statusLine = '';
            if (c.key === 'briefing') {
                var d = card.date || new Date().toISOString().slice(0, 10);
                mainLabel = d + ' 简报';
                statusLine = '<div class="small text-secondary mt-1" style="font-size:0.65rem">' + escapeHtml(card.label) + '</div>';
            } else if (c.key === 'task') {
                if (card.status === 'none') {
                    mainLabel = '当前无任务';
                    statusLine = '<div class="small text-secondary mt-1" style="font-size:0.65rem">等待中</div>';
                } else if (card.detail) {
                    mainLabel = escapeHtml(card.detail);
                    statusLine = '<div class="small text-secondary mt-1" style="font-size:0.65rem">' + escapeHtml(card.label) + '</div>';
                }
            } else if (c.key === 'knowledge') {
                mainLabel = '知识';
                statusLine = '<div class="small text-secondary mt-1" style="font-size:0.65rem">' + escapeHtml(card.label) + '</div>';
            } else if (c.key === 'suggestion') {
                mainLabel = '建议';
                statusLine = '<div class="small text-secondary mt-1" style="font-size:0.65rem">' + escapeHtml(card.label) + '</div>';
            }
            html += '<div class="col-6 col-md-3">' +
                '<div class="card h-100">' +
                '<div class="card-body py-2 px-3 text-center">' +
                '<div class="small"><i class="bi ' + c.icon + ' me-1"></i>' +
                '<span class="text-' + c.color + '">' + escapeHtml(mainLabel) + '</span></div>' +
                statusLine +
                '</div></div></div>';
        });
        html += '</div>';

        // 注入详情时间线（与事件看板一致卡片样式）
        html += '<h6 class="mb-2">注入详情</h6>';
        var timeline = data.injection_timeline || [];
        if (timeline.length === 0) {
            html += '<div class="empty-state py-3">' +
                '<i class="bi bi-activity"></i>' +
                '<p>暂无注入记录</p>' +
                '<p class="hint">当有知识被注入到 AI 上下文中时会显示在这里</p>' +
                '</div>';
        } else {
            var typeIconColor = {
                'task':              {icon: 'bi-list-task',          label: '任务',     color: 'primary'},
                'briefing':          {icon: 'bi-journal-text',       label: '简报',     color: 'info'},
                'solution':          {icon: 'bi-lightbulb',          label: '方案',  color: 'success'},
                'decision':          {icon: 'bi-signpost-2',         label: '决策',     color: 'warning'},
                'lesson':            {icon: 'bi-book',               label: '经验',     color: 'danger'},
                'process':           {icon: 'bi-gear',               label: '流程',     color: 'secondary'},
                'manual_suggestion': {icon: 'bi-hand-index-thumb',   label: '建议',  color: 'info'},
                'active_push':       {icon: 'bi-megaphone',          label: '推送',  color: 'warning'},
            };
            timeline.forEach(function(item) {
                var tc = typeIconColor[item.type] || {icon: 'bi-question-circle', label: item.type, color: 'secondary'};
                html += '<div class="d-flex align-items-start gap-2 mb-2 p-2 border rounded">';
                html += '<i class="bi ' + tc.icon + ' text-' + tc.color + '" style="font-size:1.1rem"></i>';
                html += '<div class="flex-grow-1">';
                html += '<div class="d-flex justify-content-between">';
                html += '<span class="badge bg-' + tc.color + ' bg-opacity-10 text-' + tc.color + '">' + tc.label + '</span>';
                html += '<span class="small text-secondary">' +
                    (item.score != null ? item.score.toFixed(2) : '—') +
                    ' <small>' + item.time + '</small></span>';
                html += '</div>';
                html += '<div class="small mt-1">' + escapeHtml(item.content) + '</div>';
                html += '</div></div>';
            });
        }

        	                // 指令面板标题 + 两行卡片
        var te = data.instruction_panel || {};
        html += '<h6 class="mb-2 mt-3">指令面板</h6>';
        // 任务指令卡片
        html += '<div class="row g-2 mb-2"><div class="col-12"><div class="card"><div class="card-body py-2 px-3">' +
            '<div class="d-flex align-items-center gap-2 mb-1">' +
            '<i class="bi bi-file-text text-primary"></i>' +
            '<span class="small fw-semibold">任务指令</span>' +
            '<span class="badge bg-' + (te.task_eval_injected ? 'success' : 'secondary') + ' ms-auto" style="font-size:0.6rem">' +
            (te.task_eval_injected ? '已注入' : '未注入') + '</span></div>' +
            '<div class="small text-secondary" style="font-size:0.7rem;white-space:pre-wrap">' +
            escapeHtml(te.task_eval_instruction || '') + '</div>' +
            '</div></div></div></div>';
        // 行为引导卡片
        html += '<div class="row g-2 mb-2"><div class="col-12"><div class="card" id="behavior-guide-card"><div class="card-body py-2 px-3">' +
            '<div class="d-flex align-items-center gap-2 mb-1">' +
            '<i class="bi bi-journal-code text-info"></i>' +
            '<span class="small fw-semibold">行为引导</span>' +
            '<span class="badge bg-secondary ms-auto" id="bg-status-badge" style="font-size:0.6rem">加载中...</span></div>' +
            '<div class="small text-secondary" id="bg-content" style="font-size:0.7rem;white-space:pre-wrap">加载中...</div>' +
            '</div></div></div></div>';

        container.innerHTML = html;

        // 异步加载行为引导详情
        apiClient.request('/api/v2/behavior-guide').then(function(bg) {
            var badge = document.getElementById('bg-status-badge');
            if (badge) {
                badge.textContent = bg.loaded ? '已注入' : '未注入';
                badge.className = 'badge bg-' + (bg.loaded ? 'success' : 'secondary') + ' ms-auto';
                badge.style.fontSize = '0.6rem';
            }
            var content = document.getElementById('bg-content');
            if (content) {
                content.textContent = bg.loaded ? bg.content : '';
            }
        }).catch(function() {
            var badge = document.getElementById('bg-status-badge');
            if (badge) {
                badge.textContent = '加载失败';
                badge.className = 'badge bg-danger ms-auto';
                badge.style.fontSize = '0.6rem';
            }
            var content = document.getElementById('bg-content');
            if (content) content.textContent = '';
        });
    }).catch(function(e) {
        container.innerHTML = '<div class="text-danger small py-3 text-center">加载失败: ' + escapeHtml(e.message) + '</div>';
    });
}

// v0.7.1: 监控面板 Tab 切换时加载
document.addEventListener('shown.bs.tab', function(e) {
    var target = e.target;
    if (target && target.getAttribute('data-bs-target') === '#monitor-panel') {
        loadMonitorPanel();
    }
});

// ==== 通用设置面板已移除（语言切换恢复至顶部工具栏） ====

// === 6. Lazy Loading Event Listeners ===
// 子面板 tab 切换时按需加载对应数据

document.querySelector('[data-bs-target="#task-progress-panel"]')?.addEventListener('shown.bs.tab', function() {
    loadTaskProgress();
    // v0.7.1: 切换 Tab 时同时加载序列
    loadTaskSequence();
});
document.querySelector('[data-bs-target="#memory-stream-panel"]')?.addEventListener('shown.bs.tab', function() {
    loadMemoryStream();
});
document.querySelector('[data-bs-target="#user-suggestions-panel"]')?.addEventListener('shown.bs.tab', function() {
    loadManualSuggestions();
});
document.querySelector('[data-bs-target="#watchlist-panel"]')?.addEventListener('shown.bs.tab', function() {
    loadWatchlist();
});
document.querySelector('[data-bs-target="#injection-monitor-panel"]')?.addEventListener('shown.bs.tab', function() {
    loadInjectionMonitor();
});
document.querySelector('[data-bs-target="#project-briefing-panel"]')?.addEventListener('shown.bs.tab', function() {
    state.brf.statsCache = null;
    loadBriefingPanel();
});

// 展开全部/收起全部
document.getElementById('brf-expand-all-btn')?.addEventListener('click', function() {
    state.brf._allExpanded = !state.brf._allExpanded;
    var icon = this.querySelector('i');
    icon.className = state.brf._allExpanded ? 'bi bi-arrows-collapse' : 'bi bi-arrows-expand';
    document.querySelectorAll('#brf-history-list .brf-row').forEach(function(row) {
        var id = row.getAttribute('data-id');
        var next = row.nextElementSibling;
        if (state.brf._allExpanded && (!next || !next.classList.contains('brf-detail-card'))) {
            loadBrfDetail(id, row);
        } else if (!state.brf._allExpanded && next && next.classList.contains('brf-detail-card')) {
            next.remove();
            row.querySelector('.brf-expand i').className = 'bi bi-chevron-right';
        }
    });
});

// ==== F9 SSE 实时推送 ====

// 单例 EventSource 实例（C4 约束）
var _sseClient = null;
var _ssePollInterval = null;
var _sseHealthTimer = null;
var _sseReconnectTimer = null;
var _sseConsecutiveFailures = 0;
var _sseMaxFailures = 3;
var _sseLastEventTime = Date.now();
var _taskLoading = false;
// v0.7.1: Task 序列分页偏移 + 缓存
var _taskSeqOffset = 50;
var _taskSeqCache = {};

function _sseTrackEvent() {
    _sseLastEventTime = Date.now();
    _sseConsecutiveFailures = 0;
}

function _sseFallback(reason) {
    console.log('[SSE] 降级轮询触发: reason=' + reason);
    if (_sseClient) {
        _sseClient.close();
        _sseClient = null;
    }
    if (_ssePollInterval) {
        clearInterval(_ssePollInterval);
        _ssePollInterval = null;
    }

    // 30s 轮询
    _ssePollInterval = setInterval(function() {
        loadMemoryStream();
        loadWatchlist();
        loadTaskProgress();
        loadManualSuggestions();
    }, 30000);
    console.log('[SSE] 降级轮询已启动 (30s 间隔)');

    // 10s 周期重连（替代单次 30s timer）
    if (_sseReconnectTimer) {
        clearInterval(_sseReconnectTimer);
    }
    _sseReconnectTimer = setInterval(function() {
        console.log('[SSE] 重连尝试...');
        fetch('/api/v2/sse-health').then(function(r) {
            if (r.ok) {
                console.log('[SSE] 重连成功，切回 SSE');
                clearInterval(_ssePollInterval);
                _ssePollInterval = null;
                clearInterval(_sseReconnectTimer);
                _sseReconnectTimer = null;
                _sseConsecutiveFailures = 0;
                initSSE();
            }
        }).catch(function() {
            console.log('[SSE] 重连失败，继续轮询');
        });
    }, 10000);
}

// ==== v0.7.1 简报聚合视图（替换旧 loadBriefing） ====
// 加载简报注入开关状态
function loadBriefingToggle() {
    var container = document.getElementById('brf-injection-toggle');
    if (!container) return;
    apiClient.request('/api/v2/monitor/overview').then(function(data) {
        var card = data.cards && data.cards.briefing;
        if (card && card.allow_toggle) {
            var checked = !card.delivered ? 'checked' : '';
            var isEnabled = !card.delivered;
            container.style.display = 'inline-flex';
            container.innerHTML =
                '<button class="btn btn-sm py-0 px-1 ' + (isEnabled ? 'btn-outline-info' : 'btn-outline-secondary') + '" ' +
                'onclick="toggleBriefingInjection(\'' + escapeHtml(card.briefing_id) + '\', ' + (!isEnabled) + '); loadBriefingToggle()" ' +
                'title="简报注入' + (isEnabled ? '已开启' : '已关闭') + '">' +
                '<i class="bi bi-toggle-' + (isEnabled ? 'on text-info' : 'off') + '"></i> ' +
                '<span>允许注入</span></button>';
        } else {
            container.style.display = 'none';
        }
    }).catch(function() {
        var c = document.getElementById('brf-injection-toggle');
        if (c) c.style.display = 'none';
    });
}

async function loadBriefingPanel() {
    await Promise.all([
        loadBrfStats(),
        loadBrfToday(),
        loadBrfFilters(),
    ]);
    loadBrfHistory(1);
    loadBriefingToggle();
    setTimeout(adjustBrfListHeight, 100);
}

function updateBrfBadge(dateOrState) {
    var badge = document.getElementById('brf-status-badge');
    if (!badge) return;
    if (dateOrState === 'generating') {
        badge.textContent = '生成中...';
        badge.className = 'badge bg-warning';
    } else if (dateOrState) {
        badge.textContent = '简报 ' + dateOrState;
        badge.className = 'badge bg-success';
    } else {
        badge.textContent = '未生成';
        badge.className = 'badge bg-secondary';
    }
}

// 加载简报端点/提示词选择器（仿今日回顾）
async function loadBrfFilters() {
    try {
        var data = await api('/api/llm/endpoints');
        var epSel = document.getElementById('brf-endpoint-select');
        if (!epSel) return;
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
    cascadeBrfPrompt();
}

function cascadeBrfPrompt() {
    var epSel = document.getElementById('brf-endpoint-select');
    var ep = epSel ? epSel.value : '';
    var sel = document.getElementById('brf-prompt-select');
    if (!sel) return;
    sel.innerHTML = '';
    sel.appendChild(Object.assign(document.createElement('option'), {value: '', textContent: '(自动选择提示词)'}));
    if (!_allTemplatesCache.length) return;
    var filtered;
    if (ep) {
        filtered = _allTemplatesCache.filter(function(t) {
            return t.template_type === 'briefing' && (t.id === ep + '@briefing' || t.id === 'default@briefing');
        });
        if (!filtered.length) {
            filtered = _allTemplatesCache.filter(function(t) { return t.template_type === 'briefing'; });
        }
    } else {
        filtered = _allTemplatesCache.filter(function(t) { return t.template_type === 'briefing'; });
    }
    if (!filtered.length && ep) {
        filtered = _allTemplatesCache.filter(function(t) { return t.template_type === 'briefing'; });
    }
    filtered.forEach(function(t) {
        var opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = t.name + ' v' + (t.version || 1);
        if (t.version) opt.dataset.version = t.version;
        sel.appendChild(opt);
    });
}

async function loadBrfStats() {
    var row = document.getElementById('brf-stats-row');
    if (!row) return;
    try {
        var thirtyDaysAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
        var data = await apiClient.request('/api/v2/briefing/history?limit=30&since_date=' + thirtyDaysAgo);
        var list = data.briefings || [];
        if (list.length === 0) { row.style.display = 'none'; return; }
        row.style.display = '';
        state.brf.statsCache = list;

        var totalSessions = 0, totalKnowledge = 0, fullDays = 0, consecutiveFull = 0, hasDataDays = 0;
        var latestGenerated = '--', today = new Date().toISOString().slice(0, 10);

        list.forEach(function(b) {
            hasDataDays++;
            totalSessions += (b.session_count || 0);
            totalKnowledge += (b.new_knowledge_count || 0);
            if (b.quality === 'full') fullDays++;
        });

        var sorted = list.slice().sort(function(a, b) { return a.briefing_date.localeCompare(b.briefing_date); });
        for (var i = sorted.length - 1; i >= 0; i--) {
            if (sorted[i].briefing_date >= today) continue;
            if (sorted[i].quality === 'full') consecutiveFull++;
            else break;
        }

        var avgRounds = hasDataDays > 0 ? Math.round(totalSessions / hasDataDays) : 0;
        if (list[0] && list[0].generated_at) latestGenerated = formatTime(list[0].generated_at);

        row.innerHTML =
            '<div class="col"><div class="card brf-stats-card py-2"><div class="small text-secondary">高质量天数</div><div class="fw-bold" style="color:#4fc3f7">' + fullDays + '</div></div></div>' +
            '<div class="col"><div class="card brf-stats-card py-2"><div class="small text-secondary">连续 full</div><div class="fw-bold" style="color:#81c784">' + consecutiveFull + '</div></div></div>' +
            '<div class="col"><div class="card brf-stats-card py-2"><div class="small text-secondary">总会话数</div><div class="fw-bold" style="color:#ffb74d">' + totalSessions + '</div></div></div>' +
            '<div class="col"><div class="card brf-stats-card py-2"><div class="small text-secondary">知识产出</div><div class="fw-bold" style="color:#ba68c8">' + totalKnowledge + '</div></div></div>' +
            '<div class="col"><div class="card brf-stats-card py-2"><div class="small text-secondary">日均轮次</div><div class="fw-bold" style="color:#e57373">' + avgRounds + '</div></div></div>' +
            '<div class="col"><div class="card brf-stats-card py-2"><div class="small text-secondary">最近生成</div><div class="fw-bold" style="font-size:0.85rem">' + escapeHtml(latestGenerated) + '</div></div></div>';
    } catch (e) {
        row.style.display = 'none';
    }
}

async function loadBrfToday() {
    var container = document.getElementById('brf-today-container');
    if (!container) return;
    container.innerHTML = '<div class="text-center text-secondary py-3"><div class="spinner-border spinner-border-sm me-2" role="status"></div><span>加载中...</span></div>';
    try {
        // 不传 date 参数，由后端返回最近5天最新简报
        var data = await apiClient.request('/api/v2/briefing/current');
        if (!data.exists) {
            container.innerHTML =
                '<div class="brf-today-card text-center py-4"><i class="bi bi-journal-text" style="font-size:2rem;display:block;margin-bottom:8px;"></i>' +
                '<p class="mb-1">暂无简报</p><p class="small text-secondary">最近5天无简报</p></div>';
            updateBrfBadge(null);
            return;
        }
        var c = data.content || {};
        var quality = data.quality || 'simple';
        var badgeClass = quality === 'full' ? 'bg-success' : 'bg-info';
        var sc = c.session_count || 0;
        var nc = c.new_knowledge_count || 0;
        var dc = c.task_done_count || 0;
        var tc = c.task_todo_count || 0;

        // 兼容新/旧格式摘要
        var summaryText = c.summary || '';
        if (!summaryText && c.task && c.task.progress) {
            var p = c.task.progress;
            summaryText = '项目: ' + (c.task.project || '?') + ' | 进度: ' + (p.summary || (p.done.length + '/' + (p.done.length + p.pending.length)));
        }
        if (summaryText.length > 150) summaryText = summaryText.slice(0, 150) + '...';

        var qualityLabel = quality === 'full' ? '完整' : '简版';
        var bd = data.briefing_date || data.date || '';
        var sourceHint = '';
        if (bd && bd !== data.date) {
            sourceHint = '<span class="small text-secondary ms-2">（来自 ' + escapeHtml(bd) + '）</span>';
        }

        container.innerHTML =
            '<div class="brf-today-card" id="brf-today-card">' +
            '<div class="brf-header"><strong>项目简报</strong>' + sourceHint + '</div>' +
            '<div class="brf-header mt-1"><span class="badge ' + badgeClass + '">' + qualityLabel + '</span>' +
            '<span class="small text-secondary ms-1">' + escapeHtml(data.date) + '</span>' +
            '<span class="small text-secondary ms-2">会话: ' + sc + ' · 知识: ' + nc + ' · 任务: ' + dc + '/' + (dc + tc) + '</span>' +
            '<span class="flex-grow-1"></span>' +
            '<span class="brf-expand-btn" onclick="toggleBrfToday()">展开 <i class="bi bi-chevron-down"></i></span></div>' +
            '<div class="brf-summary">' + escapeHtml(summaryText) + '</div>' +
            '<div id="brf-today-detail" class="brf-detail-card mt-2" style="display:none;"></div></div>';
        state.brf._todayData = data;
        updateBrfBadge(data.date);
    } catch (e) {
        container.innerHTML = '<div class="text-danger small text-center py-3">加载失败: <a href="#" onclick="loadBrfToday();return false;">[重试]</a></div>';
    }
}

function toggleBrfToday() {
    var detail = document.getElementById('brf-today-detail');
    var expandBtn = document.querySelector('.brf-expand-btn');
    if (!detail || !expandBtn) return;
    if (detail.style.display !== 'none') {
        detail.style.display = 'none';
        expandBtn.innerHTML = '展开 <i class="bi bi-chevron-down"></i>';
        return;
    }
    var data = state.brf._todayData;
    if (!data || !data.content) return;
    var c = data.content;
    var isNewFormat = c.hasOwnProperty('achieved') || c.hasOwnProperty('file_changes') || c.hasOwnProperty('bug_fixes');
    var html = '';

    if (isNewFormat) {
        // === 新 11 字段 Schema ===
        // 任务
        if (c.task) {
            var t = c.task;
            html += '<div class="brf-detail-section"><span class="label">任务</span><div>' +
                '<b>' + escapeHtml(t.project || '') + '</b> — ' + escapeHtml(t.goal || '') +
                ' [' + escapeHtml(t.status_label || t.status || '') + ']' +
                '<br><span class="small text-secondary">进度: ' + escapeHtml(t.progress ? t.progress.summary || (t.progress.done.length + '/' + (t.progress.done.length + t.progress.pending.length)) : '?') + '</span>' +
                '</div></div>';
            if (t.progress && t.progress.done && t.progress.done.length) {
                html += '<div class="brf-detail-section"><span class="label">已完成</span><ul class="mb-0 ps-3">';
                t.progress.done.forEach(function(d) { html += '<li>' + escapeHtml(d) + '</li>'; });
                html += '</ul></div>';
            }
            if (t.progress && t.progress.pending && t.progress.pending.length) {
                html += '<div class="brf-detail-section"><span class="label">待办</span><ul class="mb-0 ps-3">';
                t.progress.pending.forEach(function(p) { html += '<li>' + escapeHtml(p) + '</li>'; });
                html += '</ul></div>';
            }
        }

        // 工作项
        if (c.achieved && c.achieved.length) {
            html += '<div class="brf-detail-section"><span class="label">工作项</span><ul class="mb-0 ps-3">';
            c.achieved.forEach(function(a) {
                var tag = a.type ? '<span class="badge bg-secondary" style="font-size:0.7rem">' + escapeHtml(a.type) + '</span> ' : '';
                html += '<li>' + tag + '<b>' + escapeHtml(a.what) + '</b>' +
                    (a.detail ? '<br><span class="small">' + escapeHtml(a.detail) + '</span>' : '') +
                    (a.file ? '<br><code class="small">' + escapeHtml(a.file) + '</code>' : '') +
                    '</li>';
            });
            html += '</ul></div>';
        }

        // 文件变更
        if (c.file_changes) {
            var fc = c.file_changes;
            html += '<div class="brf-detail-section"><span class="label">文件变更</span><div>' +
                escapeHtml(fc.summary || '') +
                (fc.uncommitted_changes ? '<br><span class="small text-warning">[未提交] ' + escapeHtml(fc.uncommitted_changes) + '</span>' : '') +
                '</div>';
            if (fc.key_changes && fc.key_changes.length) {
                html += '<ul class="mb-0 ps-3">';
                fc.key_changes.forEach(function(kc) {
                    var statusTag = kc.commit_status === 'uncommitted' ? ' <span class="badge bg-warning" style="font-size:0.65rem">未提交</span>' : '';
                    html += '<li><code>' + escapeHtml(kc.file) + '</code>' + statusTag +
                        '<br><span class="small">' + escapeHtml(kc.purpose || '') + '</span></li>';
                });
                html += '</ul>';
            }
            html += '</div>';
        }

        // 决策
        if (c.decisions && c.decisions.length) {
            html += '<div class="brf-detail-section"><span class="label">决策</span><ul class="mb-0 ps-3">';
            c.decisions.forEach(function(d) {
                html += '<li><b>' + escapeHtml(d.what) + '</b>' +
                    (d.reason ? '<br><span class="small">' + escapeHtml(d.reason) + '</span>' : '') +
                    (d.excluded && d.excluded.length ? '<br><span class="small text-secondary">排除: ' + escapeHtml(d.excluded.join(', ')) + '</span>' : '') +
                    '</li>';
            });
            html += '</ul></div>';
        }

        // Bug 修复
        if (c.bug_fixes && c.bug_fixes.length) {
            html += '<div class="brf-detail-section"><span class="label">Bug 修复</span><ul class="mb-0 ps-3">';
            c.bug_fixes.forEach(function(b) {
                var confClass = b.confidence === 'high' ? 'bg-success' : b.confidence === 'medium' ? 'bg-warning' : 'bg-secondary';
                html += '<li><b>' + escapeHtml(b.problem) + '</b>' +
                    ' <span class="badge ' + confClass + '" style="font-size:0.65rem">' + escapeHtml(b.confidence || '') + '</span>' +
                    (b.root_cause ? '<br><span class="small">根因: ' + escapeHtml(b.root_cause) + '</span>' : '') +
                    (b.fix ? '<br><span class="small">修复: ' + escapeHtml(b.fix) + '</span>' : '') +
                    (b.file ? '<br><code class="small">' + escapeHtml(b.file) + '</code>' : '') +
                    '</li>';
            });
            html += '</ul></div>';
        }

        // 新增知识
        if (c.new_knowledge && c.new_knowledge.length) {
            html += '<div class="brf-detail-section"><span class="label">新增知识</span><ul class="mb-0 ps-3">';
            c.new_knowledge.forEach(function(k) { html += '<li class="small">' + escapeHtml(k) + '</li>'; });
            html += '</ul></div>';
        }

        // 建议下一步
        if (c.suggested_next) {
            html += '<div class="brf-detail-section"><span class="label">建议下一步</span><div>' +
                escapeHtml(c.suggested_next.summary || '') +
                (c.suggested_next.candidates && c.suggested_next.candidates.length ? '<ul class="mb-0 ps-3 mt-1"><li class="small">' + escapeHtml(c.suggested_next.candidates.join('</li><li class="small">')) + '</li></ul>' : '') +
                '</div></div>';
        }
    } else {
        // === 旧 4 字段 Schema（向前兼容） ===
        html += '<div class="brf-detail-section"><span class="label">摘要</span><div>' + escapeHtml(c.summary || '') + '</div></div>';
        html += '<div class="brf-detail-section"><span class="label">任务状态</span><div>' + escapeHtml(c.task_status || '无') + '</div></div>' +
            '<div class="brf-detail-section"><span class="label">关键事件</span><ul class="mb-0 ps-3">';
        if (c.key_events && c.key_events.length) {
            c.key_events.forEach(function(e) { html += '<li>' + escapeHtml(e) + '</li>'; });
        } else {
            html += '<li class="text-secondary">无</li>';
        }
        html += '</ul></div>' +
            '<div class="brf-detail-section"><span class="label">新增知识</span><ul class="mb-0 ps-3">';
        if (c.new_knowledge && c.new_knowledge.length) {
            c.new_knowledge.forEach(function(k) { html += '<li>' + escapeHtml(k) + '</li>'; });
        } else {
            html += '<li class="text-secondary">无</li>';
        }
        html += '</ul></div>' +
            '<div class="brf-detail-section"><span class="label">明日计划</span><div>' + escapeHtml(c.plan_tomorrow || '无') + '</div></div>';
    }

    detail.innerHTML = html;
    detail.style.display = '';
    expandBtn.innerHTML = '收起 <i class="bi bi-chevron-up"></i>';
}

async function loadBrfHistory(page) {
    if (page !== undefined) state.brf.page = page;
    var list = document.getElementById('brf-history-list');
    var container = document.getElementById('brf-history-container');
    if (!list) return;

    var items;
    if (state.brf.statsCache && page === 1) {
        items = state.brf.statsCache.slice(0, state.brf.pageSize);
        state.brf.total = state.brf.statsCache.length;
    } else {
        try {
            var params = new URLSearchParams();
            params.set('limit', state.brf.pageSize);
            params.set('offset', (state.brf.page - 1) * state.brf.pageSize);
            var data = await apiClient.request('/api/v2/briefing/history?' + params.toString());
            state.brf.total = data.total || 0;
            items = data.briefings || [];
        } catch (e) {
            list.innerHTML = '<div class="text-danger small text-center py-3">加载失败: <a href="#" onclick="loadBrfHistory(1);return false;">[重试]</a></div>';
            return;
        }
    }
    state.brf.items = items || [];

    if (state.brf.items.length > 0) {
        container.style.display = '';
        state.brf.latestDate = state.brf.items[0].briefing_date;
    }

    var heading = document.getElementById('brf-history-heading');
    if (heading) heading.textContent = '\u{1F4CB} 历史简报 共 ' + (state.brf.total || 0) + ' 条';

    if (!state.brf.items.length) {
        list.innerHTML = '<div class="text-center text-secondary small py-3">暂无历史简报</div>';
        renderBrfPagination();
        return;
    }

    function _formatBrfTs(ts) {
        if (!ts) return '';
        var d = new Date(ts * 1000);
        var pad = function(n) { return n < 10 ? '0' + n : '' + n; };
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    list.innerHTML = state.brf.items.map(function(b) {
        var badgeClass = b.quality === 'full' ? 'bg-success' : 'bg-info';
        var qualityLabel = b.quality === 'full' ? '完整' : '简版';
        var ts = _formatBrfTs(b.generated_at);
        var showSummary = b.quality === 'full';
        return '<div class="brf-row' + (b.quality === 'full' ? '' : ' brf-row-simple') + '" data-id="' + escapeHtml(b.id) + '">' +
            '<span class="brf-quality ' + badgeClass + '">' + qualityLabel + '</span>' +
            '<span class="brf-date">' + escapeHtml(b.briefing_date || '') + ' 项目简报</span>' +
            '<span class="brf-ts small text-secondary">' + ts + '</span>' +
            (showSummary ? '<span class="brf-summary">' + escapeHtml(b.summary || '') + '</span>' : '') +
            '<span class="brf-meta">' + (b.session_count || 0) + '会话</span>' +
            '<span class="brf-del-btn" title="删除此简报"><i class="bi bi-trash3 text-danger"></i></span>' +
            '<span class="brf-expand"><i class="bi bi-chevron-right"></i></span></div>';
            '<span class="brf-meta">' + (b.session_count || 0) + '会话</span>' +
            '<span class="brf-del-btn" title="删除此简报"><i class="bi bi-trash3 text-danger"></i></span>' +
            '<span class="brf-expand"><i class="bi bi-chevron-right"></i></span></div>';
    }).join('');

    list.querySelectorAll('.brf-row').forEach(function(row) {
        row.addEventListener('click', function(e) {
            if (e.target.closest('.brf-del-btn')) return;
            var id = this.getAttribute('data-id');
            var next = this.nextElementSibling;
            if (next && next.classList.contains('brf-detail-card')) {
                next.remove();
                this.querySelector('.brf-expand i').className = 'bi bi-chevron-right';
            } else {
                loadBrfDetail(id, this);
            }
        });
        var delBtn = row.querySelector('.brf-del-btn');
        if (delBtn) {
            delBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                var id = row.getAttribute('data-id');
                if (!confirm('确定删除此简报？')) return;
                apiClient.request('/api/v2/briefing/' + encodeURIComponent(id), {method: 'DELETE'}).then(function() {
                    state.brf.statsCache = null;
                    loadBrfHistory(state.brf.page);
                    toast('简报已删除', 'success');
                }).catch(function(err) {
                    toast('删除失败: ' + err.message, 'danger');
                });
            });
        }
    });

    renderBrfPagination();
    setTimeout(adjustBrfListHeight, 50);
}

function renderBrfPagination() {
    var totalPages = Math.ceil(state.brf.total / state.brf.pageSize) || 1;
    var nav = document.getElementById('brf-pagination-nav');
    if (!nav) return;
    if (state.brf.total === 0) { nav.innerHTML = ''; return; }
    var html = '<span class="small text-secondary me-1">' + state.brf.page + '/' + totalPages + '</span>';
    html += '<button class="btn btn-sm btn-outline-secondary py-0" onclick="goBrfPage(' + (state.brf.page - 1) + ')" ' + (state.brf.page <= 1 ? 'disabled' : '') + '><i class="bi bi-chevron-left"></i></button>';
    var start = Math.max(1, state.brf.page - 2);
    var end = Math.min(totalPages, state.brf.page + 2);
    for (var p = start; p <= end; p++) {
        html += '<button class="btn btn-sm ' + (p === state.brf.page ? 'btn-primary' : 'btn-outline-secondary') + ' py-0 ms-1" onclick="goBrfPage(' + p + ')">' + p + '</button>';
    }
    html += '<button class="btn btn-sm btn-outline-secondary py-0 ms-1" onclick="goBrfPage(' + (state.brf.page + 1) + ')" ' + (state.brf.page >= totalPages ? 'disabled' : '') + '><i class="bi bi-chevron-right"></i></button>';
    nav.innerHTML = html;
}

function goBrfPage(page) {
    if (page < 1 || page > Math.ceil(state.brf.total / state.brf.pageSize)) return;
    state.brf.page = page;
    loadBrfHistory(page);
}

async function loadBrfDetail(id, rowEl) {
    var expandIcon = rowEl.querySelector('.brf-expand i');
    expandIcon.className = 'bi bi-arrow-clockwise spinner-border spinner-border-sm';
    try {
        var data = await apiClient.request('/api/v2/briefing/' + encodeURIComponent(id));
        var c = data.content || {};
        var html = '<div class="brf-detail-card">';

        function arrToLis(arr) {
            if (!arr || !arr.length) return '<li class="text-secondary">无</li>';
            return arr.map(function(x) {
                if (typeof x === 'string') return '<li>' + escapeHtml(x) + '</li>';
                if (typeof x === 'object') {
                    var s = x.what || x.file || x.detail || JSON.stringify(x);
                    return '<li>' + escapeHtml(s) + '</li>';
                }
                return '<li>' + escapeHtml(String(x)) + '</li>';
            }).join('');
        }

        // 新 11-field 格式 (quality=full)
        if (data.quality === 'full' && (c.task || c.achieved || c.file_changes || c.decisions)) {
            if (c.task) {
                var t = c.task;
                html += '<div class="brf-detail-section"><span class="label">任务</span><div>' +
                    '[' + escapeHtml(t.status_label || t.status || '') + '] ' +
                    escapeHtml(t.goal || '') +
                    (t.progress ? ' (' + escapeHtml(t.progress.summary || '') + ')' : '') +
                    '</div></div>';
            }
            if (c.achieved && c.achieved.length) {
                html += '<div class="brf-detail-section"><span class="label">已完成工作</span><ul class="mb-0 ps-3">';
                c.achieved.forEach(function(a) {
                    html += '<li>' + escapeHtml(a.what || '') + (a.detail ? '<br><span class="text-secondary">' + escapeHtml(a.detail) + '</span>' : '') + '</li>';
                });
                html += '</ul></div>';
            }
            if (c.file_changes && c.file_changes.length) {
                html += '<div class="brf-detail-section"><span class="label">文件变更</span><ul class="mb-0 ps-3">' + arrToLis(c.file_changes) + '</ul></div>';
            }
            if (c.decisions && c.decisions.length) {
                html += '<div class="brf-detail-section"><span class="label">决策</span><ul class="mb-0 ps-3">' + arrToLis(c.decisions) + '</ul></div>';
            }
            if (c.bug_fixes && c.bug_fixes.length) {
                html += '<div class="brf-detail-section"><span class="label">Bug 修复</span><ul class="mb-0 ps-3">' + arrToLis(c.bug_fixes) + '</ul></div>';
            }
            if (c.new_knowledge && c.new_knowledge.length) {
                html += '<div class="brf-detail-section"><span class="label">新知识</span><ul class="mb-0 ps-3">' + arrToLis(c.new_knowledge) + '</ul></div>';
            }
            if (c.suggested_next) {
                html += '<div class="brf-detail-section"><span class="label">建议下一步</span><div>' + escapeHtml(c.suggested_next) + '</div></div>';
            }
        } else {
            // 旧 4-field 格式 (fallback/simple)
            html += '<div class="brf-detail-section"><span class="label">摘要</span><div>' + escapeHtml(c.summary || '') + '</div></div>' +
                '<div class="brf-detail-section"><span class="label">任务状态</span><div>' + escapeHtml(c.task_status || '无') + '</div></div>' +
                '<div class="brf-detail-section"><span class="label">关键事件</span><ul class="mb-0 ps-3">';
            html += arrToLis(c.key_events);
            html += '</ul></div>' +
                '<div class="brf-detail-section"><span class="label">明日计划</span><div>' + escapeHtml(c.plan_tomorrow || '无') + '</div></div>';
        }
        html += '</div>';
        rowEl.insertAdjacentHTML('afterend', html);
        expandIcon.className = 'bi bi-chevron-down';
    } catch (e) {
        rowEl.insertAdjacentHTML('afterend', '<div class="brf-detail-card text-danger small">加载失败: <a href="#" onclick="loadBrfDetail(\'' + escapeHtml(id) + '\',this.parentElement.previousElementSibling);return false;">[重试]</a></div>');
        expandIcon.className = 'bi bi-chevron-right';
    }
}

function initSSE() {
    console.log('[SSE] 初始化 EventSource');
    if (_sseReconnectTimer) {
        clearInterval(_sseReconnectTimer);
        _sseReconnectTimer = null;
    }
    if (_sseClient) {
        _sseClient.close();
    }
    _sseClient = new EventSource('/api/v2/events');

    _sseClient.addEventListener('memory_stream', function(e) {
        _sseTrackEvent();
        loadMemoryStream();
    });
    _sseClient.addEventListener('watchlist', function(e) {
        _sseTrackEvent();
        loadWatchlist();
    });
    _sseClient.addEventListener('task', function(e) {
        _sseTrackEvent();
        loadTaskProgress();
    });
    _sseClient.addEventListener('briefing', function(e) {
        _sseTrackEvent();
        if (state.brf._sseTimer) clearTimeout(state.brf._sseTimer);
        state.brf._sseTimer = setTimeout(function() {
            loadBrfToday();
            var today = new Date().toISOString().slice(0, 10);
            if (state.brf.latestDate && state.brf.latestDate < today) {
                state.brf.statsCache = null;
                loadBrfStats();
                loadBrfHistory(1);
            }
        }, 500);
    });
    _sseClient.addEventListener('feedback', function(e) {
        _sseTrackEvent();
        loadManualSuggestions();
    });

    _sseClient.onerror = function(e) {
        if (_sseClient.readyState === EventSource.CLOSED) {
            _sseConsecutiveFailures++;
            console.log('[SSE] onerror #' + _sseConsecutiveFailures + ' CLOSED');
            if (_sseConsecutiveFailures >= _sseMaxFailures) {
                _sseFallback('max-failures');
            }
        } else {
            console.log('[SSE] onerror: readyState=' + _sseClient.readyState);
        }
    };
    console.log('[SSE] EventSource已创建');
}

// 健康检查：每 15s 检测 EventSource 是否意外进入 CLOSED 状态
// EventSource 被优雅关闭（非 error）时 onerror 不触发，此定时器兜底
_sseHealthTimer = setInterval(function() {
    var state = _sseClient ? _sseClient.readyState : -1;
    if (_sseClient && state === EventSource.CLOSED) {
        console.log('[SSE] 健康检查检测到 CLOSED 状态，触发降级');
        _sseFallback('health-check');
    } else if (_sseClient) {
        var stateName = state === EventSource.CONNECTING ? 'CONNECTING' : (state === EventSource.OPEN ? 'OPEN' : 'CLOSED');
        console.log('[SSE] 健康检查: readyState=' + stateName);
    } else {
        console.log('[SSE] 健康检查: _sseClient=null');
    }
}, 15000);

// 空闲 60s 主动健康探测（v0.7.1 新增）
setInterval(function() {
    var idleSeconds = (Date.now() - _sseLastEventTime) / 1000;
    if (idleSeconds > 60 && _sseClient && _sseClient.readyState === EventSource.OPEN) {
        console.log('[SSE] 空闲 ' + idleSeconds.toFixed(0) + 's，主动健康探测');
        fetch('/api/v2/sse-health').then(function(r) {
            if (!r.ok) {
                _sseConsecutiveFailures++;
                console.log('[SSE] 健康探测失败#' + _sseConsecutiveFailures);
                if (_sseConsecutiveFailures >= _sseMaxFailures) {
                    _sseFallback('health-fail');
                }
            } else {
                _sseConsecutiveFailures = 0;
                console.log('[SSE] 健康探测通过');
            }
        }).catch(function() {
            _sseConsecutiveFailures++;
            if (_sseConsecutiveFailures >= _sseMaxFailures) {
                _sseFallback('health-fail');
            }
        });
    } else if (idleSeconds <= 60 && _sseClient) {
        _sseConsecutiveFailures = 0;
    }
}, 30000);

// 页面恢复可见时立即检查连接状态
document.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        console.log('[SSE] 页面恢复可见，检查连接状态');
        if (_sseClient && _sseClient.readyState === EventSource.CLOSED) {
            console.log('[SSE] visibilitychange 检测到 CLOSED，触发降级');
            _sseFallback('visibility');
        }
    } else {
        console.log('[SSE] 页面隐藏');
    }
});

// ==== v0.7.1: hash 路由 ====

function parseHash() {
    var hash = window.location.hash.replace(/^#/, '');
    if (!hash) return { tab: null, sub: null };
    var parts = hash.split('&');
    var result = { tab: null, sub: null };
    parts.forEach(function(p) {
        var kv = p.split('=');
        if (kv.length === 1 && kv[0]) {
            result.tab = kv[0];
        } else if (kv[0] === 'sub' && kv[1]) {
            result.sub = kv[1];
        }
    });
    return result;
}

var _subMap = {
    // 总览
    'daily-review': '#daily-review-panel',
    'briefing': '#project-briefing-panel',
    'task-progress': '#task-progress-panel',
    // 对话
    'conversation-list': '#conversation-panel',
    'memory-stream': '#memory-stream-panel',
    'monitor': '#monitor-panel',
    // 记忆
    'knowledge': '#knowledge-panel',
    // 跟进
    'todo': '#todo-panel',
    'watchlist': '#watchlist-panel',
    // 配置
    'briefing-schedule': '#briefing-schedule-panel',
    'user-suggestions': '#user-suggestions-panel',
    'prompt-manager': '#prompts-panel',
};

function applyHash() {
    var parsed = parseHash();
    if (!parsed.tab) return;
    // 一级 Tab 由 dashboard.html 的内联脚本处理，这里只处理二级面板
    if (parsed.sub) {
        var target = _subMap[parsed.sub];
        if (target) {
            var subBtn = document.querySelector('[data-bs-target="' + target + '"]');
            if (subBtn) {
                // 延迟执行让一级 Tab 先切换完成
                setTimeout(function() {
                    if (subBtn.classList.contains('active')) {
                        var tab = document.querySelector(subBtn.getAttribute('data-bs-target'));
                        if (tab && !tab.classList.contains('show')) {
                            tab.classList.add('show', 'active');
                        }
                        subBtn.dispatchEvent(new Event('shown.bs.tab', {bubbles: true}));
                    } else {
                        bootstrap.Tab.getOrCreateInstance(subBtn).show();
                    }
                }, 50);
            }
        }
    }
}

function adjustBrfListHeight() {
    var header = document.querySelector('#brf-history-header');
    var list = document.querySelector('#brf-history-list');
    if (!header || !list) return;
    var topOffset = header.getBoundingClientRect().bottom;
    var available = window.innerHeight - topOffset - 40;
    list.style.maxHeight = Math.max(200, available) + 'px';
}
window.addEventListener('resize', adjustBrfListHeight);

// v0.7.1: hash 变化时应用二级面板
window.addEventListener('hashchange', function() {
    applyHash();
});

// 页面加载完成后初始化 SSE
initSSE();

init();

// 初始化暂停推送开关
fetchNoSuggestionsStatus();
var _pauseToggle = document.getElementById('sug-pause-toggle');
if (_pauseToggle) _pauseToggle.addEventListener('click', togglePause);

// 初始化用户建议 Modal
initManualSuggestionModal();

// 简报端点选择器变更 -> 级联提示词下拉
document.getElementById('brf-endpoint-select')?.addEventListener('change', cascadeBrfPrompt);
