"""
Project Agent - Pytest Configuration and Shared Fixtures

This module provides:
- asyncio mode configuration
- Mock fixtures for LLM, MCP, and Database
- FastAPI TestClient fixture
"""

import asyncio
import json
import os
import sys
from typing import AsyncGenerator, Dict, Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Ensure backend is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import Base  # 触发全部模型注册，确保 create_all 建所有表（含 focus_sets 等）


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "slow: mark test as slow running")


@pytest.fixture(autouse=True)
def _isolate_cl_dryrun_log(tmp_path, monkeypatch):
    """把 CL 扫描 dry-run 预览文件重定向到临时目录。

    `cl_scan_service._DRY_RUN_LOG_DIR` 是相对路径（`data/logs`），按 CWD 解析；
    测试跑 dry-run 扫描时会把假数据（TV-1 之类）写进仓库目录，污染真实预览名单。
    """
    try:
        from app.services import cl_scan_service
    except ImportError:
        return
    monkeypatch.setattr(cl_scan_service, "_DRY_RUN_LOG_DIR", tmp_path / "cl_dryrun")


# =============================================================================
# Database Fixtures
# =============================================================================

@pytest_asyncio.fixture(scope="function")
async def db_engine(tmp_path, monkeypatch):
    """Create an isolated SQLite engine for testing.

    Uses a temp-file DB (not ``:memory:``) so every connection — including
    those opened in different event loops (async tests vs TestClient's
    portal loop) — reads the same data. ``:memory:`` would give each new
    connection its own empty database.

    Also monkeypatches the global ``app.db.session.AsyncSessionLocal`` so
    services that open their own sessions internally (e.g. settings_service)
    hit the test DB instead of the production database.
    """
    db_file = tmp_path / "test_project_agent.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.db import session as db_session_module
    test_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
    )
    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", test_factory)

    # 模块级 `from app.db.session import AsyncSessionLocal` 的服务在 import 时已
    # 静态绑定旧工厂，替换 db_session_module 不影响它们。逐个重新绑定到测试工厂，
    # 确保这些 service 内部直连的 session 也隔离到测试 DB（而非生产库）。
    import importlib
    for _mod_name in (
        "app.services.trend_service",
        "app.services.alert_service",
        "app.services.cl_snapshot_service",
        "app.services.cl_queue_service",
        "app.services.cl_scan_service",
        "app.services.cl_suppression_service",
        "app.services.cl_auto_notify_service",
        "app.services.task_queue",
        "app.services.agent.executor",
        "app.services.dashboard_snapshot_service",
        "app.tasks.alert_scanner",
        "app.api.feishu_bind",
        "app.api.chat",
        "app.api.upload",
        "app.services.focus_set_service",
        "app.services.focus_set_refresh_service",
        "app.services.notification_service",
        "app.services.project_plan_service",
        "app.services.scheduled_task_service",
        "app.services.feishu.adapter",
    ):
        try:
            _mod = importlib.import_module(_mod_name)
            if hasattr(_mod, "AsyncSessionLocal"):
                monkeypatch.setattr(_mod, "AsyncSessionLocal", test_factory)
        except Exception:
            pass  # module not importable in test env — leave as-is

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session with automatic rollback."""
    AsyncSessionLocal = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


# =============================================================================
# LLM Mock Fixtures
# =============================================================================

class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        self.responses = responses or {}
        self.call_history: List[Dict] = []

    async def complete(
        self,
        messages,
        model_type="commercial",
        model=None,
        temperature=0.7,
        max_tokens=None,
        stream=False,
        json_mode=False,
    ) -> str:
        """Mock completion."""
        self.call_history.append({
            "method": "complete",
            "messages": messages,
            "model_type": model_type,
            "json_mode": json_mode,
        })
        key = "json" if json_mode else "text"
        return self.responses.get(key, self.responses.get("default", "mock response"))

    async def complete_structured(
        self,
        messages,
        model_type="commercial",
        schema=None,
    ) -> Dict[str, Any]:
        """Mock structured completion."""
        self.call_history.append({
            "method": "complete_structured",
            "messages": messages,
            "model_type": model_type,
        })
        return self.responses.get("structured", {
            "intent_category": "execute",
            "action": "summarize",
            "target_skill": "jira-confluence-report",
            "confidence": 0.95,
            "entities": {},
            "clarification_needed": False,
            "clarification_question": "",
        })

    async def complete_with_tools(
        self,
        messages,
        tools,
        model_type="commercial",
        model=None,
        tool_choice=None,
        temperature=0.7,
        max_tokens=None,
    ):
        """Mock completion with tool calling."""
        self.call_history.append({
            "method": "complete_with_tools",
            "messages": messages,
            "tools": tools,
            "model_type": model_type,
        })
        # Return a response: either text OR tool_calls based on test config
        response = self.responses.get("tools", None)
        if response and isinstance(response, list):
            return None, response  # No text content, has tool calls
        text = self.responses.get("default", self.responses.get("text", "mock response"))
        return text, None  # Has text content, no tool calls

    async def embed(self, texts, model_type="local") -> List[List[float]]:
        """Mock embedding."""
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture
def mock_llm_client(monkeypatch):
    """Replace the global LLM client with a mock."""
    from app.services.llm import client as llm_client_module
    mock = MockLLMClient()
    monkeypatch.setattr(llm_client_module, "llm_client", mock)
    return mock


@pytest.fixture
def mock_llm_router(monkeypatch):
    """Replace the global LLM router with a mock."""
    from app.services.llm import router as llm_router_module
    from app.services.llm import client as llm_client_module

    mock_client = MockLLMClient()
    monkeypatch.setattr(llm_client_module, "llm_client", mock_client)

    # Also patch router methods directly for convenience
    async def mock_route_and_complete(task_type, messages, **kwargs):
        return "mock llm response"

    async def mock_route_and_complete_structured(task_type, messages, **kwargs):
        return {
            "intent_category": "execute",
            "action": "summarize",
            "target_skill": "jira-confluence-report",
            "confidence": 0.95,
            "entities": {},
            "clarification_needed": False,
            "clarification_question": "",
        }

    async def mock_route_and_complete_with_tools(task_type, messages, tools, **kwargs):
        """Mock: return text response (no tool calls) by default."""
        return "mock llm response", None

    monkeypatch.setattr(
        llm_router_module.llm_router, "route_and_complete", mock_route_and_complete
    )
    monkeypatch.setattr(
        llm_router_module.llm_router,
        "route_and_complete_structured",
        mock_route_and_complete_structured,
    )
    monkeypatch.setattr(
        llm_router_module.llm_router,
        "route_and_complete_with_tools",
        mock_route_and_complete_with_tools,
    )
    return mock_client


# =============================================================================
# MCP Mock Fixtures
# =============================================================================

class MockMCPClient:
    """Mock MCP client for testing."""

    def __init__(self, server_name: str = "mock", responses: Optional[Dict] = None):
        self.server_name = server_name
        self._initialized = False
        self.responses = responses or {}
        self.call_history: List[Dict] = []

    async def initialize(self) -> Dict:
        self._initialized = True
        return {"status": "ok"}

    async def list_tools(self) -> List[Dict]:
        return [{"name": "mock_tool", "description": "A mock tool"}]

    async def call_tool(self, tool_name: str, arguments: Dict = None) -> Any:
        self.call_history.append({"tool": tool_name, "args": arguments})

        # Return predefined responses for known tools
        if tool_name in self.responses:
            return self.responses[tool_name]

        # Default responses
        defaults = {
            "search_issues": {"issues": []},
            "get_issue": {"key": "TV-12345", "fields": {
                "summary": "Mock Issue",
                "status": "In Progress",
                "priority": "P2",
                "assignee": "test_user",
                "duedate": "2026-06-01",
                "updated": "2026-05-20T10:00:00Z",
                "issuetype": "Bug",
                "subtasks": [],
            }},
            "get_issue_comments": {"comments": []},
            "create_page": {"id": "12345", "title": "Mock Page"},
            "get_page": {"id": "12345", "title": "Mock Page"},
            "update_page": {"id": "12345", "title": "Mock Page"},
            "get_change": "**状态**: MERGED\n**项目**: test-project",
        }
        return defaults.get(tool_name, {})

    async def close(self):
        self._initialized = False


class MockMCPManager:
    """Mock MCP manager for testing."""

    def __init__(self, responses: Optional[Dict[str, Dict]] = None):
        self.clients: Dict[str, MockMCPClient] = {}
        self._responses = responses or {}
        self._init_default_clients()

    def _init_default_clients(self):
        for name in ["jira", "confluence", "gerrit_gerrit1", "gerrit_gerrit2"]:
            self.clients[name] = MockMCPClient(
                server_name=name,
                responses=self._responses.get(name, {}),
            )

    def get_client(self, server_name: str) -> MockMCPClient:
        if server_name not in self.clients:
            raise Exception(f"MCP client not found: {server_name}")
        return self.clients[server_name]

    def get_available_servers(self) -> List[str]:
        return list(self.clients.keys())

    async def initialize_all(self):
        for client in self.clients.values():
            await client.initialize()

    async def close_all(self):
        for client in self.clients.values():
            await client.close()


@pytest.fixture
def mock_mcp_manager(monkeypatch):
    """Replace the global MCP manager with a mock."""
    from app.mcp import client as mcp_client_module
    mock = MockMCPManager()
    monkeypatch.setattr(mcp_client_module, "mcp_manager", mock)
    return mock


@pytest.fixture
def mock_mcp_with_issues(mock_mcp_manager):
    """MCP manager pre-configured with sample Jira issues."""
    sample_issues = {
        "TV-11111": {
            "key": "TV-11111",
            "fields": {
                "summary": "P1 Critical Bug",
                "status": "In Progress",
                "priority": "P1",
                "assignee": "user1",
                "duedate": "2026-05-25",
                "updated": "2026-05-10T10:00:00Z",
                "issuetype": "Bug",
                "subtasks": [],
            },
        },
        "TV-22222": {
            "key": "TV-22222",
            "fields": {
                "summary": "ETA Expiring Issue",
                "status": "Open",
                "priority": "P2",
                "assignee": "user2",
                "duedate": "2026-05-23",
                "updated": "2026-05-20T10:00:00Z",
                "issuetype": "Task",
                "subtasks": [],
            },
        },
        "TV-33333": {
            "key": "TV-33333",
            "fields": {
                "summary": "Stale Issue",
                "status": "Open",
                "priority": "P3",
                "assignee": "user3",
                "duedate": "2026-06-30",
                "updated": "2026-05-01T10:00:00Z",
                "issuetype": "Task",
                "subtasks": [],
            },
        },
        "TV-44444": {
            "key": "TV-44444",
            "fields": {
                "summary": "Normal Issue",
                "status": "Open",
                "priority": "P3",
                "assignee": "user4",
                "duedate": "2026-07-01",
                "updated": "2026-05-21T10:00:00Z",
                "issuetype": "Task",
                "subtasks": [],
            },
        },
    }

    # Configure jira mock responses
    jira_responses = {
        "search_issues": {
            "issues": [{"key": k} for k in sample_issues.keys()],
        },
    }
    # Add get_issue responses for each key
    for key, issue in sample_issues.items():
        jira_responses[f"get_issue:{key}"] = issue

    # Use a custom call_tool that checks issue_key
    original_call = mock_mcp_manager.clients["jira"].call_tool

    async def custom_call_tool(tool_name, arguments=None):
        arguments = arguments or {}
        if tool_name == "get_issue" and "issue_key" in arguments:
            key = arguments["issue_key"]
            if key in sample_issues:
                return sample_issues[key]
        if tool_name in jira_responses:
            return jira_responses[tool_name]
        return await original_call(tool_name, arguments)

    mock_mcp_manager.clients["jira"].call_tool = custom_call_tool
    mock_mcp_manager.sample_issues = sample_issues
    return mock_mcp_manager


# =============================================================================
# FastAPI TestClient Fixtures
# =============================================================================

TEST_USERNAME = "admin"
TEST_PASSWORD = "admin123"


async def _seed_test_users(db_engine) -> None:
    """Seed the built-in test users into the test DB.

    Passwords are hashed with the real ``get_password_hash()`` so the actual
    ``POST /api/auth/login`` flow can authenticate against them — tests then
    exercise the production auth path rather than a fabricated user.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.core.security import get_password_hash
    from app.models import User

    AsyncSessionLocal = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
    )
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(User).where(User.username == TEST_USERNAME))
        if existing.scalar_one_or_none() is not None:
            return
        admin = User(
            username=TEST_USERNAME,
            email="admin@test.local",
            hashed_password=get_password_hash(TEST_PASSWORD),
            is_superuser=True,
            is_active=True,
            force_password_change=False,
        )
        normal = User(
            username="test_user",
            email="user@test.local",
            hashed_password=get_password_hash("test123"),
            is_superuser=False,
            is_active=True,
            force_password_change=False,
        )
        db.add_all([admin, normal])
        await db.commit()


@pytest.fixture
def test_app(db_engine):
    """FastAPI app backed by an isolated temp-file SQLite DB.

    Auth dependencies are overridden so every request acts as the seeded
    ``admin`` user. Requests still go through the real production HTTP router
    (``/api/...`` endpoints); only the DB session and current-user identity
    are swapped so tests exercise the true request path in isolation.
    """
    import asyncio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.main import app
    from app.api import deps
    from app.db import get_db
    from app.models import User

    # Seed users via a fresh loop — file-backed DB is safe across loops.
    asyncio.run(_seed_test_users(db_engine))

    AsyncSessionLocal = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
    )

    async def override_get_db():
        async with AsyncSessionLocal() as session:
            yield session

    async def override_get_current_active_user():
        return {"user_id": 1, "username": TEST_USERNAME, "sub": TEST_USERNAME, "is_superuser": True}

    async def override_get_current_db_user():
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.username == TEST_USERNAME))
            user = result.scalar_one_or_none()
            assert user is not None, "test admin user missing"
            return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[deps.get_current_active_user] = override_get_current_active_user
    app.dependency_overrides[deps.get_current_db_user] = override_get_current_db_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def test_client(test_app):
    """Create a FastAPI TestClient acting as the seeded admin user."""
    from fastapi.testclient import TestClient
    return TestClient(test_app)


@pytest.fixture
def test_client_unauthenticated(test_app):
    """Create a TestClient WITHOUT auth overrides (real JWT auth flow).

    For login/register and any endpoint that must validate real tokens.
    The DB override is kept so requests hit the isolated temp-file DB.
    """
    from fastapi.testclient import TestClient
    from app.api import deps
    from app.db import get_db

    test_app.dependency_overrides.pop(deps.get_current_active_user, None)
    test_app.dependency_overrides.pop(deps.get_current_db_user, None)
    assert get_db in test_app.dependency_overrides  # DB override must remain
    client = TestClient(test_app)
    yield client


@pytest_asyncio.fixture
async def async_client(db_engine):
    """Async HTTP client over the real FastAPI ASGI app.

    Built entirely inside the async test loop (seed + auth overrides applied
    here, not via the sync ``test_app`` fixture) so async tests can seed data
    through ``db_session`` and then hit the real production router without
    pytest-asyncio sync/async fixture ordering issues.
    """
    from sqlalchemy import select
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.api import deps
    from app.db import get_db
    from app.models import User

    await _seed_test_users(db_engine)

    async def override_get_db():
        # 走全局 AsyncSessionLocal（已被 db_engine monkeypatch 指向测试 DB），
        # 保证 async_client 与 db_session/服务层使用同一 engine，避免表不一致。
        from app.db.session import AsyncSessionLocal as _SessionLocal
        async with _SessionLocal() as session:
            yield session

    async def override_get_current_active_user():
        return {"user_id": 1, "username": TEST_USERNAME, "sub": TEST_USERNAME, "is_superuser": True}

    async def override_get_current_db_user():
        from app.db.session import AsyncSessionLocal as _SessionLocal
        async with _SessionLocal() as db:
            result = await db.execute(select(User).where(User.username == TEST_USERNAME))
            user = result.scalar_one_or_none()
            assert user is not None, "test admin user missing"
            return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[deps.get_current_active_user] = override_get_current_active_user
    app.dependency_overrides[deps.get_current_db_user] = override_get_current_db_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
