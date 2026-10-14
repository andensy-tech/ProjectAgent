"""
API integration tests for Authentication endpoints.

Test Cases:
- API-A-01: Login with valid credentials returns token
- API-A-02: Login with invalid password returns 401
- API-A-03: Login with non-existent user returns 401
- API-A-04: Get /me returns user info (no auth required)
"""

import pytest


class TestAuthLogin:
    """Test POST /api/auth/login (API-A-01~03)."""

    def test_login_valid_credentials(self, test_client):
        """API-A-01: Valid login returns access_token."""
        response = test_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == "admin"
        assert len(data["access_token"]) > 20  # JWT is reasonably long

    def test_login_invalid_password(self, test_client, monkeypatch):
        """API-A-02: Invalid password returns 401 (when DEBUG=False)."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "DEBUG", False)

        response = test_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )

        assert response.status_code == 401
        assert "detail" in response.json()

    def test_login_nonexistent_user(self, test_client):
        """API-A-03: Non-existent user returns 401."""
        response = test_client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "any"},
        )

        assert response.status_code == 401
        assert "detail" in response.json()


class TestAuthMe:
    """Test GET /api/auth/me (API-A-04)."""

    def test_get_me(self, test_client):
        """API-A-04: /me returns the logged-in admin user from DB."""
        response = test_client.get("/api/auth/me")

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "admin"
        assert data["is_superuser"] is True
        assert data["is_active"] is True
        assert data["id"] == 1


class TestAuthRegister:
    """Test POST /api/auth/register (admin creates users)."""

    @pytest.mark.asyncio
    async def test_register_user(self, async_client):
        """管理员创建新用户成功。"""
        resp = await async_client.post("/api/auth/register", json={
            "username": "newuser", "password": "password123", "email": "new@example.com",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["is_superuser"] is False

    @pytest.mark.asyncio
    async def test_register_duplicate(self, async_client):
        """重复用户名返回 409。"""
        await async_client.post("/api/auth/register", json={"username": "dup", "password": "password123"})
        resp = await async_client.post("/api/auth/register", json={"username": "dup", "password": "password123"})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_register_short_password(self, async_client):
        """密码过短返回 400。"""
        resp = await async_client.post("/api/auth/register", json={"username": "shortpwd", "password": "123"})
        assert resp.status_code == 400

    def test_register_requires_admin(self, test_client_unauthenticated):
        """非管理员注册用户返回 403（真实登录 test_user 拿 token）。"""
        client = test_client_unauthenticated
        login = client.post("/api/auth/login", json={"username": "test_user", "password": "test123"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/api/auth/register",
            json={"username": "x", "password": "password123"},
            headers=headers,
        )
        assert resp.status_code == 403


class TestAuthChangePassword:
    """Test POST /api/auth/change-password."""

    @pytest.mark.asyncio
    async def test_change_password_success(self, async_client):
        """正确旧密码可修改密码。"""
        resp = await async_client.post("/api/auth/change-password", json={
            "old_password": "admin123", "new_password": "newpass456",
        })
        assert resp.status_code == 200
        assert resp.json()["message"] == "密码已修改"

    @pytest.mark.asyncio
    async def test_change_password_wrong_old(self, async_client):
        """错误旧密码返回 400。"""
        resp = await async_client.post("/api/auth/change-password", json={
            "old_password": "wrong", "new_password": "newpass456",
        })
        assert resp.status_code == 400
        assert "原密码不正确" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_change_password_short_new(self, async_client):
        """新密码过短返回 400。"""
        resp = await async_client.post("/api/auth/change-password", json={
            "old_password": "admin123", "new_password": "123",
        })
        assert resp.status_code == 400


class TestUserManagement:
    """Test admin user-management endpoints."""

    @pytest.mark.asyncio
    async def test_list_users(self, async_client):
        """管理员列出所有用户（含 seed admin + test_user）。"""
        resp = await async_client.get("/api/auth/users")
        assert resp.status_code == 200
        users = resp.json()
        names = [u["username"] for u in users]
        assert "admin" in names
        assert "test_user" in names

    @pytest.mark.asyncio
    async def test_delete_user(self, async_client):
        """管理员删除其他用户。"""
        resp = await async_client.delete("/api/auth/users/2")  # seeded test_user
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_self_forbidden(self, async_client):
        """不能删除自己（admin id=1）。"""
        resp = await async_client.delete("/api/auth/users/1")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_toggle_user_active(self, async_client):
        """管理员启用/禁用用户（test_user 当前启用 → 禁用）。"""
        resp = await async_client.put("/api/auth/users/2/toggle-active")
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    @pytest.mark.asyncio
    async def test_admin_reset_password(self, async_client):
        """管理员重置密码并强制下次改密。"""
        resp = await async_client.post(
            "/api/auth/users/2/reset-password", json={"new_password": "reset12345"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "密码已重置"
