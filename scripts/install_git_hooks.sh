#!/usr/bin/env bash
# ============================================================================
# 安装 git pre-commit hook（测试门禁）
# ----------------------------------------------------------------------------
# 将 scripts/pre_commit_test.sh 注册到 .git/hooks/pre-commit，
# 之后每次 git commit 前自动运行后端全量测试，失败则阻止提交。
#
# 用法：bash scripts/install_git_hooks.sh
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"

cat > "$HOOK" << 'EOF'
#!/usr/bin/env bash
exec bash "$(git rev-parse --show-toplevel)/scripts/pre_commit_test.sh"
EOF
chmod +x "$HOOK"

echo "✅ 已安装 pre-commit hook：$HOOK"
echo "   后续 git commit 前将自动运行后端测试（跳过用 --no-verify）"
