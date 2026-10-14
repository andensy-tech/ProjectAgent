#!/bin/bash
# Project Agent - 统一管理脚本
# 集 start/stop/restart/dev/status 于一身
# 用法:
#   ./restart.sh              # 重启（stop + start）
#   ./restart.sh start        # 启动生产模式（gunicorn 多 worker）
#   ./restart.sh stop         # 停止所有服务
#   ./restart.sh restart      # 重启（同上）
#   ./restart.sh dev          # 开发模式（uvicorn --reload + vite）
#   ./restart.sh status       # 查看服务状态
#
# 选项（适用于 start/restart/dev）:
#   --build                   # 强制重新构建前端
#   --port PORT               # 指定端口（默认: .env 中 BACKEND_PORT=8000）

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
PID_DIR="$HOME/.project-agent"
LOG_FILE="$PID_DIR/prod.log"
FEISHU_LOG_FILE="$PID_DIR/feishu.log"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================================
# 配置读取
# ============================================================

# 从 .env 读取端口配置（默认 8000）
BACKEND_PORT=""
[ -f "$PROJECT_DIR/.env" ] && BACKEND_PORT=$(grep -E '^BACKEND_PORT=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]"' | head -1 || true)
BACKEND_PORT="${BACKEND_PORT:-8000}"

# 从 .env 读取 gunicorn worker 数（默认 4）
GUNICORN_WORKERS=""
[ -f "$PROJECT_DIR/.env" ] && GUNICORN_WORKERS=$(grep -E '^GUNICORN_WORKERS=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]"' | head -1 || true)
GUNICORN_WORKERS="${GUNICORN_WORKERS:-4}"

# 前端端口（仅 dev 模式使用）
FRONTEND_PORT=""
[ -f "$PROJECT_DIR/.env" ] && FRONTEND_PORT=$(grep -E '^FRONTEND_PORT=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]"' | head -1 || true)
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# ============================================================
# 辅助函数
# ============================================================

# 检查 .env 是否存在
check_env() {
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        echo -e "${RED}错误: 未找到 .env 配置文件${NC}"
        echo "请先运行 ./install.sh 进行配置"
        exit 1
    fi
}

# 清理端口占用
clean_port() {
    local port=$1
    local CONFLICT_PID=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$CONFLICT_PID" ]; then
        echo -e "${YELLOW}  端口 $port 已被占用 (PID: $CONFLICT_PID)，正在清理...${NC}"
        kill $CONFLICT_PID 2>/dev/null
        sleep 1
        local REMAINING=$(lsof -ti :"$port" 2>/dev/null || true)
        [ -n "$REMAINING" ] && kill -9 $REMAINING 2>/dev/null
        echo -e "${GREEN}  端口 $port 已释放${NC}"
    fi
}

# 清理残留 --reload 孤儿进程（dev 模式）
clean_reload_orphans() {
    local STALE=$(ps aux | grep 'spawn_main' | grep -v grep | awk '{print $2}')
    if [ -n "$STALE" ]; then
        echo -e "${YELLOW}  发现残留 --reload 孤儿进程，正在清理...${NC}"
        kill $STALE 2>/dev/null
        sleep 0.5
        local REMAINING=$(ps aux | grep 'spawn_main' | grep -v grep | awk '{print $2}')
        [ -n "$REMAINING" ] && kill -9 $REMAINING 2>/dev/null
    fi
}

# 清理残留飞书进程
clean_feishu_processes() {
    local PIDS=$(ps aux | grep 'run_feishu' | grep -v grep | awk '{print $2}')
    if [ -n "$PIDS" ]; then
        echo -e "${YELLOW}  发现残留飞书进程，正在清理...${NC}"
        kill $PIDS 2>/dev/null
        sleep 0.5
        local REMAINING=$(ps aux | grep 'run_feishu' | grep -v grep | awk '{print $2}')
        [ -n "$REMAINING" ] && kill -9 $REMAINING 2>/dev/null
    fi
}

# ============================================================
# stop — 停止所有服务
# ============================================================

stop_services() {
    local stopped=0

    echo "停止 Project Agent 服务..."

    # 1. 按 PID 文件停止（优先）
    mkdir -p "$PID_DIR"
    for pidfile in web.pid feishu.pid backend.pid frontend.pid; do
        local file="$PID_DIR/$pidfile"
        if [ -f "$file" ]; then
            local pid=$(cat "$file" 2>/dev/null)
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null && echo "  已停止 $pidfile (PID: $pid)" && stopped=1
                sleep 0.5
                kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null && echo "  强制停止 $pidfile (PID: $pid)"
            fi
            rm -f "$file"
        fi
    done

    # 2. 按进程名清理残留
    local CLEANED=""
    for pattern in "gunicorn" "uvicorn" "python3.*project-agent" "run_feishu" "node.*vite" "spawn_main"; do
        local PIDS=$(ps aux | grep -E "$pattern" | grep -v grep | awk '{print $2}')
        if [ -n "$PIDS" ]; then
            kill $PIDS 2>/dev/null
            sleep 0.5
            local REMAINING=$(ps aux | grep -E "$pattern" | grep -v grep | awk '{print $2}')
            [ -n "$REMAINING" ] && kill -9 $REMAINING 2>/dev/null
            CLEANED="$CLEANED $PIDS"
        fi
    done
    if [ -n "$CLEANED" ]; then
        echo "  后端服务已清理 (PID:$(echo $CLEANED | tr '\n' ' '))"
        stopped=1
    fi

    # 3. 按端口清理（最可靠兜底）
    for port in "$BACKEND_PORT" 3000; do
        local PID=$(lsof -ti :"$port" 2>/dev/null)
        if [ -n "$PID" ]; then
            kill $PID 2>/dev/null
            sleep 0.5
            local REMAINING=$(lsof -ti :"$port" 2>/dev/null)
            [ -n "$REMAINING" ] && kill -9 $REMAINING 2>/dev/null
            echo "  端口 $port 已释放"
            stopped=1
        fi
    done

    # 4. Docker 模式
    if command -v docker &>/dev/null; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "project-agent"; then
            cd "$PROJECT_DIR" && docker-compose down 2>/dev/null && echo "  Docker 容器已停止"
            stopped=1
        fi
    fi

    # 清理空 PID 目录
    [ -d "$PID_DIR" ] && [ -z "$(ls -A "$PID_DIR" 2>/dev/null)" ] && rmdir "$PID_DIR" 2>/dev/null

    if [ "$stopped" = 1 ]; then
        echo -e "${GREEN}所有服务已停止${NC}"
    else
        echo "  未找到运行中的服务"
    fi
}

# ============================================================
# start — 生产模式启动（gunicorn 多 worker）
# ============================================================

start_production() {
    local rebuild=false
    local port="$BACKEND_PORT"

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --build) rebuild=true; shift ;;
            --port) port="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    echo "========================================"
    echo "   Project Agent - 生产模式"
    echo "========================================"
    echo ""

    check_env
    mkdir -p "$PID_DIR"
    clean_port "$port"

    # 清理可能残留的旧 PID 文件
    rm -f "$PID_DIR/web.pid" "$PID_DIR/feishu.pid"

    cd "$BACKEND_DIR"
    source venv/bin/activate

    # 构建前端（生产模式默认构建）
    echo "[1/3] 构建前端..."
    cd "$FRONTEND_DIR"
    npm install --silent
    if [ "$rebuild" = true ]; then
        npm run build
    else
        # 仅当静态文件不存在时构建
        if [ ! -d "$BACKEND_DIR/app/static" ] || [ -z "$(ls -A "$BACKEND_DIR/app/static" 2>/dev/null)" ]; then
            npm run build
        else
            echo "  前端静态文件已存在，跳过构建（使用 --build 强制重建）"
        fi
    fi
    mkdir -p "$BACKEND_DIR/app/static"
    cp -r dist/* "$BACKEND_DIR/app/static/"
    echo -e "${GREEN}  前端就绪${NC}"
    cd "$BACKEND_DIR"

    # 数据库迁移
    echo "[2/3] 运行数据库迁移..."
    python3 -c "
import sqlite3, os
db = os.environ.get('DATABASE_URL', '').replace('sqlite+aiosqlite:///', '')
if db:
    conn = sqlite3.connect(db, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.close()
" 2>/dev/null || true
    alembic upgrade head 2>/dev/null || echo "  数据库已是最新"
    echo -e "${GREEN}  数据库就绪${NC}"

    # 读取飞书配置
    local FEISHU_APP_ID=$(grep -E '^FEISHU_APP_ID=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]"' | head -1 || true)
    local FEISHU_APP_SECRET=$(grep -E '^FEISHU_APP_SECRET=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]"' | head -1 || true)
    local HAVE_FEISHU=false
    [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ] && HAVE_FEISHU=true

    # 启动 Web 服务（gunicorn 多 worker）
    echo "[3/3] 启动生产服务 (端口: $port, workers: $GUNICORN_WORKERS)..."
    export PROJECT_AGENT_DISABLE_FEISHU=1
    nohup gunicorn app.main:app \
        --worker-class uvicorn.workers.UvicornWorker \
        --workers "$GUNICORN_WORKERS" \
        --timeout 600 \
        --keep-alive 5 \
        --bind "0.0.0.0:$port" \
        --access-logfile "$PID_DIR/access.log" \
        --access-logformat "%(h)s %(l)s %(u)s %(t)s \"%(r)s\" %(s)s %(b)s %(T)s" \
        --error-logfile "$PID_DIR/error.log" \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_DIR/web.pid"
    echo -e "${GREEN}  ✅ Web 服务已启动 (PID: $(cat $PID_DIR/web.pid))${NC}"

    # 启动飞书 Bot（独立进程）
    if [ "$HAVE_FEISHU" = true ]; then
        nohup python run_feishu.py \
            > "$FEISHU_LOG_FILE" 2>&1 &
        echo $! > "$PID_DIR/feishu.pid"
        echo -e "${GREEN}  ✅ Feishu Bot 已启动 (PID: $(cat $PID_DIR/feishu.pid))${NC}"
    else
        echo -e "${YELLOW}  ⚠️  Feishu Bot 未配置，跳过${NC}"
    fi

    # 等待验证
    sleep 2
    local WEB_OK=false
    local FEISHU_OK=false
    kill -0 $(cat "$PID_DIR/web.pid") 2>/dev/null && WEB_OK=true
    if [ "$HAVE_FEISHU" = true ] && [ -f "$PID_DIR/feishu.pid" ]; then
        kill -0 $(cat "$PID_DIR/feishu.pid") 2>/dev/null && FEISHU_OK=true
    fi

    echo ""
    echo "========================================"
    echo -e "   服务状态"
    echo "========================================"
    if [ "$WEB_OK" = true ]; then
        echo -e "${GREEN}  ✅ Web 服务      http://localhost:$port${NC}"
        echo -e "${GREEN}     API 文档      http://localhost:$port/docs${NC}"
    else
        echo -e "${RED}  ❌ Web 服务启动失败${NC}"
        echo "     查看日志: tail -50 $LOG_FILE"
    fi
    if [ "$FEISHU_OK" = true ]; then
        echo -e "${GREEN}  ✅ Feishu Bot    运行中${NC}"
    elif [ "$HAVE_FEISHU" = false ]; then
        echo "  ⏭️  Feishu Bot    未配置"
    else
        echo -e "${RED}  ❌ Feishu Bot 启动失败${NC}"
        echo "     查看日志: tail -50 $FEISHU_LOG_FILE"
    fi
    echo ""
    echo "  管理服务:  ./restart.sh stop | status"
}

# ============================================================
# dev — 开发模式启动（uvicorn --reload + vite）
# ============================================================

start_dev() {
    local port="$BACKEND_PORT"
    local fe_port="$FRONTEND_PORT"

    while [[ $# -gt 0 ]]; do
        case $1 in
            --port) port="$2"; fe_port="${3:-$FRONTEND_PORT}"; shift 3 ;;
            *) shift ;;
        esac
    done

    echo "========================================"
    echo "   Project Agent - 开发模式"
    echo "========================================"
    echo ""

    check_env
    mkdir -p "$PID_DIR"
    clean_port "$port"
    clean_reload_orphans
    clean_feishu_processes

    export BACKEND_PORT="$port"
    export FRONTEND_PORT="$fe_port"

    # 读取飞书配置
    local FEISHU_APP_ID=$(grep -E '^FEISHU_APP_ID=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]"' | head -1 || true)
    local FEISHU_APP_SECRET=$(grep -E '^FEISHU_APP_SECRET=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]"' | head -1 || true)
    local HAVE_FEISHU=false
    [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ] && HAVE_FEISHU=true

    # 数据库迁移
    echo "[1/4] 运行数据库迁移..."
    cd "$BACKEND_DIR"
    source venv/bin/activate
    export DATABASE_URL=$(grep '^DATABASE_URL=' "$PROJECT_DIR/.env" | head -1 | cut -d= -f2-)
    python3 -c "
import sqlite3, os
db = os.environ.get('DATABASE_URL', '').replace('sqlite+aiosqlite:///', '')
conn = sqlite3.connect(db, timeout=10)
conn.execute('PRAGMA journal_mode=WAL')
conn.close()
" 2>/dev/null || true
    alembic upgrade head 2>/dev/null || echo "  数据库已是最新"
    echo -e "${GREEN}  数据库就绪${NC}"

    # 启动后端
    echo "[2/4] 启动后端服务 (端口: $port)..."
    rm -f "$PID_DIR/backend.pid"
    export PROJECT_AGENT_DISABLE_FEISHU=1
    uvicorn app.main:app --reload --host 0.0.0.0 --port "$port" &
    echo $! > "$PID_DIR/backend.pid"
    echo -e "${GREEN}  后端 PID: $(cat $PID_DIR/backend.pid)${NC}"

    # 等待后端就绪
    sleep 3

    # 启动飞书 Bot
    if [ "$HAVE_FEISHU" = true ]; then
        echo "[3/4] 启动飞书 Bot..."
        python run_feishu.py &
        echo $! > "$PID_DIR/feishu.pid"
        echo -e "${GREEN}  Feishu Bot PID: $(cat $PID_DIR/feishu.pid)${NC}"
        sleep 1
    else
        echo "[3/4] 飞书 Bot 未配置，跳过"
    fi

    # 启动前端
    echo "[4/4] 启动前端开发服务器 (端口: $fe_port)..."
    rm -f "$PID_DIR/frontend.pid"
    cd "$FRONTEND_DIR"
    npx vite &
    echo $! > "$PID_DIR/frontend.pid"
    echo -e "${GREEN}  前端 PID: $(cat $PID_DIR/frontend.pid)${NC}"

    echo ""
    echo "========================================"
    echo -e "   ${GREEN}服务已启动${NC}"
    echo "========================================"
    echo "  后端 API:  http://localhost:$port"
    echo "  前端页面:  http://localhost:$fe_port"
    echo "  API 文档:  http://localhost:$port/docs"
    if [ "$HAVE_FEISHU" = true ]; then
        echo -e "  Feishu Bot: ${GREEN}运行中${NC}"
    fi
    echo "========================================"
    echo "  按 Ctrl+C 停止所有服务，或运行:"
    echo "  ./restart.sh stop"
    echo ""
    echo "  (端口配置在 .env: BACKEND_PORT=$port  FRONTEND_PORT=$fe_port)"

    # 等待任意子进程退出
    wait
}

# ============================================================
# status — 查看服务状态
# ============================================================

show_status() {
    echo "Project Agent 服务状态"
    echo "======================"
    echo ""

    # 检查 gunicorn（生产模式）
    local MASTER_PID=$(ps aux | grep 'gunicorn.*app.main:app' | grep -v grep | awk '{print $2}' | head -1)
    if [ -n "$MASTER_PID" ]; then
        local WORKER_COUNT=$(ps aux | grep 'gunicorn.*app.main:app' | grep -v grep | tail -n +2 | wc -l)
        echo -e "  ${GREEN}✅ 生产模式 (gunicorn)${NC}"
        echo "     Master PID: $MASTER_PID"
        echo "     Workers:    $WORKER_COUNT"
    fi

    # 检查 uvicorn（开发模式）
    local UVICORN_PID=$(ps aux | grep 'uvicorn app.main:app' | grep -v grep | awk '{print $2}' | head -1)
    if [ -n "$UVICORN_PID" ]; then
        echo -e "  ${GREEN}✅ 开发模式 (uvicorn)${NC}"
        echo "     PID: $UVICORN_PID"
    fi

    # 检查 feishu
    local FEISHU_PID=$(ps aux | grep 'run_feishu' | grep -v grep | awk '{print $2}' | head -1)
    if [ -n "$FEISHU_PID" ]; then
        echo -e "  ${GREEN}✅ Feishu Bot${NC}"
        echo "     PID: $FEISHU_PID"
    fi

    # 检查前端（开发模式）
    local VITE_PID=$(ps aux | grep 'vite' | grep -v grep | awk '{print $2}' | head -1)
    if [ -n "$VITE_PID" ]; then
        echo -e "  ${GREEN}✅ 前端开发服务器 (vite)${NC}"
        echo "     PID: $VITE_PID"
    fi

    # 检查端口
    echo ""
    local WEB_OPEN=$(lsof -ti :"$BACKEND_PORT" 2>/dev/null | head -1)
    if [ -n "$WEB_OPEN" ]; then
        echo -e "  ${GREEN}✅ 端口 $BACKEND_PORT (Web) — 服务中${NC}"
    else
        echo -e "  ${RED}❌ 端口 $BACKEND_PORT (Web) — 无服务${NC}"
    fi

    # 检查 health endpoint
    echo ""
    if lsof -ti :"$BACKEND_PORT" 2>/dev/null | head -1 > /dev/null; then
        local HEALTH=$(curl -s http://localhost:$BACKEND_PORT/health 2>/dev/null)
        if [ -n "$HEALTH" ]; then
            echo "  Health: $HEALTH" | python3 -m json.tool 2>/dev/null || echo "  Health: $HEALTH"
        fi
    fi

    if [ -z "$MASTER_PID" ] && [ -z "$UVICORN_PID" ] && [ -z "$FEISHU_PID" ] && [ -z "$VITE_PID" ]; then
        echo -e "  ${YELLOW}未发现运行中的服务${NC}"
    fi
}

# ============================================================
# 参数解析
# ============================================================

MODE=""
EXTRA_ARGS=()

if [ $# -eq 0 ]; then
    # 无参数 = restart
    MODE="restart"
else
    case $1 in
        start|stop|restart|dev|status)
            MODE="$1"
            shift
            EXTRA_ARGS=("$@")
            ;;
        --build|--port)
            # 兼容旧用法: restart.sh --build 相当于 restart.sh restart --build
            MODE="restart"
            EXTRA_ARGS=("$@")
            ;;
        --help|-h)
            echo "用法: ./restart.sh [命令] [选项]"
            echo ""
            echo "命令:"
            echo "  (无)          重启生产模式 (stop + start)"
            echo "  start         启动生产模式 (gunicorn 多 worker)"
            echo "  stop          停止所有服务"
            echo "  restart       重启 (stop + start)"
            echo "  dev           启动开发模式 (uvicorn --reload + vite)"
            echo "  status        查看服务状态"
            echo ""
            echo "选项 (start/restart/dev):"
            echo "  --build       强制重新构建前端"
            echo "  --port PORT   指定端口"
            exit 0
            ;;
        *)
            echo -e "${RED}未知命令: $1${NC}"
            echo "用法: ./restart.sh [start|stop|restart|dev|status|--help]"
            exit 1
            ;;
    esac
fi

# ============================================================
# 执行
# ============================================================

case $MODE in
    stop)
        stop_services
        ;;
    start)
        start_production "${EXTRA_ARGS[@]}"
        ;;
    dev)
        start_dev "${EXTRA_ARGS[@]}"
        ;;
    status)
        show_status
        ;;
    restart)
        stop_services
        sleep 1
        echo ""
        echo "重新启动..."
        echo ""
        start_production "${EXTRA_ARGS[@]}"
        ;;
esac
