# Dashboard 显示用户名 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** 在 Dashboard 顶部工具栏退出登录按钮前显示当前登录用户的 name

**Architecture:** 模板注入 — login 时将 `user["name"]` 存入 `request.session`，index 路由传给 Jinja2 模板渲染

**Tech Stack:** FastAPI + Starlette SessionMiddleware + Jinja2

---

### Task 1: 3 处核心改动

**Files:**
- Modify: `src/memos/web/routes/auth.py`
- Modify: `src/memos/web/routes/pages.py`
- Modify: `src/memos/web/templates/_nav.html`

- [ ] **Step 1: login 路由保存 name 到 session**

```python
# src/memos/web/routes/auth.py:43-45
# 在 request.session["role"] = role 之后添加
request.session["name"] = user["name"]
```

- [ ] **Step 2: index 路由传递 user_name 到模板**

```python
# src/memos/web/routes/pages.py:55-57
# 在 index() 函数中获取用户名
user_name = request.session.get("name", "")
# 在 TemplateResponse 的 context 中增加 "user_name": user_name
```

- [ ] **Step 3: _nav.html 显示用户名**

```html
<!-- src/memos/web/templates/_nav.html:67-68 -->
<!-- 在退出登录按钮前添加 -->
<span class="small text-secondary me-2">{{ user_name }}</span>
```

- [ ] **Step 4: 验证**

手动检查：登录后查看右上角是否显示用户名，退出登录后消失。
