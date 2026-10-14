# Project Agent - 项目管理AI助手

> 面向 SOC 方案公司项目经理的 AI 助手，定位为"信息秘书"、"文档助手"、"风险预警"和"辅助决策"。

- **单脑+外设 Agent**：LLM function calling 驱动，通过 MCP 对接 Jira / Confluence / Gerrit
- **LLM 双轨路由**：商用大模型做语义理解/复杂推理，本地大模型做敏感数据处理（防数据泄露）
- **三支柱执行**：Skills（子进程脚本）+ MCP（HTTP 客户端）+ Tools（进程内文档处理/OCR）
- **Web 聊天 + 仪表盘**：类 ChatGPT 对话界面，配合项目状态看板、预警中心、CL 监控
- **多用户**：Admin/普通用户角色分离，数据按 user_id 隔离，JWT 认证

---

## 🚀 快速启动

### 一键安装（推荐）

```bash
# 1. 进入项目目录
cd ProjectAgent

# 2. 运行安装脚本（交互式配置 LLM 基础信息）
./install.sh

# 3. 启动开发环境
./restart.sh dev

# 4. 访问
# 前端页面: http://localhost:3000
# API文档:  http://localhost:8000/docs
```

> 首次登录后请在"设置"页为当前用户配置**商用/本地大模型**与 **MCP 服务器**（Jira/Confluence/Gerrit），
> 配置按用户独立存储，新用户配置为空、不继承他人凭据。

### 手动启动

```bash
# 后端
cd backend && source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 前端（新终端）
cd frontend && npm run start
```

### OCR 依赖（可选）

图片 OCR 工具（`ocr_image`）依赖 tesseract。镜像内未捆绑二进制，请自行安装其一：

```bash
# 方式 1：系统包管理器
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim   # Debian/Ubuntu

# 方式 2：便携版放到项目根目录（backend/app/tools/tesseract_helper.py 会自动发现）
#   .tesseract/bin/tesseract
#   .tesseract/lib/*.so*        （如系统已装 leptonica 可省略）
#   .tesseract/tessdata/*.traineddata
```

未安装时 OCR 工具会返回中文提示，不影响其余功能。

### 统一管理脚本

```bash
./restart.sh start    # 生产模式（gunicorn 多 worker）
./restart.sh stop     # 停止所有服务
./restart.sh restart  # 重启
./restart.sh dev      # 开发模式（前后端分离，后端热重载）
./restart.sh status   # 查看服务状态
```

---

## 📁 项目结构

```
ProjectAgent/
├── .env                          # 基础环境变量（LLM 基础、数据库）
├── install.sh                    # 一键安装脚本
├── restart.sh                    # 统一管理脚本
├── docker-compose.yml            # PostgreSQL + Redis 编排
│
├── CLAUDE.md                     # 贡献者工作指导（架构/命令/规范）
├── docs/                         # 文档中心
│   ├── design/                   # 设计蓝图（各文档头部标注实施状态）
│   │   ├── monitoring_optimization.md    # 【主待办蓝图】监控/预警/跟踪优化
│   │   ├── dashboard_nextgen.md          # 仪表盘演进（A/B/C 已实施）
│   │   ├── agent_task_enhancement.md     # Agent 认知脚手架（已实施）
│   │   └── dashboard_ideas.md            # 仪表盘想法池
│   ├── review/                   # 评审归档（代码/架构/安全审查记录）
│   └── reference/                # 参考规范（测试计划、专项说明）
│
├── backend/                      # 后端 (FastAPI)
│   ├── app/
│   │   ├── main.py               # FastAPI 入口（lifespan 管理、静态托管）
│   │   ├── api/                  # 路由层：auth/chat/config/dashboard/alerts/skills/
│   │   │                         #   tasks/upload/trends/memory/investigate/feishu_bind
│   │   ├── agent_context/        # Agent 身份/行为（IDENTITY/SOUL/AGENTS/TOOLS/USER/MEMORY.md）
│   │   ├── core/                 # 配置、安全、异常
│   │   ├── services/
│   │   │   ├── agent/            # 单脑引擎（engine.py 主循环 + intent/planner 数据类）
│   │   │   ├── llm/              # LLM 双轨客户端/路由/prompt
│   │   │   ├── memory/           # 四层记忆 + FTS5 搜索 + daily_logger
│   │   │   ├── dashboard_service.py / trend_service.py / alert_service.py
│   │   │   ├── cl_*_service.py   # CL 监控快照/抑制/自动提醒/持续跟踪
│   │   │   ├── gerrit_service.py # Gerrit 查询封装
│   │   │   ├── feishu/           # 飞书渠道接入（REST + WebSocket）
│   │   │   ├── skill_generator/  # Agent 自我进化（LLM 生成技能）
│   │   │   └── task_queue.py / task_coordinator.py / task_manager.py
│   │   ├── mcp/                  # MCP 客户端
│   │   │   ├── capability.py     # MCPCapabilityService（统一入口，per-user）
│   │   │   └── client.py         # streamableHttp 客户端
│   │   ├── models/               # SQLAlchemy 模型（15 个）
│   │   ├── tools/                # 内置文档工具库（PDF/Word/Excel/PPT/图片/OCR/EML/Gerrit）
│   │   └── skills/               # 进程内 Skill 适配器
│   ├── scripts/                  # 运维脚本（reset_passwords / migrate_schema）
│   ├── tests/                    # pytest 测试（约 250 用例）
│   └── venv/
│
├── frontend/                     # 前端 (React + Vite + Ant Design 5)
│   ├── src/
│   │   ├── pages/                # 12 个页面：Chat/History/Dashboard/Alerts/ClMonitoring/
│   │   │                         #   Issues/Settings/SkillsConfig/Login/About/UserManagement/
│   │   │                         #   ChangePassword
│   │   ├── services/chatService.ts   # SSE 流式 + API 封装（fetch + JWT）
│   │   ├── components/           # Dashboard 组件（TrendChart/AlertBadge 等）+ Layout
│   │   └── store/                # zustand 状态管理
│   └── package.json
│
├── skills/                       # Skill 独立脚本（独立 git 仓库，不提交到本仓）
└── demo/                         # Python 原型脚本
```

---

## 🔧 核心架构

### Agent 执行引擎（单脑 + 外设）

单脑架构：**理解、规划、决策、执行、回复全部在同一个 LLM 推理循环中完成**，通过 function calling 与外部系统交互。

```
LLM Function Calling 循环:
  System Prompt (Context 注入)
    ├── IDENTITY.md / SOUL.md / AGENTS.md / TOOLS.md
    ├── USER.md / MEMORY.md（用户画像 + 长期记忆）
    └── 运行时上下文（记忆/技能/会话历史/执行状态）
          │
          ▼
  User Message → LLM 推理（选工具 / 直接回复）
          │            │
          │ tool_call  │
          ▼            ▼
  引擎执行 Meta-Tool → LLM 二次推理 → 回复 → 循环结束
```

**6 个 Meta-Tool：**

| Meta-Tool | 用途 | 路由目标 |
|-----------|------|---------|
| `execute_skill` | 运行子进程脚本 | `skills/` 独立脚本 |
| `call_mcp_tool` | 调用 MCP 服务 | `server/tool`（jira/search_issues）|
| `call_builtin_tool` | 调用内置函数 | 文档处理/OCR/Gerrit 操作 |
| `memory_search` | 搜索记忆 | 四层记忆系统 |
| `memory_save` | 保存记忆 | 四层记忆系统 |
| `ask_user` | 用户协作（提问/选项） | 信息不足/需确认/僵局时 |

**三支柱执行模型：**

```
用户请求 → 单脑 LLM 循环 → ── Skills  (子进程脚本)   — jira-confluence-report 等
                            ├── MCP    (HTTP 客户端)  — Jira/Confluence/Gerrit 查询
                            └── Tools  (进程内函数)    — 文档读写、图片 OCR
```

### 执行模式

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| `direct_chat` | 问候语 | 直接回复，不调用 LLM |
| `single_action` | 单步 LLM+tool | LLM 推理 → 执行 → 返回 |
| `complex` | 多步任务 | 规划 → 多轮 tool calling（认知脚手架：状态追踪/停滞检测/优雅退出/ask_user）|

### LLM 双轨路由

| 任务类型 | 路由 | 用途 |
|----------|------|------|
| INTENT_PARSING / RISK_ANALYSIS / REPORT_GENERATION / QA / SUMMARIZATION | 商用 LLM | 语义理解、复杂推理 |
| DATA_PARSING / TEXT_FORMATTING / DATA_MASKING | 本地 LLM | 数据处理（敏感数据不出域） |

---

## ⚙️ 配置说明

### 基础配置（.env）

```bash
# LLM（至少配置一个）
COMMERCIAL_LLM_API_BASE=https://api.deepseek.com/v1
COMMERCIAL_LLM_API_KEY=sk-xxx
COMMERCIAL_LLM_MODEL=deepseek-chat

# 数据库（默认 SQLite）
DATABASE_URL=sqlite+aiosqlite:///./data/project_agent.db

# 端口
BACKEND_PORT=8000
```

### 运行时配置（设置页，按用户持久化）

- **LLM 配置**：商用/本地大模型的 API Base / Key / Model / 默认模型选择
- **MCP 配置**：Jira / Confluence / Gerrit 服务器的 URL、用户名、密码（保存时自动验证连通性）
- **Dashboard 配置**：项目列表、Epic、Labels 过滤、Jira Web URL
- **CL 监控配置**：监控项目、标签、Gerrit 实例映射

> 配置优先级：用户设置（`user_settings.json`）> `.env`。新用户配置为空，须手动配置后使用。

---

## 📅 开发进度

> 架构与设计决策见 [CLAUDE.md](CLAUDE.md)，演进方案见 docs/design/。

| 阶段 | 状态 | 内容 |
|------|------|------|
| Phase 1-3 | ✅ | 基础框架 / Skill 系统 / 内置文档工具库（PDF/Word/Excel/PPT/OCR）|
| Phase 4 | ✅ | **单脑+外设架构重构**（LLM function calling 替代三层流水线）|
| Phase 5-7 | ✅ | 记忆系统增强 / 推理过程可视化 / 增强功能优化 |
| Phase 8-9 | ✅ | 飞书渠道接入 / 飞书体验优化 |
| Phase 10/11 | ✅ | 项目概览仪表盘 / 多用户角色分离 |
| Phase A-E | ✅ | 趋势可视化 / MCP 配置多用户化 / 记忆隔离 / 主动风险告警 / Gunicorn 多 worker |
| 近期增强 | ✅ | MCP 统一接口层 / CL 监控自动提醒+持续跟踪 / Agent 认知脚手架（ask_user/优雅退出）/ 复杂任务性能优化 |
| **待办线** | ⏳ | [docs/design/monitoring_optimization.md](docs/design/monitoring_optimization.md) — 紧迫度评分 + 项目知识库 + 看板状态图 + 三层提醒 |

---

## 📄 相关文档

- [CLAUDE.md](CLAUDE.md) — 项目架构、模块说明、编码规范、部署注意
- [测试计划](docs/reference/test_plan.md) — 测试策略与用例设计
- [监控优化设计（主待办蓝图）](docs/design/monitoring_optimization.md) — 预警/跟踪优化方案
- [Agent 认知脚手架设计](docs/design/agent_task_enhancement.md) — 复杂任务增强方案（已实施）
- [文档中心](docs/) — 全部设计蓝图、评审归档、参考规范
