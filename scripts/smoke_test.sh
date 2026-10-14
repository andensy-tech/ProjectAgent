#!/usr/bin/env bash
# ============================================================================
# 部署冒烟测试（Phase 6 - E2E/部署验证）
# ----------------------------------------------------------------------------
# 对运行中的后端服务做冒烟：health → 登录 → 关键 API 可访问。
# 用于部署后快速确认服务基本功能正常。
#
# 用法：
#   bash scripts/smoke_test.sh                      # 默认 localhost:8000 admin/admin123
#   bash scripts/smoke_test.sh http://host:8000 user pass
#
# 注意：完整 E2E（真实 Jira/Gerrit/Confluence 数据交互）需要真实 MCP 环境，
#       不在本地 CI 自动执行；本脚本验证服务可用性与 API 契约。
# ============================================================================
set -uo pipefail

BASE_URL="${1:-http://localhost:8000}"
USER="${2:-admin}"
PASS="${3:-admin123}"

fail=0
ok()   { echo "✅ $1"; }
bad()  { echo "❌ $1"; fail=1; }

echo "=== 部署冒烟测试: $BASE_URL ==="

# 1. 健康检查
if curl -sf "$BASE_URL/health" 2>/dev/null | grep -q '"status":"healthy"'; then
    ok "health 检查"
else
    bad "health 检查（服务可能未启动）"
fi

# 2. 登录（真实认证流程）
LOGIN_JSON=$(curl -sf -X POST "$BASE_URL/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" 2>/dev/null) \
    || { bad "登录失败（$USER）"; echo "❌ 冒烟测试未通过：无法登录"; exit 1; }
TOKEN=$(echo "$LOGIN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
if [ -n "$TOKEN" ]; then
    ok "登录（$USER）获取 token"
else
    bad "登录响应无 token"
fi

# 3. 关键 API 可达性
check_api() {
    local name="$1" path="$2"
    if curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL$path" > /dev/null 2>&1; then
        ok "$name ($path)"
    else
        bad "$name ($path)"
    fi
}
check_api "当前用户"    "/api/auth/me"
check_api "LLM 配置"    "/api/config/llm"
check_api "仪表盘配置"  "/api/dashboard/config"
check_api "告警列表"    "/api/alerts"
check_api "技能列表"    "/api/skills"
check_api "趋势"        "/api/trends"

echo ""
if [ "$fail" -eq 0 ]; then
    echo "🎉 冒烟测试全部通过"
else
    echo "⚠️  存在失败项，请检查服务日志"
    exit 1
fi
