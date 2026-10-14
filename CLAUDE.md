# CLAUDE.md — Project Agent

本文件面向在本仓库工作的 AI 编码 Agent 与贡献者。信息基于当前仓库实际内容整理，发现与代码不一致时以代码为准并同步更新本文档。

## 1. 项目概览

**Project Agent** — 面向项目经理的 AI 助手，定位为"信息秘书"、"文档助手"、"风险预警"和"辅助决策"。Web 聊天界面（类 ChatGPT）+ 仪表盘（项目状态概览），通过 MCP 协议对接 Jira/Confluence/Gerrit。

**核心特性：**
- **单脑+外设 Agent 架构**：LLM function calling 驱动，理解/规划/决策/执行/回复在同一推理循环完成
- LLM 双轨路由（商用 LLM 语义理解 + 本地 LLM 数据处理）
- **File-based Context 注入**（IDENTITY/SOUL/AGENTS/TOOLS/USER/MEMORY.md）
- MCP 客户端（streamableHttp 协议），按用户配置与隔离
- Skill 插件化系统（动态发现 + 子进程脚本化执行）
- **Agent 自我进化**：能力缺口检测 → 用户确认 → LLM 生成代码 → 动态注册 → 立即执行
- **内置文档工具库**：PDF/Word/Excel/PPT/图片读写 + OCR

## 2. 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端 | React + Vite | 18.x |
| UI 框架 | Ant Design | 5.x |
| 图表 | recharts | 3.x |
| 状态管理 | zustand | 4.x |
| 后端 | FastAPI | 0.109.x |
| 数据库 | SQLite (开发) / PostgreSQL (生产) | - |
| ORM | SQLAlchemy (async) | - |
| LLM 客户端 | OpenAI SDK | 1.10.x |
| 认证 | JWT (python-jose + passlib) | - |

## 3. 目录结构

```
ProjectAgent/
├── .env / .env.example           # 环境变量（必需，参见样例）
├── install.sh                    # 一键安装（交互式配置）
├── restart.sh                    # 统一管理脚本（start/stop/restart/dev/status）
├── docker-compose.yml            # PostgreSQL + Redis 编排
├── scripts/                      # 测试门禁与运维脚本
├── docs/                         # 文档中心（design / reference）
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI 入口
│   │   ├── api/                  # API 路由层（auth/chat/config/dashboard/skills/tasks/upload...）
│   │   ├── agent_context/        # Agent 身份/行为定义（MD 模板注入）
│   │   ├── core/                 # 配置 / JWT 安全 / 异常
│   │   ├── models/ crud/ db/     # SQLAlchemy 模型 / CRUD / session
│   │   ├── services/             # 业务服务层
│   │   │   ├── agent/            # Agent 执行引擎（engine/executor/planner/result_budget）
│   │   │   ├── llm/              # LLM 双轨（client/router/prompts）
│   │   │   ├── memory/           # 四层记忆系统
│   │   │   ├── ci_build/         # Google CI 构建查询/下载 + FTPS 上传
│   │   │   ├── feishu/           # 飞书渠道接入
│   │   │   └── skill_generator/  # Agent 自我进化（代码生成/校验/注册）
│   │   ├── tools/                # 内置工具库（文档/OCR/Gerrit/Confluence/Jira filter/CI...）
│   │   ├── mcp/                  # MCP 客户端 + 能力统一入口
│   │   └── skills/               # Skill 适配器 + 动态发现引擎
│   ├── alembic/                  # 数据库迁移
│   ├── scripts/                  # 运维工具脚本
│   └── tests/                    # 测试（按模块组织）
├── frontend/
│   └── src/
│       ├── pages/                # 聊天/仪表盘/预警/CL监控/设置/用户管理...
│       ├── services/ store/      # API 通信 / zustand 状态
│       └── components/           # 布局与图表组件
└── skills/                       # Skill 独立脚本（独立 git 仓，不提交到本仓）
```

## 4. 核心架构

### Meta-Tool（6 个）

| Meta-Tool | 用途 |
|-----------|------|
| `execute_skill` | 运行 skills/ 目录下的子进程脚本 |
| `call_mcp_tool` | 调用 MCP 服务（格式 `server/tool`） |
| `call_builtin_tool` | 调用内置进程内函数（文档处理/OCR 等） |
| `memory_search` / `memory_save` | 四层记忆系统读写 |
| `ask_user` | 信息不足/需确认时向用户提问 |

### 执行模型

```
用户请求 → 单脑 LLM function-calling 循环
            ├── Skills（子进程脚本）—— 批量操作
            ├── MCP（HTTP 客户端）  —— Jira/Confluence/Gerrit 查询
            └── Tools（进程内函数） —— 文档读写、图片 OCR
```

- 所有工具结果经**结果预算门**（`result_budget.py`）控制体量，溢出原文存 context spill 暂存区（`data/context_cache/`，24h TTL），LLM 可经 `context_spill_read` 有界切片取回
- 撑爆 context 时 reactive 压缩早期轮次为摘要并重试（`_compact_messages_for_overflow`）
- System Prompt 由 `agent_context/` 下 MD 模板 + 运行时上下文（技能/工具/MCP 清单、用户画像）组装
- Skill 发现**约定优先**：目录名→技能名，`main.py`→入口，docstring→描述；`skill.json` 可选覆盖；脚本 stdin 收 JSON、最后一行 stdout 输出 `{"success": bool, "message": str, "data": {...}}`

## 5. 配置管理

优先级：`.env` > 用户设置页（按用户持久化到 DB）。

- `.env`：SECRET_KEY、DATABASE_URL（开发默认 `sqlite+aiosqlite:///./data/project_agent.db`）、LLM 基础、飞书凭证
- 设置页：LLM（按用户）、MCP 服务器 URL+凭据（按用户）、CI 构建、Dashboard
- **MCP 不从任何配置文件读取**，未配置时返回中文引导提示
- **对接自有 Gerrit/Jira 时**在 `.env` 配置：`GERRIT_HOSTS`（`host=instance,host=instance`，驱动 CL URL 解析与 MCP 客户端名）、`JIRA_WEB_URL`、`CONFLUENCE_BASE_URL`、`JIRA_MONITOR_LABEL`；示例见 `.env.example`
- API Key 不提交 git；设置页 GET 返回 `"***"` 脱敏

## 6. 编码规范

### Python
- 强制类型注解；Google 风格 docstring
- 类 `PascalCase`，函数 `snake_case`，常量 `UPPER_SNAKE_CASE`
- 导入顺序：标准库 → 第三方 → 本地
- 捕获具体异常类型，禁止裸 `except:`；I/O 一律 `async/await`
- 环境变量统一经 `app.core.config.settings` 访问

### TypeScript / React
- 组件 `PascalCase`，函数/变量 `camelCase`；接口用 `interface`
- 状态优先 `useState/useEffect`，复杂状态用 zustand
- Ant Design 组件 + inline style；路由 `react-router-dom` v6

## 7. 常用命令

```bash
./restart.sh start|stop|restart|dev|status   # 服务管理（dev=热重载，start=生产 gunicorn）

cd backend && python3 -m pytest               # 全量测试
cd backend && pytest tests/api/test_config.py -v
cd frontend && npm test                       # 前端测试（vitest）
bash scripts/pre_commit_test.sh               # 提交前测试门禁
bash scripts/smoke_test.sh                    # 部署冒烟

cd backend && alembic upgrade head            # 数据库迁移
cd backend && python scripts/reset_passwords.py <username> [new_password]
```

## 8. 部署注意

- **生产不要用 `--reload`**：开发用 `./restart.sh dev`，生产用 `./restart.sh start`；dev 模式长时间运行可能出现 worker 假死，切生产模式即可恢复
- **异步入口必须 `.finally()` 卸载 loading**：前端 handleSend/handleSkillClick 等入口，异步链末尾必须挂载，防止进度条永远旋转
- **SQLite 不强制外键**：模型声明的 `ondelete="CASCADE"` 仅 PostgreSQL 生效；迁移脚本统一 `PRAGMA foreign_keys = OFF`
- **新用户配置为空隔离**：未配置 LLM/MCP 时引擎直接返回引导提示，不回退全局默认
- **多用户数据隔离**：快照/缓存/记忆/日志全部按 `user_id` 隔离；后台定时任务用 `user_id=None` 全局模式
- 首次登录 `admin/admin123`（强制改密）
