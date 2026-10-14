# Project Agent — Agent 工作指导

> 本文件面向 AI 编码 Agent。阅读前默认不了解本项目。信息基于当前仓库实际内容整理，发现与代码不一致时请以代码为准并同步更新本文档。

---

## 1. 项目概述

**Project Agent** 是面向 SOC 方案公司项目经理的 AI 助手，定位为“信息秘书”、“文档助手”、“风险预警”和“辅助决策”。

- **核心交互**：Web 聊天界面（类 ChatGPT）+ 项目仪表盘 + CL 监控 + 用户管理后台。
- **系统对接**：通过 MCP（streamableHttp）协议对接公司内网部署的 Jira / Confluence / Gerrit。
- **Agent 架构**：单脑 + 外设——理解、规划、决策、执行、回复全部在同一个 LLM function calling 循环中完成。
- **大模型路由**：商用 LLM 负责语义理解 / 复杂推理；本地 LLM 负责数据处理，敏感数据不出域。
- **多用户**：Admin / 普通用户角色分离，所有用户数据（记忆、快照、缓存、配置）按 `user_id` 隔离。
- **渠道**：Web 服务 + 飞书机器人共用同一 AgentEngine。

---

## 2. 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | React 18 + Vite 5 + TypeScript 5 | SPA，Ant Design 5 组件库 |
| 前端状态 | zustand 4 | 全局状态管理 |
| 图表 | recharts 3 | 仪表盘趋势图 |
| 后端 | FastAPI 0.109 + Pydantic 2 | Python 3.10+ |
| ORM | SQLAlchemy 2（async） | SQLite（开发）/ PostgreSQL（生产） |
| 数据库迁移 | Alembic 1.13 | `backend/alembic/` |
| 认证 | JWT（python-jose + passlib/bcrypt） | 双角色 |
| 缓存/后台任务 | Redis + Celery 5.3 | 可选，生产使用 |
| LLM 客户端 | OpenAI SDK 1.10 | 兼容 DeepSeek / 本地 OpenAI 风格接口 |
| MCP 客户端 | 自研 `backend/app/mcp/` | `streamableHttp` 协议 |
| 文档/OCR | python-docx / openpyxl / python-pptx / pdfplumber / Pillow / pytesseract | 内置工具库 |
| 容器 | Docker + Docker Compose | `docker-compose.yml` 编排 PostgreSQL + Redis + Backend |

---

## 3. 目录结构与模块划分

```
ProjectAgent/
├── .env                          # 根目录环境变量（LLM/数据库/端口/密钥）
├── install.sh                    # 一键安装脚本（交互式创建 .env、安装依赖）
├── restart.sh                    # 统一管理脚本：start/stop/restart/dev/status
├── docker-compose.yml            # PostgreSQL + Redis + Backend 编排
├── .pre-commit-config.yaml       # pre-commit 框架（调用 scripts/pre_commit_test.sh）
├── scripts/                      # 测试门禁与运维脚本
│   ├── pre_commit_test.sh        # 提交前全量 pytest 门禁
│   ├── install_git_hooks.sh      # 安装 git pre-commit hook
│   └── smoke_test.sh             # 部署冒烟测试
│
├── docs/                         # 文档中心
│   ├── design/                   # 设计蓝图（monitoring_optimization 为主待办）
│   ├── review/                   # 评审归档
│   └── reference/                # 参考规范（test_plan 等）
│
├── backend/                      # FastAPI 后端
│   ├── app/
│   │   ├── main.py               # FastAPI 入口、lifespan、静态文件托管
│   │   ├── api/                  # 路由层
│   │   │   ├── auth.py           # 登录 + JWT
│   │   │   ├── chat.py           # 聊天 SSE + 对话 CRUD
│   │   │   ├── config.py         # LLM/MCP/项目/系统配置
│   │   │   ├── dashboard.py      # 仪表盘、FocusSet、项目计划
│   │   │   ├── alerts.py         # 预警规则与列表
│   │   │   ├── skills.py         # Skill 列表与执行
│   │   │   ├── tasks.py          # 后台任务管理
│   │   │   ├── upload.py         # 文件上传
│   │   │   ├── notifications.py  # 站内消息
│   │   │   ├── memory.py         # 记忆 API
│   │   │   ├── project_plans.py  # 项目计划知识库
│   │   │   ├── feishu_bind.py    # 飞书绑定
│   │   │   ├── investigate.py    # 调研类任务
│   │   │   ├── trends.py         # 趋势指标
│   │   │   └── deps.py           # 依赖注入（JWT、DB session）
│   │   ├── agent_context/        # Agent Context 文件与加载器
│   │   │   ├── IDENTITY.md       # 身份定义
│   │   │   ├── SOUL.md           # 人格与价值观
│   │   │   ├── AGENTS.md         # 行为规则（注意：不是本文件）
│   │   │   ├── TOOLS.md          # 环境/工具说明
│   │   │   ├── USER.md           # 用户画像模板
│   │   │   ├── MEMORY.md         # 长期记忆模板
│   │   │   └── __init__.py       # build_system_prompt()
│   │   ├── core/                 # 配置、安全、异常
│   │   │   ├── config.py         # pydantic-settings 配置
│   │   │   ├── security.py       # JWT + bcrypt
│   │   │   └── exceptions.py     # 自定义异常层次
│   │   ├── models/               # SQLAlchemy 模型（约 18 个）
│   │   ├── schemas/              # Pydantic schemas
│   │   ├── crud/                 # 数据库 CRUD 操作
│   │   ├── db/                   # 异步引擎 + session 工厂
│   │   ├── services/             # 业务逻辑层
│   │   │   ├── agent/            # Agent 执行引擎（核心）
│   │   │   ├── llm/              # LLM 双轨客户端/路由/prompt
│   │   │   ├── memory/           # 四层记忆系统 + FTS5 + daily_logger
│   │   │   ├── feishu/           # 飞书 REST + WebSocket 适配
│   │   │   ├── skill_generator/  # Agent 自我进化：生成/校验/注册技能
│   │   │   ├── context_spill.py  # Context spill 暂存（大数据溢出，非 memory，TTL 文件）
│   │   │   ├── scheduled_task_service.py / scheduled_task_handlers.py  # 通用定时任务引擎 + 声明式 handler（注册表扩展）
│   │   │   ├── tools/            # 内置文档/OCR/Gerrit/Confluence/spill 取回/定时任务工具
│   │   │   └── 各业务 service... # dashboard/alert/cl/notification/focus_set 等
│   │   ├── mcp/                  # MCP 客户端
│   │   │   ├── capability.py     # MCPCapabilityService（per-user 懒加载）
│   │   │   └── client.py         # streamableHttp 客户端
│   │   ├── skills/               # 进程内 Skill 适配器 + 动态发现
│   │   └── task_coordinator.py   # 跨进程任务协调（fcntl 文件锁）
│   ├── alembic/                  # 数据库迁移脚本
│   ├── scripts/                  # 运维脚本
│   ├── tests/                    # pytest 测试（约 123 个文件）
│   ├── requirements.txt          # Python 依赖
│   ├── Dockerfile                # 后端镜像
│   └── run_feishu.py             # 飞书 Bot 独立入口
│
├── frontend/                     # React 前端
│   ├── src/
│   │   ├── App.tsx               # 路由配置
│   │   ├── pages/                # 页面组件（Chat/Dashboard/Alerts/CL 监控等）
│   │   ├── services/             # API 封装（chatService.ts 含 SSE）
│   │   ├── store/                # zustand 状态
│   │   └── components/           # 可复用组件
│   ├── package.json              # npm 依赖
│   ├── vite.config.ts            # /api 代理到后端
│   └── Dockerfile                # 前端镜像（仅开发）
│
├── skills/                       # Skill 独立脚本（独立 git 仓库，不提交到本仓）
└── demo/                         # Python 原型脚本
```

---

## 4. 核心架构

### 4.1 单脑 + 外设 Agent

所有用户请求进入同一个 LLM function calling 循环：

1. 注入 System Prompt（`IDENTITY.md / SOUL.md / AGENTS.md / TOOLS.md / USER.md / MEMORY.md` + 运行时上下文）。
2. LLM 决定直接回复或调用 Meta-Tool。
3. 引擎执行 Meta-Tool，结果返回 LLM。
4. LLM 再次推理，直到直接回复或触发优雅退出。

### 4.2 6 个 Meta-Tool

| Meta-Tool | 路由目标 |
|-----------|----------|
| `execute_skill` | `skills/` 独立子进程脚本 |
| `call_mcp_tool` | MCP server（命名格式 `server/tool`） |
| `call_builtin_tool` | 进程内文档/OCR/Gerrit 等函数 |
| `memory_search` | 四层记忆系统 |
| `memory_save` | 四层记忆系统 |
| `ask_user` | 用户协作（信息不足/需确认/僵局） |

### 4.3 三支柱执行

- **Skills**：子进程脚本，stdin/stdout JSON 协议，适合批量操作。
- **MCP**：streamableHttp 客户端，适合外部系统查询。
- **Tools**：进程内异步函数，适合文档处理/OCR。

### 4.4 配置优先级

1. 用户设置（`UserSetting` 表 / `backend/config/user_settings.json`）
2. 环境变量（根目录 `.env`）

> 注意：MCP 服务器 URL 与凭据**必须通过 Web 设置页按用户配置**，禁止从 `backend/config.yaml` 读取（`config.example.yaml` 仅作历史示例保留，运行时不使用）。

---

## 5. 构建与运行命令

### 5.1 一键安装

```bash
./install.sh        # 交互式安装（推荐）
./install.sh --dev  # 本地开发模式
./install.sh --prod # 生产部署模式
```

### 5.2 统一管理脚本

```bash
./restart.sh start    # 生产模式（gunicorn 多 worker，默认 4 个）
./restart.sh stop     # 停止所有服务
./restart.sh restart  # 重启
./restart.sh dev      # 开发模式（uvicorn --reload + vite）
./restart.sh status   # 查看服务状态
./restart.sh --build  # 强制重新构建前端
```

### 5.3 手动启动

```bash
# 后端
cd backend && source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 前端
cd frontend && npm run start      # 开发服务器 :3000
cd frontend && npm run build      # 生产构建 → frontend/dist/
```

### 5.4 Docker

```bash
docker-compose up -d              # 启动 PostgreSQL + Redis + Backend
docker-compose --profile dev up -d # 额外启动前端开发容器
```

---

## 6. 测试命令与策略

### 6.1 后端测试

```bash
cd backend && source venv/bin/activate
pytest                            # 全量测试
pytest tests/agent/test_intent.py -v
pytest tests/api/test_config.py -v
pytest tests/api/test_e2e_scenarios.py -v   # 端到端场景
```

### 6.2 前端测试

```bash
cd frontend && npm test           # Vitest
```

### 6.3 门禁与冒烟

```bash
bash scripts/pre_commit_test.sh   # 提交前测试门禁
bash scripts/install_git_hooks.sh # 注册 git pre-commit hook
bash scripts/smoke_test.sh        # 部署冒烟（需服务已启动）
```

### 6.4 测试策略

- 每个功能模块至少 1 个用例，重要模块 ≥ 5 个。
- 有 HTTP 端点的模块走后端真实生产 API；无 HTTP 端点的底层组件保留单元测试。
- 外部依赖（Jira/Gerrit/飞书/LLM）在测试中 mock。
- DB 使用临时 SQLite + `tests/conftest.py` 全局替换 `AsyncSessionLocal`，隔离生产库。
- API 变动必须同步更新测试；模块删除时同步删除过时测试。

---

## 7. 代码风格规范

### 7.1 Python

- **强制类型注解**：所有函数参数和返回值必须标注类型。
- **Google 风格文档字符串**。
- **命名**：类 `PascalCase`，函数/变量 `snake_case`，常量 `UPPER_SNAKE_CASE`。
- **导入顺序**：标准库 → 第三方 → 本地，组间空行分隔。
- **异常处理**：捕获具体异常类型，禁止裸 `except:`。
- **异步**：I/O 操作用 `async/await`，同步阻塞任务放线程池。
- **配置访问**：所有环境变量通过 `app.core.config.settings` 统一访问。
- **模型**：继承 `app.models.base.Base` + `TimestampMixin`。
- **CRUD**：使用 SQLAlchemy async session。

### 7.2 TypeScript / React

- **命名**：组件 `PascalCase`，函数/变量 `camelCase`。
- **接口**：用 `interface` 定义数据类型。
- **状态**：简单用 Hooks，复杂用 zustand。
- **API**：基于 `fetch`，统一 `getAuthHeaders()` 注入 JWT。
- **样式**：Ant Design 组件 + inline style（无 CSS 模块/SASS）。
- **路由**：`react-router-dom` v6，`AppLayout` 包裹所有页面。

---

## 8. 数据库与迁移

```bash
cd backend && source venv/bin/activate
alembic upgrade head                           # 迁移到最新
alembic revision --autogenerate -m "desc"      # 生成迁移
```

- 开发默认 SQLite（`aiosqlite`），生产切换 PostgreSQL（`asyncpg`）。
- SQLite 默认 `PRAGMA foreign_keys = OFF`，生产 PostgreSQL 才强制执行外键级联。
- 部分生产环境未使用 alembic（`alembic_version` 为空，由 `create_all` 建表），列变更可能需手动 `ALTER TABLE`。

---

## 9. 安全注意事项

- **API Key / 密码不入库**：`.env` 和 `frontend/.env` 已加入 `.gitignore`。
- **MCP 凭据按用户隔离**：运行时通过 `MCPCapabilityService` 按用户懒加载，禁止全局单例共享凭据。
- **LLM Provider 覆盖参数传递**：`default_provider` 通过参数链传递，避免修改全局路由表导致并发问题。
- **数据隔离**：所有 snapshot/缓存/搜索 key/日志路径按 `user_id` 隔离。
- **Context spill 独立于 memory 系统**：工具结果过大溢出时暂存到 `data/context_cache/{user_id}/`（`services/context_spill.py`），**绝不写入 memory 系统**（避免二次撑爆 context / 污染 FTS 检索 / 无 TTL）；按 user 目录隔离、默认 24h TTL，取回走内置工具 `context_spill_read`（有界切片，勿整读）。
- **API Key 脱敏**：设置页 GET 返回 `"***"`，POST 跳过 `"***"` 值。
- **强制改密码**：首次登录用户 `force_password_change=True`，改密时跳过旧密码验证。
- **Admin 路由守卫**：`AdminOnly` 组件嵌套在 `RequireAuth` 内部，防止越权直接访问 URL。
- **配置错误不做代码规避**：MCP/LLM 配置错误时明确提示用户修正，不在代码中绕过。
- **Skill 独立仓库**：`skills/` 是独立 git 仓库，禁止 `git add -f skills/...` 强制提交到 ProjectAgent 仓库。

---

## 10. 开发与文档纪律

- **使用中文沟通交流**，文档与 commit 信息用中文。
- **每次完成开发后**：若改动触及当前架构/配置/命令/约束，同步更新本 `AGENTS.md` 与 CLAUDE.md 对应章节；一次性历史/实现过程不写入文档。
- **CLAUDE.md** 是面向 AI 编码 Agent 的工作指导，**README.md** 是项目说明。

---

## 11. 常见陷阱

- `restart.sh dev` 长时间运行后可能出现 uvicorn worker 假死，此时用 `./restart.sh restart` 切到生产模式可恢复。
- 前端 `handleSend` / `handleSkillClick` 等入口必须在异步链末尾 `.finally(() => setIsLoading(false))`，防止 server 挂死时进度条永远旋转。
- 后端端口在 `.env` 的 `BACKEND_PORT`；前端端口由 `FRONTEND_PORT` 注入到 `vite.config.ts`。
- 新 Skill 必须返回统一格式 `{"success": bool, "message": str, "data": {...}}`。
- 新 service 需加入 `tests/conftest.py` 的全局 `AsyncSessionLocal` 模块替换列表，否则测试可能连生产库。
