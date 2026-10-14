#!/usr/bin/env bash
# ============================================================================
# 提交前测试门禁（pre-commit gate）
# ----------------------------------------------------------------------------
# 每次 git commit 前自动运行后端全量测试。任何失败都会阻止提交，
# 防止"新开发破坏既有功能"无声无息地进入仓库。
#
# 用法：
#   bash scripts/pre_commit_test.sh          # 手动运行一次
#   bash scripts/install_git_hooks.sh        # 注册到 git commit 前自动执行
#
# 跳过本次检查：git commit --no-verify
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -d backend/venv ]; then
    echo "⚠️  backend/venv 不存在，跳过测试门禁（请先安装依赖）"
    exit 0
fi

echo "🔍 [pre-commit] 运行后端测试门禁..."
# shellcheck disable=SC1091
( cd backend && source venv/bin/activate && python -m pytest -q )

echo "✅ [pre-commit] 后端测试全部通过"
