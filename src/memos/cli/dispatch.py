"""
MEMOS CLI — 安装后的命令行入口。

子命令：
  init      首次初始化（创建目录、下载模型、写入配置）
  server    启动 MCP Server（stdio 模式）
  dashboard 启动 Web 仪表板
  status    查看系统状态
  doctor    诊断健康度
  config    查看/修改配置
  hook      Hook 管理
  prompt    提示词模板管理
"""

import argparse
import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

from ..errors import MemoError, format_error


def cmd_init(args):
    """初始化 ~/.memos/ 目录结构，下载模型，创建默认配置。

    - 默认使用交互式 6 步向导
    - --force: 跳过向导，使用默认值强制初始化
    - --non-interactive: 从 JSON 配置文件读取参数，全自动初始化
    """
    from ..config import MemoConfig, ensure_memos_home

    cfg = MemoConfig.load()
    home = ensure_memos_home()

    # 处理迁移
    if getattr(args, "migrate_from", None):
        _migrate_from(Path(args.migrate_from), home)

    # 配置模型路径
    if getattr(args, "model_path", None):
        model_path_abs = str(Path(args.model_path).resolve())
        cfg.model.path = model_path_abs
        cfg.save()
        print(f"  [OK] 模型路径: {model_path_abs}")

    # --non-interactive 模式：从 JSON 配置文件全自动初始化
    if getattr(args, "non_interactive", None):
        _init_non_interactive(cfg, home, args.non_interactive)
        return

    # --force 模式
    if args.force:
        from ..features.wizard import InitWizard

        wizard = InitWizard(cfg, force=True, home=home)
        wizard._run_force_mode()
        return

    # 默认：交互式向导
    from ..features.wizard import InitWizard

    wizard = InitWizard(cfg, force=False, home=home)
    success = wizard.run()
    if not success:
        sys.exit(1)


def cmd_server(args):
    """启动 MCP Server。"""
    from ..server.mcp import mcp as _mcp

    _mcp.run()


def cmd_dashboard(args):
    """启动 Web 仪表板。"""
    import uvicorn

    from ..config import config as cfg

    host = args.host or cfg.dashboard.host
    port = args.port or cfg.dashboard.port
    uvicorn.run(
        "memos.web:app",
        host=host,
        port=port,
        reload=args.reload,
        timeout_graceful_shutdown=3,
    )


def cmd_today(args):
    """基于今日对话记录生成开发日报。"""
    from pathlib import Path as _Path

    from ..engine.memory import ContextMemory
    from ..engine.review import generate_daily_report, write_daily_report

    target_date = getattr(args, "date", None)
    project_id = getattr(args, "project_id", None)  # None = 不按项目过滤
    print_only = getattr(args, "print", False)
    project_dir = getattr(args, "project_dir", None)
    output_dir = _Path(project_dir) / "document" / "日报" if project_dir else None

    print("正在查询对话记录...")
    mem = ContextMemory()

    result = generate_daily_report(
        mem=mem,
        target_date=target_date,
        project_id=project_id,
    )

    print(f"  日期: {result['date']}")
    print(f"  对话轮数: {result['conversation_count']}")
    if result.get("strategy"):
        print(f"  策略: {result['strategy']}")
    if result.get("llm_calls"):
        print(f"  LLM 调用: {result['llm_calls']} 次")

    report = result.get("report")
    if not report:
        if result.get("conversation_count", 0) == 0:
            print(f"\n{result['date']} 暂无对话记录，日报未生成。")
        elif result.get("fallback"):
            print(f"\n[降级模式] {result['message']}")
            # fallback 模式下 report 包含时间线文本
            if report:
                if print_only:
                    print(report)
                else:
                    file_path, is_append = write_daily_report(report, result["date"], output_dir)
                    print(f"  日报已保存至: {file_path}")
        else:
            print(f"\n{result['message']}")
        return

    if print_only:
        print(f"\n{report}")
    else:
        file_path, is_append = write_daily_report(report, result["date"], output_dir)
        action = "追加" if is_append else "保存"
        print(f"  日报已{action}至: {file_path.resolve()}")


def cmd_status(args):
    """显示系统状态概览。"""
    from ..config import config as cfg
    from ..config import get_memos_home

    print("┌──────────────────────────────────────────────┐")
    print("│               MEMOS 系统状态                  │")
    print("├──────────────────────────────────────────────┤")

    home = get_memos_home()
    print(f"│ MEMOS_HOME    {home}")

    from .._version import __version__

    print(f"│ 版本          {__version__}")

    # 模型状态（使用配置中的实际路径）
    from pathlib import Path as _Path

    from ..storage.embeddings import get_download_progress, model_exists

    model_path = _Path(cfg.model.path)
    if model_path.exists() and model_exists(model_path):
        print(f"│ 模型          [OK] {cfg.model.name} ({cfg.model.vector_dim}维)")
    else:
        status_text = get_download_progress(model_path)
        print(f"│ 模型          [!!] {status_text}")

    # ChromaDB 状态
    from ..engine.memory import ContextMemory

    try:
        mem = ContextMemory()
        stats = mem._get_deleted_stats()
        print(f"│ ChromaDB      [OK] 就绪 ({stats['active']} 活跃 + {stats['deleted']} 已删除)")
        # 提示 Vacuum 建议
        if stats["deleted"] > 0:
            ratio = stats["deleted"] / max(stats["total"], 1)
            if ratio > 0.2:
                print(f"│                   建议运行 memos vacuum 回收磁盘空间 ({ratio:.0%} 已删除)")
    except Exception as e:
        print(f"│ ChromaDB      [!!] 不可用: {e}")

    # LLM 状态
    if cfg.llm.api_base:
        print(f"│ LLM           [OK] {cfg.llm.api_base} ({cfg.llm.active})")
    else:
        print("│ LLM           [!!] 未配置")

    # 认证状态
    if cfg.auth.token_hash and cfg.auth.secret_key:
        print("│ 认证          [OK] Token 已配置")
    else:
        print("│ 认证          [!!] 未配置，请运行 memos init")

    # Dashboard
    print(f"│ 仪表板        http://{cfg.dashboard.host}:{cfg.dashboard.port}")

    print("└──────────────────────────────────────────────┘")


def cmd_doctor(args):
    """诊断系统健康度。"""
    import importlib

    issues = []
    checks = 0

    print("MEMOS 系统诊断")
    print("=" * 50)

    # Python 版本
    checks += 1
    py_ver = sys.version_info
    if py_ver >= (3, 12):
        print(f"  [OK] Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}")
    else:
        print(f"  [!!] Python {py_ver.major}.{py_ver.minor} — 需要 >=3.12")
        issues.append("升级 Python 到 3.12+")

    # 依赖
    for pkg in ["chromadb", "sentence_transformers", "mcp", "rank_bm25", "fastapi"]:
        checks += 1
        try:
            importlib.import_module(pkg.replace("-", "_"))
            print(f"  [OK] {pkg}")
        except ImportError:
            print(f"  [!!] {pkg} 未安装")
            issues.append(f"pip install {pkg}")

    # 模型
    checks += 1
    from ..config import config as cfg
    from ..storage.embeddings import get_download_progress, get_model_path, model_exists

    model_path = get_model_path()
    if model_exists(model_path):
        print(f"  [OK] 模型就绪: {model_path}")
        # 尝试加载
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(str(model_path))
            vec = model.encode("hello")
            assert len(vec) == cfg.model.vector_dim, f"{len(vec)} != {cfg.model.vector_dim}"
            print(f"  [OK] 模型可推理 ({cfg.model.vector_dim}维)")
        except Exception as e:
            print(f"  [!!] 模型加载失败: {e}")
            issues.append("重新下载模型: memos init --force")
    else:
        status_text = get_download_progress(model_path)
        print(f"  [!!] 模型状态: {status_text}")
        issues.append("运行 memos init 下载模型")

    # ChromaDB
    checks += 1

    try:
        import chromadb

        client = chromadb.PersistentClient(path=cfg.chroma.path)
        client.list_collections()
        print(f"  [OK] ChromaDB 可连接 ({cfg.chroma.path})")
    except Exception as e:
        print(f"  [!!] ChromaDB 不可用: {e}")
        issues.append("检查 ChromaDB 路径和权限")

    # 配置合法性
    checks += 1
    from ..config import _get_config_file, validate_config

    config_path = _get_config_file()
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            try:
                config_data = json.load(f)
                config_errors = validate_config(config_data)
                if config_errors:
                    print(f"  [!!] 配置校验失败 ({len(config_errors)} 个错误)")
                    for err in config_errors[:5]:  # 最多显示 5 条
                        print(f"       - {err}")
                    issues.append("运行 memos config validate 查看详情")
                else:
                    print("  [OK] 配置文件合法")
            except json.JSONDecodeError as e:
                print(f"  [!!] 配置文件 JSON 损坏: {e}")
                issues.append("从 etc/config.json.bak 恢复配置")
    else:
        print("  [!] 配置文件不存在")

    # LLM 连通性（与 Dashboard _check_llama_health 保持同一套检测方案）
    checks += 1
    if cfg.llm.api_base:
        try:
            import requests

            endpoint_name = cfg.llm.active
            api_base = cfg.llm.api_base.rstrip("/")
            ep = cfg.llm.active_endpoint
            timeout = cfg.dashboard.test_connection_timeout
            ok = False
            method = ""

            # 尝试 1：/health 端点（兼容本地 llama.cpp）
            health_base = api_base[:-3] if api_base.endswith("/v1") else api_base
            try:
                r = requests.get(f"{health_base}/health", timeout=timeout)
                if r.status_code == 200:
                    ok = True
                    method = "/health"
            except Exception:
                pass

            # 尝试 2：轻量 /chat/completions 调用（兼容无 /health 的服务）
            # 任何 <500 的响应（含 401 鉴权错误）均说明服务器可达
            if not ok:
                try:
                    headers = {"Content-Type": "application/json"}
                    if ep.api_key:
                        headers["Authorization"] = f"Bearer {ep.api_key}"
                    payload = {
                        "model": ep.model or "default",
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                        "stream": False,
                    }
                    r = requests.post(
                        f"{api_base}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=timeout,
                    )
                    if r.status_code < 500:
                        ok = True
                        method = f"chat ({r.status_code})"
                    else:
                        print(f"  [!!] LLM 不可达 [{endpoint_name} {api_base}]: HTTP {r.status_code}")
                        issues.append(f"检查 LLM 端点 [{endpoint_name}] 服务状态")
                except Exception as e:
                    print(f"  [!!] LLM 不可达 [{endpoint_name} {api_base}]: {e}")
                    issues.append(f"检查 LLM 端点 [{endpoint_name}] 地址和网络")

            if ok:
                print(f"  [OK] LLM 可达 [{endpoint_name} {method}]: {api_base}")
        except ImportError:
            print("  [!] requests 未安装（可选）")
    else:
        print("  [!] LLM 未配置（可选）")

    # B1: ChromaDB 并发状态检查
    checks += 1
    try:
        import chromadb

        client = chromadb.PersistentClient(path=cfg.chroma.path)
        collections = client.list_collections()
        print(f"  [OK] ChromaDB 可连接 ({cfg.chroma.path}, {len(collections)} 个 collection)")
        print("  [!] 注意: ChromaDB PersistentClient 不支持多进程并发写入")
        print("        MCP Server (stdio) 和 Dashboard 不要同时对同一项目进行写操作")
        print("        同时写入可能导致 SQLite 锁冲突和数据损坏")
        print("        v0.5.0 HTTP 模式将解决此限制")
    except Exception as e:
        print(f"  [!!] ChromaDB 并发检查失败: {e}")

    # B2: safetensors 环境变量检查（仅 Windows 需要）
    checks += 1
    import os as _os

    if _os.name == "nt":
        safe_fast = _os.environ.get("SAFETENSORS_FAST_LOAD", "0")
        omp_threads = _os.environ.get("OMP_NUM_THREADS", "1")
        mkl_threads = _os.environ.get("MKL_NUM_THREADS", "1")
        if safe_fast == "0" and omp_threads == "1":
            print(
                f"  [OK] safetensors 安全模式已启用"
                f" (SAFETENSORS_FAST_LOAD=0, OMP_NUM_THREADS=1, MKL_NUM_THREADS={mkl_threads})"
            )
            print("       编码性能稍降低，但避免 Windows 多线程加载崩溃")
        else:
            print(
                f"  [!!] safetensors 环境变量未正确设置 (SAFETENSORS_FAST_LOAD={safe_fast}, OMP_NUM_THREADS={omp_threads})"
            )
            issues.append("设置环境变量 SAFETENSORS_FAST_LOAD=0 OMP_NUM_THREADS=1 避免 Windows 崩溃")
    else:
        print("  [-] safetensors 环境变量检查（仅 Windows 需要，跳过）")

    print()
    if issues:
        print(f"发现 {len(issues)} 个问题:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
        return 1
    else:
        print("所有检查通过!")
        return 0


def cmd_vacuum(args):
    """回收 ChromaDB 已删除文档占用的磁盘空间。"""
    from pathlib import Path as _Path

    from ..engine.memory import ContextMemory

    mem = ContextMemory()

    # --purge-test: 清理测试残留 collection
    if getattr(args, "purge_test", False):
        print("清理测试 collection...")
        try:
            import chromadb

            from ..config import config as _cfg

            client = chromadb.PersistentClient(path=_cfg.chroma.path)
            all_cols = client.list_collections()
            to_delete = [c for c in all_cols if c.name != "project_memory"]
            if not to_delete:
                print("  无测试 collection 需要清理")
            else:
                for i, c in enumerate(to_delete):
                    if c.count() > 0:
                        print(f"  [!] {c.name}: {c.count()} 条非空数据，跳过")
                        continue
                    client.delete_collection(c.name)
                    if (i + 1) % 500 == 0:
                        print(f"  已删除 {i + 1}/{len(to_delete)}...")
                print(f"  [OK] 已删除 {len(to_delete)} 个空测试 collection")
        except Exception as e:
            print(f"  [ERROR] 清理失败: {e}")
            return

    stats = mem._get_deleted_stats()
    print(f"总记录: {stats['total']}  |  活跃: {stats['active']}  |  已删除: {stats['deleted']}")
    if stats["deleted"] == 0:
        print("无需清理")
        return

    from ..config import config

    db_path = _Path(config.chroma.path) / "chroma.sqlite3"
    if db_path.exists():
        before = db_path.stat().st_size
    else:
        before = 0

    print(f"数据库文件: {db_path}")
    if not before:
        print("数据库文件不存在")
        return

    # v0.4.0 HIGH-2: 加锁防止 VACUUM 期间并发写入
    with mem._vacuum_lock:
        ok = mem.store.vacuum()
    if ok:
        after = db_path.stat().st_size
        reclaimed = before - after
        print(
            f"VACUUM 完成: {before / 1024 / 1024:.1f}MB → {after / 1024 / 1024:.1f}MB"
            f" (回收 {reclaimed / 1024 / 1024:.1f}MB)"
        )


def cmd_reindex(args):
    """全量重建向量索引 + BM25 索引（异常恢复用）。
    v0.4.7: 扩展为重建 ChromaDB HNSW 向量索引（修复 Error finding id 类索引损坏）。
    """
    from ..engine.memory import ContextMemory

    mem = ContextMemory()

    # 阶段一：重建向量索引（ChromaDB HNSW）
    print("阶段 1/3: 导出全量数据...")
    result = mem.store.reindex()
    if result["status"] == "error":
        print(f"  [ERROR] 向量索引重建失败: {result.get('error')}", file=sys.stderr)
        sys.exit(1)
    if result["status"] == "empty":
        print("  [OK] 数据库为空，无需重建")
    else:
        detail = (
            f"{result['count']} 条"
            if result["count"] == result.get("total")
            else f"{result['count']}/{result.get('total')} 条"
        )
        print(f"  [OK] 向量索引已重建 ({detail})")

    # 阶段二：重建 BM25 索引
    print("阶段 2/3: 重建 BM25 索引...")
    mem._invalidate_bm25()
    mem._ensure_bm25_index()
    if mem._bm25 is not None:
        print(f"  [OK] BM25 索引已重建 ({mem._bm25.corpus_size} 篇文档)")
    else:
        print("  [OK] BM25 索引已清空（无活跃文档）")

    # 阶段三：VACUUM 回收空间
    print("阶段 3/3: 回收磁盘空间...")
    try:
        mem.store.vacuum()
        print("  [OK] 磁盘空间已回收")
    except Exception as e:
        print(f"  [!] VACUUM 跳过: {e}")

    print("全部完成。建议运行 memos doctor 验证健康状态。")


def cmd_config(args):
    """查看/修改配置。"""
    from ..config import config as cfg

    if args.action == "show":
        for k, v in cfg.flatten().items():
            print(f"{k}: {v}")
    elif args.action == "set":
        if not args.key or args.value is None:
            print("用法: memos config set <key> <value>", file=sys.stderr)
            sys.exit(1)
        ok = cfg.update_field(args.key, args.value)
        if ok:
            # v0.4.0 MED-1: 模型切换时联动更新 path/vector_dim 并提示向量维度不兼容
            if args.key == "model.name":
                _handle_model_switch(cfg, args.value)
            cfg.save()
            print(f"[OK] {args.key} = {args.value}")
        else:
            print(f"[ERROR] 无法设置 {args.key}（不存在或非标量字段）", file=sys.stderr)
            sys.exit(1)
    elif args.action == "validate":
        from ..config import _get_config_file, validate_config

        config_path = Path(args.file) if args.file else _get_config_file()
        if not config_path.exists():
            print(f"[ERROR] 配置文件不存在: {config_path}", file=sys.stderr)
            sys.exit(1)

        with open(config_path, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                print(f"[ERROR] JSON 格式错误: {e}", file=sys.stderr)
                print("建议: 检查 JSON 语法，或从 etc/config.json.bak 恢复", file=sys.stderr)
                sys.exit(1)

        errors = validate_config(data)
        if errors:
            print(f"配置校验失败 ({len(errors)} 个错误):")
            for i, err in enumerate(errors, 1):
                print(f"  {i}. {err}")
            print()
            print("建议: 修正上述错误，或从 etc/config.json.bak 恢复备份")
            sys.exit(1)
        else:
            # 统计子配置和字段数
            sections = [k for k in data if k != "prompt"]
            field_count = sum(len(v) if isinstance(v, dict) else 1 for v in data.values())
            print(f"[OK] 配置校验通过 ({len(sections)} 个子配置, {field_count} 个字段)")
    elif args.action == "reload":
        from ..config import MemoConfig

        new_cfg = MemoConfig.load()
        # 更新全局单例
        import memos.config as mod

        mod.config = new_cfg
        print("[OK] 配置已重载")


def _handle_model_switch(cfg, new_model_name: str):
    """v0.4.0 MED-1: 模型切换时联动更新 path/vector_dim 并检查维度兼容性。"""
    # 联动更新模型路径
    from ..storage.embeddings import get_model_path

    cfg.model.path = str(get_model_path(new_model_name))
    # 联动更新向量维度
    if "minilm" in new_model_name.lower() or "miniLM" in new_model_name.lower():
        new_dim = 384
    else:
        new_dim = 1024
    old_dim = cfg.model.vector_dim
    cfg.model.vector_dim = new_dim
    print(f"  [OK] 模型路径已联动更新: {cfg.model.path}")
    print(f"  [OK] 向量维度已联动更新: {old_dim} → {new_dim}")
    # 维度不兼容警告：检查 ChromaDB 中是否有存量记忆
    if old_dim != new_dim:
        try:
            from ..engine.memory import ContextMemory

            mem = ContextMemory()
            existing = mem.count_memories(include_archived=True)
            if existing > 0:
                print(f"  [!!] 警告: 已有 {existing} 条记忆的向量维度为 {old_dim}，与目标维度 {new_dim} 不兼容")
                print("  重建索引方法: ① memos export --include-embeddings > backup.jsonl")
                print("                ② memos import backup.jsonl --strategy overwrite")
                print("                ③ memos reindex")
        except Exception:
            pass  # ChromaDB 不可用时静默跳过


def _configure_llm_interactive(config):
    """交互式 LLM 配置引导（在 memos init 中调用）。"""
    print("LLM 自动提炼配置（可选，按回车跳过）")
    print("-" * 40)

    try:
        api_base = input("  LLM 地址 (api_base): ").strip()
    except (EOFError, KeyboardInterrupt):
        api_base = ""

    if api_base:
        config.llm.api_base = api_base

        try:
            api_key = input("  API Key（无则不填）: ").strip()
        except (EOFError, KeyboardInterrupt):
            api_key = ""
        if api_key:
            config.llm.api_key = api_key

        try:
            model = input("  模型名（如 gemma, deepseek-chat）: ").strip()
        except (EOFError, KeyboardInterrupt):
            model = ""
        if model:
            config.llm.active_endpoint.model = model

        config.save()
        print(f"  [OK] LLM 已配置: {api_base}")
    else:
        print("  [跳过] LLM 未配置（可稍后通过 memos config set 或环境变量配置）")


def _verify_components(cfg, home: Path):
    """验证核心组件可用。"""
    from ..storage.embeddings import model_exists

    # 检查模型（使用配置中的实际路径）
    model_path = Path(cfg.model.path)
    if model_path.exists() and model_exists(model_path):
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(str(model_path))
            vec = model.encode("hello")
            assert len(vec) == cfg.model.vector_dim, f"{len(vec)} != {cfg.model.vector_dim}"
            print(f"  [OK] 模型可加载 ({cfg.model.vector_dim}维)")
        except Exception as e:
            print(f"  [!!] 模型加载失败: {e}")
    else:
        print("  [!] 模型未就绪（可能需手动下载）")

    # 检查 ChromaDB
    try:
        import chromadb

        client = chromadb.PersistentClient(path=cfg.chroma.path)
        client.list_collections()
        print("  [OK] ChromaDB 可连接")
    except Exception as e:
        print(f"  [!!] ChromaDB 不可用: {e}")


def _migrate_from(src: Path, dst: Path):
    """从旧目录迁移数据。"""
    import shutil

    if not src.exists():
        print(f"[!!] 源目录不存在: {src}", file=sys.stderr)
        return

    migrations = [
        ("etc/config.json", "etc/config.json"),
        ("etc/prompts.json", "etc/prompts.json"),
        ("memdb", "memdb"),
        ("model", "model"),
    ]

    for src_rel, dst_rel in migrations:
        src_path = src / src_rel
        dst_path = dst / dst_rel
        if src_path.exists() and not dst_path.exists():
            if src_path.is_dir():
                shutil.copytree(src_path, dst_path)
            else:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
            print(f"  [OK] 已迁移: {src_rel}")


def _init_non_interactive(cfg, home: Path, config_file: str):
    """从 JSON 配置文件全自动初始化（CI/CD 场景）。"""
    config_path = Path(config_file)
    if not config_path.exists():
        print(f"[ERROR] 配置文件不存在: {config_file}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        init_config = json.load(f)

    from ..config import ensure_memos_home

    ensure_memos_home()

    # 模型配置
    model_name = init_config.get("model_name", "bge-large-zh-v1.5")
    model_path = init_config.get("model_path")
    if model_path:
        cfg.model.path = str(Path(model_path).resolve())
    print(f"  [OK] 模型: {model_name}")

    # LLM 配置
    llm_config = init_config.get("llm", {})
    if llm_config:
        from ..config import LLMEndpoint

        ep = LLMEndpoint(
            name=llm_config.get("name", "default"),
            api_base=llm_config.get("api_base", "http://localhost:11434/v1"),
            api_key=llm_config.get("api_key", ""),
            model=llm_config.get("model", ""),
        )
        cfg.llm.endpoints = [ep]
        cfg.llm.active = "default"
        print(f"  [OK] LLM: {ep.api_base}")

    # 认证
    from ..web.auth import generate_secret_key, generate_token, hash_token

    plain_token = generate_token()
    cfg.auth.token_hash = hash_token(plain_token)
    cfg.auth.secret_key = generate_secret_key()

    # 保存配置
    cfg.save()
    print("  [OK] 配置已写入")

    # 提示词模板
    cfg.prompt.ensure_default_template()
    cfg.prompt.save()
    print("  [OK] 提示词模板已写入")

    # 下载模型
    from ..storage.embeddings import download_model, get_model_path, model_exists

    target = Path(model_path) if model_path else get_model_path()
    if not model_exists(target):
        download_model(model_name, target)

    # 更新全局单例
    import memos.config as cfg_mod

    cfg_mod.config = cfg

    print()
    print(f"MEMOS_HOME: {home}")
    print("初始化完成!")
    if plain_token:
        print(f"Dashboard Token: {plain_token}")


def cmd_export(args):
    """导出记忆为 JSON Lines 格式（.memos v1.0）。"""
    import json

    from ..engine.memory import ContextMemory

    mem = ContextMemory()
    type_filter = args.type if args.type else None
    project_id = args.project_id if args.project_id else None

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        count = 0
        for item in mem.export_memories(
            project_id=project_id,
            type_filter=type_filter,
            include_embeddings=args.include_embeddings,
            since=args.since if hasattr(args, "since") else None,
            until=args.until if hasattr(args, "until") else None,
            review_status=args.review_status if hasattr(args, "review_status") else None,
        ):
            # 格式头部
            if "_header" in item:
                out.write("# " + json.dumps(item["_header"], ensure_ascii=False) + "\n")
                continue
            if not args.include_embeddings:
                item.pop("embedding", None)
            out.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
            if count % 100 == 0:
                print(f"已导出 {count} 条...", file=sys.stderr)
    finally:
        if args.output:
            out.close()
    print(f"导出完成: {count} 条", file=sys.stderr)


def cmd_backup(args):
    """执行全量物理备份或列出已有备份。"""
    from ..features.backup import backup_memdb, list_backups

    if args.list_backups:
        result = list_backups(args.target)
        backups = result["backups"]
        if not backups:
            print("暂无备份")
        else:
            print(f"备份目标: {result['target_dir']}")
            print(f"备份总数: {result['total']} (最大保留: {result['max_backups']})")
            if result.get("days_since_export") is not None:
                print(f"距上次逻辑导出: {result['days_since_export']} 天")
            print()
            for b in backups:
                ts_str = datetime.fromtimestamp(b["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                size_mb = b.get("size_bytes", 0) / (1024 * 1024)
                status_label = "✓" if b.get("status") == "complete" else "⚠" if b.get("status") == "partial" else "✗"
                print(f"  {status_label} {b['id']}")
                print(f"    时间: {ts_str}  大小: {size_mb:.2f} MB  文件: {b.get('file_count', '?')}")
                if b.get("status") == "missing":
                    print("    ⚠ 备份目录已不存在")
        return

    print("开始全量物理备份...")
    try:
        result = backup_memdb(args.target)
        print("备份完成!")
        print(f"  路径: {result['path']}")
        print(f"  大小: {result['size_mb']} MB ({result['file_count']} 文件)")
        print(f"  耗时: {result['elapsed_seconds']} 秒")
        print(f"  状态: {'✓ 完整' if result['status'] == 'complete' else '⚠ 部分（完整性校验未通过）'}")
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_restore(args):
    """从指定备份恢复数据。"""
    from ..features.backup import restore_backup

    result = restore_backup(args.path, force=args.force, interactive=True)
    if result["success"]:
        print(result["message"])
    else:
        print(f"恢复失败: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_import(args):
    """从 JSON Lines 文件导入记忆（.memos v1.0）。"""
    from ..engine.memory import ContextMemory

    mem = ContextMemory()
    dry_run = getattr(args, "dry_run", False)
    preserve_ids = getattr(args, "preserve_ids", False)

    with open(args.file, encoding="utf-8") as f:
        result = mem.import_memories(
            f,
            target_project_id=args.project_id,
            strategy=args.strategy,
            preserve_ids=preserve_ids,
            dry_run=dry_run,
        )

    if dry_run:
        print(f"预校验完成: 总 {result['total_lines']} 条, 合法 {result['valid_lines']} 条, 错误 {result['failed']} 条")
    else:
        print(f"导入完成: 成功 {result['imported']} 条, 跳过 {result['skipped']} 条, 失败 {result['failed']} 条")
    if result["errors"]:
        print("错误详情:")
        for err in result["errors"][:20]:  # 最多显示 20 条
            print(f"  行 {err['line']}: {err['error']}")
        if len(result["errors"]) > 20:
            print(f"  ... 等 {len(result['errors'])} 条错误")
    if result["failed"] > 0:
        sys.exit(1)


def cmd_auth(args):
    """认证管理（regen 重新生成 Token）。"""
    from ..config import config as cfg
    from ..web.auth import generate_secret_key, generate_token, hash_token

    if args.action == "regen":
        token = generate_token()
        cfg.auth.token_hash = hash_token(token)
        cfg.auth.secret_key = generate_secret_key()
        cfg.save()
        print("=" * 50)
        print("新的 Dashboard 访问 Token（仅展示一次）:")
        print(f"  {token}")
        print("=" * 50)
        print()
        print("配置已保存，重启 Dashboard 后生效。")


def cmd_hook(args):
    """安装/卸载 Hook。"""
    action = args.action or "status"

    if action == "install":
        _install_hooks(args.global_mode)
    elif action == "uninstall":
        _uninstall_hooks(args.global_mode)
    elif action == "status":
        _hook_status(args.global_mode)


def _get_settings_path(global_mode: bool) -> Path:
    """获取 settings.json 路径。"""
    if global_mode:
        return Path.home() / ".claude" / "settings.json"
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    return project_dir / ".claude" / "settings.json"


def _make_hook_config() -> dict:
    """构建 MEMOS Hook 的 settings.json 片段。使用 sys.executable 确保走 venv 解释器。"""
    py = sys.executable
    if platform.system() == "Windows":
        # Windows cmd 不支持 KEY=VALUE 前缀，需用 cmd /c + set
        prompt_cmd = f'cmd /c "set SAFETENSORS_FAST_LOAD=0 && \\"{py}\\" -m memos.hooks.prompt"'
        stop_cmd = f'cmd /c "set SAFETENSORS_FAST_LOAD=0 && \\"{py}\\" -m memos.hooks.stop"'
    else:
        prompt_cmd = f'SAFETENSORS_FAST_LOAD=0 "{py}" -m memos.hooks.prompt'
        stop_cmd = f'SAFETENSORS_FAST_LOAD=0 "{py}" -m memos.hooks.stop'
    return {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": prompt_cmd,
                        "timeout": 60,
                    }
                ]
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": stop_cmd,
                        "timeout": 30,
                    }
                ]
            }
        ],
    }


def _install_hooks(global_mode: bool):
    """安装 Hook 到 settings.json（保留已有配置不覆盖）。"""
    settings_path = _get_settings_path(global_mode)
    scope = "全局" if global_mode else "项目"

    # 读取现有配置
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        data = {}

    # 合并 hooks
    hooks = data.setdefault("hooks", {})
    new_config = _make_hook_config()

    for event_name, hook_list in new_config.items():
        existing = hooks.setdefault(event_name, [])
        # 检查是否已存在相同的 hook 配置
        for entry in hook_list:
            already_installed = any("memos.hooks" in e.get("hooks", [{}])[0].get("command", "") for e in existing)
            if not already_installed:
                existing.append(entry)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Hook 已安装到 {scope}配置: {settings_path}")
    print("      UserPromptSubmit → python -m memos.hooks.prompt")
    print("      Stop             → python -m memos.hooks.stop")


def _uninstall_hooks(global_mode: bool):
    """从 settings.json 移除 MEMOS Hook 配置。"""
    settings_path = _get_settings_path(global_mode)
    scope = "全局" if global_mode else "项目"

    if not settings_path.exists():
        print(f"[!] {scope}配置不存在: {settings_path}")
        return

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})

    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        hooks[event_name] = [
            e for e in entries if not any("memos.hooks" in h.get("command", "") for h in e.get("hooks", []))
        ]
        if not hooks[event_name]:
            del hooks[event_name]

    if not hooks:
        data.pop("hooks", None)

    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Hook 已从 {scope}配置卸载: {settings_path}")


def _hook_status(global_mode: bool):
    """显示当前 Hook 安装状态。"""
    settings_path = _get_settings_path(global_mode)
    scope = "全局" if global_mode else "项目"

    if not settings_path.exists():
        print(f"MEMOS Hook 状态 ({scope})")
        print(f"  配置文件不存在: {settings_path}")
        print(f"  运行 memos hook install{' --global' if global_mode else ''} 安装")
        return

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})

    print(f"MEMOS Hook 状态 ({scope})")
    print(f"  配置文件: {settings_path}")
    print()

    prompt_hooks = hooks.get("UserPromptSubmit", [])
    stop_hooks = hooks.get("Stop", [])

    prompt_installed = any("memos.hooks" in h.get("command", "") for e in prompt_hooks for h in e.get("hooks", []))
    stop_installed = any("memos.hooks" in h.get("command", "") for e in stop_hooks for h in e.get("hooks", []))

    status_icon = "[OK]" if prompt_installed else "[!!]"
    print(f"  UserPromptSubmit {status_icon} {'已安装' if prompt_installed else '未安装'}")
    status_icon = "[OK]" if stop_installed else "[!!]"
    print(f"  Stop             {status_icon} {'已安装' if stop_installed else '未安装'}")

    if not prompt_installed and not stop_installed:
        print()
        print(f"  运行 memos hook install{' --global' if global_mode else ''} 安装")


def main():
    # Windows GBK 终端兼容：强制 stdout 使用 UTF-8，避免 Unicode 字符报错
    try:
        if sys.stdout.encoding and sys.stdout.encoding.upper() != "UTF-8":
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="memos", description="长时记忆系统 CLI")
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="首次初始化")
    p_init.add_argument("--model-path", help="指定本地模型路径（跳过下载）")
    p_init.add_argument("--force", action="store_true", help="强制重新初始化（覆盖已有配置）")
    p_init.add_argument("--non-interactive", metavar="CONFIG", help="从 JSON 配置文件全自动初始化（CI/CD 场景）")
    p_init.add_argument("--migrate-from", help="从旧目录迁移数据")

    # auth
    p_auth = sub.add_parser("auth", help="认证管理")
    p_auth_subs = p_auth.add_subparsers(dest="action")
    p_auth_subs.add_parser("regen", help="重新生成 Dashboard 访问 Token")

    # server
    p_server = sub.add_parser("server", help="启动 MCP Server")
    p_server.add_argument("--debug", action="store_true", help="开启调试日志")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="启动 Web 仪表板")
    p_dash.add_argument("--host", help="监听地址（默认 127.0.0.1）")
    p_dash.add_argument("--port", type=int, help="监听端口（默认 8000）")
    p_dash.add_argument("--reload", action="store_true", help="热重载（仅开发用）")

    # today
    p_today = sub.add_parser("today", help="生成今日开发日报")
    p_today.add_argument("--date", help="指定日期 YYYY-MM-DD（默认今天）")
    p_today.add_argument("--project-id", help="指定项目 ID 过滤")
    p_today.add_argument(
        "-D",
        "--project-dir",
        help="项目根目录（日报保存到此目录的 document/日报/ 下，默认自动检测 CLAUDE_PROJECT_DIR 或 CWD）",
    )
    p_today.add_argument("--print", action="store_true", help="仅终端输出，不写入文件")

    # status
    sub.add_parser("status", help="查看系统状态")

    # doctor
    sub.add_parser("doctor", help="诊断系统健康度")

    # vacuum
    p_vacuum = sub.add_parser("vacuum", help="回收数据库已删除文档的磁盘空间")
    p_vacuum.add_argument("--purge-test", action="store_true", help="清理测试残留的 ChromaDB collection")

    # reindex (v0.4.7: 升级为全量重建向量索引 + BM25 索引)
    sub.add_parser("reindex", help="全量重建向量索引和 BM25 索引（修复索引损坏）")

    # config
    p_config = sub.add_parser("config", help="配置管理")
    p_config_subs = p_config.add_subparsers(dest="action")
    p_config_subs.add_parser("show", help="查看当前配置")
    p_set = p_config_subs.add_parser("set", help="更新配置项")
    p_set.add_argument("key", help="配置键（如 llm.active）")
    p_set.add_argument("value", help="配置值")
    p_config_subs.add_parser("reload", help="从文件重载配置")
    p_validate = p_config_subs.add_parser("validate", help="校验配置文件合法性")
    p_validate.add_argument("--file", help="指定配置文件路径（默认 etc/config.json）")

    # export
    p_export = sub.add_parser("export", help="导出记忆为 JSON Lines 文件")
    p_export.add_argument("--format", default="jsonl", choices=["jsonl"], help="输出格式（默认 jsonl）")
    p_export.add_argument("--output", "-o", help="输出文件路径（默认 stdout）")
    p_export.add_argument("--project-id", help="按项目过滤")
    p_export.add_argument(
        "--type",
        nargs="*",
        choices=[
            "fact",
            "decision",
            "preference",
            "todo",
            "bug_fix",
            "feature_design",
            "code_optimize",
            "tech_knowledge",
        ],  # P2-7: 扩展支持 Dashboard 4 类
        help="按类型过滤（可多选，默认全部）",
    )
    p_export.add_argument("--include-embeddings", action="store_true", help="包含向量")
    p_export.add_argument("--since", help="起始日期 YYYY-MM-DD")
    p_export.add_argument("--until", help="结束日期 YYYY-MM-DD")
    p_export.add_argument("--review-status", choices=["approved", "pending"], help="按复审状态过滤")

    # import
    p_import = sub.add_parser("import", help="从 JSON Lines 文件导入记忆")
    p_import.add_argument("file", help="要导入的 .jsonl 文件路径")
    p_import.add_argument("--project-id", help="目标项目 ID（默认保留原始 project_id）")
    p_import.add_argument(
        "--strategy", default="skip", choices=["skip", "overwrite", "duplicate"], help="去重策略（默认 skip）"
    )
    p_import.add_argument("--dry-run", action="store_true", help="仅校验不导入")
    p_import.add_argument("--preserve-ids", action="store_true", help="保留原始 ID（与 --strategy overwrite 配合使用）")

    # hook
    p_hook = sub.add_parser("hook", help="Hook 管理")
    p_hook_subs = p_hook.add_subparsers(dest="action")
    p_install = p_hook_subs.add_parser("install", help="安装 Hook")
    p_install.add_argument(
        "--global", dest="global_mode", action="store_true", help="全局模式（~/.claude/settings.json）"
    )
    p_uninstall = p_hook_subs.add_parser("uninstall", help="卸载 Hook")
    p_uninstall.add_argument(
        "--global", dest="global_mode", action="store_true", help="全局模式（~/.claude/settings.json）"
    )
    p_status = p_hook_subs.add_parser("status", help="查看 Hook 状态")
    p_status.add_argument(
        "--global", dest="global_mode", action="store_true", help="全局模式（~/.claude/settings.json）"
    )

    # backup
    p_backup = sub.add_parser("backup", help="全量物理备份")
    p_backup.add_argument("--target", help="备份目标目录（默认 memdb/backups/）")
    p_backup.add_argument("--list", dest="list_backups", action="store_true", help="列出已有备份")

    # restore
    p_restore = sub.add_parser("restore", help="从备份恢复")
    p_restore.add_argument("path", help="备份目录路径")
    p_restore.add_argument("--force", action="store_true", help="跳过交互确认")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "init": cmd_init,
        "today": cmd_today,
        "server": cmd_server,
        "dashboard": cmd_dashboard,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "vacuum": cmd_vacuum,
        "reindex": cmd_reindex,
        "config": cmd_config,
        "export": cmd_export,
        "import": cmd_import,
        "auth": cmd_auth,
        "hook": cmd_hook,
        "backup": cmd_backup,
        "restore": cmd_restore,
    }
    # doctor 返回退出码
    try:
        if args.command == "doctor":
            sys.exit(cmd_doctor(args))
        else:
            dispatch[args.command](args)
    except MemoError as e:
        print(format_error(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
