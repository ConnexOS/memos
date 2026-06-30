// nav-modals.js — 共享模态驱动层
// 从 _nav.html 加载，所有页面共享。dashboard.js 的同名函数声明会覆盖本文件版本。
//
// 约束：本脚本在 Bootstrap JS 之前加载。
// 禁止在模块顶层调用 bootstrap.Modal / bootstrap.Toast 等 API。
// 所有 bootstrap 引用必须放在函数体内，在用户交互后执行。
// [SHARED] 与 dashboard.js 保持同步。修改时请同时更新两端。

// ============================================================
// 工具函数
// ============================================================

function _safeErrMsg(e) {
    if (e === null || e === undefined) return '未知错误';
    if (typeof e === 'string') return e;
    if (e.message && typeof e.message === 'string') return e.message;
    if (typeof e === 'object') return JSON.stringify(e);
    return String(e);
}

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

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
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
        const text = await resp.text().catch(() => '');
        const preview = text.substring(0, 200);
        throw new Error(`服务器响应异常 (HTTP ${resp.status}): ${preview}`);
    }
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
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

// ============================================================
// 备份管理
// ============================================================

var _backupListData = null;
var _bdId = null;

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
    _bdId = backupId;
    document.getElementById('bd-name').textContent = backupId;
    const btn = document.getElementById('bd-execute');
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-trash me-1"></i>删除';
    bootstrap.Modal.getInstance(document.getElementById('backupModal'))?.hide();
    new bootstrap.Modal(document.getElementById('backupDeleteModal')).show();
}

// [SHARED] bd-execute click handler — nav-modals 统一注册版本
document.getElementById('bd-execute')?.addEventListener('click', async function() {
    if (!_bdId) return;
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>删除中...';
    try {
        await api('/api/backups/' + encodeURIComponent(_bdId), {method: 'DELETE'});
        bootstrap.Modal.getInstance(document.getElementById('backupDeleteModal'))?.hide();
        toast('备份已删除', 'success');
        new bootstrap.Modal(document.getElementById('backupModal')).show();
        await loadBackupList();
        // loadBackupStatus 已在提取范围内，无需守卫
        loadBackupStatus();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-trash me-1"></i>删除';
    }
});

// 备份管理模态框打开时刷新列表
document.getElementById('backupModal')?.addEventListener('show.bs.modal', function() {
    loadBackupList();
});

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
    const maxWait = 600;
    const interval = 2000;
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
                // loadBackupStatus 已在提取范围内，无需守卫
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

// ============================================================
// 项目管理
// ============================================================

var _pmDeletePid = null;
var _pmDeleteName = '';

async function _loadProjectManagerData() {
    document.getElementById('pm-loading').style.display = '';
    document.getElementById('pm-content').style.display = 'none';

    try {
        const data = await api('/api/projects');
        const projects = data.projects || [];
        const tbody = document.getElementById('pm-table-body');
        tbody.innerHTML = '';

        projects.sort((a, b) => {
            if (a.project_id === data.current_project) return -1;
            if (b.project_id === data.current_project) return 1;
            return (b.latest_time || 0) - (a.latest_time || 0);
        });

        for (const p of projects) {
            const tr = document.createElement('tr');

            const nameTd = document.createElement('td');
            nameTd.textContent = p.project_name || p.project_id;
            if (p.project_id === data.current_project) {
                const badge = document.createElement('span');
                badge.className = 'badge bg-info ms-1';
                badge.textContent = '当前';
                nameTd.appendChild(badge);
            }
            tr.appendChild(nameTd);

            const idTd = document.createElement('td');
            idTd.className = 'text-muted small font-monospace';
            idTd.textContent = p.project_id;
            tr.appendChild(idTd);

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

// [SHARED] refreshProjectManager — 加 typeof 守卫兼容双页环境
async function refreshProjectManager() {
    const tasks = [_loadProjectManagerData()];
    if (typeof loadProjects === 'function') {
        tasks.push(loadProjects());
    }
    await Promise.all(tasks);
}

function confirmPmDelete(pid, name) {
    _pmDeletePid = pid;
    _pmDeleteName = name;

    document.getElementById('pm-del-name').textContent = name;
    document.getElementById('pm-del-id').textContent = pid;
    document.getElementById('pm-del-stats').textContent = '加载中...';
    document.getElementById('pm-del-confirm').value = '';
    document.getElementById('pm-del-execute').disabled = true;

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

    bootstrap.Modal.getInstance(document.getElementById('projectMgmtModal'))?.hide();
    const confirmModal = new bootstrap.Modal(document.getElementById('pmDeleteConfirmModal'));
    confirmModal.show();
}

// 输入项目名匹配后启用删除按钮
document.getElementById('pm-del-confirm')?.addEventListener('input', function() {
    document.getElementById('pm-del-execute').disabled = this.value !== _pmDeleteName;
});

// [SHARED] pm-del-execute handler — nav-modals 唯一注册守卫版本，dashboard.js 不再注册
// Dashboard 专属调用通过 typeof 守卫跳过收件箱页执行
document.getElementById('pm-del-execute')?.addEventListener('click', async function() {
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>删除中...';

    try {
        await api(`/api/projects/${_pmDeletePid}`, { method: 'DELETE' });
        // Dashboard 专属：如果删除的是当前选中项目，重置
        if (typeof state !== 'undefined' && state.currentProject === _pmDeletePid) {
            state.currentProject = null;
            const saved = localStorage.getItem('memos_default_project');
            if (saved === _pmDeletePid) localStorage.removeItem('memos_default_project');
        }
        // 关闭确认弹窗
        bootstrap.Modal.getInstance(document.getElementById('pmDeleteConfirmModal'))?.hide();
        // Dashboard 专属：刷新各面板
        if (typeof loadProjects === 'function') {
            await loadProjects();
            Promise.all([loadMemories(), loadConversations()]);
        }
        toast('项目已删除', 'success');
        // 通用操作：重新打开项目管理对话框
        openProjectManager();
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-trash me-1"></i>确认删除';
    }
});

// ============================================================
// 系统设置 — 数据常量
// ============================================================

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
    'chroma.collection_name': 'ChromaDB 集合名，用于按项目隔离数据',
    'chroma.path': '本地持久化目录路径',
    'chroma.timeout': 'ChromaDB 操作超时时间（秒）',
    'llm.api_base': 'LLM 服务地址，health 和 chat 接口自动拼接后缀',
    'llm.api_key': 'API 密钥（如不需要可留空）',
    'llm.temperature': '生成温度 (0-1)，越高越随机。建议 0.1-0.3',
    'llm.max_tokens': '单次生成的最大 token 数',
    'llm.request_timeout': 'HTTP 请求超时（秒）',
    'llm.max_retries': '请求失败的最大重试次数',
    'llm.retry_base_delay': '重试退避基础延迟（秒），每次递增',
    'memory.path': '嵌入模型路径（本地目录）',
    'memory.vector_dim': '嵌入向量维度，需与模型一致',
    'memory.decay_lambda': '时间衰减系数，越大越偏向近期记忆。0=不衰减',
    'memory.similarity_threshold': '语义相似度阈值（余弦距离），低于此值判定重复',
    'memory.dedup_top_k': '去重检查的候选记忆数',
    'memory.default_type': '新建记忆的默认类型',
    'memory.archive_days': '超过此天数的记忆自动归档（软删除）',
    'memory.rerank_multiplier': '重排序候选倍数，增大提高质量但降低速度',
    'memory.rerank_min_candidates': '重排序最小候选数，低于此值直接返回',
    'buffer.max_tokens': '对话缓冲最大 token 数，超限从头截断',
    'buffer.truncate_target': '截断目标 token 数',
    'buffer.trigger_rounds': '自动提炼触发的对话轮数',
    'buffer.rate_limit_seconds': 'LLM 提炼冷却时间（秒）',
    'buffer.token_ratio': '字符数 ÷ 此值 ≈ token 数',
    'dashboard.status_cache_ttl': '系统状态缓存有效期（秒）',
    'dashboard.projects_cache_ttl': '项目列表缓存有效期（秒）',
    'dashboard.health_check_timeout': 'LLM 健康检查超时（秒）',
    'dashboard.search_default_top_k': '搜索默认返回条数',
    'dashboard.search_top_k_max': '搜索最大返回条数上限',
    'dashboard.search_default_decay': '搜索默认时间衰减系数',
    'dashboard.search_default_bm25_weight': 'BM25 权重: 0=纯向量, 1=纯关键词',
    'dashboard.list_default_limit': '列表每页默认条数',
    'dashboard.list_limit_max': '列表每页条数上限',
    'server.id_length': '自动生成的记忆 ID 长度',
    'server.mcp_top_k_max': 'MCP recall 最大返回条数',
    'server.response_truncate_length': 'MCP 响应中长文本截断长度',
    'backup.target_dir': '备份文件输出目录',
    'backup.max_backups': '最大保留备份数，超出自动覆盖最旧备份',
    'backup.remind_after_days': '距上次备份超过此天数时提醒',
    'backup.verify_after_backup': '备份完成后自动校验数据完整性',
    'notification.retention_days': '通知保留天数，过期的自动清理',
    'notification.rate_limit_minutes': '同类通知最小间隔（分钟），超出合并',
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
    'system_suggestion.enabled': '系统状态型建议全局开关（管道二）',
    'system_suggestion.daily_limit': '管道二每日最大推送数',
    'system_suggestion.cooldown_hours': '同类系统事件冷却时间（小时）',
    'hook_proxy.timeout': 'Hook 请求超时秒数（server_url 从 server.port 自动派生）',
};

const fieldSaveMap = {
    'memory.path': 'model.path',
    'memory.vector_dim': 'model.vector_dim',
};

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

// ============================================================
// 系统设置 — 渲染与加载
// ============================================================

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

        var html = '';

        // 端点管理
        html += '<div class="card mb-2"><div class="card-header p-2 d-flex justify-content-between align-items-center" data-bs-toggle="collapse" data-bs-target="#collapse-endpoint" role="button"><h6 class="mb-0"><i class="bi bi-hdd-network me-1"></i>端点管理</h6><span class="badge bg-danger bg-opacity-25 text-danger">P0 · 必配</span></div><div class="collapse show" id="collapse-endpoint"><div class="card-body p-2">';
        if (sections.llm) {
            html += '<div id="llm-endpoint-manager" class="mb-2"><div class="text-center py-2"><span class="spinner-border spinner-border-sm"></span> 加载端点列表...</div></div>';
            Object.entries(sections.llm).forEach(function(e) {
                if (['active', 'api_base', 'api_key'].includes(e[0]) || Array.isArray(e[1]) || (typeof e[1] === 'object' && e[1] !== null)) return;
                html += _renderCfgFieldRow('llm', e[0], e[1]);
            });
        }
        html += '</div></div></div>';

        // 基础设置
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
        if (sections.memory) {
            html += '<div class="cfg-section-desc mb-1">' + (sectionDescs.memory || '记忆') + '</div>';
            ['default_top_k', 'similarity_threshold', 'archive_days'].forEach(function(fn) {
                if (sections.memory[fn] !== undefined) html += _renderCfgFieldRow('memory', fn, sections.memory[fn]);
            });
        }
        html += '</div></div></div>';

        // 高级设置
        html += '<div class="card mb-2"><div class="card-header p-2 d-flex justify-content-between align-items-center collapsed" data-bs-toggle="collapse" data-bs-target="#collapse-advanced" role="button"><h6 class="mb-0"><i class="bi bi-tools me-1"></i>高级设置</h6><span class="badge bg-secondary bg-opacity-25 text-secondary">P2</span></div><div class="collapse" id="collapse-advanced"><div class="card-body p-2">';
        var advancedKeys = Object.keys(sections).filter(function(k) { return !['llm', 'dashboard', 'model', 'suggestion', 'memory'].includes(k); });
        advancedKeys.forEach(function(key) {
            var fields = sections[key];
            if (!fields) return;
            html += '<div class="cfg-section-desc mb-1">' + (sectionDescs[key] || key) + '</div>';
            if (key === 'memory') {
                Object.entries(fields).forEach(function(e) {
                    if (['default_top_k', 'similarity_threshold', 'archive_days'].includes(e[0])) return;
                    if (Array.isArray(e[1]) || (typeof e[1] === 'object' && e[1] !== null)) return;
                    html += _renderCfgFieldRow(key, e[0], e[1]);
                });
            } else {
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

        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(el) {
            new bootstrap.Tab(el);
        });
        document.querySelectorAll('.memory-subtabs .nav-link').forEach(el => {
            new bootstrap.Tab(el);
        });
        document.querySelectorAll('.cfg-help-icon').forEach(el => {
            new bootstrap.Tooltip(el);
        });
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
        loadLLMEndpoints();
    } catch (e) {
        body.innerHTML = `<div class="alert alert-danger small py-2 mb-0">加载配置失败: ${escapeHtml(e.message)}</div>`;
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

// ============================================================
// LLM 端点管理
// ============================================================

var _editingEndpointName = null;

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

// [SHARED] activateLLMEndpoint — 加 typeof 守卫兼容双页环境
async function activateLLMEndpoint(btn, name) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>激活中...';
    try {
        const data = await api('/api/llm/activate', {
            method: 'POST',
            body: JSON.stringify({name}),
        });
        toast(data.message || `已切换到端点 '${name}'`, data.status === 'online' ? 'success' : 'warning');
        const tasks = [loadSettings()];
        if (typeof loadLLMEndpointsForExtract === 'function') {
            tasks.push(loadLLMEndpointsForExtract());
        }
        await Promise.all(tasks);
    } catch (e) {
        btn.disabled = false;
        btn.textContent = '激活';
        toast('切换失败: ' + e.message, 'danger');
    }
}

// [SHARED] deleteLLMEndpoint — 加 typeof 守卫兼容双页环境
async function deleteLLMEndpoint(name) {
    if (!confirm(`确定要删除端点 '${name}' 吗？`)) return;
    try {
        await api(`/api/llm/endpoints/${encodeURIComponent(name)}`, {method: 'DELETE'});
        toast(`端点 '${name}' 已删除`, 'success');
        const tasks = [loadSettings()];
        if (typeof loadLLMEndpointsForExtract === 'function') {
            tasks.push(loadLLMEndpointsForExtract());
        }
        await Promise.all(tasks);
    } catch (e) {
        toast('删除失败: ' + e.message, 'danger');
    }
}

function editLLMEndpoint(name) {
    _editingEndpointName = name;
    const title = document.getElementById('endpointModalTitle');
    const nameInput = document.getElementById('ep-name');
    const origInput = document.getElementById('ep-original-name');
    const baseInput = document.getElementById('ep-api-base');
    const keyInput = document.getElementById('ep-api-key');
    const modelInput = document.getElementById('ep-model');

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

// [SHARED] saveLLMEndpoint — 加 typeof 守卫兼容双页环境
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
            const body = {api_base: apiBase, model};
            body.api_key = (apiKey === '******' || apiKey === '') ? null : apiKey;
            await api(`/api/llm/endpoints/${encodeURIComponent(origName)}`, {
                method: 'PUT',
                body: JSON.stringify(body),
            });
            toast('端点已更新', 'success');
        } else {
            await api('/api/llm/endpoints', {
                method: 'POST',
                body: JSON.stringify({name, api_base: apiBase, api_key: apiKey === '******' ? '' : apiKey, model}),
            });
            toast('端点已创建', 'success');
        }
        bootstrap.Modal.getInstance(document.getElementById('endpointModal')).hide();
        const tasks = [loadSettings()];
        if (typeof loadLLMEndpointsForExtract === 'function') {
            tasks.push(loadLLMEndpointsForExtract());
        }
        await Promise.all(tasks);
    } catch (e) {
        toast('保存失败: ' + e.message, 'danger');
    } finally {
        saveBtn.disabled = false;
    }
}

// ============================================================
// 系统设置 — 按钮事件绑定
// ============================================================

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

// ============================================================
// 语言切换
// ============================================================

document.getElementById('lang-switch')?.addEventListener('click', async function() {
    const currentLang = document.documentElement.lang || 'zh';
    const newLang = currentLang === 'zh' ? 'en' : 'zh';
    try {
        await api('/api/config', { method: 'PUT', body: JSON.stringify({key: 'dashboard.locale', value: newLang}) });
    } catch(e) { /* 静默 */ }
    document.documentElement.lang = newLang;
    location.reload();
});
