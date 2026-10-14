# CL 监控 V2 方案 —— 追踪队列 + 状态驱动

> **状态**：待确认（2026-09-16 讨论定稿，未动代码）
> **范围**：`backend/app/services/cl_*.py`、`gerrit_service.py`、`dashboard_service.py`、`api/dashboard.py`、`frontend/src/pages/ClMonitoringPage.tsx`、`chatService.ts`
> **取代**：`cl_monitor_snapshots` 的"快照 diff"模型、`cl_verified_tracking` 与 `cl_reminders` 两份 JSON

---

## 一、背景：为什么要重构

2026-09-16 排查发现 TV-XXXXXX / TV-XXXXXX / TV-XXXXXX / TV-XXXXXX 四个题长期未被监控，根因如下：

| # | 根因 | 位置 |
|---|---|---|
| 1 | 配置的 `advanced_jql` 被静默忽略，实际生效的是隐藏的 `labels` 条件 | `dashboard_service._build_cl_jql` |
| 2 | 快照只收录"评论里有 Gerrit 链接"的题，其余题根本不进监控视野 | `gerrit_service.get_cl_status_for_issues` |
| 3 | 跨 Jira 的 CL 去重键缺 `jira_key`，两题共用同一 CL 时后一题整组消失 | `gerrit_service.py:136` |
| 4 | CL URL 正则只认 `.../c/<project>/+/<id>`，漏掉 gerrit1 短链 | `gerrit_service.py:16` |
| 5 | 只按"当前是 Resolved/Verified"筛选，无法感知状态迁移过程 | 整体模型 |

前 4 条是缺陷（本次一并修），第 5 条是模型问题 —— 由此引出 V2。

---

## 二、需求（用户确认版）

**核心诉求**：确保"问题已解决且验证通过"的 Jira，其对应的代码修改 CL 不会被遗忘；同时确保 Jira 按 `Resolved → Verified → Closed` 流程正常推进。

**规则表**

| Jira 状态 | CL 情况 | Jira 评论 @owner | 频率 | PM summary | 队列 |
|---|---|---|---|---|---|
| **Resolved** | 任意 | 「已进入 Resolved，请及时走验证流程」 | 每天 1 次 | ✅ | 留 |
| **Verified** | 未合并 / 未登记 | 「已处于 Verified，请确保 CL 已经提交并最终合并」 | 每天 1 次 | ✅ | 留 |
| **Verified** | 全部已合并 | 不提醒 | — | ✅ | 留（跟踪到 Closed） |
| **Closed** | 未合并 / 未登记 | **不评论 owner** | — | ✅ | 留 |
| **Closed** | 全部已合并 | 不提醒 | — | — | **退出队列** |

**入队**：配置的 JQL 命中的题全部入队（首轮命中的 + 之后新命中的）。入队后**只进不出**，直到满足退出条件。

**消失即复查**：队列里的题若不在本轮 JQL 结果中，逐个 `get_issue` 复查真实状态 —— 确认是 Closed 则走 Closed 规则；若"非 Resolved/Verified 直接跳 Closed"，同样按 Closed 规则处理。

**首轮**：已经是 Closed 的历史记录，跳过 CL 确认，标记为「已归档」。

---

## 三、核心设计

### 3.1 数据模型：`cl_jira_states`（追踪队列）

**一题一行**，替代 `cl_verified_tracking`（UserSetting JSON）+ `cl_reminders`（UserSetting JSON）。

```sql
CREATE TABLE cl_jira_states (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    project_name        VARCHAR NOT NULL,      -- 配置的监控项目名
    issue_key           VARCHAR NOT NULL,
    summary             VARCHAR DEFAULT '',
    assignee            VARCHAR DEFAULT '',

    -- ① 状态跟踪（变化检测的"上一状态"在这里）
    status              VARCHAR DEFAULT '',    -- 本轮看到的真实状态
    prev_status         VARCHAR DEFAULT '',    -- 上一轮看到的真实状态
    status_changed_at   DATETIME,              -- 状态最近变化时间

    -- ② 追踪队列
    queued              BOOLEAN DEFAULT 1,     -- 是否在队列（退出=0）
    queue_reason        VARCHAR DEFAULT '',    -- initial/new/transition/skip_to_closed/migrated
    archived            BOOLEAN DEFAULT 0,     -- 首轮历史 Closed，跳过 CL 确认
    exited_at           DATETIME,              -- 退出队列时间
    fail_count          INTEGER DEFAULT 0,     -- get_issue 连续失败计数

    -- ③ CL 确认结果
    cl_total            INTEGER DEFAULT 0,
    cl_merged           INTEGER DEFAULT 0,
    cl_unmerged         INTEGER DEFAULT 0,
    cl_noregister       BOOLEAN DEFAULT 0,     -- 无任何 CL 登记
    cl_checked_at       DATETIME,
    cl_items_json       TEXT DEFAULT '[]',     -- CL 明细
    issue_updated       VARCHAR DEFAULT '',    -- Jira updated 快照，用于跳过重复查询

    -- ④ 提醒
    last_reminded_at    DATETIME,
    last_reminded_status VARCHAR DEFAULT '',

    first_seen_at       DATETIME,
    last_seen_at        DATETIME,
    created_at          DATETIME,
    updated_at          DATETIME,

    CONSTRAINT uq_cl_jira_states UNIQUE (user_id, issue_key)
);
CREATE INDEX ix_cl_jira_states_queue ON cl_jira_states (user_id, queued, status);
```

**理由**：状态 diff、提醒去重时间戳、CL 缓存三处状态原本散在 3 个地方（快照 JSON / `cl_verified_tracking` / `cl_reminders`），合并到一张表后是**净减少**复杂度。新表走 `create_all` 自动建，生产库（不用 alembic）零迁移脚本。规模：今天的口径 ~168 行/用户，即使 JQL 放宽到全项目也仅 ~1300 行。

### 3.2 每日扫描流程

```
run_daily_scan(user_id):
  for project in 配置启用的项目:
    ① 取当前集合
       jql = advanced_jql（有值则原样使用，绝不追加/回退）
       search_issues(jql, fields=["key","status","summary","assignee","updated"])
         ⚠️ maxResults 实测无效，返回全部命中项
         ⚠️ fields 不能只传 ["key"]（MCP 报 'Issue' object is not subscriptable）

    ② 与队列 diff（cl_jira_states）
       新 key      → queued=1, queue_reason='initial'|'new'
                     若 status 已是 Closed → archived=1（跳过 CL 确认）
       已有 key:
         status != prev_status → prev_status=旧值, status=新值,
                                 status_changed_at=now, queue_reason='transition'
                                 若 archived=1 → 解除归档（唤醒）
         status == prev_status → 仅更新 summary/assignee/last_seen_at

    ③ 消失即复查
       队列中 queued=1 但不在本轮结果的 key → 逐个 get_issue
         是 Closed            → 走 Closed 规则，保持 queued
         是其他（Reopened 等）→ 回到正常跟踪
         查询失败             → fail_count += 1，≥3 次标记 archived

    ④ CL 确认（只对 queued=1 且 archived=0 的题）
       确认集合 = 本轮状态变化的题 ∪ 当前 Resolved/Verified 的题 ∪ 新入队的题
       对每条：
         若 issue.updated 与 issue_updated 相同且已有 cl_checked_at
             → 复用上次 CL 结果（省掉一整轮 Gerrit 查询）
         否则 get_issue_comments → 解析 CL → 查合并状态 → 写回
             （评论或 CL 状态有变化 → 清空 last_reminded_at，重新武装提醒）

    ⑤ 提醒
       Resolved                       → 每天 1 次 @owner「请及时走验证流程」
       Verified 且 (未合并 或 未登记)  → 每天 1 次 @owner「请确保 CL 已提交并最终合并」
       Verified 且 全部已合并          → 不发
       Closed 且 (未合并 或 未登记)    → 不评论，进 PM summary
       Closed 且 全部已合并            → queued=0, exited_at=now（退出队列）

    ⑥ 刷新页面缓存快照
```

**成本控制**：焦点过滤发生在"查 CL"之前 —— 每天只对 ~55 个在途题拉评论查 Gerrit，而不是 168 或 1279 个。

### 3.3 CL 解析改造

统一改造为 `extract_cls_from_comments(comments, jira_key) -> List[ClItem]`：

| 来源形式 | 处理 |
|---|---|
| `https://gerrit-2.example.com/c/<project>/+/<id>` | 保留，走 Gerrit MCP 查真实状态 |
| `Change proposed: '<subject>' (<project> <branch>) \| https://gerrit-1.example.com/<id>` | 新增：登记为 gerrit1 CL（状态待定） |
| `Change merged from https://gerrit-1.example.com/<id> to <url>` | 新增：**直接判定该 CL 已合并**（无需查 Gerrit） |

> 实测：`https://gerrit-1.example.com/<id>` 不是 Gerrit permalink（`change:<id>` 查询返回 0 条），但 Jira 机器人评论成对记录了"提交"与"合并"两个事件，解析评论即可确定合并状态，比走 MCP 更快更稳。

**去重键** 由 `(project, change_id)` 改为 `(jira_key, instance, project, change_id)`。

### 3.4 已确认的边界行为

| 场景 | 行为 |
|---|---|
| 首次入队即 Closed（历史记录） | 跳过 CL 确认，`archived=1`，不进 PM summary |
| 已归档题重新活跃（状态变化） | 解除归档，补做 CL 确认 |
| 同日走完 `Resolved → Verified` | 只按最高状态（Verified）提醒一次（日频采样的固有限制） |
| JQL 变更导致旧题不再命中 | 保留在队列（"只进不出"语义），不做处理 |
| 题被 Reopened 回到非焦点状态 | 保留在队列，CL 全部合并后仍跟踪至 Closed |

---

## 四、模块变更清单

### 4.1 新增

| 文件 | 内容 |
|---|---|
| `app/models/cl_jira_state.py` | `ClJiraState` ORM 模型 |
| `app/services/cl_scan_service.py` | `run_daily_scan(user_id)` + 队列 diff + 提醒编排 |
| `app/services/cl_queue_service.py` | 队列 CRUD / 查询（焦点集合、PM summary 数据源） |
| `backend/tests/services/test_cl_jira_state.py` | 状态机与扫描用例 |
| `backend/tests/services/test_gerrit_parsing.py` | CL 解析用例 |

### 4.2 改造

| 文件 | 变更 |
|---|---|
| `services/gerrit_service.py` | 去重键 +jira_key；CL 正则扩展；`MERGED_STATUSES` 兼容中文；统一 `fields` 参数 |
| `services/dashboard_service.py` | `_build_cl_jql` → **`advanced_jql` 有值则原样使用**，删除 `cl_status_filter` 拼装 |
| `services/cl_snapshot_service.py` | 快照改为从 `cl_jira_states` 组装（保留 `_queried_at` 契约供前端） |
| `services/cl_auto_notify_service.py` | 提醒规则换成 3.2-⑤ 的表；`cl_reminders` 改为 `last_reminded_at`（每天 1 次） |
| `services/cl_tracking_service.py` | **删除**，职责并入 `cl_scan_service` |
| `services/cl_suppression_service.py` | 保留，语义不变 |
| `api/dashboard.py` | `cl_status` 删 `status_filter` 参数；新增 `cl_focus_summary` 端点 |
| `schemas/` | `ClMonitoringProject` 删 `cl_status_filter` |

### 4.3 配置结构（前后一致）

```json
{
  "name": "R+长虹T6d",
  "enabled": true,
  "cl_monitoring_enabled": true,
  "advanced_jql": "status not in (Closed) AND \"Project ID\" = \"PROJECTX-CHIPX4\" AND issuetype not in (Epic)",
  "jira_project_key": "",       // 基本模式字段，二选一
  "labels": "",                 // 基本模式字段，二选一
  "epics": ""                   // 基本模式字段，二选一
}
```

**二选一规则**：`advanced_jql` 有值 → 只用它；为空 → 用 `jira_project_key`/`labels`/`epics` 拼装。**不再有交叉回退**。

---

## 五、前端变更

### 5.1 配置卡片（`ProjectConfigCard`）

- 删除「CL 状态过滤（逗号分隔）」字段
- 模式切换时**清空另一侧字段**（修掉本次踩坑的根源：UI 显示 JQL 模式、实际生效的是隐藏 label）
- JQL 输入框加说明：「完整 JQL，系统不再追加任何状态条件」

### 5.2 主表格

新增列：`上次状态` / `状态变更时间` / `CL 状态`（已合并·未合并·未登记）/ `是否已提醒` / `入队原因`

### 5.3 新增「重点监控 summary」区块（PM review 用）

按状态分 Tab：`Resolved` / `Verified` / `Closed 但 CL 未合并` / `未登记 CL`。每行：Jira 链接、概要、Owner、当前状态、CL 明细、停滞天数。支持导出。

### 5.4 加载行为

页面加载**只读缓存**；只有点击「刷新」才触发实时扫描（与后端每日扫描解耦）。

---

## 六、存量数据迁移

| 数据 | 处理 |
|---|---|
| `cl_monitor_snapshots`（88 条） | 保留为历史，不再写入。新快照从 `cl_jira_states` 组装 |
| `cl_verified_tracking`（3 条） | 迁入 `cl_jira_states`，`queued=1, queue_reason='migrated'` |
| `cl_reminders` | 迁移 `last_reminded_at`（避免上线当天重复轰炸），迁移后 UserSetting 保留只读 |
| `cl_suppressed`（53 条） | 保留，语义不变 |
| `cl_rd_scan` | 保留（RD 回复 LLM 判断逻辑不动） |

**首轮扫描**：只对"在途"的题（若 JQL 为 `status not in (Closed)` 则约 168 个）入队并做 CL 确认；历史 Closed 只在 JQL 显式包含它们时才入队，且 `archived=1` 跳过 CL 确认。

---

## 七、测试用例清单

> 按 CLAUDE.md 测试纪律：API 变动同步更新用例；有 HTTP 端点的模块走后端真实生产 API。

**`tests/services/test_cl_jira_state.py`（新增）**

1. JQL 原样执行 —— 断言传给 MCP 的 `jql` 与配置值逐字相同，未追加任何 status 条件
2. `advanced_jql` 有值时不回退到 labels/epics
3. 新题入队：首见 Resolved → `queued=1`
4. 状态变化检测：`Resolved → Verified` 正确写 `prev_status`
5. 消失即复查：不在 JQL 结果的题 `get_issue` 返回 Closed → 走 Closed 规则
6. 跳过验证直接 Closed（非 Resolved → Closed）→ 留队列 + 进 PM summary
7. `Closed` + CL 全合并 → `queued=0`，退出队列
8. `Closed` + CL 未合并 → 留队列，**不**评论 owner
9. 未登记 CL → 提醒文案走"未登记"分支
10. 每天 1 次：同一天重复扫描不重复发评论
11. 首轮历史 Closed → `archived=1`，不触发 CL 确认
12. `issue.updated` 未变时跳过评论重拉（复用上次 CL 结果）
13. 传给 MCP 的 `fields` 不止含 `key`

**`tests/services/test_gerrit_parsing.py`（新增/改造）**

14. 同一 CL 被两个 Jira 引用 → 两个 Jira 都出现（去重键修复回归测试）
15. gerrit1 短链 `Change proposed` 解析出 CL
16. `Change merged from ...` 判定为已合并
17. 中文状态「已合并」计入 merged

**`tests/api/test_cl_monitoring.py`（新增）**

18. `GET /api/dashboard/cl_status` 返回新字段（`prev_status`/`focus_reason`/`cl_unregistered`）
19. `PUT /api/dashboard/cl_monitoring_config` 不再接受 `cl_status_filter`
20. `GET /api/dashboard/cl_focus_summary` 返回按状态分组的 summary

**回归**：`tests/services/test_cl_snapshot_service.py`、`test_cl_auto_notify.py` 按新语义改写；`cl_tracking_service` 相关用例随模块删除。

---

## 八、上线灰度顺序

1. **修 4 个既有缺陷**（去重键 / 正则 / fields / MERGED_STATUSES）—— 独立可验证，先行合并
2. **建表 + 服务层**（`cl_jira_states` + `cl_queue_service`）—— 不影响现有页面
3. **接入每日扫描**，加 `dry_run` 开关：扫描与 summary 照常，**评论只写日志不发 Jira**
4. **用户 1 单独 dry-run 观察 2~3 天**，核对：入队数量、状态变化识别、CL 合并判定、汇总列表是否漏项
5. dry-run 无误 → 关闭 dry_run，开启真实评论
6. 前端改造 + 旧快照链路下线 + 删 `cl_tracking_service`

---

## 九、风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| 日频采样丢中间态 | 同日走完 `Resolved→Verified` 只提醒一次 | 已确认接受；如需精确需提高频率或读 Jira changelog（MCP 未暴露） |
| 提醒量 | 30 个长期未合并题 = 30 条/天 Jira 评论 | 间隔可配（默认 1 天）；PM summary 区块降低对评论的依赖 |
| `maxResults` 失效 | JQL 返回多少就多少，无法分页 | JQL 范围控制是唯一手段；配置页提示"JQL 请自行限定范围" |
| 首次全量成本 | 168 题 × `get_issue_comments`，实测内网单次 15~50s | 放凌晨低峰；首轮不含历史 Closed |
| Gerrit 查询成本 | 每 CL 一次 `get_change` | 只对焦点题查；`updated` 未变的题复用上次结果 |
| 多用户 | 目前仅 admin 配置了 CL 监控 | 维持 per-user；新用户未配置时页面显示引导文案 |
