"""
MEMOS 安装向导 —— 交互式分步引导初始化。

将 memos init 从线性脚本升级为 6 步交互式向导：
  1. 环境检测 (Python/磁盘/网络)
  2. 选择嵌入模型 (bge-large / miniLM)
  3. 配置 LLM 端点
  4. 下载模型
  5. 生成认证 Token
  6. 初始化完成

支持中断恢复（etc/.init_state.json）和 --force / --non-interactive 模式。
"""

import json
import shutil
import socket
import sys
from pathlib import Path


def _is_tty():
    """检测是否为交互式终端（可接受用户输入）。"""
    return sys.stdin.isatty()


class InitWizard:
    """MEMOS 安装向导 6 步状态机"""

    STEPS = [
        "环境检测",
        "选择嵌入模型",
        "配置 LLM 端点",
        "下载模型",
        "生成认证 Token",
        "初始化完成",
    ]

    def __init__(self, config, force: bool = False, home: Path | None = None):
        self.config = config
        self.force = force
        from ..config import get_memos_home

        self.home = home or get_memos_home()
        self.state_file = self.home / "etc" / ".init_state.json"
        self._state = self._load_state()

    # ------------------------------------------------------------------
    # 状态持久化（中断恢复）
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _mark_step_done(self, step_idx: int):
        self._state[f"step_{step_idx}"] = True
        self._state["last_step"] = step_idx
        self._save_state()

    def _clear_state(self):
        if self.state_file.exists():
            self.state_file.unlink()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self) -> bool:
        """执行向导，返回 True=成功。非交互式终端自动降级为 force 模式。"""
        if not _is_tty():
            print("[!!] 非交互式终端，自动降级为 --force 模式")
            return self._run_force_mode()

        start_step = self._state.get("last_step", 0)
        if start_step > 0:
            print(f"\n检测到未完成的初始化，从步骤 {start_step + 1}/{len(self.STEPS)} 继续\n")

        print("=" * 55)
        print("  MEMOS 初始化向导")
        print("=" * 55)

        for i in range(start_step, len(self.STEPS)):
            step_name = self.STEPS[i]
            print(f"\n── 步骤 {i + 1}/{len(self.STEPS)}: {step_name} ──")

            method = getattr(self, f"_step_{i + 1}", None)
            if method is None:
                continue

            try:
                result = method()
            except KeyboardInterrupt:
                print("\n\n[!!] 向导已中断，进度已保存。")
                print(f"下次运行 memos init 将从步骤 {i + 1} 继续。")
                return False
            except EOFError:
                print("\n\n[!!] 输入流已关闭，进度已保存。")
                return False

            if not result:
                print(f"\n[!!] 步骤 '{step_name}' 未完成，请修复后重新运行 memos init")
                return False

            self._mark_step_done(i)

        # 完成
        self._step_complete()
        self._clear_state()
        return True

    def _run_force_mode(self) -> bool:
        """force 模式：跳过所有交互，使用默认值完成初始化。"""
        from ..config import ensure_memos_home
        from ..web.auth import generate_secret_key, generate_token, hash_token

        ensure_memos_home()

        # 环境检测（仅报告，不阻塞）
        self._print_env_check()

        # 默认模型
        model_name = "bge-large-zh-v1.5"
        print(f"  模型: {model_name} (默认)")

        # 认证
        plain_token = generate_token()
        self.config.auth.token_hash = hash_token(plain_token)
        self.config.auth.secret_key = generate_secret_key()
        self.config.save()
        print("  [OK] 配置已写入")

        # 提示词模板
        self.config.prompt.ensure_default_template()
        self.config.prompt.save()

        # 下载模型
        from ..storage.embeddings import download_model, get_model_path, model_exists

        target = get_model_path()
        if not model_exists(target):
            download_model(model_name, target)

        # 验证
        self._verify_components()

        # 更新全局单例
        import memos.config as cfg_mod

        cfg_mod.config = self.config

        print("\n初始化完成!")
        print("\nDashboard 访问 Token:")
        print(f"  {plain_token}")
        print(f"\nMEMOS_HOME: {self.home}")
        return True

    # ------------------------------------------------------------------
    # 步骤 1: 环境检测
    # ------------------------------------------------------------------

    def _step_1(self) -> bool:
        """检测 Python 版本 / 磁盘空间 / 网络连通性。"""
        self._print_env_check()
        return True  # 环境检测不阻塞（警告但继续）

    def _print_env_check(self) -> bool:
        """输出环境检测结果，返回是否全部通过。"""
        all_ok = True

        # Python 版本
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        if sys.version_info >= (3, 10):
            print(f"  {'[OK]':<6} Python {py_ver}")
        else:
            print(f"  {'[!!]':<6} Python {py_ver}（需要 ≥ 3.10）")
            all_ok = False

        # 磁盘空间（检查 home 目录所在磁盘）
        parent = self.home
        while not parent.exists():
            parent = parent.parent
        try:
            usage = shutil.disk_usage(parent)
            free_gb = usage.free / (1024**3)
            if free_gb >= 2.0:
                print(f"  {'[OK]':<6} 磁盘可用 {free_gb:.1f}GB")
            else:
                print(f"  {'[!!]':<6} 磁盘可用 {free_gb:.1f}GB（建议 ≥ 2GB）")
                all_ok = False
        except Exception:
            print(f"  {'[-]':<6} 磁盘空间（无法检测）")

        # huggingface-hub 依赖
        try:
            import huggingface_hub  # noqa: F401

            print(f"  {'[OK]':<6} huggingface-hub 已安装")
        except ImportError:
            print(f"  {'[!!]':<6} 依赖缺失: huggingface-hub 未安装（pip install huggingface-hub）")
            all_ok = False

        # 网络连通性
        try:
            socket.setdefaulttimeout(3)
            socket.gethostbyname("huggingface.co")
            print(f"  {'[OK]':<6} 网络连通")
        except Exception:
            print(f"  {'[!!]':<6} 无法连接 HuggingFace（可能需要代理）")
            all_ok = False
        finally:
            socket.setdefaulttimeout(None)

        return all_ok

    # ------------------------------------------------------------------
    # 步骤 2: 选择嵌入模型
    # ------------------------------------------------------------------

    def _detect_existing_collection_dim(self) -> int | None:
        """检测已有 ChromaDB collection 的 embedding 维度。"""
        try:
            from ..storage.chroma import create_store

            store = create_store()
            collections = store._client.list_collections()
            for col in collections:
                if col.name == store._collection_name:
                    results = col.get(limit=1, include=["embeddings"])
                    if results["embeddings"]:
                        return len(results["embeddings"][0])
            return None
        except Exception:
            return None

    def _step_2(self) -> bool:
        """选择嵌入模型：bge-large（推荐）或 miniLM（轻量）。"""
        # 维度兼容检查
        existing_dim = self._detect_existing_collection_dim()
        model_dims = {"bge-large-zh-v1.5": 1024, "all-MiniLM-L6-v2": 384}

        print("  请选择嵌入模型：")
        print("  [1] bge-large-zh-v1.5 (推荐)")
        print("      维度: 1024 | 体积: ~1.3GB | 适用: 生产环境")
        print("  [2] all-MiniLM-L6-v2 (轻量)")
        print("      维度: 384  | 体积: ~100MB | 适用: 测试/低资源环境")

        if existing_dim:
            print(f"\n  [!] 检测到已有 collection 维度: {existing_dim}")
            print(f"      所选模型维度需匹配 ({existing_dim})，否则查询会报错。")

        choice = self._input("  请选择 [1]: ").strip()
        if not choice or choice == "1":
            selected = "bge-large-zh-v1.5"
        elif choice == "2":
            selected = "all-MiniLM-L6-v2"
        else:
            print("  无效选择，使用默认 bge-large-zh-v1.5")
            selected = "bge-large-zh-v1.5"

        self._state["model_name"] = selected
        selected_dim = model_dims.get(selected, 1024)
        print(f"  → 已选择 {selected}")

        if existing_dim and existing_dim != selected_dim:
            print(f"  ⚠ 已有 collection 维度 ({existing_dim}) 与所选模型 ({selected_dim}) 不匹配")
            print("    选项: [1] 换回匹配模型  [2] --migrate-from 迁移  [3] --force 重建")

        self._save_state()
        return True

    # ------------------------------------------------------------------
    # 步骤 3: 配置 LLM 端点
    # ------------------------------------------------------------------

    def _step_3(self) -> bool:
        """交互式配置第一个 LLM 端点。"""
        print("  配置 LLM 端点用于知识提炼（可稍后在 Dashboard 或环境变量配置）")

        # 选择模板
        print("  常用模板：")
        print("    [1] Ollama   http://localhost:11434/v1")
        print("    [2] vLLM     http://localhost:8000/v1")
        print("    [3] ModelScope  https://api-inference.modelscope.cn/v1")
        print("    [4] 自定义")

        tmpl = self._input("  请选择 [4]: ").strip()
        templates = {
            "1": ("http://localhost:11434/v1", ""),
            "2": ("http://localhost:8000/v1", ""),
            "3": ("https://api-inference.modelscope.cn/v1", ""),
        }
        if tmpl in templates:
            api_base = templates[tmpl][0]
            api_key = templates[tmpl][1]
        else:
            api_base = self._input("  LLM 地址 (api_base): ").strip()

        if not api_base:
            print("  [跳过] LLM 未配置（稍后可通过 Dashboard 或环境变量配置）")
            return True

        api_key = self._input("  API Key（无则不填）: ").strip()
        model_name = self._input("  模型名（如 qwen2.5, deepseek-chat）: ").strip()

        from ..config import LLMEndpoint

        ep = LLMEndpoint(name="default", api_base=api_base, api_key=api_key or "", model=model_name or "")
        self.config.llm.endpoints = [ep]
        self.config.llm.active = "default"
        self.config.save()
        print(f"  [OK] LLM 已配置: {api_base}")

        self._state["llm_configured"] = True
        self._save_state()
        return True

    # ------------------------------------------------------------------
    # 步骤 4: 下载模型
    # ------------------------------------------------------------------

    def _step_4(self) -> bool:
        """下载选定的嵌入模型（集成 Phase 1 的 download_model）。"""
        from ..storage.embeddings import download_model, get_model_path, model_exists

        model_name = self._state.get("model_name", "bge-large-zh-v1.5")
        target = get_model_path(model_name)

        # 更新配置：模型路径、名称、向量维度
        self.config.model.path = str(target)
        self.config.model.name = model_name
        if "miniLM" in model_name.lower() or "minilm" in model_name.lower():
            self.config.model.vector_dim = 384
        else:
            self.config.model.vector_dim = 1024
        self.config.save()

        if model_exists(target):
            print(f"  [OK] 模型已就绪: {target}")
            return True

        print(f"  正在下载 {model_name} ...")
        success = download_model(model_name, target)
        if success:
            print(f"  [OK] 模型下载完成: {target}")
        return success

    # ------------------------------------------------------------------
    # 步骤 5: 生成认证 Token
    # ------------------------------------------------------------------

    def _step_5(self) -> bool:
        """生成 Dashboard 认证 Token。"""
        from ..web.auth import generate_secret_key, generate_token, hash_token

        print("  生成 Dashboard 访问凭据...")

        plain_token = generate_token()
        self.config.auth.token_hash = hash_token(plain_token)
        self.config.auth.secret_key = generate_secret_key()
        self.config.save()
        print("  [OK] 认证 Token 已生成")

        self._state["token_generated"] = True
        # 明文 Token 仅通过内存变量传递，不持久化到状态文件（安全审计 CRIT-2）
        self._plain_token = plain_token
        self._save_state()
        return True

    # ------------------------------------------------------------------
    # 步骤 6: 完成
    # ------------------------------------------------------------------

    def _step_complete(self):
        """打印完成信息。"""
        print("\n" + "=" * 55)

        # 验证组件
        self._verify_components()

        # 更新全局单例
        import memos.config as cfg_mod

        cfg_mod.config = self.config

        print(f"\nMEMOS_HOME: {self.home}")
        print("\n初始化完成! 下一步:")
        print("  memos server          启动 MCP Server")
        print("  memos dashboard       启动 Web 仪表板")
        print("  memos doctor          检查 LLM 配置")
        print("  memos hook install    安装对话自动采集 Hook")
        print()

        # 展示 Token（仅首次生成，从内存变量读取，不落盘）
        plain_token = getattr(self, "_plain_token", None)
        if plain_token:
            print("=" * 55)
            print("Dashboard 访问 Token（仅展示一次，请妥善保存）:")
            print(f"  {plain_token}")
            print("=" * 55)

        print()
        print("MCP 注册提示（在项目目录下执行）:")
        print("  claude mcp add --scope user memos -- python -m memos.server")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _input(self, prompt: str) -> str:
        """带 EOF/中断保护的 input 封装。EOF 返回空字符串，KeyboardInterrupt 重新抛出由 run() 统一处理。"""
        try:
            return input(prompt)
        except EOFError:
            return ""
        except KeyboardInterrupt:
            # 重新抛出让 run() 的中断恢复机制处理（安全审计 CRIT-7）
            raise

    def _verify_components(self):
        """验证核心组件可用。"""
        from pathlib import Path as _Path

        from ..storage.embeddings import model_exists

        print()
        print("  [验证] 检查组件...")

        model_path = _Path(self.config.model.path)
        if model_path.exists() and model_exists(model_path):
            try:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(str(model_path))
                vec = model.encode("hello")
                actual_dim = len(vec)
                expected_dim = self.config.model.vector_dim
                assert actual_dim == expected_dim, f"维度不匹配: {actual_dim} != {expected_dim}"
                print(f"  [OK] 模型可推理 ({actual_dim}维)")
            except Exception as e:
                print(f"  [!!] 模型加载失败: {e}")
        else:
            print("  [!!] 模型未就绪，请运行 memos init 下载")

        # ChromaDB
        from ..engine.memory import ContextMemory

        try:
            mem = ContextMemory()
            count = mem.store.count()
            print(f"  [OK] ChromaDB 就绪 ({count} 条记忆)")
        except Exception as e:
            print(f"  [!!] ChromaDB 异常: {e}")
