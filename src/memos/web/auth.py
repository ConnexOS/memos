"""Dashboard 登录认证：JWT 签发/验证 + Token 哈希管理。

自实现轻量 JWT（hmac + base64），不引入 python-jose / pyjwt 等外部依赖。
Token 存储 SHA256 哈希，不存明文。
"""

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode


def generate_token() -> str:
    """生成 32 位随机 hex token（仅 memos init 时展示一次明文）"""
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    """对 token 做 SHA256 哈希，仅存储哈希值"""
    return hashlib.sha256(token.encode()).hexdigest()


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
