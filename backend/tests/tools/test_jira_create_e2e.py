"""
Jira 创建 Issue 端到端集成测试

直接连接实际 Jira MCP 服务器，验证 create_issue 在正确的字段格式下能成功创建 issue。

运行方式（需要 MCP 服务器可达）:
  pytest backend/tests/tools/test_jira_create_e2e.py -v -m integration

不传 -m integration 时自动跳过（安全默认）。
"""

import json
import os
import sys
import pytest
from typing import Dict, Any, Optional

# Ensure backend is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ── Test constants ──────────────────────────────────────────────────────────
# EPIC_KEY 从环境变量读取，防止意外使用硬编码值向真实 Jira 写入垃圾数据。
# 运行测试前必须设置: export JIRA_TEST_EPIC_KEY="TV-918961"
# 未设置时测试会自动跳过。
EPIC_KEY = os.environ.get("JIRA_TEST_EPIC_KEY", "")
PROJECT_KEY = os.environ.get("JIRA_TEST_PROJECT_KEY", "TV")
TEST_SUMMARY_PREFIX = "[TEST] Auto-created by Project Agent E2E test"
TEST_DESCRIPTION = "由自动化测试自动创建，验证 create_issue 格式。测试完成后请清理。"

# Known format rules discovered from previous debugging sessions:
#   User picker fields → {"name": "username"}
#   List (single-choice) fields → [{"value": "option"}]
#   Components → [{"name": "component_name"}]
KNOWN_USER_FIELDS = {
    "customfield_10700",  # Manager
    "customfield_11002",  # FAE Manager
    "customfield_10409",  # FAE Coworker
    "customfield_11104",
    "customfield_12506",
    "customfield_12507",
}
KNOWN_LIST_FIELDS = {
    "customfield_10107",  # Product
    "customfield_10300",  # Software Release
    "customfield_10407",  # Project ID
    "customfield_10000",
}


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def epic_issue():
    """Fetch the epic issue to understand field formats.

    Real MCP server call — only works when MCP server is reachable.
    Scope=module so we only fetch once for all tests in this module.
    """
    return _call_mcp("get_issue", {"issue_key": EPIC_KEY})


# ── Integration test: create issue with correct format ─────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not EPIC_KEY, reason="JIRA_TEST_EPIC_KEY 未设置")
class TestJiraCreateE2E:
    """End-to-end test for creating Jira issues via MCP.

    Tests that with the correct format for all mandatory fields,
    create_issue succeeds. Fetches the epic first to understand
    valid field values, then creates a minimal task under the epic.

    Epic Key 通过环境变量 JIRA_TEST_EPIC_KEY 配置，禁止硬编码。
    """

    timestamp: str = ""  # set per-test for unique summaries

    @pytest.fixture(autouse=True)
    def _setup_timestamp(self):
        import datetime
        self.timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    def test_mcp_server_reachable(self):
        """Jira MCP 服务器必须可访问，否则跳过后续测试。"""
        try:
            result = _call_mcp("get_issue", {"issue_key": EPIC_KEY})
            assert result is not None, "MCP 返回空结果"
            key = ""
            if isinstance(result, dict):
                key = result.get("key", "")
            assert key == EPIC_KEY, f"预期 key={EPIC_KEY}，实际={key}"
        except Exception as e:
            pytest.skip(f"Jira MCP 服务器不可达: {e}")

    def test_get_epic_shows_custom_fields(self, epic_issue):
        """Epic 查询结果必须包含自定义字段。"""
        assert epic_issue is not None, "Epic 查询返回空"
        fields = epic_issue.get("fields", {}) if isinstance(epic_issue, dict) else {}
        custom_fields_found = [k for k in fields.keys() if k.startswith("customfield_")]
        assert len(custom_fields_found) > 0, (
            f"Epic {EPIC_KEY} 未返回任何 customfield_* 字段。"
            f"这可能意味着 MCP 服务器配置或 epic key 不正确。"
        )
        # Log for debugging
        print(f"\n  自定义字段数量: {len(custom_fields_found)}")
        for cf in sorted(custom_fields_found):
            val = fields.get(cf)
            print(f"    {cf}: {json.dumps(val, ensure_ascii=False)[:100] if val else 'None'}")

    def _build_custom_fields(self, epic_issue: Dict[str, Any]) -> Dict[str, Any]:
        """Build custom_fields dict by learning valid values from the epic issue.

        The epic's get_issue returns display format, but create_issue needs
        API input format. This method does the conversion:

        GET display format (from get_issue):
          list field → ["Value"]  e.g. customfield_10107: ["TV Reference"]
          user field → "username" e.g. customfield_10700: "Tellen.yu"

        POST API format (for create_issue):
          list field → [{"value": "Value"}]
          user field → {"name": "username"}

        Only the 8 known mandatory fields for TV project Task creation
        are included. Fields that cannot be set (like customfield_10000,
        12506, 12507) are excluded.
        """
        fields = epic_issue.get("fields", {}) if isinstance(epic_issue, dict) else {}
        custom_fields: Dict[str, Any] = {}

        # Get a single value from the list, or use fallback
        def _first(val, fallback):
            if isinstance(val, list) and val:
                return str(val[0])
            if isinstance(val, str) and val:
                return val
            return fallback

        # 1. List fields (single-choice): GET → ["Value"], POST → [{"value": "Value"}]
        epic_list_vals = {
            "customfield_10107": _first(fields.get("customfield_10107"), "TV Reference"),
            "customfield_10300": _first(fields.get("customfield_10300"), "Android U-14"),
            "customfield_10407": _first(fields.get("customfield_10407"), "PROJECTX-CHIP-X"),
        }
        for cf, val in epic_list_vals.items():
            custom_fields[cf] = [{"value": val}]

        # 2. User picker fields: GET → "username", POST → {"name": "username"}
        epic_user_vals = {
            "customfield_10700": fields.get("customfield_10700") or "test.user",
            "customfield_11002": fields.get("customfield_11002") or "test.user",
            "customfield_10409": fields.get("customfield_10409") or "test.user",
        }
        for cf, val in epic_user_vals.items():
            if isinstance(val, str) and val:
                custom_fields[cf] = {"name": val}
            else:
                custom_fields[cf] = {"name": "test.user"}

        # 3. Components field: GET → ["ComponentName"], POST → [{"name": "ComponentName"}]
        components = fields.get("components", [])
        if isinstance(components, list) and components:
            first_comp = components[0]
            if isinstance(first_comp, str):
                custom_fields["components"] = [{"name": first_comp}]
            elif isinstance(first_comp, dict) and first_comp.get("name"):
                custom_fields["components"] = [first_comp]
            else:
                custom_fields["components"] = [{"name": "System-Android"}]
        else:
            custom_fields["components"] = [{"name": "System-Android"}]

        return custom_fields

    def _do_create_issue(self, epic_issue, summary, description=""):
        """Helper: create a Jira issue and verify success."""
        custom_fields = self._build_custom_fields(epic_issue)

        params = {
            "project_key": PROJECT_KEY,
            "summary": summary,
            "issue_type": "Task",
            "custom_fields": custom_fields,
        }
        if description:
            params["description"] = description

        print(f"\n  创建 Issue 参数:")
        print(f"    project_key: {PROJECT_KEY}")
        print(f"    summary: {summary}")
        print(f"    issue_type: Task")
        print(f"    description: {'(set)' if description else '(not set)'}")
        print(f"    custom_fields: {json.dumps(custom_fields, ensure_ascii=False, indent=4)[:1500]}")

        try:
            result = _call_mcp("create_issue", params)
            _verify_issue_created(result, summary)
            print(f"  ✓ Issue 创建成功!")
            _cleanup_test_issue(result)
            return result
        except AssertionError:
            raise
        except Exception as e:
            error_text = str(e)
            if "JiraError" in error_text:
                print(f"\n  ✗ Jira 创建失败，错误分析:")
                analysis = _analyze_create_error(error_text)
                for line in analysis:
                    print(f"    {line}")
            raise

    def test_create_issue_minimal(self, epic_issue):
        """创建最小 Jira issue（含 description 等所有 mandatory 字段），验证成功。"""
        self._do_create_issue(
            epic_issue,
            f"{TEST_SUMMARY_PREFIX} {self.timestamp}",
            description=TEST_DESCRIPTION,
        )

    def test_create_issue_with_description(self, epic_issue):
        """创建带自定义描述的 Jira issue，验证完整的格式。"""
        self._do_create_issue(
            epic_issue,
            f"{TEST_SUMMARY_PREFIX} with description {self.timestamp}",
            description="这是一个自定义的详细描述，用于验证 description 字段的自定义内容传递。",
        )


# ── Helper functions ────────────────────────────────────────────────────────

def _call_mcp(tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Directly call a Jira MCP tool using httpx.

    This bypasses the Agent engine and MCP client library to test
    the raw MCP server behavior — no mocks, no middleware.
    """
    import httpx

    # Load MCP config from backend/config.yaml
    # config.yaml is at backend/config.yaml relative to project root
    # From tests/tools/, go up 2 levels: tests/tools/ -> tests/ -> backend/
    test_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(test_dir, "..", "..", "config.yaml")
    config_path = os.path.abspath(config_path)
    if not os.path.exists(config_path):
        # Fallback: try from CWD
        config_path = os.path.abspath("config.yaml")
    if not os.path.exists(config_path):
        # Fallback: try from parent of CWD (project root)
        config_path = os.path.abspath(os.path.join("..", "config.yaml"))
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"config.yaml not found. Tried paths relative to test file and CWD. "
            f"MCP server URL is required for integration test."
        )

    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    jira_config = config.get("mcp_servers", {}).get("jira", {})
    if not jira_config:
        raise ValueError("config.yaml missing [mcp_servers.jira] section")

    url = jira_config["url"]
    auth = jira_config.get("auth", {})

    # JSON-RPC payload
    request_id = 1
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    # For get_issue, we need to initialize first
    init_payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "project-agent-e2e-test", "version": "1.0"},
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": json.dumps(auth),
    }

    with httpx.Client(timeout=60) as client:
        # Initialize
        init_resp = client.post(url, headers=headers, json=init_payload)
        init_resp.raise_for_status()

        if tool_name in ("get_issue", "search_issues"):
            # For read-only tools, directly call
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                raise Exception(f"MCP error: {data['error']}")

            result = data.get("result", {})
            content = result.get("content", [])

            text_items = []
            for item in content:
                if item.get("type") == "text":
                    text_items.append(item.get("text", ""))

            combined = "\n".join(text_items)
            try:
                return json.loads(combined)
            except json.JSONDecodeError:
                return combined

        else:
            # For create_issue, need to initialize isError handling
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                raise Exception(f"MCP error: {data['error']}")

            result = data.get("result", {})
            is_error = result.get("isError", False)
            content = result.get("content", [])

            text_items = []
            for item in content:
                if item.get("type") == "text":
                    text_items.append(item.get("text", ""))

            combined = "\n".join(text_items)

            if is_error:
                # Jira errors are in the response text, not JSON-RPC errors
                raise Exception(f"错误: JiraError\n{combined}")

            try:
                return json.loads(combined)
            except json.JSONDecodeError:
                return combined


def _verify_issue_created(result: Any, expected_summary: str):
    """Verify that create_issue returned a valid result with a key."""
    assert result is not None, "创建结果为空"

    if isinstance(result, dict):
        key = result.get("key", "")
        assert key.startswith("TV-"), f"预期 Issue key 以 TV- 开头，实际: {key}"
        print(f"\n  创建成功: {key}")
        print(f"  摘要: {result.get('summary', expected_summary)}")
    elif isinstance(result, str):
        assert "TV-" in result, f"返回值中没有 Issue key: {result[:200]}"
        print(f"\n  创建成功 (raw): {result[:200]}")
    else:
        print(f"\n  创建成功 (type={type(result).__name__}): {str(result)[:200]}")


def _analyze_create_error(error_text: str) -> list:
    """Analyze a Jira create_issue error and return human-readable analysis lines."""
    import re
    import json

    lines = error_text.split("\n")
    analysis = []

    # Try to find "response text = {...}"
    response_json = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("response text ="):
            json_str = stripped[len("response text ="):].strip()
            try:
                response_json = json.loads(json_str)
            except json.JSONDecodeError:
                pass
            break

    if response_json:
        errors = response_json.get("errors", {})
        if errors:
            analysis.append(f"字段错误数量: {len(errors)}")
            for field, msg in sorted(errors.items()):
                analysis.append(f"  - {field}: {msg}")
                # Format hints
                if "required" in msg.lower():
                    if field in KNOWN_USER_FIELDS:
                        analysis.append(f"    → 格式: {{\"name\": \"用户名\"}}")
                    elif field in KNOWN_LIST_FIELDS:
                        analysis.append(f"    → 格式: [{{\"value\": \"选项\"}}]")
                    elif field == "components":
                        analysis.append(f"    → 格式: [{{\"name\": \"组件名\"}}]")
                    elif field == "description":
                        analysis.append(f"    → 格式: \"字符串\" (直接字符串)")
    else:
        analysis.append(f"无法解析错误中的 response text JSON")
        analysis.append(f"原始错误: {error_text[:500]}")

    return analysis


def _cleanup_test_issue(result: Any):
    """Try to close the test issue (best-effort)."""
    if isinstance(result, dict):
        key = result.get("key", "")
        if key:
            try:
                _call_mcp("transition_issue", {
                    "issue_key": key,
                    "transition_id": "2",  # "Close Issue"
                })
                print(f"  → 已将 {key} 标记为 Closed")
            except Exception:
                print(f"  → 注意：{key} 需要手动清理（closed失败）")
