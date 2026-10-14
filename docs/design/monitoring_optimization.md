# 项目问题监控 / 风险预警 / 问题跟踪推动优化方案（修正版）
> **状态**：实施中（2026-08-07 定稿，2026-08-10 P-A 已落地）。当前监控/预警方向唯一主待办蓝图，建议按 P-A→P-B+P-C→P-F→P-D 顺序推进。
>
> **实施进度**：
> - ✅ **P-A 紧迫度模型**（2026-08-10）：`app/services/urgency.py` + alert_service R1/R2/R4 重构 + 修复 R1 bug（读 p1 但字段 p0）
> - ✅ **P-B 聊天式关注指令**（2026-08-10）：`focus_sets`/`focus_set_members` 表 + `jira_resolve_filter`/`create_focus_set` 内置工具 + `services/focus_set_service.py` + 看板 API
> - ✅ **P-C 看板状态图**（2026-08-10）：前端 `FocusSetCard`/`FocusCommandList` + DashboardPage「我的关注指令」区块
> - ✅ **P-F 消息中心**（2026-08-10）：`notifications` 表 + API + 前端铃铛红点；预警生成自动写站内消息
> - ✅ **P-D 项目计划知识库核心**（2026-08-10）：`project_plans`/`project_plan_phases` 表 + 服务 + API（LLM 提取工具为增量）
> - ✅ **P-E 实时判定**（2026-08-10）：`judge_progress` 工具结合计划+关注集合判断项目进展
> - ✅ **P-G LLM 精判**（2026-08-10）：`risk_analyze` 工具对高紧迫度 issue 跑 LLM 风险分析
> - ✅ **P0-1 数据保鲜**（2026-08-13）：后台每 30 分钟刷新关注集合成员快照（`focus_set_refresh_service.run_focus_set_cycle`），成员停滞/状态分布不再停留在创建那一刻
> - ✅ **P0-2 主动提醒 PM**（2026-08-13）：到期未达标 → 站内消息（铃铛红点）提醒 PM；达标 → 主动提示。按日/状态去重，复用 `create_notification(type="focus_set")`
>
> **🎉 全部 7 个阶段（P-A~P-G）已落地**（2026-08-10）；**P0-1/P0-2 推动闭环增量落地**（2026-08-13，数据保鲜 + 主动提醒 PM）

> 设计文档 · 2026-08-07
> 基于 PM 视角对现有监控/预警/跟踪能力的评估，并吸收用户两点反馈修正：①飞书仅少数 PM 在用，提醒走 Jira mcp + 站内消息；②核心难点是"判断 Jira 是否超时"——需补项目背景和判断标准。

---

## 一、现状评估

### 1.1 已有能力

| 能力 | 现状 |
|------|------|
| Jira 看板 | 指标聚合（总问题/P0/stale/ETA）、项目列表、趋势对比、问题列表过滤 |
| 预警系统 | 5 条规则（R1-R5）、去重、状态流转（ACTIVE/ACKNOWLEDGED/RESOLVED/IGNORED） |
| CL 监控 | 未合并跟踪、自动 Jira 提醒 owner、不关注标记、曾 Verified 持续跟踪、RD 回复 LLM 语义判断 |
| 趋势 | 5 个指标序列 7/14/30 天 |
| 周报 | 模板驱动周报生成 |

### 1.2 核心短板

1. **推动闭环几乎空白**：预警只展示不推送，无站内消息/飞书推送，无超时催办/升级，无自动关闭
2. **风险发现滞后**：扫描 24h 一次 + 基于 24h 旧缓存快照
3. **监控口径缺陷**：R1 bug（读 p1 但字段是 p0，P0 预警永不触发）；无项目健康度总览
4. **规则不灵活**：阈值硬编码，无预测性/组合规则
5. **判断超时标准缺失**（用户反馈核心）：现有只用"ETA 7天内"单维度，不结合优先级、Epic 目标日期、状态停滞；Agent 缺项目背景信息

---

## 二、方案修正说明（用户反馈）

| 反馈 | 修正 |
|------|------|
| 飞书只有几个 PM 用 | 提醒三层：**Jira mcp @经办人**（全员）+ **站内消息中心**（在用 PM）+ **飞书**（在用 PM 即时推送） |
| 缺判断超时标准和项目背景 | **紧迫度评分模型**（替代简单计数）+ **项目计划知识库**（Epic 目标日期 + Confluence 计划页） |

---

## 三、核心设计：问题紧迫度评分模型（Urgency Score）

### 3.1 公式

```
紧迫度 = 优先级权重 × 时间紧迫度 × 状态停滞度
```

### 3.2 三维度

**优先级权重**（priority 字段）：

| Priority | 权重 |
|----------|------|
| P0 / Blocker / Highest | 3 |
| P1 / Critical / High | 2 |
| P2 / Major / Medium | 1 |
| P3 及以下 / 未设置 | 0.5 |

**时间紧迫度**（距目标日期剩余天数 D，目标日期见"计划知识库"）：

| 剩余天数 | 紧迫度 |
|----------|--------|
| 已超期（D<0） | 3 |
| < 3 天 | 2.5 |
| < 7 天 | 2 |
| < 14 天 | 1.5 |
| < 30 天 | 1 |
| 无日期 | 1（中性） |

**状态停滞度**：
- 无更新时间 > stale_days → 1.5（停滞）
- 正常更新 → 1.0

### 3.3 分档（预警标准）

| 档位 | 紧迫度 | 级别 | 动作 |
|------|--------|------|------|
| 🔴 紧急 | ≥ 6 | HIGH | 通知 PM + Jira @经办人 |
| 🟠 关注 | ≥ 3 | MEDIUM | 通知 PM + Jira 评论 |
| 🟡 提示 | ≥ 1.5 | LOW | 站内消息 + 看板黄标 |
| 🟢 正常 | < 1.5 | 无 | 不打扰 |

### 3.4 示例

- P0 + 已超期 + 停滞 → 3×3×1.5 = 13.5 → 🔴
- P2 + 无 ETA + 正常 → 1×1×1 = 1 → 🟢
- P1 + Epic 目标 5 天后 + 停滞 → 2×2×1.5 = 6 → 🔴

---

## 四、项目知识体系（获取 → 知识库 → 实时判定）

> **核心洞察**：Agent"了解项目"= **两个来源的知识 → 统一知识库 → 实时判定进展**。来源一是**外部资料**（Confluence 计划页、计划 PPT——项目整体计划的时间基准）；来源二是 **PM 日常关注指令**（filter/epic——阶段性重点的优先级基准）。两者共同构成项目知识库，支撑 Agent 实时判断"当前到哪、下个节点是什么、问题相对计划的偏差"。

### 4.1 总览：两来源 → 统一知识库

```
┌─ 来源 A：外部资料 ──────────────┐   ┌─ 来源 B：PM 关注指令 ────────────┐
│ Confluence 计划页（chat 发链接） │   │ filter / epic（chat 下发）       │
│ 计划 PPT（上传文件）             │   │ 关注集合 + deadline + 指标        │
│ Agent 读取 + 结构化提取          │   │ 解析成 FocusSet                  │
└──────────────┬──────────────────┘   └──────────────┬──────────────────┘
               ↓                                      ↓
┌───────────────────────────────────────────────────────────────┐
│                 项目知识库（统一存储）                          │
│  ├─ project_plans：阶段/里程碑/目标日期/状态（时间基准）        │
│  └─ focus_sets：关注集合/deadline/指标/语义（优先级基准）        │
└───────────────────────────────────────────────────────────────┘
               ↓
┌───────────────────────────────────────────────────────────────┐
│  Agent 实时判定                                               │
│  ① 当前处于项目哪个阶段？下个里程碑什么时候？                  │
│  ② 当前关注集合的 deadline / 指标达成情况                      │
│  ③ 问题紧迫度（相对最近的相关阶段/集合 deadline）+ 风险预警     │
└───────────────────────────────────────────────────────────────┘
```

### 4.2 来源 A：外部资料 → 项目计划知识库（时间基准）

#### 4.2.1 读取方式（web chat，复用现有能力）

| 方式 | PM 操作 | Agent 能力 |
|------|---------|-----------|
| Confluence 计划页 | chat 发链接："读取这个页面了解项目计划 `https://confluence.../pageId=xxx`" | 已有 `confluence_get_page_content` + `find_rows_in_table` |
| 计划 PPT | 上传 PPT 文件："这是项目计划，提取里程碑" | 已有 `read_pptx` |
| 计划文档（Word/Excel） | 上传文件 | 已有 `read_docx` / `read_xlsx` |

#### 4.2.2 结构化提取（LLM 辅助）

- **Confluence 计划页**：读取表格 → 识别"阶段/里程碑 | 目标日期 | 状态"列 → 结构化 phases
- **PPT/文档**：提取文字 → LLM 识别时间线/里程碑 → 结构化
- **确认**：提取后 `ask_user` 与 PM 确认理解（"我理解项目分 5 个阶段，第五轮测试 9/20……对吗？"）

#### 4.2.3 项目计划知识库数据模型（关系型：主表 + 阶段子表）

**设计原则**：`target_date` 拆为 DATE 列（可按日期查询"哪些阶段临近"）、阶段明细拆子表（增量更新、按状态聚合）。仅 `deliverables` 等描述性数据保留 JSON。

```
project_plans（主表，每项目一条）
  id, user_id, project_name,
  overall_status,          -- "phase3/5" 或描述
  source,                  -- confluence_page | ppt | doc | manual
  source_ref,              -- pageId / file 引用
  updated_at

project_plan_phases（阶段子表）
  id, plan_id (FK→project_plans), 
  phase_name,
  target_date,             -- DATE 列（可按日期查询/排序）
  status,                  -- planned | in_progress | done | delayed
  deliverables,            -- JSON 数组（"客户P0全清"，描述性，保留 JSON）
  related_sets,            -- ["filter:59983"] 关联 FocusSet
  sort_order
```

**查询示例**：
- "哪些阶段 30 天内临近" → `WHERE target_date BETWEEN today AND today+30`
- "第五轮测试的目标日期" → `WHERE phase_name = '第五轮测试'`
- 更新某阶段 → 只动一行，不重写整个计划

### 4.3 来源 B：PM 关注指令 → FocusSet（优先级基准）

#### 4.3.1 核心交互：Chat 即配置

PM 不需要打开配置页，直接在 web chat 说：

> **PM**：`https://jira.example.com/issues/?filter=60513` 这个 filter 里的问题是本周需要 fix 的问题列表，周五的时候问题 fix 率需要达到 80%

> **Agent**：✅ 已理解并记录关注指令：
> - 📌 **关注集合**：filter 60513「本周必清 Bug」
> - 🎯 **目标**：fix 率 ≥ 80%（当前 60/75 = 80% 差 3 个）
> - ⏰ **截止**：本周五（8-07）
> - 已生成看板状态图，将定时跟踪该集合

#### 4.3.2 指令解析流程

```
PM 在 chat 输入指令
    ↓
① 链接识别：正则提取 filter=(\d+) / epic key / pageId
    ↓
② 集合解析（新增内置工具 jira_resolve_filter）
   Jira REST GET /rest/api/2/filter/{id} → {name, jql, description}
   执行 jql 拉取成员（状态/优先级/更新时间）
    ↓
③ 目标解析（自然语言 → 结构化）
   "周五" / "8/13号之前" / "第五轮测试前" → deadline
   "fix率80%" → {metric: fix_rate, target: 0.8}
   "P0问题" / "客户反馈" → priority_semantics
    ↓
④ 确认（ask_user 复用）：Agent 复述理解，PM 确认或纠正
    ↓
⑤ 持久化 FocusSet → 看板生成状态图
```

**关键工具**：`jira_resolve_filter(filter_id)` 内置工具——REST 读 filter 元信息 + 执行 JQL 拉成员，复用 `gerrit_tool` 的内置工具模式（用户 MCP 凭据）。

**目标解析**：日期/指标用规则提取（稳定），语义标签用 LLM 理解（灵活）；"第五轮测试前"这类相对 deadline 需查 `project_plans` 解析。

#### 4.3.3 FocusSet 数据模型（关系型：主表 + 成员子表）

**设计原则**：`deadline` 拆 DATE 列（到期扫描）、`metric_target` 拆 NUMERIC 列（比较聚合）、成员拆子表（按 issue_key 反查、按 status 聚合、增量更新）。仅 `command_history`/`description` 保留 JSON。

```
focus_sets（主表）
  id, user_id, type,                 -- filter | epic | manual
  identifier,                        -- filter id / epic key（唯一，同 identifier 更新用）
  name, jql, description(JSON/Text),
  deadline,                          -- DATE 列（到期扫描）
  metric_type,                       -- fix_rate | all_fix | ...
  metric_target,                     -- NUMERIC（0.8）
  priority_semantics,                -- "本周必清" | "客户P0"
  active, created_at, updated_at,
  command_history                    -- JSON 数组（纯展示，保留 JSON）

focus_set_members（成员子表，高频更新）
  id, focus_set_id (FK→focus_sets),
  issue_key, summary, status, priority,
  updated_at, stagnant, stagnant_days,
  snapshot_time
  索引: (focus_set_id), (issue_key)
```

**查询示例**：
- "今天到期的集合" → `WHERE deadline = today AND active = 1`
- "TV-XXXXXX 挂在哪些关注集合" → `WHERE issue_key = 'TV-XXXXXX'`（反查）
- "filter 60513 各状态计数" → `GROUP BY status`
- 更新成员状态 → 只动该成员行，不重写整个 JSON

#### 4.3.4 动态更新

| 场景 | Agent 行为 |
|------|-----------|
| PM 下发新 filter 指令 | 解析 → 新建 FocusSet → 看板新增卡片 |
| PM 对同一 filter 更新指令（改 deadline/指标） | 按 `identifier` 识别 → **更新**而非重复创建 |
| PM 说"取消关注 xxx" | `active=false`，看板隐藏 |
| 集合达标 | 主动提示"✅ filter 60513 已达 80%" |
| 集合临近/超期未达标 | 提醒 PM + 停滞问题 Jira @RD |

### 4.4 知识库实时判定（Agent 判断进展）

**Agent 判断"当前进展"的三步查询：**

```
① 查 project_plans → 项目整体时间基准
   当前处于第几阶段？下个里程碑（如第五轮测试 9-20）是什么？
② 查 focus_sets → 当前关注重点
   哪些集合在进行中？deadline 何时？指标达成多少？
③ 算问题紧迫度 → 相对最近的相关阶段/集合 deadline
   "filter 59983 客户P0，deadline 8-13，剩 2 个 Open → 紧迫度高"
```

**示例**：
> 现在 8-07。项目计划：第五轮测试 **9-20**。关注集合：filter 59983 客户P0（**8-13 前全清**）、filter 60513 本周Bug（**8-07，80%**）。
> → Agent 判定：filter 60513 今天到期差 3 个 → 🔴 紧急；filter 59983 剩 2 个 Open（1 个停滞 5 天）→ 🟠 关注并提醒 RD。

**判定能力**：`project_plans` 提供"现在到哪个节点"，`focus_sets` 提供"这批问题什么时候要解决"——两者结合，Agent 才能回答"问题相对项目计划的偏差"。

### 4.5 Epic 作为补充

- 项目配置的 epics 列表 → 自动拉取
- Epic 目标日期（若有字段）→ issue 继承作为 deadline 兜底

### 4.6 落地要点

| 组件 | 说明 | 复用 |
|------|------|------|
| `jira_resolve_filter` 工具 | REST 读 filter + 拉成员 | gerrit_tool 内置工具模式 |
| `confluence_get_page_content` / `read_pptx` | 读计划页/PPT | 已有 |
| `project_plans` 表 | 阶段/里程碑/目标日期 | 新表 |
| `focus_sets` 表 | 关注集合 + 指令历史 | 新表 |
| 结构化提取 | LLM 从计划页/PPT 提取 phases | LLM + ask_user 确认 |
| 指令解析 | 链接正则 + 日期/指标规则 + LLM 语义 | 引擎层 |
| 集合健康度 | fix rate / 倒计时 / 停滞计算 | alert_service 模式 |
| 看板区块 | 指令列表 + 状态图卡片 | DashboardPage（见下） |
| 到期提醒 | 站内 + Jira @RD + 飞书 | 层 2 提醒机制 |

---

## 五、LLM 精判（差异化价值）

```
规则引擎：全部 issue 算紧迫度 → 筛出 🔴🟠
    ↓
LLM 精判：对高紧迫度 issue，结合评论/历史/项目背景/近期进展
  判断：真超时 or 有合理解释？根本原因？建议动作？
    ↓
输出：结构化风险分析（问题/原因/建议/责任人）
```

- 消除误报（ETA 到期但实际快完成）
- 省 token（只对初筛的高紧迫度 issue 跑 LLM）

---

## 六、提醒渠道（三层）

```
┌─ 对在用 PM ──────────────┐
│ ① 站内消息中心（新增）     │
│ ② 飞书（现有，高紧迫度推送）│
├─ 对经办人（全员）─────────┤
│ ③ Jira mcp @经办人（复用  │
│    现有 CL 提醒模式）      │
└─────────────────────────┘
```

**站内消息中心**（新增）：
- 新表 `notifications`：{user_id, type, title, content, link, read, created_at}
- 后台任务生成预警时写入 → 前端顶栏铃铛 + 红点 + 消息列表
- 与飞书共用触发逻辑

---

## 七、预警规则重构

| 规则 | 现有（计数） | 重构后（紧迫度） |
|------|-------------|----------------|
| R1 P1_STALE | p1>0 且 stale>0（有 bug） | 🔴 紧急 issue 数 > 0 |
| R2 STALE_PILEUP | stale>5 | 🟠🟡 issue 数 > 阈值 |
| R3 CL_BOTTLENECK | 未合并 CL>3 | 保留（CL 独立维度） |
| R4 ETA_EXPIRING | ETA 7天内>3 | 🔴 临期 issue 数（结合优先级） |
| R6 新增 | — | 高紧迫度 issue 的 LLM 风险分析 |

---

## 八、看板状态图前端设计（DashboardPage 扩展）

### 8.1 布局：DashboardPage 新增"关注指令"区块

位置：DashboardPage 顶部（MetricCards 之下、项目 Tab 之上），作为 PM 关注点的入口。

```
┌──────────────────────────────────────────────────────┐
│  📌 我的关注指令（PM 下发 · 2 进行中 · 1 已达标）      │
│  ┌──────────────────────────────────────────────────┐│
│  │ [🔴] filter 60513 本周必清Bug   距截止 0 天  77%  ││
│  │ [🟠] filter 59983 客户P0        距截止 6 天  5/8  ││
│  │ [✅] epic TV-XXXXXX R+长虹     已达标 8-01       ││
│  └──────────────────────────────────────────────────┘│
│                                                       │
│  本周必清Bug (filter 60513)  ← 状态图卡片             │
│  ┌──────────────────────────────────────────────────┐│
│  │  ⏰ 距截止 0 天 · 已超期 🎯 目标 80% 📊 当前 77%  ││
│  │  ████████████████░░░ 77%（差 3 个达标）          ││
│  │  状态: Open 8 | InProg 12 | Res 30 | Ver 15 | Cl 10│
│  │  ⚠️ 停滞: TV-XXXXXX (Open 5天无更新)             ││
│  └──────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────┘
```

### 8.2 两个核心组件

| 组件 | 职责 |
|------|------|
| **FocusCommandList**（指令列表） | 展示 PM 下发的所有关注指令（进行中/已达标/已过期），含状态徽标 + 关键数字 |
| **FocusSetCard**（集合状态图卡片） | 每个集合的健康度可视化（指标达成 + 倒计时 + 状态分布 + 瓶颈问题） |

### 8.3 FocusSetCard 详细设计

**卡片内容区：**
1. **头部**：集合名称 + 类型标签（`filter`/`epic`）+ 状态徽标（🟢🟡🔴）
2. **目标栏**：⏰ deadline 倒计时（"距截止 X 天" / "已超期 X 天"）+ 🎯 目标指标（"fix率≥80%"）
3. **指标达成条**：Progress bar（当前值 vs 目标，不足目标红色/达标绿色），下方标注"差 N 个达标"
4. **成员状态分布**：横向 bar 或 Tag 组（Open / In Progress / Resolved / Verified / Closed 各计数）
5. **瓶颈问题**：停滞/滞留的成员列表（key + 标题 + 停滞天数，超链接跳 Jira）
6. **操作**：查看全部成员（展开表格）、刷新、取消关注

**数据接口：**

```ts
interface FocusSet {
  id: number
  type: 'filter' | 'epic' | 'manual'
  identifier: string
  name: string
  description: string
  deadline?: string            // ISO 日期
  metric?: { type: 'fix_rate' | 'all_fix'; target: number }
  members: FocusMember[]
  health: {
    progress: number           // 当前指标达成值（fix rate 0.77）
    target: number             // 目标（0.8）
    days_left: number          // 距截止天数（负=超期）
    diff_to_target: number     // 还差几个达标
    status: 'green' | 'yellow' | 'red'
  }
}

interface FocusMember {
  key: string
  summary: string
  status: string
  priority: string
  updated: string
  stagnant: boolean
  stagnant_days: number
}
```

**健康度计算（后端）**：
```
progress   = resolved + verified + closed / total
days_left  = deadline - today
diff       = ceil(total * target) - done
status     = red(超期或严重不达标) / yellow(接近或停滞) / green(达标)
```

### 8.4 交互（三级刷新机制）

**① 自动轮询（可配置，默认 15 分钟）**
- config 新增 `refresh_interval_minutes: 5 | 15 | 30 | 60`（默认 15）
- 前端 `setInterval` 从 config 读取，**不再硬编码 30s**（Jira 状态是低频变化数据，30s 轮询浪费 MCP 请求）
- 现有 `refresh_interval_seconds` 配置项替换为分钟级

**② 手动刷新（立即）**
- 看板/卡片"刷新"按钮 → 立即拉取最新，不受轮询频次限制
- 覆盖"我刚处理完一个 Jira，想看效果"的场景

**③ 事件触发（主动）**
- 新关注指令到达、预警生成、集合达标/到期 → 后端推送/前端感知 → 立即刷新对应卡片

| 交互 | 行为 |
|------|------|
| 指令列表项点击 | 滚动定位到对应 FocusSetCard / 展开详情 |
| 卡片成员点击 | 跳转 Jira issue（`jira_web_url` 已有配置） |
| 自动轮询 | 按 `refresh_interval_minutes` 配置值（默认 15min），低频保底 |
| 手动刷新 | 按钮立即拉取，不受轮询限制 |
| **新指令到达** | PM 在 chat 下发指令 → Agent 建 FocusSet → 事件触发立即刷新 |
| 取消关注 | 隐藏卡片（active=false） |

### 8.5 技术实现

| 组件 | 方案 |
|------|------|
| 前端文件 | `DashboardPage.tsx` 新增区块；`FocusSetCard.tsx` / `FocusCommandList.tsx` 组件 |
| 后端 API | `GET /api/dashboard/focus_sets`（列表 + 健康度 + 成员）；`PUT /api/dashboard/focus_sets/{id}`（取消关注） |
| 数据服务 | `chatService.ts` 新增 `fetchFocusSets` / `dismissFocusSet` |
| UI 复用 | Ant Design `Card` / `Progress` / `Tag` / `List` / `Badge` |
| 样式 | 沿用淡蓝主题（`#4A90E2`），状态色：绿 `#52c41a` / 黄 `#faad14` / 红 `#ff4d4f` |

---

## 九、落地路线

| 阶段 | 内容 | 产出 | 验证 |
|------|------|------|------|
| **P-A 紧迫度模型** | 评分公式 + 替换 R1-R4（修 R1 bug） | 预警更准，识别"P0 超期" | 抽查 10 个 issue 紧迫度 |
| **P-B 聊天式关注指令** | `jira_resolve_filter` 工具 + FocusSet 表 + 指令解析 | PM chat 下发指令 → 看板状态图 | PM 在 chat 下发 filter，看板出现卡片 |
| **P-C 看板状态图** | 指令列表 + FocusSetCard 前端 | PM 一眼看所有关注集合健康度 | 看板展示 2 个 filter 健康度 |
| **P-D 项目计划知识库** | `project_plans` 表 + Confluence 计划页/PPT 读取 + LLM 结构化提取 | Agent 知道"第五轮测试 9-20"等时间基准，"第五轮测试前"可解析 | PM chat 发计划页链接，Agent 提取里程碑并确认 |
| **P-E 实时判定** | 结合 project_plans + focus_sets 判断问题紧迫度/项目进展 | 问题相对计划偏差的判定 | 对 1 个 filter 给出"相对第五轮测试还有 X 天"的判断 |
| **P-F 消息中心 + Jira 提醒** | 消息表 + 铃铛 + Jira @经办人 + 飞书 | 预警主动触达 | 生成一条预警验证三渠道 |
| **P-G LLM 精判 + Summary** | 高紧迫度集合跑 LLM 风险分析 / 智能总结 | 可执行建议 | 对 1 集合生成风险报告 |

**建议优先 P-B + P-C**（chat 指令定义关注 + 看板状态图）——这是 PM 最直接的日常入口；**P-D 紧随其后**（项目计划知识库给 Agent 时间基准，是"判断进展"的前提）。

---

## 十、设计原则

**"超时判断"的答案不是让 Agent 凭空猜测，而是给它两样东西**：① 紧迫度评分模型（优先级 × 目标日期 × 停滞度，可计算）；② 项目知识（Filter/Epic 关注集合 + Confluence 计划页，可获取）。有了这两样，Agent 才能回答"这个 Jira 是否超时、是否该预警、建议怎么推动"。

**"PM 怎么定义重点，Agent 就怎么理解"**——通过 web chat 下发关注指令（filter/epic 链接 + 目标 + 截止），是 PM 最自然的工作方式。Agent 的三件事：**听懂指令**（解析 filter + 目标）→ **记住指令**（FocusSet + 看板状态图）→ **执行指令**（跟踪达标、到期提醒、停滞推动、智能 summary）。
