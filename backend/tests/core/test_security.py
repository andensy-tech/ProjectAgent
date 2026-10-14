"""
Tests for app.core.security module.

Test Cases:
- SEC-01: Password hashing and verification success
- SEC-02: Password verification failure
- SEC-03: JWT token creation and decoding
- SEC-04: Expired token decoding
- SEC-05: Tampered token decoding
"""

import base64
import time
from datetime import timedelta

import pytest
from jose import jwt

from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_access_token,
    create_file_token,
    verify_file_token,
)
from app.core.config import settings


class TestPasswordSecurity:
    """Test password hashing and verification."""

    def test_password_hash_and_verify_success(self):
        """SEC-01: verify_password returns True for correct password."""
        password = "test_password_123"
        hashed = get_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_password_verify_failure(self):
        """SEC-02: verify_password returns False for wrong password."""
        password = "test_password_123"
        wrong_password = "wrong_password"
        hashed = get_password_hash(password)

        assert verify_password(wrong_password, hashed) is False

    def test_password_hash_is_different(self):
        """SEC-01: Same password produces different hashes (salt)."""
        password = "test_password_123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)

        assert hash1 != hash2
        assert verify_password(password, hash1) is True
        assert verify_password(password, hash2) is True


class TestJWTToken:
    """Test JWT token creation and validation."""

    def test_create_and_decode_token(self):
        """SEC-03: decode_access_token returns original payload."""
        data = {"sub": "user123", "username": "testuser"}
        token = create_access_token(data)

        decoded = decode_access_token(token)

        assert decoded is not None
        assert decoded["sub"] == "user123"
        assert decoded["username"] == "testuser"

    def test_create_token_with_expiry(self):
        """SEC-03: Token includes expiration claim."""
        data = {"sub": "user123"}
        expires = timedelta(minutes=30)
        token = create_access_token(data, expires_delta=expires)

        decoded = decode_access_token(token)

        assert decoded is not None
        assert "exp" in decoded
        assert decoded["sub"] == "user123"

    def test_decode_expired_token(self):
        """SEC-04: decode_access_token returns None for expired token."""
        data = {"sub": "user123"}
        # Create a token that expired 1 second ago
        expires = timedelta(seconds=-1)
        token = create_access_token(data, expires_delta=expires)

        # Small delay to ensure expiration
        time.sleep(0.1)
        decoded = decode_access_token(token)

        assert decoded is None

    def test_decode_tampered_token(self):
        """SEC-05: decode_access_token returns None for tampered token."""
        data = {"sub": "user123"}
        token = create_access_token(data)

        # Tamper with the token
        tampered = token[:-5] + "XXXXX"

        decoded = decode_access_token(tampered)

        assert decoded is None

    def test_decode_invalid_token(self):
        """SEC-05: decode_access_token returns None for invalid string."""
        decoded = decode_access_token("not.a.valid.token")

        assert decoded is None

    def test_decode_empty_token(self):
        """SEC-05: decode_access_token returns None for empty string."""
        decoded = decode_access_token("")

        assert decoded is None

    def test_token_uses_correct_algorithm(self):
        """SEC-03: Token uses HS256 algorithm."""
        data = {"sub": "user123"}
        token = create_access_token(data)

        # Decode without verification to check header
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "HS256"

    def test_token_uses_config_secret(self):
        """SEC-03: Token uses SECRET_KEY from settings."""
        data = {"sub": "user123"}
        token = create_access_token(data)

        # Should decode successfully with correct secret
        decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        assert decoded["sub"] == "user123"


class TestFileDownloadToken:
    """Agent 产出文件的下载令牌（SEC-06）。"""

    def test_sign_and_verify_roundtrip(self, tmp_path):
        """SEC-06: 签发后可用同一用户校验回文件路径。"""
        f = tmp_path / "report.html"
        f.write_text("<html>ok</html>", encoding="utf-8")

        token = create_file_token(str(f), user_id=1)

        assert verify_file_token(token, user_id=1) == str(f)

    def test_reject_other_user(self, tmp_path):
        """SEC-06: 令牌与用户绑定，他人拿到链接也无效。"""
        f = tmp_path / "report.html"
        f.write_text("x", encoding="utf-8")

        token = create_file_token(str(f), user_id=1)

        assert verify_file_token(token, user_id=2) is None

    def test_reject_tampered_token(self, tmp_path):
        """SEC-06: 篡改载荷 → 签名不符 → 拒绝。"""
        f = tmp_path / "report.html"
        f.write_text("x", encoding="utf-8")
        token = create_file_token(str(f), user_id=1)

        payload, _, sig = token.rpartition(".")
        forged = create_file_token("/etc/passwd", user_id=1).rpartition(".")[0]
        tampered = f"{forged}.{sig}"

        assert verify_file_token(tampered, user_id=1) is None
        assert verify_file_token(f"{payload}.{sig[:-1]}0", user_id=1) is None

    def test_reject_expired_token(self, tmp_path):
        """SEC-06: 过期令牌拒绝。"""
        f = tmp_path / "report.html"
        f.write_text("x", encoding="utf-8")

        token = create_file_token(str(f), user_id=1, expires_in=-1)

        assert verify_file_token(token, user_id=1) is None

    def test_reject_garbage(self):
        """SEC-06: 非法输入不抛异常，直接返回 None。"""
        assert verify_file_token("", user_id=1) is None
        assert verify_file_token("no-dot", user_id=1) is None
        assert verify_file_token("!!!.###", user_id=1) is None
        assert verify_file_token(".", user_id=1) is None
        assert verify_file_token("payload.", user_id=1) is None

    def test_reject_non_ascii_token_without_crashing(self):
        """SEC-06: 含非 ASCII 的令牌不能抛 TypeError（compare_digest 会炸）→ 拒绝而非 500。"""
        assert verify_file_token("abc.中文签名", user_id=1) is None
        assert verify_file_token("中文载荷.abcdef", user_id=1) is None
        # 长度恰好等于签名长度时绕不过长度检查，靠异常兜底
        assert verify_file_token("abc." + "中" * 32, user_id=1) is None
        assert verify_file_token("😀" * 32 + ".abc", user_id=1) is None

    def test_reject_oversized_token(self):
        """SEC-06: 超长令牌直接拒绝，不做无谓的 HMAC 计算。"""
        assert verify_file_token("x" * 5000 + "." + "y" * 32, user_id=1) is None

    def test_reject_wrong_signature_length(self):
        """SEC-06: 签名长度不符（截断/加长）→ 拒绝。"""
        assert verify_file_token("payload." + "a" * 31, user_id=1) is None
        assert verify_file_token("payload." + "a" * 33, user_id=1) is None

    def test_reject_valid_signature_broken_payload(self):
        """SEC-06: 签名正确但载荷不是合法 JSON → 拒绝（不抛异常）。"""
        from app.core.security import _file_token_signature
        payload_b64 = base64.urlsafe_b64encode(b"not-json").decode().rstrip("=")
        token = f"{payload_b64}.{_file_token_signature(payload_b64)}"

        assert verify_file_token(token, user_id=1) is None

    def test_reject_none_user_id(self, tmp_path):
        """SEC-06: user_id 为空一律拒绝（无用户上下文的令牌不可被任何请求消费）。"""
        f = tmp_path / "report.html"
        f.write_text("x", encoding="utf-8")
        token = create_file_token(str(f), user_id=None)

        assert verify_file_token(token, user_id=None) is None
        assert verify_file_token(token, user_id=1) is None

    def test_token_carries_realpath(self, tmp_path):
        """SEC-06: 令牌里存的是归一化后的真实路径（软链接/相对路径都指向同一文件）。"""
        real = tmp_path / "real.html"
        real.write_text("x", encoding="utf-8")
        link = tmp_path / "link.html"
        link.symlink_to(real)

        assert verify_file_token(create_file_token(str(link), 1), 1) == str(real)
