"""Dashboard 登录认证：多用户 Token 管理 + JWT session。

多用户 Token 使用 bcrypt 哈希，通过 users.json 持久化。
旧版单 Token（SHA256 + JWT）保持兼容。
"""

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path

_USERS_FILE = None


def _get_users_file() -> Path:
    """获取 users.json 文件路径（惰性查找 + 缓存）。"""
    global _USERS_FILE
    if _USERS_FILE:
        return _USERS_FILE
    from ..config import get_memos_home

    return get_memos_home() / "etc" / "users.json"


def generate_token() -> str:
    """生成新格式 Token（mtok_ 前缀 + 20 位随机 hex）"""
    return "mtok_" + secrets.token_hex(10)


def hash_token(token: str) -> str:
    """对 token 做 bcrypt 哈希"""
    import bcrypt

    return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()


def verify_token(token: str, token_hash: str) -> bool:
    """用 bcrypt 验证 token 与已存储的哈希是否匹配"""
    import bcrypt

    try:
        return bcrypt.checkpw(token.encode(), token_hash.encode())
    except Exception:
        return False


def verify_token_against_users(token: str) -> dict | None:
    """遍历 users.json，查找匹配的用户并返回身份信息。"""
    users_file = _get_users_file()
    if not users_file.exists():
        return None
    try:
        with open(users_file, encoding="utf-8") as f:
            users = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    for user in users:
        if verify_token(token, user.get("token_hash", "")):
            return {
                "creator_id": user.get("creator_id", user["name"]),
                "role": user.get("role", "member"),
                "name": user["name"],
            }
    return None


def _read_users() -> list[dict]:
    """读取 users.json，返回用户列表（文件不存在时返回 []）。"""
    users_file = _get_users_file()
    if not users_file.exists():
        return []
    try:
        with open(users_file, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_users(users: list[dict]) -> None:
    """原子写入 users.json（先写临时文件，再 rename，避免 Windows msvcrt 文件锁问题）。"""
    import os
    import tempfile

    users_file = _get_users_file()
    users_file.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(users, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_path = tempfile.mkstemp(dir=str(users_file.parent), suffix=".tmp")
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, users_file)


def _make_user_entry(name: str, token_hash: str, role: str) -> dict:
    """构造用户字典条目。"""
    from datetime import datetime, timezone

    return {
        "name": name,
        "token_hash": token_hash,
        "role": role,
        "creator_id": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "token_updated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_user(name: str, token_hash: str, role: str = "member") -> dict:
    """保存用户到 users.json（原子写入，并发安全）。"""
    users = _read_users()
    for u in users:
        if u["name"] == name:
            raise ValueError(f"用户 '{name}' 已存在")
    user = _make_user_entry(name, token_hash, role)
    users.append(user)
    _write_users(users)
    return user


def remove_user(name: str) -> bool:
    """从 users.json 删除用户（原子写入，并发安全）。返回 True 表示删除成功。"""
    users = _read_users()
    filtered = [u for u in users if u["name"] != name]
    if len(filtered) == len(users):
        return False
    _write_users(filtered)
    return True


def list_users() -> list[dict]:
    """列出所有用户。"""
    users_file = _get_users_file()
    if not users_file.exists():
        return []
    with open(users_file, encoding="utf-8") as f:
        return json.load(f)


def create_admin_on_first_start():
    """首次启动时创建 admin 用户（users.json 不存在时）。返回明文的 admin token。"""
    users_file = _get_users_file()
    if users_file.exists():
        return None
    token = generate_token()
    token_hash_val = hash_token(token)
    save_user("admin", token_hash_val, role="admin")
    return token


def _resolve_creator_id(from_ctx: bool = False) -> str:
    """从当前 context 获取请求者标识。"""
    if from_ctx:
        from ..server.mcp import _auth_token_ctx  # 惰性导入：避免循环导入

        token = _auth_token_ctx.get()
        if token:
            user = verify_token_against_users(token)
            if user:
                return user["creator_id"]
    return "unknown"


def generate_secret_key() -> str:
    """生成 64 位随机 hex 作为 JWT HMAC 签名密钥"""
    return secrets.token_hex(64)


def create_session_token(token_hash: str, secret_key: str, ttl: int) -> str:
    """签发 JWT session token。

    Payload 含 token_hash（用于验证）+ exp（过期时间）。
    签名使用 HMAC-SHA256。
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "token_hash": token_hash,
        "exp": int(time.time()) + ttl,
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")))
    signing_input = f"{header_b64}.{payload_b64}"
    signature = _hmac_sign(signing_input, secret_key)
    return f"{signing_input}.{signature}"


def verify_session_token(token_str: str, secret_key: str) -> dict | None:
    """验证 JWT session token，返回 payload 或 None（无效/过期）"""
    try:
        parts = token_str.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, signature = parts
        signing_input = f"{header_b64}.{payload_b64}"
        expected_sig = _hmac_sign(signing_input, secret_key)
        if not hmac.compare_digest(signature, expected_sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        # P2-2: 区分程序 bug 与正常认证失败，未知异常用 WARNING 便于生产排查
        import logging

        logging.getLogger(__name__).warning("Session token 验证异常", exc_info=True)
        return None


def _b64url_encode(data: str) -> str:
    return urlsafe_b64encode(data.encode()).decode().rstrip("=")


def _b64url_decode(data: str) -> str:
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return urlsafe_b64decode(data.encode()).decode()


def _hmac_sign(message: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), message.encode(), hashlib.sha256)
    return urlsafe_b64encode(mac.digest()).decode().rstrip("=")
