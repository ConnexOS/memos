"""Sync dev repo to GitHub repo with sanitization."""
import os, shutil, json

SRC = os.getcwd()
TGT = "D:/MyGitHub/MEMOS"

include_paths = [
    "src/memos/", "tests/", "docs/", "scripts/",
    "pyproject.toml", "CHANGELOG.md", "CLAUDE.md", "LICENSE",
    "README.md", "README.zh.md", ".github/", ".gitignore",
]

exclude_dirs = {"__pycache__", ".pytest_cache", ".ruff_cache", "venv", ".venv"}
exclude_exts = {".pyc"}


def should_include(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    for d in parts[:-1]:
        if d in exclude_dirs:
            return False
    if any(parts[-1].endswith(e) for e in exclude_exts):
        return False
    return True


# 1. Sync files
print("=== 同步文件 ===")
for path in include_paths:
    src_path = os.path.join(SRC, path)
    dst_path = os.path.join(TGT, path)
    if os.path.isdir(src_path):
        for root, dirs, files in os.walk(src_path):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            rel = os.path.relpath(root, SRC)
            for f in files:
                rel_file = os.path.join(rel, f).replace("\\", "/")
                if should_include(rel_file):
                    src_file = os.path.join(root, f)
                    dst_file = os.path.join(TGT, rel_file)
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    print(f"  [COPY] {rel_file}")
    else:
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy2(src_path, dst_path)
        print(f"  [COPY] {path}")

# 2. Generate sanitized config.example.json
cfg_src = os.path.join(SRC, "etc", "config.json")
cfg_dst = os.path.join(TGT, "etc", "config.example.json")
if os.path.exists(cfg_src):
    with open(cfg_src, "r", encoding="utf-8") as f:
        example = json.load(f)
    # Sanitize
    example.get("llm", {})["api_key"] = "your-api-key-here"
    for ep in example.get("llm", {}).get("endpoints", []):
        ep["api_base"] = "http://localhost:11434/v1"
        ep["api_key"] = ""
    if "auth" in example:
        example["auth"]["token_hash"] = ""
        example["auth"]["secret_key"] = ""
    os.makedirs(os.path.join(TGT, "etc"), exist_ok=True)
    with open(cfg_dst, "w", encoding="utf-8") as f:
        json.dump(example, f, ensure_ascii=False, indent=2)
    print("  [GEN] etc/config.example.json (sanitized)")
else:
    print("  [SKIP] etc/config.json not found")

# 3. Ensure .gitignore has etc/config.json
gitignore_path = os.path.join(TGT, ".gitignore")
extra = "etc/config.json"
if os.path.exists(gitignore_path):
    with open(gitignore_path, "r", encoding="utf-8") as f:
        content = f.read()
    if extra not in content:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write(f"\n{extra}\n")
        print(f"  [APPEND] .gitignore: {extra}")

print("\n=== 同步完成 ===")
