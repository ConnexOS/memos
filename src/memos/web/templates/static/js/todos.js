// ==== v0.4.5 R2 / v0.4.8 待办面板交互 ====

// 轮询定时器
var _todoPollingTimer = null;
var _todoLoading = false;  // 加载守卫，防止并发

// 状态操作按钮矩阵
const _TODO_ACTIONS = {
    pending: [
        {action: 'in_progress', label: '开始执行', cls: 'btn-outline-primary'},
        {action: 'completed', label: '标记完成', cls: 'btn-outline-success'},
        {action: 'cancelled', label: '取消', cls: 'btn-outline-secondary'},
    ],
    in_progress: [
        {action: 'completed', label: '标记完成', cls: 'btn-outline-success'},
        {action: 'cancelled', label: '取消', cls: 'btn-outline-secondary'},
    ],
    completed: [
        {action: 'pending', label: '重新打开', cls: 'btn-outline-warning'},
    ],
    cancelled: [
        {action: 'pending', label: '重新打开', cls: 'btn-outline-warning'},
    ],
};

const _TODO_STATUS_ICONS = {
    pending: 'bi-circle',
    in_progress: 'bi-arrow-repeat',
    completed: 'bi-check-circle-fill',
    cancelled: 'bi-x-circle',
};

const _TODO_PRIORITY_ICONS = {
    high: 'bi-arrow-up-circle-fill text-danger',
    medium: 'bi-dash-circle-fill text-warning',
    low: 'bi-arrow-down-circle-fill text-success',
};

function _sourceLabel(raw) {
    var map = {review_extracted: '日报提取', user_appended: '手动创建', mcp_created: 'MCP'};
    return map[raw] || raw || '?';
}

// 加载待办列表（含并发守卫）
async function loadTodos() {
    if (_todoLoading) return;
    _todoLoading = true;
    var list = document.getElementById('todo-list');
    // 只在初始加载（列表为空或占位态）时显示"加载中"，避免轮询闪烁
    var isEmpty = !list || !list.querySelector('.todo-card, .todo-sort-container');
    if (isEmpty) {
        list.innerHTML = '<div class="text-muted small text-center py-3">加载中...</div>';
    }
    try {
        var filter = document.getElementById('todo-filter').value;
        var sort = document.getElementById('todo-sort').value;
        var showArchived = document.getElementById('todo-show-archived')?.checked || false;
        var url = '/api/todos?limit=100&sort=' + sort;
        if (filter) url += '&todo_status=' + filter;
        if (showArchived) url += '&show_archived=true';
        var data = await apiClient.request(url);
        var todos = data.todos || [];
        if (todos.length === 0) {
            list.innerHTML = '<div class="text-muted small text-center py-3">暂无待办事项</div>';
            return;
        }
        renderTodos(todos, sort);
    } catch (e) {
        list.innerHTML = '<div class="text-danger small text-center py-3">加载失败</div>';
        console.error('loadTodos error:', e);
    } finally {
        _todoLoading = false;
    }
}

function renderTodos(todos, sortMode) {
    var list = document.getElementById('todo-list');
    var isCustom = sortMode === 'custom';
    var statusLabels = {pending: '待处理', in_progress: '进行中', completed: '已完成', cancelled: '已取消'};

    // 自定义排序模式下：平铺不分组，让拖放真正改变全局顺序
    if (isCustom) {
        if (todos.length === 0) {
            list.innerHTML = '<div class="text-muted small text-center py-3">暂无待办事项</div>';
            return;
        }
        var html = '';
        html += '<div class="todo-sort-container">';
        todos.forEach(function(t) {
            var dueHtml = renderDueDate(t.due_date);
            var createdInfo = t.created_at ? formatTime(t.created_at) : '?';
            var prioIcon = _TODO_PRIORITY_ICONS[t.priority] || _TODO_PRIORITY_ICONS.medium;
            var statusLabel = statusLabels[t.todo_status] || t.todo_status;

            var actionsHtml = '';
            var actions = _TODO_ACTIONS[t.todo_status] || [];
            if (actions.length > 0) {
                if (t.todo_status === 'completed' || t.todo_status === 'cancelled') {
                    if (t.active !== false) {
                        actionsHtml += '<button class="btn btn-sm py-0 px-1 btn-outline-secondary" onclick="archiveTodo(\'' + t.id + '\')" style="font-size:.7rem" title="归档">归档</button>';
                    }
                    actionsHtml += '<button class="btn btn-sm py-0 px-1 btn-outline-danger" onclick="deleteTodo(\'' + t.id + '\')" style="font-size:.7rem" title="删除">删除</button>';
                } else {
                    actionsHtml += '<button class="btn btn-sm py-0 px-1 btn-outline-secondary" onclick="openEditTodoModal(\'' + t.id + '\')" style="font-size:.7rem" title="编辑"><i class="bi bi-pencil"></i></button>';
                }
                actions.forEach(function(a) {
                    actionsHtml += '<button class="btn btn-sm py-0 px-1 ' + a.cls + '" onclick="changeTodoStatus(\'' + t.id + '\',\'' + a.action + '\')" style="font-size:.7rem">' + a.label + '</button>';
                });
            }

            html += '<div class="card mb-1 todo-card" id="todo-card-' + t.id + '" draggable="true" data-id="' + t.id + '" data-sort="' + (t.sort_order || 0) + '">';
            html += '<div class="card-body py-1 px-2">';
            html += '<div class="d-flex align-items-center gap-1">';
            html += '<span class="todo-move-group me-1" style="white-space:nowrap">';
            html += '<button class="btn btn-sm py-0 px-0 border-0" onclick="moveTodo(\'' + t.id + '\', -1)" title="上移" style="font-size:.65rem;line-height:1"><i class="bi bi-chevron-up"></i></button>';
            html += '<button class="btn btn-sm py-0 px-0 border-0" onclick="moveTodo(\'' + t.id + '\', 1)" title="下移" style="font-size:.65rem;line-height:1"><i class="bi bi-chevron-down"></i></button>';
            html += '</span>';
            html += '<span class="todo-drag-handle me-1"><i class="bi bi-grip-vertical"></i></span>';
            html += '<i class="bi ' + prioIcon + '" style="font-size:.75rem"></i>';
            html += '<span class="badge bg-secondary" style="font-size:.6rem">' + statusLabel + '</span>';
            if (dueHtml) html += dueHtml;
            html += '<div class="flex-grow-1"></div>';
            html += '<div class="d-flex gap-1 flex-shrink-0">' + actionsHtml + '</div>';
            html += '</div>';
            html += '<div class="small mt-1">' + escHtml(t.content) + '</div>';
            if (t.context) html += '<div class="small text-secondary mt-1" style="font-size:.75rem;border-left:2px solid var(--bs-gray-600,#6c757d);padding-left:8px">' + escHtml(t.context) + '</div>';
            html += '<div class="small text-secondary" style="font-size:.7rem">创建于 ' + createdInfo + ' · 来源: ' + _sourceLabel(t.source) + '</div>';
            html += '</div></div>';
        });
        html += '</div>';
        list.innerHTML = html;
        setupTodoDragDrop();
        return;
    }

    // 非自定义模式：按状态分组
    var groups = {pending: [], in_progress: [], completed: [], cancelled: []};
    todos.forEach(function(t) {
        var s = t.todo_status || 'pending';
        if (groups[s]) groups[s].push(t); else groups.pending.push(t);
    });
    var statusOrder = ['pending', 'in_progress', 'completed', 'cancelled'];
    var html = '';

    statusOrder.forEach(function(s) {
        var items = groups[s] || [];
        if (items.length === 0) return;
        html += '<div class="mb-2">';
        if (s === 'completed' || s === 'cancelled') {
            html += '<div class="small fw-semibold text-secondary mb-1 d-flex justify-content-between align-items-center"><span>' + statusLabels[s] + ' (' + items.length + ')</span><span><button class="btn btn-sm py-0 px-1 btn-outline-secondary" onclick="archiveTodosByStatus(\'' + s + '\')" style="font-size:.7rem" title="归档所有"><i class="bi bi-archive"></i> 归档</button><button class="btn btn-sm py-0 px-1 btn-outline-danger ms-1" onclick="deleteTodosByStatus(\'' + s + '\')" style="font-size:.7rem" title="删除所有"><i class="bi bi-trash"></i> 删除</button></span></div>';
        } else {
            html += '<div class="small fw-semibold text-secondary mb-1">' + statusLabels[s] + ' (' + items.length + ')</div>';
        }
        items.forEach(function(t) {
            var dueHtml = renderDueDate(t.due_date);
            var createdInfo = t.created_at ? formatTime(t.created_at) : '?';
            var prioIcon = _TODO_PRIORITY_ICONS[t.priority] || _TODO_PRIORITY_ICONS.medium;
            var statusLabel = statusLabels[t.todo_status] || t.todo_status;

            var actionsHtml = '';
            var actions = _TODO_ACTIONS[t.todo_status] || [];
            if (actions.length > 0) {
                if (t.todo_status === 'completed' || t.todo_status === 'cancelled') {
                    if (t.active !== false) {
                        actionsHtml += '<button class="btn btn-sm py-0 px-1 btn-outline-secondary" onclick="archiveTodo(\'' + t.id + '\')" style="font-size:.7rem" title="归档">归档</button>';
                    }
                    actionsHtml += '<button class="btn btn-sm py-0 px-1 btn-outline-danger" onclick="deleteTodo(\'' + t.id + '\')" style="font-size:.7rem" title="删除">删除</button>';
                } else {
                    actionsHtml += '<button class="btn btn-sm py-0 px-1 btn-outline-secondary" onclick="openEditTodoModal(\'' + t.id + '\')" style="font-size:.7rem" title="编辑"><i class="bi bi-pencil"></i></button>';
                }
                actions.forEach(function(a) {
                    actionsHtml += '<button class="btn btn-sm py-0 px-1 ' + a.cls + '" onclick="changeTodoStatus(\'' + t.id + '\',\'' + a.action + '\')" style="font-size:.7rem">' + a.label + '</button>';
                });
            }

            html += '<div class="card mb-1 todo-card" id="todo-card-' + t.id + '" draggable="false" data-id="' + t.id + '" data-sort="0">';
            html += '<div class="card-body py-1 px-2">';
            html += '<div class="d-flex align-items-center gap-1">';
            html += '<i class="bi ' + prioIcon + '" style="font-size:.75rem"></i>';
            html += '<span class="badge bg-secondary" style="font-size:.6rem">' + statusLabel + '</span>';
            if (dueHtml) html += dueHtml;
            html += '<div class="flex-grow-1"></div>';
            html += '<div class="d-flex gap-1 flex-shrink-0">' + actionsHtml + '</div>';
            html += '</div>';
            html += '<div class="small mt-1">' + escHtml(t.content) + '</div>';
            if (t.context) html += '<div class="small text-secondary mt-1" style="font-size:.75rem;border-left:2px solid var(--bs-gray-600,#6c757d);padding-left:8px">' + escHtml(t.context) + '</div>';
            html += '<div class="small text-secondary" style="font-size:.7rem">创建于 ' + createdInfo + ' · 来源: ' + _sourceLabel(t.source) + '</div>';
            html += '</div></div>';
        });
        html += '</div>';
    });

    list.innerHTML = html;
}

// --- v0.4.8: 30s 自动轮询（Tab 隐藏时暂停）---
function startTodoPolling() {
    if (_todoPollingTimer) return;
    _todoPollingTimer = setInterval(function() {
        if (!document.hidden) {
            loadTodos();
        }
    }, 30000);
}

function stopTodoPolling() {
    if (_todoPollingTimer) {
        clearInterval(_todoPollingTimer);
        _todoPollingTimer = null;
    }
}

function renderDueDate(dueDate) {
    if (!dueDate) return '';
    var parts = dueDate.split('-');
    if (parts.length !== 3) return '<span class="badge bg-secondary" style="font-size:.6rem">到期 ' + escHtml(dueDate) + '</span>';
    var due = new Date(parts[0], parts[1] - 1, parts[2]);
    var now = new Date();
    now.setHours(0, 0, 0, 0);
    var diffDays = Math.floor((due - now) / (86400000));
    if (diffDays < 0) return '<span class="badge bg-danger" style="font-size:.6rem">已过期 ' + Math.abs(diffDays) + ' 天</span>';
    if (diffDays === 0) return '<span class="badge bg-warning text-dark" style="font-size:.6rem">今天到期</span>';
    return '<span class="badge bg-secondary" style="font-size:.6rem">到期 ' + escHtml(parts[0] + '/' + parts[1] + '/' + parts[2]) + '</span>';
}

// 拖放排序
function setupTodoDragDrop() {
    var container = document.querySelector('.todo-sort-container');
    if (!container) return;

    // 容器级 dragover 必须 preventDefault，否则 drop 不触发
    container.addEventListener('dragover', function(e) { e.preventDefault(); });

    var cards = container.querySelectorAll('.todo-card[draggable="true"]');
    cards.forEach(function(card) {
        card.addEventListener('dragstart', function(e) {
            e.dataTransfer.setData('text/plain', this.dataset.id);
            this.classList.add('opacity-50');
        });
        card.addEventListener('dragend', function(e) {
            this.classList.remove('opacity-50');
        });
        card.addEventListener('dragover', function(e) {
            e.preventDefault();
            this.classList.add('border-primary');
        });
        card.addEventListener('dragleave', function() {
            this.classList.remove('border-primary');
        });
        card.addEventListener('drop', function(e) {
            e.preventDefault();
            this.classList.remove('border-primary');
            var fromId = e.dataTransfer.getData('text/plain');
            var toId = this.dataset.id;
            if (fromId && toId && fromId !== toId) {
                handleTodoDrop(fromId, toId);
            }
        });
    });
}

async function handleTodoDrop(fromId, toId) {
    var container = document.querySelector('.todo-sort-container');
    if (!container) return;

    // 1) 获取当前 DOM 顺序的快照
    var cards = Array.from(container.querySelectorAll('.todo-card[data-id]'));
    var fromIdx = cards.findIndex(function(c) { return c.dataset.id === fromId; });
    var toIdx = cards.findIndex(function(c) { return c.dataset.id === toId; });
    if (fromIdx === -1 || toIdx === -1) return;

    // 2) 从原位置移除，插入到目标位置
    //    drop 到哪条记录，就插入到该记录之前（等同"占据目标位，后续后移"）
    var item = cards.splice(fromIdx, 1)[0];
    var insertAt = toIdx;
    // 移除后数组缩短：若 fromIdx < toIdx，原 toIdx 对应元素已左移一位，但我们要的是"原 toIdx 的位置"，insertAt 不变
    cards.splice(insertAt, 0, item);

    // 3) 全量重分配 sort_order = position * 10000
    try {
        for (var i = 0; i < cards.length; i++) {
            await apiClient.request('/api/todos/' + cards[i].dataset.id, {
                method: 'PUT',
                body: JSON.stringify({sort_order: i * 10000}),
                headers: {'Content-Type': 'application/json'}
            });
        }
        loadTodos();
    } catch (e) {
        console.error('handleTodoDrop error:', e);
    }
}

// 上移 / 下移（替代拖放方案）
async function moveTodo(id, direction) {
    var container = document.querySelector('.todo-sort-container');
    if (!container) return;
    var cards = Array.from(container.querySelectorAll('.todo-card[data-id]'));
    var idx = cards.findIndex(function(c) { return c.dataset.id === id; });
    if (idx === -1) return;
    var targetIdx = idx + direction;
    if (targetIdx < 0 || targetIdx >= cards.length) return;

    // 在数组里交换相邻两项
    var tmp = cards[idx];
    cards[idx] = cards[targetIdx];
    cards[targetIdx] = tmp;

    // 全量重分配 sort_order = position * 10000，确保顺序一致
    try {
        for (var i = 0; i < cards.length; i++) {
            await apiClient.request('/api/todos/' + cards[i].dataset.id, {
                method: 'PUT',
                body: JSON.stringify({sort_order: i * 10000}),
                headers: {'Content-Type': 'application/json'}
            });
        }
        loadTodos();
    } catch (e) {
        console.error('moveTodo error:', e);
        toast('移动失败', 'danger');
    }
}

// 排序模式切换提示
function onTodoSortChange() {
    loadTodos();
}

// 新建待办 — 模态框版
function showCreateTodoModal() {
    var modal = new bootstrap.Modal(document.getElementById('todoCreateModal'));
    modal.show();
    // 模态框显示后聚焦输入框
    document.getElementById('todoCreateModal').addEventListener('shown.bs.modal', function() {
        document.getElementById('todo-new-content').focus();
    }, { once: true });
}

async function createTodo() {
    var content = document.getElementById('todo-new-content').value.trim();
    if (!content) { toast('请输入待办内容', 'warning'); return; }
    var priority = document.getElementById('todo-new-priority').value;
    var dueDate = document.getElementById('todo-new-duedate').value || '';
    try {
        var body = {content: content, priority: priority};
        if (dueDate) body.due_date = dueDate;
        await apiClient.request('/api/todos', {method: 'POST', body: JSON.stringify(body), headers: {'Content-Type': 'application/json'}});
        bootstrap.Modal.getInstance(document.getElementById('todoCreateModal')).hide();
        toast('待办已创建', 'success');
        loadTodos();
    } catch (e) {
        toast('创建失败: ' + (e.message || e), 'danger');
    }
}

function resetCreateTodoForm() {
    document.getElementById('todo-new-content').value = '';
    document.getElementById('todo-new-priority').value = 'medium';
    document.getElementById('todo-new-duedate').value = '';
}

// 状态变更
async function changeTodoStatus(id, status) {
    try {
        await apiClient.request('/api/todos/' + id + '/status', {method: 'POST', body: JSON.stringify({todo_status: status}), headers: {'Content-Type': 'application/json'}});
        loadTodos();
    } catch (e) {
        toast('状态变更失败: ' + (e.message || e), 'danger');
    }
}

// 编辑待办（Modal 版）
var _editTodoId = null;

function openEditTodoModal(id) {
    _editTodoId = id;
    fetchTodoContent(id).then(function(content) {
        document.getElementById('todo-edit-content').value = content || '';
        var modal = new bootstrap.Modal(document.getElementById('todoEditModal'));
        modal.show();
    });
}

async function fetchTodoContent(id) {
    try {
        var data = await apiClient.request('/api/memories/' + id);
        return data.document || '';
    } catch(e) {
        return '';
    }
}

async function saveEditTodo() {
    if (!_editTodoId) return;
    var newContent = document.getElementById('todo-edit-content').value.trim();
    if (!newContent) { toast('内容不能为空', 'warning'); return; }
    try {
        await apiClient.request('/api/todos/' + _editTodoId, {method: 'PUT', body: JSON.stringify({content: newContent}), headers: {'Content-Type': 'application/json'}});
        bootstrap.Modal.getInstance(document.getElementById('todoEditModal')).hide();
        toast('待办已更新', 'success');
        _editTodoId = null;
        loadTodos();
    } catch (e) {
        toast('更新失败: ' + (e.message || e), 'danger');
    }
}

// 删除单个待办
async function deleteTodo(id) {
    if (!confirm('确定删除该待办？')) return;
    try {
        await apiClient.request('/api/todos/' + id, {method: 'DELETE'});
        toast('待办已删除', 'success');
        loadTodos();
    } catch (e) {
        toast('删除失败: ' + (e.message || e), 'danger');
    }
}

// 归档单个待办
async function archiveTodo(id) {
    try {
        await apiClient.request('/api/todos/' + id + '/archive', {method: 'POST'});
        toast('待办已归档', 'success');
        loadTodos();
    } catch (e) {
        toast('归档失败: ' + (e.message || e), 'danger');
    }
}

// 批量归档指定状态的所有待办
async function archiveTodosByStatus(status) {
    var label = {completed: '已完成', cancelled: '已取消'}[status] || status;
    if (!confirm('确定归档所有"' + label + '"待办？')) return;
    try {
        var result = await apiClient.request('/api/todos/bulk-archive?todo_status=' + status, {method: 'POST'});
        toast('已归档 ' + (result.count || 0) + ' 条' + label + '待办', 'success');
        loadTodos();
    } catch (e) {
        toast('批量归档失败: ' + (e.message || e), 'danger');
    }
}

// 批量删除指定状态的所有待办
async function deleteTodosByStatus(status) {
    var label = {completed: '已完成', cancelled: '已取消'}[status] || status;
    if (!confirm('确定删除所有"' + label + '"待办？此操作不可恢复。')) return;
    try {
        var result = await apiClient.request('/api/todos/bulk?todo_status=' + status, {method: 'DELETE'});
        toast('已删除 ' + (result.count || 0) + ' 条' + label + '待办', 'success');
        loadTodos();
    } catch (e) {
        toast('批量删除失败: ' + (e.message || e), 'danger');
    }
}

// 一级导航切换时加载 + 轮询控制 + 编辑框快捷键 + 创建待办模态框绑定
document.addEventListener('DOMContentLoaded', function() {
    var todoGroupTab = document.getElementById('tab-todo');
    if (todoGroupTab) {
        todoGroupTab.addEventListener('click', function() {
            loadTodos();
            startTodoPolling();
        });
    }
    // 页面可见性变化时暂停/恢复轮询
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            stopTodoPolling();
        } else {
            var todoGroup = document.getElementById('group-todo');
            if (todoGroup && todoGroup.classList.contains('active')) {
                startTodoPolling();
            }
        }
    });
    var editInput = document.getElementById('todo-edit-content');
    if (editInput) {
        editInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                saveEditTodo();
            }
        });
    }
    // 创建待办模态框
    var createModal = document.getElementById('todoCreateModal');
    if (createModal) {
        document.getElementById('todo-create-save-btn')?.addEventListener('click', createTodo);
        createModal.addEventListener('hidden.bs.modal', resetCreateTodoForm);
    }
    // 刷新按钮
    document.getElementById('todo-refresh-btn')?.addEventListener('click', function() {
        loadTodos();
        toast('待办已刷新', 'success');
    });
});
