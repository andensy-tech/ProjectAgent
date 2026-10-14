# Project Agent - 测试计划

> 生成日期：2026-05-22  
> 最近更新：2026-08-10（文档整理：对齐当前代码现状）  
> 适用项目：`/home/user/share/ProjectAgent`  
> 当前项目状态：Phase 1-11 与 Phase A-E 已全部完成（2026-08），新增 Dashboard/Trends/Alerts/CL 监控/飞书/多用户/ask_user 等模块。  
> 测试套件现状：`backend/tests/` 41 文件约 250 用例；存在约 24 个失败用例 + 2 个收集错误（部分测试未随 MCP 统一层/记忆 user_id/多用户重构同步更新），修复中。**前端测试与 E2E 未落地。**

---

## 1. 项目概述

| 项目 | 说明 |
|------|------|
| **项目名称** | Project Agent - 项目管理AI助手 |
| **技术栈** | FastAPI (后端) + React + Vite + Ant Design (前端) |
| **数据库** | SQLite (开发) / PostgreSQL (生产) |
| **核心功能** | Web聊天界面、Agent执行引擎、LLM双轨路由、MCP客户端、Skill系统 |

### 1.1 架构概览

> 当前架构为**单脑+外设**（2026-05-28 重构，替代旧三层流水线）：理解/规划/决策/执行/回复全在同一个 LLM function calling 循环内完成。

```
用户消息 → LLM 推理（选工具 / 直接回复）
    │           │
    │ tool_call │
    ▼           ▼
引擎执行 Meta-Tool → 结果回填 → LLM 二次推理 → 回复 → 循环结束
（execute_skill / call_mcp_tool / call_builtin_tool / memory_search / memory_save / ask_user）
```

### 1.2 执行模式

| 模式 | 触发条件 | 进度条 | 示例 |
|------|---------|--------|------|
| `direct_chat` | greeting | 无 | "你好" → 直接回复 |
| `single_action` | 单步任务 | 1个 | "汇总Jira" → thinking → card |
| `complex` | 多步任务 | 多个 | 待扩展 |

---

## 2. 测试范围

### 2.1 包含范围

| 层级 | 模块 | 关键文件 |
|------|------|----------|
| **后端 API** | Auth、Chat、Skills、Config、Health、Dashboard、Alerts、Trends、Tasks、Upload、Memory、Investigate、FeishuBind | `app/api/*.py` |
| **Agent引擎** | 单脑主引擎（LLM 循环 + 认知脚手架/ask_user）、意图、规划 | `app/services/agent/*.py` |
| **LLM服务** | 客户端、路由、Prompt管理 | `app/services/llm/*.py` |
| **记忆系统** | 四层记忆 + FTS5 搜索 + daily_logger | `app/services/memory/*.py` |
| **MCP** | 统一层 capability + 客户端 | `app/mcp/*.py` |
| **内置文档工具** | PDF/Word/Excel/PPT/图片/OCR/Gerrit | `app/tools/*.py` |
| **Skill系统** | jira-confluence-report、jira-cl-merge-monitor 等 | `app/skills/*.py` |
| **监控服务** | dashboard/trend/alert/cl_* 快照与告警 | `app/services/*_service.py` |
| **数据库** | 15 张表 CRUD | `app/models/*.py`, `app/crud/*.py` |
| **前端** | 12 页面（Chat/Dashboard/Alerts/ClMonitoring/Issues 等）+ chatService | `frontend/src/**/*.tsx` |
| **部署脚本** | install.sh、restart.sh start/stop/dev/status | 根目录 |

### 2.2 不包含范围

- `skills/` 目录下的原始 Skill 脚本（用户明确跳过）
- 外部 MCP Server 的内部实现
- LLM 模型本身的准确性测试

---

## 3. 测试策略

### 3.1 测试金字塔

```
        ┌─────────┐
        │  E2E    │  ← 前端+后端端到端 (5%)
        │  测试   │
       ┌┴─────────┴┐
       │  API/集成  │  ← API接口、MCP集成、Skill集成 (25%)
       │   测试    │
      ┌┴───────────┴┐
      │    单元测试   │  ← 核心逻辑、工具函数 (70%)
      └─────────────┘
```

### 3.2 测试环境

| 环境 | 配置 |
|------|------|
| **开发测试** | SQLite + Mock LLM + Mock MCP |
| **集成测试** | SQLite + 真实LLM(可选) + 真实MCP(可选) |
| **CI测试** | SQLite + Mock 所有外部依赖 |

---

## 4. 测试用例详细设计

### 4.1 后端单元测试

#### 4.1.1 核心配置测试 (`tests/core/test_config.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| CFG-01 | 加载默认配置 | 所有配置项有默认值 |
| CFG-02 | 从.env加载配置 | 环境变量正确覆盖默认值 |
| CFG-03 | 缺失必需配置的处理 | 使用默认值，不报错 |

#### 4.1.2 安全模块测试 (`tests/core/test_security.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| SEC-01 | 密码哈希与验证 | `verify_password` 返回 True |
| SEC-02 | 密码验证失败 | `verify_password` 返回 False |
| SEC-03 | JWT Token 创建与解码 | `decode_access_token` 返回原始payload |
| SEC-04 | 过期Token解码 | `decode_access_token` 返回 None |
| SEC-05 | 篡改Token解码 | `decode_access_token` 返回 None |

#### 4.1.3 异常类测试 (`tests/core/test_exceptions.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| EXC-01 | LLMException 创建 | status_code=503, provider正确 |
| EXC-02 | MCPException 创建 | status_code=502, server正确 |
| EXC-03 | SkillException 创建 | status_code=500, skill_name正确 |
| EXC-04 | ValidationException 创建 | status_code=400 |
| EXC-05 | AuthenticationException 创建 | status_code=401 |

#### 4.1.4 意图理解测试 (`tests/agent/test_intent.py`)

| 用例ID | 测试内容 | 输入 | 预期结果 |
|--------|----------|------|----------|
| INT-01 | 问候语识别-中文 | "你好" | category=greeting, confidence=1.0 |
| INT-02 | 问候语识别-英文 | "hello" | category=greeting |
| INT-03 | 问候语识别-在吗 | "在吗" | category=greeting |
| INT-04 | 问候语识别-早上好 | "早上好" | category=greeting |
| INT-05 | 问候语识别-短消息 | "hi" | category=greeting |
| INT-06 | 非问候语短消息 | "汇总" | category≠greeting |
| INT-07 | Skill匹配-Jira报告 | "汇总Jira状态" | target_skill=jira-confluence-report |
| INT-08 | Skill匹配-CL监控 | "监控CL合并" | target_skill=jira-cl-merge-monitor |
| INT-09 | Skill匹配-周报 | "生成周报" | target_skill=weekly-report |
| INT-10 | 规则提取 Epic ID | "查看 TV-12345 的状态" | entities.epic_ids=["TV-12345"] |
| INT-11 | 规则提取多个Epic | "TV-111 和 TV-222" | entities.epic_ids=["TV-111", "TV-222"] |
| INT-12 | 规则提取 Label | "label=bug" | entities.labels=["bug"] |
| INT-13 | 规则提取项目名 | "AML123项目" | entities.project="AML123" |
| INT-14 | 低置信度澄清 | "随便看看" | clarification_needed=true |
| INT-15 | LLM解析失败回退 | LLM异常 | 返回规则解析结果，不崩溃 |
| INT-16 | 实体归一化-Epic大写 | "tv-12345" | epic_ids=["TV-12345"] |
| INT-17 | 实体归一化-Label小写 | "Label=Bug" | labels=["bug"] |

#### 4.1.5 任务规划测试 (`tests/agent/test_planner.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| PLN-01 | 问候语直接回复 | steps为空, response为问候语文本 |
| PLN-02 | Skill单步计划 | 1个SKILL_CALL步骤，参数正确 |
| PLN-03 | 查询直接LLM | 1个LLM_GENERATE步骤 |
| PLN-04 | 复杂任务多步计划 | 多步骤，含依赖关系 |
| PLN-05 | 参数构建-有config_name | params包含config_name |
| PLN-06 | 参数构建-有epic_ids | params包含epics |
| PLN-07 | 参数构建-默认配置 | 无参数时config_name="default" |
| PLN-08 | LLM规划失败回退 | 返回基础fallback计划（1个LLM_GENERATE） |
| PLN-09 | 确认动作标记 | API_CALL步骤requires_confirmation=true |

#### 4.1.6 动作执行测试 — ~~`tests/agent/test_executor.py`~~（已删除）

> 旧三层架构的 `executor.py`（execute_plan/_execute_step/_execute_db_query 等）于 2026-06-23 随单脑重构清理删除，
> 配套的 413 行 `test_executor.py` 一并移除。动作执行现收敛到 `engine.py` 的 `_execute_skill`/`_execute_tool`，由 engine 测试覆盖。

#### 4.1.7 Agent引擎测试 (`tests/agent/test_engine.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| ENG-01 | direct_chat模式-问候语 | 无thinking，直接返回text+complete |
| ENG-02 | single_action模式-Skill | 1个thinking，然后card，最后complete |
| ENG-03 | single_action模式-失败 | thinking → error → complete |
| ENG-04 | complex模式 | text → thinking → 结果 → complete |
| ENG-05 | 需要澄清 | 返回澄清问题text，然后complete |
| ENG-06 | 执行失败 | 返回error类型，然后complete |
| ENG-07 | Card格式提取-report | 正确提取type=card的数据 |
| ENG-08 | Text格式提取 | 返回纯文本内容 |
| ENG-09 | 确认处理-取消 | 返回"已取消"文本 |
| ENG-10 | 确认处理-确认 | 继续执行，返回结果 |
| ENG-11 | 记忆更新失败不影响响应 | 异常被捕获，用户正常收到回复 |
| ENG-12 | 引擎整体异常处理 | 返回error，不崩溃 |

#### 4.1.8 LLM客户端测试 (`tests/llm/test_client.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| LLM-01 | 商业LLM配置缺失 | 抛出 LLMException |
| LLM-02 | 本地LLM配置缺失 | 抛出 LLMException |
| LLM-03 | 正常completion | 返回非空字符串 |
| LLM-04 | JSON模式响应 | 返回可解析的JSON |
| LLM-05 | JSON解析失败 | 抛出 LLMException |
| LLM-06 | 流式completion | 返回完整字符串 |
| LLM-07 | Embedding调用 | 返回向量列表 |
| LLM-08 | Embedding失败 | 抛出 LLMException |

#### 4.1.9 LLM路由测试 (`tests/llm/test_router.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| RTR-01 | INTENT_PARSING路由 | provider=commercial |
| RTR-02 | RISK_ANALYSIS路由 | provider=commercial |
| RTR-03 | DATA_PARSING路由 | provider=local |
| RTR-04 | TEXT_FORMATTING路由 | provider=local |
| RTR-05 | 自定义路由覆盖 | 使用自定义配置 |
| RTR-06 | 未知任务默认商业 | provider=commercial |
| RTR-07 | route_and_complete调用 | 正确路由到对应client |
| RTR-08 | route_and_complete_structured | 返回结构化JSON |

#### 4.1.10 记忆系统测试 (`tests/memory/test_system.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| MEM-01 | 系统记忆Skill注册 | skills字典正确存储 |
| MEM-02 | 系统记忆Tool注册 | tools字典正确存储 |
| MEM-03 | 工作记忆消息管理 | 消息按序存储 |
| MEM-04 | 工作记忆消息截断 | 超过max_messages时保留最近 |
| MEM-05 | 工作记忆实体追踪 | 实体按类型存储，带时间戳 |
| MEM-06 | 工作记忆上下文变量 | set/get正确 |
| MEM-07 | 会话记忆项目设置 | active_project正确 |
| MEM-08 | 会话记忆Skill结果缓存 | 缓存命中返回结果 |
| MEM-09 | 会话记忆偏好设置 | preferences正确存储 |
| MEM-10 | 长期记忆用户画像创建 | 默认画像正确初始化 |
| MEM-11 | 长期记忆用户画像更新 | 更新字段正确合并 |
| MEM-12 | 长期记忆学习记录 | 记录正确添加，带时间戳 |
| MEM-13 | 完整上下文聚合 | 四层记忆正确合并为dict |

#### 4.1.11 MCP客户端测试 (`tests/mcp/test_client.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| MCP-01 | 初始化连接 | 返回成功状态，_initialized=True |
| MCP-02 | 重复初始化 | 返回already_initialized |
| MCP-03 | 列出工具 | 返回工具列表 |
| MCP-04 | 调用工具 | 返回正确结果 |
| MCP-05 | JSON响应解析 | 正确解析JSON内容 |
| MCP-06 | 文本响应解析 | 返回原始文本 |
| MCP-07 | 错误响应 | 抛出 MCPException |
| MCP-08 | 请求ID递增 | 每次调用id+1 |
| MCP-09 | Manager多客户端 | 正确管理多个server |
| MCP-10 | Manager获取不存在client | 抛出 MCPException |
| MCP-11 | Manager初始化全部 | 部分失败不阻断其他 |
| MCP-12 | Manager关闭全部 | 所有client正确关闭 |
| MCP-13 | Auth header JSON解析 | 正确解析JSON格式auth |
| MCP-14 | Auth header 非JSON | 原样使用字符串 |

#### 4.1.12 Skill适配器测试 - Jira报告 (`tests/skills/test_jira_report.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| SKR-01 | HTML转义-& | `&` → `&amp;` |
| SKR-02 | HTML转义-< | `<` → `&lt;` |
| SKR-03 | HTML转义-> | `>` → `&gt;` |
| SKR-04 | 状态颜色-Resolved | `#36b37e` |
| SKR-05 | 状态颜色-In Progress | `#0052cc` |
| SKR-06 | 优先级颜色-P1 | `#de350b` |
| SKR-07 | 优先级颜色-P2 | `#ff8b00` |
| SKR-08 | Issue分类-P1优先 | P1问题进入p1_issues |
| SKR-09 | Issue分类-ETA到期 | duedate<=7天进入eta_expiring |
| SKR-10 | Issue分类-Stale | updated<7天进入stale_issues |
| SKR-11 | Issue分类-剩余 | 其他进入remaining |
| SKR-12 | Issue分类-P1优先于ETA | P1同时ETA到期仍进p1_issues |
| SKR-13 | 报告HTML生成 | 包含Summary、P1、ETA、Stale、Remaining |
| SKR-14 | Confluence发布成功 | 返回page_id和url |
| SKR-15 | Confluence页面已存在 | 尝试更新页面 |
| SKR-16 | Confluence发布失败 | 返回success=False |
| SKR-17 | 参数覆盖-label | params.label覆盖默认config |
| SKR-18 | 参数覆盖-epics | params.epics覆盖默认config |
| SKR-19 | 参数覆盖-space_key | params.space_key覆盖默认config |
| SKR-20 | 整体handler成功 | 返回type=report的dict |
| SKR-21 | 整体handler异常 | 返回type=error的dict |

#### 4.1.13 Skill适配器测试 - CL监控 (`tests/skills/test_cl_monitor.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| SKC-01 | CL URL正则提取 | 正确提取project和change_id |
| SKC-02 | CL去重 | 相同(project, change_id)只保留一条 |
| SKC-03 | Gerrit状态查询-MERGED | 状态解析为MERGED |
| SKC-04 | Gerrit状态查询-NEW | 状态解析为NEW |
| SKC-05 | Gerrit状态查询失败 | 状态为Error |
| SKC-06 | 风险分析-P1阻塞 | P1+未合并CL被标记 |
| SKC-07 | 风险分析-Epic收敛 | 同Epic>=3个未合并CL |
| SKC-08 | 风险分析-CL过多 | 同Jira>=5个CL |
| SKC-09 | 风险分析-缺少Assignee | assignee为空或None |
| SKC-10 | 报告HTML生成 | 包含统计、风险告警、CL表格 |
| SKC-11 | 状态徽章-MERGED | 绿色背景 |
| SKC-12 | 状态徽章-NEW | 橙色背景 |
| SKC-13 | 状态徽章-ABANDONED | 红色背景 |
| SKC-14 | 整体handler成功 | 返回type=report的dict |
| SKC-15 | 整体handler异常 | 返回type=error的dict |

### 4.2 数据库测试

#### 4.2.1 模型测试 (`tests/db/test_models.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| DB-M-01 | User模型创建 | 所有字段正确存储 |
| DB-M-02 | Conversation模型创建 | messages JSON默认为list |
| DB-M-03 | Message模型创建 | extra_data JSON默认为dict |
| DB-M-04 | Project模型创建 | 各config JSON正确 |
| DB-M-05 | SkillConfig模型创建 | is_default默认为False |
| DB-M-06 | Alert模型创建 | status默认为ACTIVE |
| DB-M-07 | TimestampMixin自动填充 | created_at、updated_at有值 |

#### 4.2.2 CRUD测试 (`tests/db/test_crud.py`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| DB-C-01 | 创建对话 | 返回Conversation对象，id非空 |
| DB-C-02 | 获取对话-存在 | 正确返回Conversation |
| DB-C-03 | 获取对话-不存在 | 返回None |
| DB-C-04 | 获取对话-带user_id过滤 | 非本人对话返回None |
| DB-C-05 | 列��对话 | 按updated_at倒序 |
| DB-C-06 | 列��对话-分页 | limit/offset生效 |
| DB-C-07 | 更新对话标题 | 标题正确更新 |
| DB-C-08 | 删除对话-存在 | 返回True，数据删除 |
| DB-C-09 | 删除对话-不存在 | 返回False |
| DB-C-10 | 删除对话-级联 | 关联messages一并删除 |
| DB-C-11 | 创建消息 | 正确关联conversation |
| DB-C-12 | 获取消息 | 按created_at正序 |
| DB-C-13 | 获取消息-分页 | limit/offset生效 |
| DB-C-14 | JSON消息追加(legacy) | conversation.messages正确追加 |

#### 4.2.3 迁移测试 — ~~`tests/db/test_migrations.py`~~（未实施）

> 计划中 DB-MG-01~03（Alembic upgrade/downgrade）从未实现，当前无此测试文件。开发环境 SQLite 用 `create_all` 重建，
> 迁移用 `scripts/migrate_schema.py`。如需正式迁移回归测试可作为待办。

### 4.3 API集成测试

> **2026-08-10 全面重构**：所有 API 测试走**后端真实生产 HTTP 端点**（httpx AsyncClient / TestClient 调 `/api/...`），DB 隔离到临时文件 SQLite，预置真实 admin/test_user 用户（真实密码 hash + 真实登录链路），仅网络型外部服务（Jira/Gerrit/飞书/LLM）mock。测试基座见 `tests/conftest.py`。全量 **319 passed / 7 skipped**。

#### 4.3.1 Auth API (`tests/api/test_auth.py`，16 用例)

| 覆盖 | 说明 |
|------|------|
| login | 正确密码 / 错误密码 / 不存在用户（真实 DB 验证） |
| /me | 返回当前登录用户（DB） |
| register | 管理员创建 / 重复用户名 409 / 短密码 400 / 非 admin 403 |
| change-password | 正确旧密码 / 错误旧密码 400 / 短新密码 400 |
| 用户管理 | 用户列表 / 删除用户 / 删自己 400 / toggle-active / reset-password |

#### 4.3.2 Chat API (`tests/api/test_chat.py`，8 用例)

| 覆盖 | 说明 |
|------|------|
| conversations CRUD | 列表 / 详情 / 删除 / 不存在 404 |
| /message SSE | 流式返回 text + complete 事件；自动创建并持久化会话 |
| 认证 | 未认证请求 401 |

#### 4.3.3 Skills API (`tests/api/test_skills.py`，6 用例)

列表（真实 registry）/ 执行（execution_id+status+result）/ 未知技能 404 / 配置结构（default_config+user_override+merged_config）/ 认证 401

#### 4.3.4 Config API (`tests/api/test_config.py`，7 用例)

LLM 配置掩码 / MCP 服务器（预置 UserSetting，密码掩码 ***）/ project 配置 CRUD（roundtrip）/ system / 认证 401

#### 4.3.5 Dashboard API (`tests/api/test_dashboard.py`，9 用例)

配置 CRUD / CL 监控配置 / CL 抑制（添加-列表-恢复）/ summary + cl_status + project-issues（网络服务 mock，走真实端点）

#### 4.3.6 Alerts API (`tests/api/test_alerts.py`，8 用例)

列表 + level 过滤 / summary / 详情 / 不存在 404 / 状态更新 resolve / 非法状态 400 / manual scan（扫描服务 mock）

#### 4.3.7 Tasks API (`tests/api/test_tasks.py`，5 用例)

提交 + 轮询状态 / _user_id 注入（真实编排）/ 未知技能 404 / 未知任务返回 pending / cancel

#### 4.3.8 Trends API (`tests/api/test_trends.py`，5 用例)

列表空结构 / project+days 过滤透传 / summary

#### 4.3.9 Memory API (`tests/api/test_memory_api.py`，5 用例)

维护状态 / 手动蒸馏（LLM 阶段 mock）/ 蒸馏参数透传 / embedding backfill / backfill 失败 500

#### 4.3.10 Feishu Bind API (`tests/api/test_feishu_bind.py`，5 用例)

绑定状态（未绑定）/ 生成 6 位验证码 / 重复生成 / 未绑定解绑 400 / 验证码持久化到 DB

#### 4.3.11 Investigate API (`tests/api/test_investigate.py`，3 用例)

发起调查（agent mock，真实建会话）/ 带 context_data / agent error 响应

#### 4.3.12 Health API (`tests/api/test_health.py`，2 用例)

健康检查 / 无需认证

### 4.4 前端测试

> **2026-08-10 已落地服务层**：Vitest + jsdom 已配置（`frontend/vitest.config.ts`），`npm test` 运行。已实现 13 个服务测试（SSE 流式解析/认证流程）。组件与页面测试（下表）为待办。

#### 4.4.0 服务测试（已实现，`src/services/__tests__/`，13 用例）

| 文件 | 覆盖 |
|------|------|
| chatService.test.ts（7） | SSE 事件解析/跨 chunk 合并/`[DONE]` 忽略/末尾 flush/HTTP 错误/会话列表 |
| authService.test.ts（6） | 登录成功/失败错误消息/无 token 保护/改密请求体（带/不带旧密码） |

#### 4.4.1 组件测试（待办，`src/components/__tests__/`）

> 下表为规划用例（FE-COMP/FE-PAGE），作待办清单，未实现。

#### 4.4.1 组件测试 (`frontend/src/components/__tests__/`)

| 用例ID | 测试内容 | 工具 |
|--------|----------|------|
| FE-COMP-01 | JiraReportCard 渲染 | Vitest + React Testing Library |
| FE-COMP-02 | JiraReportCard 风险边框 | p1>0时边框红色 |
| FE-COMP-03 | ClMergeReportCard 渲染 | Vitest |
| FE-COMP-04 | ClMergeReportCard 未合并红色 | unmerged>0时数字红色 |
| FE-COMP-05 | ReportSummaryBadge 渲染 | 正确显示label和value |
| FE-COMP-06 | 风险标签映射 | unmerged_cl_blocking → 'P1阻塞未合并' |
| FE-COMP-07 | AppLayout 渲染 | 菜单项正确 |
| FE-COMP-08 | AppLayout 导航 | 点击菜单正确跳转 |

#### 4.4.2 页面测试 (`frontend/src/pages/__tests__/`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| FE-PAGE-01 | ChatPage 初始状态 | 显示欢迎消息 |
| FE-PAGE-02 | 发送消息 | 用户消息出现在列表右侧 |
| FE-PAGE-03 | thinking消息显示 | 显示加载动画+文字 |
| FE-PAGE-04 | thinking消息替换 | 新消息替换上一条thinking |
| FE-PAGE-05 | card消息渲染-Jira | 正确渲染JiraReportCard |
| FE-PAGE-06 | card消息渲染-CL | 正确渲染ClMergeReportCard |
| FE-PAGE-07 | error消息渲染 | 显示错误标签+红色文字 |
| FE-PAGE-08 | markdown渲染 | 代码块正确高亮 |
| FE-PAGE-09 | 新建对话 | 清空消息，显示欢迎 |
| FE-PAGE-10 | 选择对话 | 加载历史消息 |
| FE-PAGE-11 | 删除对话 | 从列表移除，切换到新对话 |
| FE-PAGE-12 | 回车发送 | Enter发送，Shift+Enter换行 |
| FE-PAGE-13 | 空消息不发送 | 按钮disabled |
| FE-PAGE-14 | 加载中禁用输入 | disabled=true |

#### 4.4.3 服务测试 (`frontend/src/services/__tests__/`)

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| FE-SVC-01 | sendMessage SSE解析 | 正确解析data:行 |
| FE-SVC-02 | sendMessage 多行数据 | 正确处理缓冲区 |
| FE-SVC-03 | sendMessageSync | 返回响应数组 |
| FE-SVC-04 | fetchConversations | 返回对话列表 |
| FE-SVC-05 | fetchConversation | 返回对话详情 |
| FE-SVC-06 | deleteConversation | 成功无返回值 |
| FE-SVC-07 | login | 存储token到localStorage |
| FE-SVC-08 | API 401错误 | 抛出Error |
| FE-SVC-09 | API 500错误 | 抛出Error |
| FE-SVC-10 | SSE解析异常数据 | 控制台报错，不崩溃 |

### 4.5 E2E测试

| 用例ID | 测试内容 | 场景 |
|--------|----------|------|
| E2E-01 | 完整聊天流程 | 登录 → 发送问候 → 收到回复 |
| E2E-02 | Skill执行流程 | 发送"汇总Jira" → thinking → card展示 |
| E2E-03 | 对话持久化 | 发送消息 → 刷新页面 → 消息仍在 |
| E2E-04 | 多对话管理 | 创建2个对话 → 切换 → 删除 |
| E2E-05 | 错误恢复 | 发送异常消息 → 显示错误 → 可继续聊天 |

### 4.6 部署脚本测试

| 用例ID | 测试内容 | 预期结果 |
|--------|----------|----------|
| DEP-01 | install.sh --dev | 创建venv，安装依赖，配置.env |
| DEP-02 | install.sh --prod | 构建前端，配置生产环境 |
| DEP-03 | install.sh --update | 更新现有部署 |
| DEP-04 | restart.sh dev | 启动后端(8000)+前端(3000)，PID文件正确 |
| DEP-05 | restart.sh dev SIGINT | trap清理，停止所有服务 |
| DEP-06 | restart.sh start | 启动生产服务，端口8000 |
| DEP-07 | restart.sh start --build | 强制重新构建前端 |
| DEP-08 | restart.sh stop PID文件 | 优先使用PID文件停止 |
| DEP-09 | restart.sh stop 进程名降级 | 无PID文件时按进程名匹配 |
| DEP-10 | restart.sh stop Docker | 停止docker-compose容器 |
| DEP-11 | docker-compose up | 所有服务健康启动 |
| DEP-12 | docker-compose healthcheck | postgres/redis健康检查通过 |

---

## 5. 测试实施计划

### 5.1 优先级矩阵

| 优先级 | 模块 | 原因 |
|--------|------|------|
| **P0** | Agent引擎(intent/planner/executor/engine) | 核心业务流程 |
| **P0** | Auth/Chat API | 用户入口 |
| **P0** | Skill适配器 | Phase 2核心交付 |
| **P1** | LLM客户端/路由 | 外部依赖，需Mock |
| **P1** | MCP客户端 | 外部依赖，需Mock |
| **P1** | 数据库CRUD | 数据持久化 |
| **P2** | 记忆系统 | 辅助功能 |
| **P2** | 前端组件 | UI展示 |
| **P2** | 部署脚本 | 运维 |
| **P3** | E2E | 完整流程验证 |

### 5.2 实施阶段

> 标注实施状态（2026-08-10 核对）：Phase 1-4 ✅ 已落地；Phase 5（前端）❌ 未实施；Phase 6（E2E/部署）⚠️ 未实施（test_executor 已随 executor 清理删除）。

```
Phase 1 (1-2天): 基础设施 + P0单元测试  ✅ 已实施
  ├── pytest 配置 (conftest.py, fixtures)
  ├── Mock LLM Client
  ├── Mock MCP Client
  ├── test_intent.py
  ├── test_planner.py
  ├── test_executor.py      ← 已删除（executor 清理，2026-06-23）
  └── test_engine.py        ← 实际已扩展：认知脚手架/ask_user/工具缓存/Gerrit批量

Phase 2 (1-2天): API集成测试  ✅ 已实施
  ├── test_auth.py / test_chat.py / test_skills.py / test_config.py / test_health.py

Phase 3 (1天): Skill适配器测试  ✅ 已实施
  ├── test_jira_report.py (Mock MCP)
  └── test_cl_monitor.py (Mock MCP)

Phase 4 (1天): 数据库 + 记忆系统  ✅ 已实施（另新增 tools/ 9 个测试 + e2e_format/table_format）
  ├── test_models.py / test_crud.py / test_system.py（记忆）

Phase 5 (1-2天): 前端测试  ❌ 未实施（待办）
  ├── Vitest 配置
  ├── 组件测试
  └── 服务测试

Phase 6 (1天): E2E + 部署测试  ❌ 未实施（待办）
  ├── Playwright/Cypress E2E
  └── 脚本测试
```

---

## 6. Mock策略

### 6.1 LLM Mock

```python
# tests/conftest.py 示例
import pytest
from unittest.mock import AsyncMock

class MockLLMClient:
    async def complete(self, *args, **kwargs):
        return '{"intent_category": "execute", "action": "summarize", "target_skill": "jira-confluence-report"}'
    
    async def complete_structured(self, *args, **kwargs):
        return {
            "intent_category": "execute",
            "action": "summarize",
            "target_skill": "jira-confluence-report",
            "confidence": 0.95,
        }

@pytest.fixture
def mock_llm_client(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.client.llm_client",
        MockLLMClient()
    )
```

### 6.2 MCP Mock

```python
@pytest.fixture
def mock_mcp_manager(monkeypatch):
    class MockMCPClient:
        def __init__(self):
            self._initialized = True
        
        async def initialize(self):
            return {"status": "ok"}
        
        async def call_tool(self, name, args):
            if name == "search_issues":
                return {"issues": [
                    {"key": "TV-12345", "fields": {"summary": "Test Issue"}}
                ]}
            elif name == "get_issue":
                return {"key": "TV-12345", "fields": {
                    "summary": "Test",
                    "status": "In Progress",
                    "priority": "P1",
                    "assignee": "user",
                    "duedate": "2026-05-30",
                    "updated": "2026-05-20T10:00:00Z",
                }}
            elif name == "create_page":
                return {"id": "12345", "title": "Test Report"}
            return {}
        
        async def close(self):
            pass

    class MockManager:
        def __init__(self):
            self.clients = {
                "jira": MockMCPClient(),
                "confluence": MockMCPClient(),
                "gerrit": MockMCPClient(),
            }
        
        def get_client(self, name):
            return self.clients[name]
        
        def get_available_servers(self):
            return list(self.clients.keys())
        
        async def initialize_all(self):
            pass
        
        async def close_all(self):
            pass

    monkeypatch.setattr("app.mcp.client.mcp_manager", MockManager())
```

> **⚠️ 更新（2026-08-10）**：2026-08-01 MCP 统一接口层重构后，业务代码不再直接使用 `mcp_manager`，统一走
> `app/mcp/capability.py` 的 `MCPCapabilityService`（get_servers/get_tool_definitions/call 按用户懒加载客户端）。
> 新测试的 MCP mock 应 patch `MCPCapabilityService` 的 `call()`/`get_servers()`，而非 `mcp_manager`。conftest 现有
> `MockMCPManager` 亦待同步（其内部还硬编码旧 Gerrit 服务器名 `gerrit_gerrit2`，标准命名应为 `gerrit_{hostname}`）。

### 6.3 数据库 Mock

```python
@pytest.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.base import Base
    
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
```

### 6.4 FastAPI TestClient

```python
@pytest.fixture
def client(mock_llm_client, mock_mcp_manager):
    from fastapi.testclient import TestClient
    from app.main import app
    
    # Override dependencies if needed
    return TestClient(app)
```

---

## 7. 测试文件结构（与实际一致，2026-08-10 核对）

```
backend/tests/
├── conftest.py              # 真实 HTTP 基座：临时SQLite + 预置 admin/test_user
│                            #   + 认证链覆盖 + 全局 AsyncSessionLocal 隔离生产库
├── __init__.py
├── test_e2e_format.py / test_table_format.py
├── core/   test_config / test_security / test_exceptions
├── agent/  test_intent / test_planner / test_engine（单脑引擎+认知脚手架）
├── llm/    test_client / test_router
├── memory/ test_system（DB-backed）
├── mcp/    test_client（MCPManager 懒加载）
├── skills/ test_jira_report（subprocess 适配）/ test_cl_monitor
├── db/     test_models（已修复，去 Project）/ test_crud
├── tools/  test_docx/pdf/pptx/xlsx/image/registry/upload/upload_and_summarize/jira_create_e2e
│           test_gerrit_tool（新增）/ test_confluence_tool（新增）
└── api/    test_auth(16)/chat(8)/skills(6)/config(7)/health(2)
            test_dashboard(9)/alerts(8)/tasks(5)/trends(5)
            test_memory_api(5)/feishu_bind(5)/investigate(3)
            test_e2e_scenarios（★真实引擎端到端：jira查询/skill执行/cl查询/confluence读取）

frontend/src/services/__tests__/    # ✅ 已落地（Vitest，13 用例）
├── chatService.test.ts        # SSE 流式解析 / 跨 chunk / [DONE] / 会话列表
└── authService.test.ts        # 登录 / 未登录保护 / 改密请求体
frontend/src/components|pages/__tests__/   # （规划，待办：组件/页面渲染测试）
```

---

## 8. 关键风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| LLM调用不可控 | 测试不稳定、成本高 | 全面Mock LLMClient，仅集成测试使用真实LLM |
| MCP服务器不可用 | Skill测试失败 | Mock MCPManager，提供固定返回数据 |
| 异步代码测试复杂 | 测试编写困难 | 使用pytest-asyncio，async/await fixture |
| 数据库状态污染 | 测试间干扰 | 每个测试独立内存DB，或事务回滚 |
| SSE流式测试困难 | Chat API难测 | 使用同步端点(/message/sync)辅助测试 |
| 前端SSE解析 | 测试环境差异 | Mock fetch + ReadableStream |

---

## 9. 质量门禁

| 指标 | 目标 | 说明 |
|------|------|------|
| 后端单元测试覆盖率 | ≥ 70% | 核心模块 ≥ 90% |
| API集成测试覆盖率 | ≥ 60% | 所有API端点至少1个测试 |
| 前端组件测试覆盖率 | ≥ 50% | 关键组件 ≥ 80% |
| 关键路径(P0)覆盖率 | ≥ 90% | Agent引擎、API、Skill |
| 测试通过率 | 100% | 所有测试必须通过 |
| 测试执行时间 | < 5分钟 | 单元测试 < 2分钟 |

---

## 10. 执行命令

### 10.1 后端测试

```bash
cd /home/user/share/ProjectAgent/backend
source venv/bin/activate

# 运行所有测试
pytest

# 详细输出
pytest -xvs

# 仅运行单元测试（跳过集成）
pytest -m "not integration"

# 仅运行集成测试
pytest -m "integration"

# 覆盖率报告
pytest --cov=app --cov-report=term-missing
pytest --cov=app --cov-report=html

# 仅运行特定模块
pytest tests/agent/test_intent.py
pytest tests/agent/ -v

# 并行执行
pytest -n auto
```

### 10.2 前端测试

> **2026-08-10 已落地**：Vitest + jsdom 已配置（`vitest.config.ts`），`package.json` 有 `test` 脚本。

```bash
cd /home/user/share/ProjectAgent/frontend

# 运行全部前端测试（服务层 13 用例）
npm test

# 仅运行特定文件
npx vitest run src/services/__tests__/chatService.test.ts
```

### 10.3 端到端场景测试（后端真实引擎）

```bash
cd /home/user/share/ProjectAgent/backend && source venv/bin/activate

# 端到端：web chat 查 jira / 执行 skill / 查 cl / 读 confluence（mock 外部系统）
pytest tests/api/test_e2e_scenarios.py -v
```

### 10.4 部署冒烟测试（真实服务）

```bash
# 对运行中的后端服务做冒烟（health → 登录 → 关键 API）
bash /home/user/share/ProjectAgent/scripts/smoke_test.sh
bash /home/user/share/ProjectAgent/scripts/smoke_test.sh http://host:8000 user pass
```

### 10.3 API手动测试

```bash
# 健康检查
curl http://localhost:8000/health

# 登录
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 发送消息（同步）
curl -X POST http://localhost:8000/api/chat/message/sync \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message":"你好"}'

# 获取对话列表
curl http://localhost:8000/api/chat/conversations \
  -H "Authorization: Bearer <token>"

# 获取Skill列表
curl http://localhost:8000/api/skills \
  -H "Authorization: Bearer <token>"

# 获取配置
curl http://localhost:8000/api/config/llm \
  -H "Authorization: Bearer <token>"
```

---

## 11. 附录：核心代码文件清单

| 文件路径 | 说明 | 测试优先级 |
|----------|------|-----------|
| `backend/app/core/config.py` | 应用配置 | P1 |
| `backend/app/core/security.py` | JWT/密码 | P1 |
| `backend/app/core/exceptions.py` | 自定义异常 | P2 |
| `backend/app/api/auth.py` | 认证API | P0 |
| `backend/app/api/chat.py` | 聊天API | P0 |
| `backend/app/api/skills.py` | Skill API | P0 |
| `backend/app/api/config.py` | 配置API | P1 |
| `backend/app/api/deps.py` | 依赖注入 | P1 |
| `backend/app/services/agent/intent.py` | 意图理解 | P0 |
| `backend/app/services/agent/planner.py` | 任务规划 | P0 |
| `backend/app/services/agent/engine.py` | Agent主引擎（单脑循环+认知脚手架） | P0 |
| `backend/app/services/agent/executor.py` | 动作执行（仅剩 _execute_skill/_execute_tool） | P1 |
| `backend/app/services/llm/client.py` | LLM客户端 | P1 |
| `backend/app/services/llm/router.py` | LLM路由 | P1 |
| `backend/app/services/memory/*.py` | 记忆系统（system/search/daily_logger） | P2 |
| `backend/app/mcp/capability.py` | MCP 统一入口（capability） | P0 |
| `backend/app/mcp/client.py` | MCP 客户端 | P1 |
| `backend/app/services/dashboard_service.py` | 仪表盘聚合 | P1 |
| `backend/app/services/trend_service.py` | 趋势快照 | P2 |
| `backend/app/services/alert_service.py` | 告警规则引擎 | P1 |
| `backend/app/services/cl_*_service.py` | CL 监控（快照/抑制/自动提醒/持续跟踪） | P1 |
| `backend/app/services/gerrit_service.py` | Gerrit 查询封装 | P1 |
| `backend/app/tools/*.py` | 内置文档工具库 | P2 |
| `backend/app/skills/jira_report_skill.py` | Jira报告Skill | P0 |
| `backend/app/skills/jira_cl_monitor_skill.py` | CL监控Skill | P0 |
| `backend/app/models/*.py` | 数据模型（15 个） | P1 |
| `backend/app/crud/conversation.py` | 对话CRUD | P1 |
| `backend/app/db/session.py` | 数据库会话 | P1 |
| `frontend/src/pages/ChatPage.tsx` | 聊天页面 | P1 |
| `frontend/src/services/chatService.ts` | 聊天服务（SSE 解析） | P1 |
| `frontend/src/pages/DashboardPage.tsx` | 仪表盘页面 | P2 |
| `frontend/src/pages/ClMonitoringPage.tsx` | CL 监控页面 | P2 |
