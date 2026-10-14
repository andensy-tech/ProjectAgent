# Phase 10: 下一代仪表盘 — 超越 Jira 原生 Dashboard
> **状态**：Phase A/B/C 已实施；Phase D 后端已完成、前端按钮未接线；Phase E/F 待实施（可与 monitoring 方案合并推进）。

## Context

Dashboard 的基础功能（Jira 指标卡片 + 项目列表 + 配置弹窗 + 30s 自动刷新）已上线。但用户提出的核心问题是：**我们的 Dashboard 和 Jira 系统自带的 Dashboard 有什么区别？优势在哪里？**

Jira 原生 Dashboard 能做：自定义 Gadget、JQL 过滤、图表展示、分享给团队。

我们比 Jira 强的关键在于：
1. **跨系统聚合** — Jira 看不到 Gerrit CL 状态、Confluence 文档关联
2. **主动风险检测** — 不只是展示数字，自动分析风险并推送告警
3. **趋势可视化** — Jira Dashboard 是静态快照，我们能追踪指标变化趋势
4. **AI 深度分析** — 每个数字都可以"点一下问 AI"，Jira 做不到

## 核心差异化策略

```
Jira Dashboard:          Project Agent Dashboard:
┌─────────────────┐     ┌─────────────────────────────────────┐
│ Jira-only 数据    │     │ Jira + Gerrit + Confluence 聚合      │
│ 静态 Gadget      │     │ 趋势图 + 风险告警 + AI 分析           │
│ 被动展示         │     │ 主动推送异常 + 可交互深度分析           │
│ 只回答"是什么"   │     │ 回答"为什么" + "怎么办"               │
└─────────────────┘     └─────────────────────────────────────┘
```

## 分阶段实施计划

推荐执行顺序：**A → B → C → D → E → F**，每个阶段独立交付价值。

---

## Phase A: 跨系统聚合 — Gerrit CL 状态接入

**为什么眼前一亮**：Jira Dashboard 永远看不到 Gerrit CL 状态。这个仪表盘能告诉你"Jira 状态是 Verified，但 CL 还没 merge"——这是项目经理最痛的真实场景。

### 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/services/gerrit_service.py` | **新增** | Gerrit CL 查询服务，封装 MCP 调用 |
| `backend/app/services/dashboard_service.py` | 修改 | summary 中加入 CL 字段 |
| `frontend/src/pages/DashboardPage.tsx` | 修改 | MetricCard 替换硬编码 0，ProjectCard 加 CL 状态区 |
| `frontend/src/services/chatService.ts` | 修改 | DashboardProject 加 cl_* 字段 |
| `frontend/src/components/Dashboard/ClStatusCard.tsx` | **新增** | CL 状态展示组件 |

### 后端：gerrit_service.py

```python
async def get_cl_status_for_project(
    issues: List[Dict],  # 项目的 Jira issue 列表
    gerrit_clients: Dict[str, MCPClient],
    max_concurrent: int = 5
) -> Dict:
    """提取每个 issue 的 CL 引用 → 查询 Gerrit 状态 → 聚合结果。
    
    逻辑从 jira_cl_monitor_skill.py 提取：
    1. 对每个 issue 调用 get_issue_comments
    2. 从 comments 中正则提取 CL URL / Change-Id
    3. 调用 gerrit.get_change() 查状态
    4. 聚合: total_cls, merged_cls, unmerged_cls, items[]
    """
```

关键设计：
- **错误隔离**：Gerrit 查询失败不影响 Jira 数据展示，只标记 CL 状态为"查询失败"
- **并发控制**：asyncio.Semaphore(max_concurrent) 防止 Gerrit 过载
- **缓存**：复用 dashboard 10s 缓存，不增加额外缓存层
- **复用现有逻辑**：`backend/app/skills/jira_cl_monitor_skill.py` 已有 CL 提取正则 + Gerrit 查询，提取为服务层共享

### 后端：dashboard_service.py 变更

在 `get_dashboard_summary()` 完成 Jira 查询后，追加 Gerrit CL 查询批次：
```python
# 对每个有 issue 的项目，并行查 Gerrit CL 状态
cl_coros = []
for p in project_results:
    if p.get("epic_issues"):
        cl_coros.append(gerrit_service.get_cl_status_for_project(
            p["epic_issues"], gerrit_clients
        ))
    else:
        cl_coros.append(asyncio.sleep(0, result={...}))  # 空结果

cl_results = await asyncio.gather(*cl_coros, return_exceptions=True)
# 合并 cl_* 字段到对应的 project_results
```

### 前端：DashboardPage 变更

- **MetricCard**：`label="未合入 CL" value={summary.total.unmerged_cls ?? 0}` → 红色当 > 0
- **ProjectCard**：新增 CL 状态行，显示 "CL: 3 未合并 / 5 总计"，可展开看 CL 详情列表（Change-Id、项目、状态）
- **颜色语义**：`unmerged > 0` = 红色，`merged === total && total > 0` = 绿色，`no data` = 灰色

### 关键可复用代码

`jira_cl_monitor_skill.py` 中需要提取到 `gerrit_service.py` 的逻辑：
- CL URL 正则匹配模式（约第 250 行）
- Change-Id 提取逻辑
- Gerrit `get_change` 调用 + 状态解析
- 多 Gerrit 实例路由逻辑（先查 gerrit2 再查 gerrit1）

### 复杂度：M

---

## Phase B: 主动风险告警系统

**为什么眼前一亮**：Jira Dashboard 被动展示，用户自己去找问题。这个仪表盘主动告诉你"P1 问题已 7 天未更新"、"CL 堆积超过 3 个"——从看见到发现。

### 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/services/alert_service.py` | **新增** | 告警 CRUD + 规则引擎 |
| `backend/app/api/alerts.py` | **新增** | 告警 REST API |
| `backend/app/tasks/alert_scanner.py` | **新增** | 定时扫描任务 |
| `frontend/src/pages/AlertsPage.tsx` | **新增** | 告警列表页面 |
| `frontend/src/components/Dashboard/AlertBadge.tsx` | **新增** | 仪表盘告警小部件 |
| `backend/app/api/__init__.py` | 修改 | 注册 alerts router |
| `backend/app/main.py` | 修改 | lifespan 启动扫描任务 |
| `frontend/src/App.tsx` | 修改 | 添加 `/alerts` 路由 |
| `frontend/src/components/Layout/AppLayout.tsx` | 修改 | 侧边栏告警数 badge |
| `frontend/src/services/chatService.ts` | 修改 | 告警 API 函数 |
| `frontend/src/pages/DashboardPage.tsx` | 修改 | 集成 AlertBadge 小部件 |

### 后端：alert_service.py

```python
# CRUD
async def list_alerts(filters) -> List[Alert]
async def get_alert(id) -> Alert
async def update_alert_status(id, status, note) -> Alert
async def get_alert_summary() -> Dict  # 各状态/级别计数

# 规则引擎 - 输入 dashboard summary，输出新告警
async def generate_alerts(summary: Dict) -> List[int]:
    """运行预定义规则集合，对每个项目检查：
    
    规则 R1 - P1_STALE: P1 > 0 且 stale > 0 → HIGH, PROGRESS
    规则 R2 - STALE_PILEUP: stale > 5 (可配置) → MEDIUM, PROGRESS  
    规则 R3 - CL_BOTTLENECK: unmerged_cls > 3 → MEDIUM, QUALITY
    规则 R4 - ETA_EXPIRING: eta_expiring > 3 → LOW, SCOPE
    规则 R5 - EPIC_CONVERGENCE: epic 下 issue 多但 CL 少 → MEDIUM, PROGRESS
    
    去重: 相同 rule_id + project_id 已有 ACTIVE 告警时跳过
    相同告警已 RESOLVED 但有新证据 (count 变化) → 重新激活
    """
    # 返回创建的告警 ID 列表
```

### 后端：alert_scanner.py

```python
async def run_alert_scan():
    """后台扫描任务，每 5 分钟运行一次。
    
    1. 调用 dashboard_service.get_dashboard_summary() (绕过缓存)
    2. 调用 alert_service.generate_alerts(summary)
    3. 日志记录本次扫描创建/跳过的告警数
    4. 异常只记日志，不中断扫描循环
    """
```

启动方式：在 `main.py` 的 lifespan 中 `asyncio.create_task(periodic_scan())`，使用 `asyncio.sleep(300)` 循环。

### 后端：api/alerts.py

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/alerts` | GET | 列表，支持 `status/level/category/project_id/limit/offset` 过滤 |
| `/api/alerts/{id}` | GET | 单条详情 |
| `/api/alerts/{id}/status` | PATCH | 更新状态，body: `{status, resolution_note?}` |
| `/api/alerts/scan` | POST | 手动触发一次扫描，返回 `{created: N}` |
| `/api/alerts/summary` | GET | 聚合统计：各状态计数、各级别计数、各分类计数 |

所有接口需 `Depends(get_current_active_user)` 鉴权。

### 前端：AlertsPage.tsx

```
┌──────────────────────────────────────────────────────┐
│ 预警中心                        [手动扫描] [全部标记已读] │
│                                                        │
│ [全部] [待处理 N] [已确认] [已解决] [已忽略]             │
│                                                        │
│ ┌────┬──────────┬──────────────┬──────────┬────────┐  │
│ │ 级别 │ 规则      │ 标题          │ 项目      │ 时间    │  │
│ ├────┼──────────┼──────────────┼──────────┼────────┤  │
│ │ 🔴 │ P1逾期    │ P1问题7天未更新│ 长虹T6D   │ 10:30  │  │
│ │ 🟡 │ CL堆积    │ 5个CL未合并    │ 长虹T6D   │ 10:25  │  │
│ └────┴──────────┴──────────────┴──────────┴────────┘  │
│                                                        │
│ 展开详情：                                              │
│ ├ 描述: 项目有3个P1问题超过7天未更新                    │
│ ├ 影响: 可能导致Sprint目标无法达成                      │
│ ├ 建议: 检查阻塞原因，安排Code Review资源              │
│ └ 操作: [确认] [解决(需备注)] [忽略]                   │
└──────────────────────────────────────────────────────┘
```

关键交互：
- **Tab 切换**：全部 / 待处理 (ACTIVE) / 已确认 / 已解决 / 已忽略
- **行展开**：描述 + 影响 + 建议，Ant Design `Table` expandableRow
- **操作列**：每行有 [确认] [解决] [忽略] 按钮
- **空状态**：✅ 一切正常，没有需要关注的告警
- **加载状态**：Skeleton 表格
- **分页**：每页 20 条

### 前端：AppLayout.tsx 变更

在 `/alerts` 菜单项上显示红色 badge：调用 `GET /api/alerts/summary`，取 `ACTIVE` 且 `HIGH` 的计数。

```typescript
{
  key: '/alerts',
  icon: <Badge count={highAlertCount} size="small" offset={[12, 0]}>
    <WarningOutlined />
  </Badge>,
  label: '预警',
}
```

### 前端：DashboardPage.tsx 变更

在 MetricCards 下方加一个 AlertPanel 小部件，显示最近 5 条活跃告警：
- 每条一行：级别图标 + 标题 + 时间
- 点击跳转到 `/alerts` 页面
- 空状态：绿色小勾 "无活跃告警"

### 数据库

Alert 模型已存在（`backend/app/models/alert.py`）且已迁移到数据库（`alerts` 表）。可以直接使用，无需新建 migration。
- 字段齐全 ✓
- 表已创建 ✓（在初始 migration 中）
- 需要：创建 CRUD 查询、确认 `created_at` 有默认值

### 去重策略

```python
async def generate_alerts(summary):
    for project in summary.projects:
        # 检查是否已有相同 rule_id + project_id 的 ACTIVE 告警
        existing = await db.execute(
            select(Alert).where(
                Alert.rule_id == rule_id,
                Alert.project_id == project_name,
                Alert.status == "ACTIVE"
            )
        )
        if existing.first():
            continue  # 已存在活跃告警，跳过创建
        # 创建新告警
```

需注意：Alert 表的 `project_id` 是 FK 到 `projects.id`，但 dashboard 项目存在 `user_settings.json` 中而非 `projects` 表。解决方法：用 `project_name` 存储在 `title`/`description` 字段中，或者将 `project_id` 改为可为空，用 `rule_id` + `description` 做去重标识。

### 复杂度：L

---

## Phase C: 趋势可视化

**为什么眼前一亮**：Jira Dashboard 只给你此刻的快照。这个仪表盘能告诉你"P1 问题在过去一周是变多了还是变少了"——趋势比绝对值更有决策价值。

### 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/models/metric_snapshot.py` | **新增** | 指标快照模型 |
| `backend/app/crud/metric_snapshot.py` | **新增** | 快照 CRUD |
| `backend/app/services/trend_service.py` | **新增** | 快照采集 + 趋势查询 |
| `backend/app/api/trends.py` | **新增** | 趋势 API |
| `frontend/src/components/Dashboard/TrendChart.tsx` | **新增** | 趋势图表组件 |
| `frontend/package.json` | 修改 | 添加 recharts 依赖 |
| `backend/app/models/__init__.py` | 修改 | 导出 MetricSnapshot |
| `backend/app/api/__init__.py` | 修改 | 注册 trends router |
| `backend/app/main.py` | 修改 | 启动快照采集任务 |
| `frontend/src/services/chatService.ts` | 修改 | 趋势 API 类型 + 函数 |
| `frontend/src/pages/DashboardPage.tsx` | 修改 | 嵌入 TrendChart |

### 后端：MetricSnapshot 模型

```python
class MetricSnapshot(Base, TimestampMixin):
    __tablename__ = "metric_snapshots"
    
    id: int (PK)
    project_name: str = ""  # "" 表示全局汇总
    snapshot_time: datetime (tz-aware)
    total_issues: int = 0
    p1_count: int = 0
    stale_count: int = 0
    eta_expiring: int = 0
    unmerged_cls: int = 0   # Phase A 完成后才有此数据
    extra_metrics: dict = {}  # JSON, 扩展用
    
    # 复合索引: (project_name, snapshot_time)
    __table_args__ = (Index("ix_snap_project_time", "project_name", "snapshot_time"),)
```

### 后端：trend_service.py

```python
async def collect_snapshot():
    """每 5 分钟采集一次当前指标。
    
    1. 调用 dashboard_service 获取最新数据 (绕过缓存)
    2. INSERT 一条 project_name="" 的汇总行
    3. 对每个项目 INSERT 一条 project_name=name 的行
    4. 异常只记日志
    """

async def get_trends(project: str = "", days: int = 7):
    """获取趋势数据点。
    
    SELECT snapshot_time, total_issues, p1_count, stale_count, ...
    FROM metric_snapshots
    WHERE project_name = :project
      AND snapshot_time >= NOW() - :days
    ORDER BY snapshot_time ASC
    """
```

### 后端：api/trends.py

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/trends` | GET | 趋势数据，参数 `project=&days=7` |
| `/api/trends/summary` | GET | 趋势摘要（方向↑↓→ + 百分比变化） |

### 前端：TrendChart.tsx

使用 `recharts` 的 `AreaChart`：
```
┌────────────────────────────────────────────────────────┐
│ 趋势图 [7天▼] [全部项目▼]                               │
│                                                        │
│  ████████████████████████████████████████████  ██ P1    │
│  ████████████████████████████████████          ██ Stale │
│  ████████████████████                          ██ CL   │
│  └─────────────────────────────────►                     │
│  06/08  06/09  06/10  06/11  06/12  06/13  06/14       │
│                                                        │
│  趋势: P1 ↑23%  Stale →2%  CL ↓15%                     │
└────────────────────────────────────────────────────────┘
```

- 时间范围选择：7天 / 14天 / 30天
- 项目筛选：全部 / 各项目
- 颜色匹配 MetricCard: P1=红色, Stale=紫色, ETA=橙色, CL=灰色
- 悬浮 tooltip 显示精确值
- 空状态："数据采集中，请稍后再看"

### 前端：DashboardPage.tsx 变更

在 MetricCards 下方添加可折叠的 "趋势" 区域：
```tsx
<Collapse ghost items={[{
  key: 'trends', 
  label: '趋势分析',
  children: <TrendChart />
}]} />
```

### 后台采集任务

在 `main.py` lifespan 中：
```python
async def snapshot_loop():
    while True:
        await asyncio.sleep(300)  # 5分钟
        try:
            await trend_service.collect_snapshot()
        except Exception as e:
            logger.warning("Snapshot collection failed: %s", e)
```

### 复杂度：M

---

## Phase D: 一键 AI 深度分析

**为什么眼前一亮**：Jira Dashboard 的数字是死的。这个仪表盘每个数字点一下都能跟 AI 对话——"为什么这个 P1 还没解决?"、"这个项目的 CL 阻塞在哪里?"——从看见问题到了解问题。

### 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/api/investigate.py` | **新增** | 调查启动 API |
| `frontend/src/components/Dashboard/InvestigateButton.tsx` | **新增** | AI 分析按钮 |
| `backend/app/api/__init__.py` | 修改 | 注册 investigate router |
| `frontend/src/pages/DashboardPage.tsx` | 修改 | 指标卡 + 项目卡 + issue 行加"AI分析"按钮 |
| `frontend/src/services/chatService.ts` | 修改 | 调查 API 函数 |

### 后端：api/investigate.py

```python
@router.post("/api/investigate")
async def start_investigation(body: {
    context_type: str,  # "metric_card" | "project_issues" | "alert" | "cl_status" | "trend"
    context_data: dict,  # 相关上下文数据 (metric name, values, project name, etc.)
    message: str = "",   # 用户自定义问题，不传则自动生成
}):
    """启动 AI 调查。
    
    1. 创建新 conversation（或用已有的）
    2. 将 context_data 格式化为初始 prompt
    3. 调用 agent_engine.process_message() 但不等待结果
    4. 返回 {conversation_id, redirect_url: "/chat?conv={id}"}
    5. 前端 redirect 到聊天页面，AI 已经开始分析
    """
```

自动生成的 prompt 示例：
- metric_card: `"分析仪表盘指标：项目 {project} 当前 P1={N}，其中 {stale} 个超过 {days} 天未更新。请调查根因并给出建议。"`
- cl_status: `"分析项目 {project} 的 CL 状态：共 {total} 个 CL，{unmerged} 个未合并。请逐个检查阻塞原因。"`

### 前端：InvestigateButton.tsx

```tsx
interface Props {
  contextType: string
  contextData: object
  label?: string  // 默认 "AI 分析"
  size?: 'small' | 'default'
}

// 点击 → 调用 POST /api/investigate → navigate(`/chat?conv=${id}`)
// 加载状态: button loading spinner
// 错误状态: antMsg.error
```

### 前端：DashboardPage.tsx 点击目标

- **MetricCard**：点击数值 → 调查该指标（如 "为什么 P1 有 5 个?"）
- **ProjectCard**：标题栏增加 "AI 分析该问题" 小按钮
- **Issue 行**：表格增加 "AI 分析" 列，点击调查该 Jira issue
- **CL 项**：点击调查该 CL 阻塞原因
- **Alert 行**：AlertPanel 的每条告警增加 "调查" 按钮

### 前端：ChatPage 变更

接收 `?conv=` 参数时，显示 "AI 正在分析中..." 提示框代替空白界面。

### 复杂度：S

---

## Phase E: 报告归档页面

**为什么眼前一亮**：项目管理需要回顾。Jira Dashboard 不能保存历史报告。这个仪表盘可以查看以往所有自动生成的周报、CL 监控报告。

### 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/models/report_record.py` | **新增** | 报告记录模型 |
| `backend/app/services/report_service.py` | **新增** | 报告服务 |
| `backend/app/api/reports.py` | **新增** | 报告 API |
| `frontend/src/pages/ReportsPage.tsx` | **新增** | 报告列表页面 |
| `backend/app/models/__init__.py` | 修改 | 导出 ReportRecord |
| `backend/app/api/__init__.py` | 修改 | 注册 reports router |
| `frontend/src/App.tsx` | 修改 | 添加 `/reports` 路由 |
| `frontend/src/services/chatService.ts` | 修改 | 报告 API 函数 |

### 后端

- **ReportRecord 模型**：id, title, skill_name, summary_data (JSON), status (generating/completed/failed), generated_at, generated_by
- **api/reports.py**：`GET /api/reports`（列表）、`POST /api/reports/generate`（手动触发）、`GET /api/reports/{id}`（详情）
- **复用现有技能**：`jira_report_skill.py`、`jira_cl_monitor_skill.py` 等技能已在后端注册，报告页提供一键触发界面

### 前端：ReportsPage.tsx

```
┌──────────────────────────────────────────────────────┐
│ 报告中心                          [生成报告 ▼]         │
│                                                        │
│ ┌──────────────────────────────────────────────────┐  │
│ │ 📄 CL合并监控周报   │ 长虹T6D  │ 2026-06-14  │  │  │
│ │   12 issues, 3 unmerged CLs                      │  │
│ ├──────────────────────────────────────────────────┤  │
│ │ 📄 Jira周报         │ 所有项目  │ 2026-06-07  │  │  │
│ │   68 issues, 5 P1, 8 stale                      │  │
│ └──────────────────────────────────────────────────┘  │
│                                                        │
│ [加载更多...]                                           │
└──────────────────────────────────────────────────────┘
```

关键交互：
- Ant Design `Card` 列表，每张卡显示报告标题、生成时间、关键指标摘要
- "生成报告" 按钮 → Modal 选择技能类型 + 参数 → 异步执行
- 点击报告卡片 → 查看详情（跳转 Confluence 或内嵌展示）
- 空状态："还没有生成过报告"

### 复杂度：S

---

## Phase F: 每周叙事摘要

**为什么眼前一亮**：Jira Dashboard 只给你静态数字。这个功能让 AI 把一周的项目变化写成"故事"——"本周完成了 12 个问题，新增 3 个 P1，CL 堆积有改善，建议下周优先处理 TV-XXXXXX 的 code review"。项目经理打开仪表盘，不是看数字，而是看到**发生了什么**。

### 功能定义

每周叙事摘要 = **LLM 将一周的原始项目数据转化为结构化叙事文本**，包含：

| 段落 | 内容 | 数据来源 |
|------|------|---------|
| **完成事项** | 本周已解决的问题、已合并的 CL、状态推进的 issue | JQL `status changed` / `resolutiondate > -7d` |
| **变化趋势** | 新增 P1、Stale 变化、ETA 到期增减、CL 变化 | 当前指标 vs 上周快照对比 |
| **风险提醒** | 值得关注的问题（长期未更新、P1 堆积、CL 阻塞） | dashboard 当前风险指标 |
| **下周展望** | 建议优先处理的事项、需要关注的领域 | LLM 基于以上数据推理生成 |

### 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/services/digest_service.py` | **新增** | 摘要数据采集 + LLM 生成 |
| `backend/app/api/digest.py` | **新增** | 摘要 API |
| `backend/app/models/digest.py` | **新增** | 摘要记录模型（可选，存 DB） |
| `backend/app/api/__init__.py` | 修改 | 注册 digest router |
| `frontend/src/components/Dashboard/WeeklyDigestCard.tsx` | **新增** | 摘要卡片组件 |
| `frontend/src/pages/DashboardPage.tsx` | 修改 | 嵌入摘要卡片 |
| `frontend/src/pages/ReportsPage.tsx` | **新增** | 报告列表页面（历史摘要归档） |
| `frontend/src/App.tsx` | 修改 | 添加 `/reports` 路由 |
| `frontend/src/services/chatService.ts` | 修改 | 摘要 API 类型 + 函数 |

### 后端：digest_service.py

```python
from app.services.llm import llm_router, TaskType
from app.services.dashboard_service import get_dashboard_config, _build_labels_clause, _build_epic_clause
from app.mcp.client import mcp_manager

# 摘要存储（JSON 格式，可存 DB 或 user_settings.json）
_digest_cache: Dict[str, Any] = {}  # key: project_name, value: { digest, generated_at, week_range }


async def collect_weekly_data(proj: Dict[str, Any]) -> Dict:
    """收集项目一周的数据变更。
    
    1. 本周解决的问题: JQL "status changed to (Closed,Verified,Done,Resolved) AFTER -7d"
    2. 本周新增的问题: JQL "created > -7d"
    3. 本周更新的问题: JQL "updated > -7d"
    4. 本周P1变化: 从 fetch 的 issues 中按 priority + updated 过滤
    5. 当前风险指标: 直接从 dashboard_summary 复用（无需额外查询）
    
    所有 JQL 复用 dashboard_service.py 的 _build_* 辅助函数构建过滤器。
    """

async def generate_digest(project_name: str) -> Dict:
    """收集数据 → LLM 生成叙事摘要 → 存储 → 返回。
    
    Step 1: collect_weekly_data() — 获取原始数据
    Step 2: 构造 prompt，将原始数据格式化为结构化文本：
    
    prompt = f"""
    你是一个项目管理助手。以下是 {project_name} 项目过去一周（{week_start} ~ {week_end}）的数据变更。
    请用中文生成一份简洁的"每周叙事摘要"，包含以下四个段落，每段2-4句话：
    
    1. **完成事项**：本周解决了{resolved_count}个问题，合并了{merged_cls}个CL...
    2. **变化趋势**：P1 问题从{p1_before}变为{p1_now}...
    3. **风险提醒**：有{stale_count}个问题超过{stale_days}天未更新...
    4. **下周展望**：建议优先处理XXX...
    
    原始数据：
    - 本周解决: {json.dumps(resolved_issues, ensure_ascii=False)}
    - 本周新增: {json.dumps(new_issues, ensure_ascii=False)}
    - CL 动态: {json.dumps(cl_data, ensure_ascii=False)}
    - 当前风险: P1={p1}, Stale={stale}, ETA到期={eta}
    
    请直接输出摘要文本，不要加额外说明。
    """
    
    Step 3: await llm_router.route_and_complete(
        task_type=TaskType.REPORT_GENERATION,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    
    Step 4: 存储摘要（内存 + 持久化），返回 {digest_text, generated_at, week_range, raw_data}
    """

async def get_latest_digest(project_name: str) -> Optional[Dict]:
    """获取最新的已生成摘要。"""
```

关键设计：
- **LLM 使用**：只对已收集的原始数据进行总结归纳，不做外部查询。LLM 的调用是纯文本生成，不涉及 function calling
- **错误隔离**：LLM 调用失败 → 返回基于模板的 fallback 摘要（"本周数据采集完成，LLM 摘要生成暂时不可用"），不阻塞 UI
- **复用现有**：JQL 构建函数、Gerrit CL 查询全部复用 dashboard_service / gerrit_service 已有代码
- **fallback 兜底**：LLM 不可用时，提供简单模板摘要

### 后端：api/digest.py

| 端点 | 方法 | 说明 |
|------|------|------|
| `POST /api/digest/generate` | POST | 生成摘要，参数 `{project: ""}`（空=全部项目） |
| `GET /api/digest/latest` | GET | 获取最新摘要 |

### 前端：WeeklyDigestCard.tsx

```
┌──────────────────────────────────────────────────────────────┐
│ 📋 每周叙事摘要                          [生成新摘要] [全部▼]  │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ 2026/06/08 — 2026/06/14 · 长虹T6D                      │ │
│ │                                                          │ │
│ │ 📌 完成事项                                               │ │
│ │   本周项目共解决了 8 个问题，合并 5 个 CL。其中 TV-XXXXXX │ │
│ │   从"In Progress"推进到"Code Review"，TV-XXXXXX 已完成    │ │
│ │   验证等待发布。整体交付进度符合预期。                      │ │
│ │                                                          │ │
│ │ 📈 变化趋势                                               │ │
│ │   P1 问题从上周的 5 个增加到 6 个（新增 1 个：TV-XXXXXX  │ │
│ │   优先级调高）。Stale 问题从 12 个减少到 8 个，有改善。    │ │
│ │                                                          │ │
│ │ ⚠️ 风险提醒                                               │ │
│ │   TV-XXXXXX 已 10 天未更新，建议关注阻塞原因。TV-XXXXXX  │ │
│ │   的 CL 已有 3 天未合并，建议安排 code review。           │ │
│ │                                                          │ │
│ │ 🔭 下周展望                                               │ │
│ │   建议优先完成 TV-XXXXXX 的 code review，并关注将于下周  │ │
│ │   到期的 3 个 ETA 任务。P1 问题的根因分析建议在周会上讨论。│ │
│ └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

关键交互：
- **卡片式展示**：每段用 emoji 标题区分，清晰可读
- **项目选择**：Dropdown 切换项目（"全部" / "长虹T6D" / 各项目），默认"全部"
- **生成按钮**：点击 → loading + "AI 正在生成摘要..." → 展示结果
- **加载状态**：Spin + "正在生成叙事摘要，这需要几秒钟..."
- **空状态**："本周摘要尚未生成，点击「生成新摘要」按钮创建"
- **错误状态**：显示 fallback 模板摘要 + "LLM 摘要生成不可用"提示

### 前端：DashboardPage.tsx 变更

在 MetricCards 下方、项目详情上方插入摘要区：

```tsx
{/* 每周叙事摘要 */}
<Card style={{ marginBottom: 24, borderRadius: 10 }}>
  <WeeklyDigestCard />
</Card>
```

### 前端：ReportsPage.tsx

新的独立页面，展示所有历史摘要记录。链接从侧边栏"报告"菜单进入：

```
┌──────────────────────────────────────────────────────────────┐
│ 报告中心                                    [生成新摘要 ▼]    │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐  │
│ │ 📋 每周叙事摘要         长虹T6D    2026/06/08-06/14    │  │
│ │   完成8个问题 · 新增1个P1 · 5个CL合并                    │  │
│ ├──────────────────────────────────────────────────────────┤  │
│ │ 📋 每周叙事摘要         长虹T6D    2026/06/01-06/07    │  │
│ │   完成12个问题 · 新增2个P1 · 3个CL合并                   │  │
│ └──────────────────────────────────────────────────────────┘  │
│                                                              │
│ [加载更多...]                                                  │
└──────────────────────────────────────────────────────────────┘
```

- 点击卡片 → 展开查看完整摘要内容
- 空状态："还没有生成过摘要报告"
- 后续可扩展为支持其他类型的报告（如 CL 监控报告直接用现有 skill 生成）

### 后端：Digest 存储

方案选择（推荐方案一）：

| 方案 | 存储方式 | 复杂度 | 说明 |
|------|---------|--------|------|
| **方案一 ✓** | 持久化到 `user_settings.json` | 低 | 复用 `settings_service.save_user_settings()`，存到 `settings["digests"]` 数组 |
| 方案二 | 新建 `weekly_digests` 数据库表 | 中 | 需要新建 model + migration + CRUD |
| 方案三 | 纯内存，每次重新生成 | 低 | 无法保留历史，不推荐 |

方案一足够：摘要文本不大（~500字），保留最近 10 条，超出则裁剪。

存储结构：
```json
{
  "digests": [
    {
      "id": "2026-W25",
      "project": "长虹T6D",
      "week_start": "2026-06-15",
      "week_end": "2026-06-21",
      "generated_at": "2026-06-21T10:00:00+08:00",
      "digest_text": "...",
      "raw_data": { "resolved_count": 8, "new_count": 3, ... }
    }
  ]
}
```

### LLM Prompt 设计

**目标**：让 LLM 写一份"周报"，但语气是叙述性的（叙事），而不是列表式的（清单）。

关键指令：
```
你是一个项目管理助手。请根据以下项目数据，生成一份「每周叙事摘要」。
目标是让项目经理快速了解「这一周发生了什么」。

要求：
- 语言简洁、口语化，像同事在汇报
- 每个段落 2-4 句话
- 不要用列表/编号
- 涉及具体 issue 时用 Key 引用
- 数据中未提及的不要编造
```

Prompt 中包含的原始数据：
- 本周解决的问题列表（Key + 概要）
- 本周新增的问题列表（Key + 概要 + 优先级）
- CL 合并/新增情况
- 当前风险指标（P1/Stale/ETA）
- 对比上周的数据变化

### 后台生成时机

1. **手动触发**：用户点击"生成新摘要" → 实时调用 LLM → 返回结果
2. **自动触发（可选）**：通过 `main.py` lifespan 中的定时任务，每周一 9:00 自动生成
   - 需要判断是否已有同一周的摘要（避免重复生成）
   - 仅在服务运行期间有效（不依赖外部调度器）

### 复杂度：M

核心逻辑在 `digest_service.py` 中，约 150 行。LLM prompt 是关键难点——需要调试到输出格式稳定。

### 验证方式

1. 点击 Dashboard 的"生成新摘要"按钮 → 确认加载状态出现
2. 确认 LLM 成功返回叙事文本（4 个段落完整）
3. 确认摘要展示了正确的数据（解决数、P1 数、CL 数等与实际一致）
4. 重新生成 → 确认新摘要覆盖旧摘要
5. 断掉 LLM 服务 → 确认 fallback 摘要正常显示

---

## 各阶段对比

| 阶段 | 特性 | 差异化价值 | 后端文件 | 前端文件 | 复杂度 |
|------|------|-----------|---------|---------|--------|
| **A** | Gerrit CL 状态 | 跨系统聚合，看得见"Verified 但未合并" | 2 新 + 1 改 | 1 新 + 2 改 | M |
| **B** | 风险告警系统 | 主动发现，不需要人盯着看 | 3 新 + 2 改 | 2 新 + 4 改 | L |
| **C** | 趋势图 | 看得见"变好还是变差" | 3 新 + 2 改 | 1 新 + 2 改 | M |
| **D** | AI 深度分析 | 每个数字都能"问为什么" | 1 新 + 1 改 | 1 新 + 2 改 | S |
| **E** | 报告归档 | 历史报告可回溯 | 3 新 + 2 改 | 1 新 + 2 改 | S |
| **F** | 每周叙事摘要 | AI 把数字写成"故事"，一眼看到一周发生了什么 | 2 新 + 1 改 | 2 新 + 3 改 | M |

## 依赖关系

```
Phase A (Gerrit CL) ── 独立
Phase B (告警系统) ── 独立，但 A 的 CL 数据能让告警规则更丰富
Phase C (趋势图) ── 依赖 A 的 CL 数据，依赖 B 的告警计数（可选）
Phase D (AI分析) ── 独立，但前期数据越多分析越精准
Phase E (报告页) ── 独立
Phase F (叙事摘要) ── 独立，复用 A 的 CL 数据（有则更丰富）
```

推荐顺序：**A → B → C → D → E**。如果资源有限，A+D 组合能最快体现差异化（跨系统聚合 + AI 分析）。

## 关键设计原则

### 1. 错误隔离

任何外部 MCP 调用失败都不能阻塞整体：
```python
# Gerrit 失败 → 只标记 CL 状态为不可用，Jira 数据照常显示
# 告警扫描失败 → 日志记录，下一轮继续
# 快照采集失败 → 跳过该轮，不影响 dashboard
```

### 2. 缓存策略

- Dashboard 10s 缓存不变（Jira 数据实时性要求高）
- Gerrit CL 数据可以 30-60s 缓存（CL 状态变化慢）
- 告警扫描 5 分钟间隔（不需要秒级告警）
- 快照采集 5 分钟间隔（趋势分析不需要高频数据）

### 3. 配置扩展

Dashboard 配置（`user_settings.json`）扩展字段：
```json
{
  "dashboard": {
    "projects": [
      {
        "name": "长虹T6D",
        "enabled": true,
        "jira_project_key": "TV",
        "...": "...",
        "gerrit_enabled": true,
        "gerrit_instances": ["gerrit1", "gerrit2"],
        "alert_rules": {
          "p1_stale": true,
          "stale_threshold": 5,
          "cl_bottleneck_threshold": 3
        }
      }
    ],
    "thresholds": { "...": "..." },
    "snapshot_interval_seconds": 300
  }
}
```

### 4. 无重蹈覆辙

关键历史教训：
- **--reload worker 挂死**：生产模式启动，不用 `--reload`
- **Pydantic model_dump() 遗漏字段**：所有 API 模型显式声明每个字段，不靠继承
- **MCP search_issues fields 参数 bug**：不传 `fields` 参数，用默认
- **前端 `.finally()` 防护**：所有 loading 状态确保卸载

## 验证方式

### Phase A 验证
1. 配置一个有 epic_issues 的项目
2. 打开 Dashboard → 确认 CL 数据出现（不再是 0）
3. 确认 CL 状态正确（故意改一个 Jira 评论中的 CL 为 merged，看仪表盘更新）
4. 断掉 Gerrit MCP 服务 → 确认 Jira 数据不受影响，CL 区显示"查询失败"

### Phase B 验证
1. 访问 `/alerts` 页面 → 应显示生成的告警（或"无告警"空状态）
2. 创建一个高危场景（如 P1 问题超过 7 天未更新）→ 确认 HIGH 告警出现
3. 点击确认/解决/忽略 → 确认状态更新
4. 再次扫描 → 确认已解决的告警不会重复创建
5. 仪表盘显示告警 badge

### Phase C 验证
1. 启动后等待 5 分钟 → 确认数据库有 snapshot 数据
2. 访问 Dashboard → 趋势区域显示图表
3. 切换时间范围（7d/14d/30d）→ 图表更新
4. 趋势方向指示器与数据一致

### Phase D 验证
1. 点击 MetricCard 的 P1 数值 → 跳转到聊天页面
2. AI 已经开始分析 P1 问题
3. 确认 AI 的分析结果正确（调用了 MCP 工具）

### Phase E 验证
1. 访问 `/reports` 页面 → 显示已有报告或空状态
2. 点击"生成报告" → Modal 选择技能 → 确认报告生成
3. 点击报告卡片 → 查看详情

## 文件依赖映射

```
Phase A 依赖:
  └── backend/app/skills/jira_cl_monitor_skill.py (CL提取逻辑来源)

Phase B 依赖:  
  └── backend/app/models/alert.py (已有模型, 确认迁移已执行)
  └── backend/app/db/session.py (get_db 依赖)

Phase C 依赖:
  └── backend/app/db/session.py (get_db 依赖)
  └── alembic 版本 (需要新建 migration 创建 metric_snapshots 表)

Phase D 依赖:
  └── backend/app/services/agent/engine.py (agent_engine.process_message)

Phase E 依赖:
  └── backend/app/api/__init__.py (路由注册模式参考)
  └── backend/app/skills/ (列表已注册的技能)

Phase F 依赖:
  └── backend/app/services/llm/__init__.py (llm_router, TaskType)
  └── backend/app/services/dashboard_service.py (复用JQL查询 + 获取当前指标)
  └── backend/app/services/gerrit_service.py (复用CL状态查询)
  └── backend/app/mcp/client.py (mcp_manager, Jira查询)
```
