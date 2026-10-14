"""
Security utilities: password hashing, JWT token handling, file download tokens.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional, Union
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 文件下载令牌有效期（秒）——Agent 生成的文件回传用
FILE_TOKEN_TTL_SECONDS = 30 * 24 * 3600

# 令牌签名（hex 字符数）与总长上限：畸形/超长输入直接拒绝，不做无谓计算
_FILE_TOKEN_SIG_LENGTH = 32
_FILE_TOKEN_MAX_LENGTH = 4096


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        return None


def _file_token_signature(payload_b64: str) -> str:
    """对令牌载荷做 HMAC-SHA256 签名（截断到 32 hex 字符）。"""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def create_file_token(
    file_path: str,
    user_id: Optional[int],
    expires_in: int = FILE_TOKEN_TTL_SECONDS,
) -> str:
    """为 Agent 生成的文件签发下载令牌（HMAC 自校验，无需服务端存储）。

    Agent 可以把文件写到任意路径（技能目录、/tmp、用户指定目录），前端拿到的是
    令牌而非路径，因此既不用暴露文件系统权限，也能跨 worker/重启长期有效。

    Args:
        file_path: 文件绝对路径（内部会 realpath 归一化）。
        user_id: 文件所属用户，令牌与该用户绑定，他人拿到链接也无法下载。
        expires_in: 有效期（秒），默认 30 天。

    Returns:
        "<payload_base64>.<signature>" 形式的令牌字符串。
    """
    payload = {
        "u": user_id,
        "e": int(time.time()) + int(expires_in),
        "p": os.path.realpath(file_path),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{payload_b64}.{_file_token_signature(payload_b64)}"


def verify_file_token(token: str, user_id: Optional[int]) -> Optional[str]:
    """校验下载令牌并返回文件路径。

    token 来自请求参数（不可信输入），任何格式异常都返回 None 而不是抛异常，
    避免畸形输入把接口打成 500。

    Args:
        token: create_file_token() 签发的令牌。
        user_id: 当前请求用户，必须与签发时一致；None 一律拒绝。

    Returns:
        文件绝对路径；签名不符、已过期或用户不匹配时返回 None。
    """
    if not token or not user_id or len(token) > _FILE_TOKEN_MAX_LENGTH:
        return None
    payload_b64, sep, signature = token.rpartition(".")
    if not sep or not payload_b64 or not signature:
        return None
    # 签名固定为 hex 长度；长度不符直接拒绝，不做 HMAC 计算
    if len(signature) != _FILE_TOKEN_SIG_LENGTH:
        return None
    try:
        if not hmac.compare_digest(signature, _file_token_signature(payload_b64)):
            return None
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("u") != user_id:
        return None
    try:
        if int(payload.get("e", 0)) < time.time():
            return None
    except (TypeError, ValueError):
        return None
    path = payload.get("p")
    return path if isinstance(path, str) and path else None
