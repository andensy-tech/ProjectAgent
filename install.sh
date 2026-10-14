#!/bin/bash
#
# Project Agent - 一键安装部署脚本
# 支持: 本地开发模式 / 生产部署模式
#
# 用法:
#   ./install.sh              # 交互式安装
#   ./install.sh --dev        # 本地开发模式
#   ./install.sh --prod       # 生产部署模式
#   ./install.sh --update     # 更新现有部署
#

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 项目路径
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
ENV_FILE="$PROJECT_DIR/.env"

# 默认配置
MODE=""
BACKEND_PORT=8000
FRONTEND_PORT=3000
# 如果 .env 已存在，从中读取端口配置
if [ -f "$ENV_FILE" ]; then
    _bp=$(grep -E '^BACKEND_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2 | tr -d '[:space:]"')
    _fp=$(grep -E '^FRONTEND_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2 | tr -d '[:space:]"')
    [ -n "$_bp" ] && BACKEND_PORT="$_bp"
    [ -n "$_fp" ] && FRONTEND_PORT="$_fp"
fi
SKIP_DEPS=false

# 打印函数
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查命令是否存在
check_command() {
    if ! command -v "$1" &> /dev/null; then
        return 1
    fi
    return 0
}

# 检查系统环境
check_system() {
    log_info "检查系统环境..."
    
    # 检查Python
    if check_command python3; then
        PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        log_success "Python: $PYTHON_VERSION"
    else
        log_error "未找到 python3，请先安装 Python 3.10+"
        exit 1
    fi
    
    # 检查Node.js
    if check_command node; then
        NODE_VERSION=$(node --version 2>&1)
        log_success "Node.js: $NODE_VERSION"
    else
        log_error "未找到 node，请先安装 Node.js 18+"
        exit 1
    fi
    
    # 检查npm
    if check_command npm; then
        NPM_VERSION=$(npm --version 2>&1)
        log_success "npm: $NPM_VERSION"
    else
        log_error "未找到 npm"
        exit 1
    fi
    
    # 检查可选: PostgreSQL
    if check_command psql; then
        PG_VERSION=$(psql --version 2>&1 | head -1)
        log_success "PostgreSQL: $PG_VERSION"
    else
        log_warn "未找到 PostgreSQL（可选，生产模式需要）"
    fi
    
    # 检查可选: Redis
    if check_command redis-cli; then
        REDIS_VERSION=$(redis-cli --version 2>&1 | head -1)
        log_success "Redis: $REDIS_VERSION"
    else
        log_warn "未找到 Redis（可选，生产模式需要）"
    fi
    
    log_success "系统环境检查完成"
}

# 交互式选择模式
select_mode() {
    if [ -n "$MODE" ]; then
        return
    fi
    
    echo ""
    echo "========================================"
    echo "   Project Agent - 选择部署模式"
    echo "========================================"
    echo ""
    echo "1) 本地开发模式 (推荐日常开发)"
    echo "   - 前后端分离启动，支持热重载"
    echo "   - 后端: http://localhost:${BACKEND_PORT}"
    echo "   - 前端: http://localhost:${FRONTEND_PORT}"
    echo ""
    echo "2) 生产部署模式 (推荐正式环境)"
    echo "   - 前端构建后由后端统一服务"
    echo "   - 单端口访问: http://localhost:${BACKEND_PORT}"
    echo "   - 需要 PostgreSQL + Redis"
    echo ""
    echo "3) 仅安装依赖 (不启动服务)"
    echo ""
    
    read -p "请选择 [1/2/3]: " choice
    
    case $choice in
        1) MODE="dev" ;;
        2) MODE="prod" ;;
        3) MODE="deps" ;;
        *) 
            log_warn "无效选择，默认使用开发模式"
            MODE="dev"
            ;;
    esac
}

# 安装后端依赖
install_backend() {
    log_info "安装后端依赖..."
    
    cd "$BACKEND_DIR"
    
    # 创建虚拟环境
    if [ ! -d "venv" ]; then
        log_info "创建 Python 虚拟环境..."
        python3 -m venv venv
    fi
    
    # 激活虚拟环境
    source venv/bin/activate
    
    # 升级pip
    pip install --upgrade pip -q
    
    # 安装依赖
    log_info "安装 Python 依赖包..."
    pip install -r requirements.txt -q
    
    log_success "后端依赖安装完成"
}

# 安装前端依赖
install_frontend() {
    log_info "安装前端依赖..."
    
    cd "$FRONTEND_DIR"
    
    # 检查 node_modules
    if [ ! -d "node_modules" ] || [ "$MODE" = "update" ]; then
        log_info "安装 npm 依赖包..."
        npm install
    else
        log_info "node_modules 已存在，跳过安装"
    fi
    
    log_success "前端依赖安装完成"
}

# 创建环境配置文件
create_env_file() {
    if [ -f "$ENV_FILE" ] && [ "$MODE" != "update" ]; then
        log_warn ".env 文件已存在，跳过创建"
        log_info "如需重新配置，请删除 $ENV_FILE 后重新运行"
        return
    fi
    
    log_info "创建环境配置文件..."
    
    echo ""
    echo "========================================"
    echo "   配置 LLM 连接信息"
    echo "========================================"
    echo ""
    
    # Commercial LLM
    read -p "商用 LLM API Key [必填]: " commercial_key
    read -p "商用 LLM API Base [默认: https://api.deepseek.com/v1]: " commercial_base
    commercial_base=${commercial_base:-"https://api.deepseek.com/v1"}
    read -p "商用 LLM 模型 [默认: deepseek-chat]: " commercial_model
    commercial_model=${commercial_model:-"deepseek-chat"}
    
    echo ""
    read -p "本地 LLM API Base [可选，回车跳过]: " local_base
    read -p "本地 LLM API Key [可选，回车跳过]: " local_key
    read -p "本地 LLM 模型 [可选，回车跳过]: " local_model
    
    echo ""
    echo "========================================"
    echo "   配置 MCP 服务器 (可选)"
    echo "========================================"
    echo ""
    read -p "Jira MCP URL [可选]: " jira_url
    read -p "Jira MCP Auth Token [可选]: " jira_auth
    read -p "Confluence MCP URL [可选]: " conf_url
    read -p "Confluence MCP Auth Token [可选]: " conf_auth
    
    echo ""
    echo "========================================"
    echo "   配置数据库 (生产模式需要)"
    echo "========================================"
    echo ""
    read -p "数据库 URL [默认: sqlite+aiosqlite:///./projectagent.db]: " db_url
    db_url=${db_url:-"sqlite+aiosqlite:///./projectagent.db"}
    read -p "Redis URL [默认: redis://localhost:6379/0]: " redis_url
    redis_url=${redis_url:-"redis://localhost:6379/0"}
    
    # 写入 .env 文件
    cat > "$ENV_FILE" << EOF
# Project Agent 环境配置
# 生成时间: $(date)

# Application
APP_NAME=Project Agent
APP_VERSION=0.1.0
DEBUG=true
SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | xxd -p | head -c 64)

# Commercial LLM
COMMERCIAL_LLM_PROVIDER=deepseek
COMMERCIAL_LLM_API_BASE=$commercial_base
COMMERCIAL_LLM_API_KEY=$commercial_key
COMMERCIAL_LLM_MODEL=$commercial_model

# Local LLM (optional)
LOCAL_LLM_API_BASE=$local_base
LOCAL_LLM_API_KEY=$local_key
LOCAL_LLM_MODEL=$local_model

# Database
DATABASE_URL=$db_url

# Redis
REDIS_URL=$redis_url

# MCP Servers
JIRA_MCP_URL=$jira_url
JIRA_MCP_AUTH=$jira_auth
CONFLUENCE_MCP_URL=$conf_url
CONFLUENCE_MCP_AUTH=$conf_auth

# File Upload
UPLOAD_DIR=uploads
MAX_UPLOAD_SIZE=52428800
EOF
    
    log_success ".env 配置文件创建完成: $ENV_FILE"
}

# 构建前端（生产模式）
build_frontend() {
    log_info "构建前端生产包..."
    
    cd "$FRONTEND_DIR"
    npm run build
    
    # 复制到后端 static 目录
    mkdir -p "$BACKEND_DIR/app/static"
    cp -r dist/* "$BACKEND_DIR/app/static/"
    
    log_success "前端构建完成，已复制到 backend/app/static/"
}

# 初始化数据库
init_database() {
    log_info "初始化数据库..."
    
    cd "$BACKEND_DIR"
    source venv/bin/activate
    
    # 运行 Alembic 迁移
    if [ -d "alembic" ]; then
        alembic upgrade head || log_warn "数据库迁移失败，可能需要手动处理"
    fi
    
    log_success "数据库初始化完成"
}

# 确保脚本可执行
ensure_scripts_executable() {
    chmod +x "$PROJECT_DIR/restart.sh" 2>/dev/null || true
    log_success "脚本权限已设置"
}

# 显示使用说明
show_usage() {
    echo ""
    echo "========================================"
    echo "   Project Agent 安装完成"
    echo "========================================"
    echo ""
    
    case $MODE in
        dev)
            echo "部署模式: 本地开发"
            echo ""
            echo "启动命令:"
            echo "  ./restart.sh dev"
            echo ""
            echo "访问地址:"
            echo "  前端页面: http://localhost:${FRONTEND_PORT}"
            echo "  后端API:  http://localhost:${BACKEND_PORT}"
            echo "  API文档:  http://localhost:${BACKEND_PORT}/docs"
            ;;
        prod)
            echo "部署模式: 生产环境"
            echo ""
            echo "启动命令:"
            echo "  ./restart.sh start        # 启动"
            echo ""
            echo "访问地址:"
            echo "  统一入口: http://localhost:${BACKEND_PORT}"
            echo "  API文档:  http://localhost:${BACKEND_PORT}/docs"
            ;;
        deps)
            echo "仅安装依赖完成"
            echo ""
            echo "请手动配置 .env 文件后，使用对应模式启动"
            ;;
    esac
    
    echo ""
    echo "其他命令:"
    echo "  ./restart.sh stop         # 停止"
    echo "  ./install.sh    - 重新安装/配置"
    echo ""
    echo "配置文件: $ENV_FILE"
    echo ""
}

# 主流程
main() {
    echo ""
    echo "========================================"
    echo "   Project Agent - 一键安装部署"
    echo "========================================"
    echo ""
    
    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dev)
                MODE="dev"
                shift
                ;;
            --prod)
                MODE="prod"
                shift
                ;;
            --deps)
                MODE="deps"
                shift
                ;;
            --update)
                MODE="update"
                shift
                ;;
            --skip-deps)
                SKIP_DEPS=true
                shift
                ;;
            -h|--help)
                echo "用法: ./install.sh [选项]"
                echo ""
                echo "选项:"
                echo "  --dev         本地开发模式"
                echo "  --prod        生产部署模式"
                echo "  --deps        仅安装依赖"
                echo "  --update      更新现有部署"
                echo "  --skip-deps   跳过依赖安装"
                echo "  -h, --help    显示帮助"
                exit 0
                ;;
            *)
                log_error "未知选项: $1"
                echo "使用 -h 或 --help 查看帮助"
                exit 1
                ;;
        esac
    done
    
    # 检查系统
    check_system
    
    # 选择模式
    select_mode
    
    # 安装依赖
    if [ "$SKIP_DEPS" = false ]; then
        install_backend
        install_frontend
    fi
    
    # 创建环境配置
    if [ "$MODE" != "update" ]; then
        create_env_file
    fi
    
    # 生产模式构建前端
    if [ "$MODE" = "prod" ]; then
        build_frontend
        init_database
    fi
    
    # 确保启动脚本可执行
    ensure_scripts_executable
    
    # 显示使用说明
    show_usage
}

# 运行
main "$@"
