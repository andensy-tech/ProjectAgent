# Dashboard Feature Ideas — ProjectAgent for SOC Project Managers
> **状态**：想法池 / 头脑风暴，非实施方案（2026-06-24）。

> Brainstorming document. No code changes implied.
> Target: 1-2 day implementation per idea, leveraging existing MCP/LLM infrastructure.

---

## Priority 1: Highest Impact / Easiest to Build

### 1. Weekly Narrative Digest — "What Happened This Week"

**The "Wow" Factor:** One-click generation of a plain-language weekly status report that a PM can paste directly into their weekly email — no more manual copy-pasting from 10 different JQL queries.

**Data Sources:**
- Jira: issues resolved/created/updated this week (per project)
- Gerrit: CLs merged/open/abandoned this week
- Confluence: pages updated this week for the project

**LLM Role:**
- Summarize raw data into narrative paragraphs (e.g., "This week the team resolved 8 P1 bugs in the CHIPX4 driver, but 3 new critical issues were opened. 12 CLs merged in the graphics stack, though review time increased to 2.3 days average.")
- Identify the most important 3-5 events and explain *why* they matter
- Tone configurable: executive summary vs. detailed technical

**Technical Feasibility:** Easy. Backend already has JQL query, MCP clients, and LLM router. The weekly digest is a single new API endpoint that fetches a fixed set of queries, feeds results to LLM, returns markdown. Frontend: one new card section with a "Generate Weekly Digest" button.

**Quick Win Score:** 9/10

**Implementation Sketch:**
```
1. New endpoint: GET /api/dashboard/weekly-digest?project=XXX
2. Backend runs 6-8 JQL queries in parallel (resolved this week, created this week, etc.)
3. LLM receives structured data + template prompt → outputs ~3 paragraph markdown
4. Frontend renders it in a styled card with copy-to-clipboard button
```

---

### 2. Stale Issue Root-Cause Analysis

**The "Wow" Factor:** Instead of just showing "42 issues are stale (>7 days)," the dashboard tells you *why* — blocked by QA, waiting for component owner, or just abandoned — so you know exactly where to poke.

**Data Sources:**
- Jira: stale issue details (comments, status, assignee, last update timestamp)
- Jira: linked issues (blocks/is blocked by)
- Jira: changelog (when did it last transition?)

**LLM Role:**
- Analyze each stale issue's comments, status history, and linked issues to classify the *root cause* into categories:
  - `BLOCKED_EXTERNAL` — waiting on another team/vendor
  - `AWAITING_INFO` — Jira has a question unanswered for >3 days
  - `ASSIGNEE_GONE` — assignee has no recent activity anywhere
  - `ABANDONED` — no comments, no transitions, just sitting
  - `IN_REVIEW_HELL` — stuck in code review with no reviewer action
- Aggregate: "5 of 12 stale issues are blocked by QA capacity on the CHIPX4 driver"

**Technical Feasibility:** Medium. The tricky part is fetching issue changelogs via Jira MCP — need to verify the MCP exposes changelog. Comments are readily available. Classification logic is straightforward LLM categorization from structured data.

**Quick Win Score:** 8/10

**Implementation Sketch:**
```
1. Backend fetches stale issues (already have JQL for this)
2. For each, fetch last 5 comments, linked issues, status
3. Batch-send to LLM: "Classify each issue's stall reason into one of [categories]"
4. Return aggregated counts + per-issue classification
5. Frontend: Pie chart or colored bar showing breakdown, clickable to see issues in each category
```

---

### 3. Cross-System Risk Radar — "Hot Spots"

**The "Wow" Factor:** A single card that answers "What should I worry about right now?" by combining Jira bug influx, Gerrit CL pileup, and Confluence doc staleness into a per-component risk score — something no traditional dashboard can compute.

**Data Sources:**
- Jira: P1/P2 issue counts, unresolved bug trend (last 7 days vs previous 7 days)
- Gerrit: open CL count per component, CLs waiting >2 days without review
- Gerrit: CLs with negative scores or build failures
- Confluence: last update date of key project documentation pages

**LLM Role:**
- Normalize disparate signals (Jira bug count, Gerrit review lag, doc staleness) into a unified risk score (1-5) per project component
- Explain *why* the score is elevated: "Graphics driver risk is HIGH (score 4) — 3 P1 bugs opened this week while 8 CLs pile up waiting for a single reviewer (user X)"
- Detect accelerating trends: "Bug influx doubled in the last 3 days"

**Technical Feasibility:** Medium. All data sources are accessible. The challenge is defining the risk scoring formula — a simple weighted sum (LLM generates weights based on project context) would be sufficient for Phase 1. No new infrastructure needed.

**Quick Win Score:** 8/10

**Implementation Sketch:**
```
1. New periodic background fetch (every 5 min or on-demand)
2. Query Jira for recent bug counts, Gerrit for open CLs per component, Confluence for doc freshness
3. LLM receives structured data → outputs risk matrix per project component
4. Frontend: Traffic-light colored component list (Red/Yellow/Green), click for LLM explanation
```

---

## Priority 2: High Impact, Moderate Effort

### 4. Requirement Traceability Chain

**The "Wow" Factor:** Click a Confluence requirement and instantly see which Jira issues implement it and which Gerrit CLs changed the code — full traceability from spec to silicon without manual cross-referencing.

**Data Sources:**
- Confluence: requirement pages (structured, probably with requirement IDs or headings)
- Jira: issues referencing those requirements (text search in description/comments for requirement ID)
- Gerrit: CLs referencing Jira issue keys in commit messages

**LLM Role:**
- Parse Confluence pages to extract requirement IDs/headings and their descriptions
- Match requirement IDs against Jira issue descriptions (fuzzy matching for partial references)
- Build a traceability map: Requirement → Jira Epics → Jira Tasks → Gerrit CLs
- Flag orphan requirements (no Jira issues) and orphan issues (no CLs) — these are the gaps a PM cares about

**Technical Feasibility:** Medium. Confluence page content is accessible via MCP. Text search across Jira descriptions needs the search_issues tool. The traceability map construction is the core LLM value-add. Risk: Confluence page structure varies; may need project-specific configuration.

**Quick Win Score:** 7/10

**Implementation Sketch:**
```
1. PM configures which Confluence page(s) contain requirements
2. Backend fetches page content, LLM extracts requirement IDs
3. For each requirement ID, JQL text search in issue descriptions
4. For each found Jira issue, Gerrit search in commit messages
5. Frontend: Expandable tree view (Requirement → Issue → CL)
```

---

### 5. Code Review Health Dashboard

**The "Wow" Factor:** See at a glance which repos/teams have healthy review processes and which are bottlenecks — average time-to-first-review, reviewers with most outstanding requests, CLs stuck in endless revision cycles.

**Data Sources:**
- Gerrit: all open CLs with timestamps, scores, reviewers
- Gerrit: merged CL history (last 30 days) for cycle time calculation
- Jira: issues linked to CLs (to correlate with priority/severity)

**LLM Role:**
- Identify *bottleneck reviewers*: individuals who are tagged on many CLs but have low review output
- Detect *review ping-pong*: CLs with 5+ patch sets but no score, indicating unclear requirements
- Prioritize CLs by correlated Jira priority: "These 3 P1 CLs are waiting >24h for review"
- Generate actionable suggestions: "Reassign CL-1234 to reviewer Y who has bandwidth"

**Technical Feasibility:** Medium. Gerrit MCP already available. Main effort is Gerrit query design and LLM analysis prompt engineering. CL cycle time calculation is straightforward from timestamps.

**Quick Win Score:** 7/10

**Implementation Sketch:**
```
1. Query Gerrit for all open CLs per project + merged CLs last 30 days
2. Compute per-reviewer stats (open requests, avg review time, merge rate)
3. LLM analyzes the data → identifies bottlenecks and patterns
4. Frontend: Reviewer table sorted by workload, CL list sorted by priority×wait time
```

---

### 6. Sprint Goal Confidence Score

**The "Wow" Factor:** The LLM looks at what's left in the sprint, how fast the team has been resolving issues, and tells you: "65% chance of completing the sprint goal — here are the 3 issues most likely to slip."

**Data Sources:**
- Jira: sprint issues (open, resolved, statuses, story points)
- Jira: historical velocity (last 3 sprints data)
- Jira: issue dependencies and blockers
- Gerrit: related CLs still open (blocking issue completion)

**LLM Role:**
- Compute a confidence score based on: remaining work vs historical velocity + blocker count + unresolved CL correlation
- NOT just a mathematical burn-down — the LLM adds context:
  - "Issues assigned to Developer X historically complete on time, but Developer Y's issues slip 40%"
  - "This sprint has 3 cross-team dependencies, which caused slips in 2 of the last 3 sprints"
- Identify the *top risk issues* and explain why each is at risk
- Suggest concrete actions: "Unblock CL-5678 to reduce risk on issue TV-22001"

**Technical Feasibility:** Medium-Hard. Historical sprint data requires Jira board/spring configuration access. The LLM analysis is straightforward but the data gathering is more involved. A simplified version (no historical velocity, just current sprint + blocker analysis) could be done in 1 day.

**Quick Win Score:** 6/10

**Implementation Sketch:**
```
1. Fetch current sprint issues + linked CLs
2. Fetch resolution history per assignee (last 30 days)
3. LLM receives: remaining issues with assignee history + blockers + open CLs
4. LLM outputs: confidence %, top 3 risks, suggested actions
5. Frontend: Progress ring with score, risk list below
```

---

## Priority 3: Innovative but More Effort

### 7. Gerrit CL-to-Jira Issue Coverage Map

**The "Wow" Factor:** Instantly see which Jira issues have *no code changes* attached and which CLs reference *no Jira issue* — the two gaps that indicate process breakdowns — in a single visual.

**Data Sources:**
- Jira: issues in "In Progress" or "In Review" status (should have CLs)
- Gerrit: open CLs (should have Jira issue keys in commit message)
- Jira: resolved issues in last 7 days (should have had CLs)

**LLM Role:**
- Categorize uncovered issues:
  - `NO_CLS_YET` — in progress but 0 CLs found (possibly not started)
  - `WRONG_KEY` — CL references a different Jira key (potential tracking error)
  - `MULTI_CL_UNTRACKED` — 1 Jira issue, 5 CLs but no sub-tasks (should break down)
- Categorize unmapped CLs:
  - `NO_ISSUE_KEY` — commit message has no Jira reference
  - `CLOSED_ISSUE` — references a resolved issue (likely backport or hotfix)
- Generate a "coverage percentage": "82% of in-progress issues have associated CLs"

**Technical Feasibility:** Medium. JQL and Gerrit queries are straightforward. Cross-referencing is the bulk of the work. LLM categorization adds genuine insight on top.

**Quick Win Score:** 6/10

**Implementation Sketch:**
```
1. Query Jira "In Progress" + "In Review" issues → extract keys
2. Query Gerrit open CLs → extract Jira keys from commit messages
3. Cross-reference both sets, identify gaps
4. LLM categorizes each gap
5. Frontend: Two-column view (Issues without CLs | CLs without Issues) with category badges
```

---

### 8. Confluence Knowledge Gap Detector

**The "Wow" Factor:** Automatically finds topics that the team is working on (from Jira) but hasn't documented (no Confluence page exists), and suggests creating docs — turning tribal knowledge into shared knowledge without anyone asking for it.

**Data Sources:**
- Jira: issue summaries and descriptions (what the team is working on)
- Confluence: page titles and content (what's documented)
- Jira: component/label fields (topic categorization)

**LLM Role:**
- Extract key topics from recent Jira issues using LLM summarization:
  - "Last week the team worked on: HDMI 2.1 compliance, CHIPX4 power management, Android 14 boot optimization"
- Cross-reference each topic against Confluence page titles and content:
  - "HDMI 2.1 compliance" → found in "HDMI Compliance Test Guide" ✅
  - "CHIPX4 power management" → no Confluence page found ❌
  - "Android 14 boot optimization" → only in a meeting note from March ⚠️
- Prioritize gaps by Jira issue count/severity: "The top undocumented topic is CHIPX4 power management (8 issues, 3 P1s)"
- Draft a suggested page outline for each gap

**Technical Feasibility:** Medium-Hard. Topic extraction from Jira requires LLM processing of issue text. Confluence search via MCP. The cross-reference logic is the main effort. Quality depends on Confluence organization.

**Quick Win Score:** 5/10

**Implementation Sketch:**
```
1. Fetch last 14 days of Jira issues, send to LLM for topic clustering
2. For each topic, search Confluence for related pages
3. Classify each topic as Documented / Partial / Missing
4. Frontend: "Knowledge Gaps" card listing undocumented topics with severity + "Draft Outline" button
```

---

### 9. Release Readiness Composite Score

**The "Wow" Factor:** A single number (0-100) that tells the PM "are we ready to release?" — computed from bug counts, open CLs, test status, documentation freshness, and requirement completion — with an LLM-generated breakdown explaining what needs to happen to reach 100.

**Data Sources:**
- Jira: unresolved bugs by severity, issues linked to release fixVersion
- Gerrit: open CLs targeting the release branch
- Confluence: release notes page status, test report pages
- Jira: issues in "Resolved" but not "Closed" (potential verification gap)

**LLM Role:**
- Compute score with configurable weights (PM can prioritize: bugs > docs > CLs)
- Generate a checklist: "7 items remain before release readiness reaches 90:"
  1. "Resolve 2 P1 bugs in the graphics driver (-15 pts)"
  2. "Merge 5 remaining CLs on release/6.0 branch (-10 pts)"
  3. "Update the CHIPX4 release notes page (-5 pts)"
  4. ...
- Detect regressions: compare score to last week/last snapshot
- Suggest a target release date based on trend: "At current closure rate, estimated readiness by Jun 23"

**Technical Feasibility:** Medium. All individual data queries exist. The composite scoring formula is new logic. LLM generates the checklist from structured data. Requires defining what "release readiness" means per project (configurable).

**Quick Win Score:** 5/10

**Implementation Sketch:**
```
1. Snapshot state: query bugs, CLs, docs for the release version
2. Compute sub-scores: BugScore, CLScore, DocScore (0-100 each)
3. Weighted composite = w1*BugScore + w2*CLScore + w3*DocScore
4. LLM receives sub-scores + raw data → generates checklist narrative
5. Frontend: Big gauge number with colored ring, checklist below, compare arrow (vs last week)
```

---

### 10. Engineer Capacity Forecaster

**The "Wow" Factor:** Instead of PMs manually tracking "who's busy," the dashboard shows each engineer's current load (assigned issues + open CLs), identifies who is overcapacity, and who has bandwidth — using LLM to factor in issue complexity, not just count.

**Data Sources:**
- Jira: open issues per assignee (weighted by priority and story points)
- Gerrit: open CLs per author/owner
- Jira: resolved issues last 14 days (to estimate throughput)
- Jira: comments per issue (LLM estimates effort from conversation volume)

**LLM Role:**
- Estimate effort from available signals when story points are missing:
  - P1 > P2 > P3 weighting
  - "5 comments on this issue → higher complexity"
  - "Linked to 3 sub-tasks → effort multiplier"
- Detect context switching: "Engineer X is assigned to 4 unrelated components"
- Suggest rebalancing: "Engineer Y has bandwidth and relevant expertise for TV-22501"
- Forecast: "At current allocation, Engineer X will complete their sprint by Thursday, then has 2 days free"

**Technical Feasibility:** Medium-Hard. The LLM effort estimation is speculative without story points. A simpler version (just count of open issues × priority weight + open CLs) could be done in 1 day. The LLM "context switching detection" and "rebalancing suggestions" add the most value but are harder to validate.

**Quick Win Score:** 5/10

**Implementation Sketch:**
```
1. Query open issues grouped by assignee (with priority, story points, components)
2. Query open CLs grouped by author
3. LLM analyzes workload per person → capacity status (Under / Normal / Over)
4. Frontend: Team grid with capacity traffic lights + detail tooltip
```

---

### 11. Jira Changelog Activity Timeline

**The "Wow" Factor:** A scrollable timeline of all important project activity (status changes, priority changes, assignee changes, CL merges) with LLM-powered highlights — "3 things you missed since yesterday" — so PMs don't need to watch everything.

**Data Sources:**
- Jira: issue changelogs (status transitions, field changes)
- Gerrit: CL merge events
- Confluence: page update events (ideally via Confluence audit log or RSS)

**LLM Role:**
- Summarize raw events into natural language highlights:
  - Not: "TV-22501: status changed from In Progress to In Review"
  - But: "TV-22501 (HDMI 2.1 compliance) is now in review — this was a P1 blocker"
- Deduplicate noisy events: batch similar events together
- Identify *first occurrences*: "This is the first P1 opened in the graphics driver this sprint"
- Generate a "Morning Briefing" format: 3-5 bullet points of what changed

**Technical Feasibility:** Medium. Jira changelog access via MCP is needed (need to verify). Gerrit events via its event stream or simple query. LLM summarization is the core value. Risk: changelog may be large; need time-windowing (last 24h).

**Quick Win Score:** 5/10

**Implementation Sketch:**
```
1. Periodic query (every hour or on page load): fetch changelogs since last check
2. Also fetch new CL merges, new Confluence updates
3. LLM summarizes into 3-5 highlights
4. Frontend: Timeline widget with "X new events since you last looked" badge
```

---

### 12. Cross-Project Dependency Map

**The "Wow" Factor:** A visual map showing how SOC projects depend on each other — "Project A (chip CHIPX4) is blocked on Project B (Android 14 bringup), which has 12 open P1s" — automatically discovered from Jira issue links, not manually maintained.

**Data Sources:**
- Jira: issue links ("blocks", "is blocked by", "depends on", "relates to") across projects
- Jira: Epic-level issue links (Epics that span projects)
- Jira: custom fields for cross-project dependencies (if used)
- Jira: Fix versions that share components across projects

**LLM Role:**
- Build a dependency graph from Jira issue links — discover implicit dependencies:
  - "Project A and Project B both have issues targeting the same fix version → likely coupled"
  - "Project A's graphics component frequently blocks Project B's integration testing"
- Identify *critical path*: "TV-21896 is the longest dependency chain (4 levels deep) — a slip here delays 3 downstream projects"
- Quantify risk: "Project C has 8 incoming dependencies but its closure rate is 30% below average"
- Suggest decoupling: "Moving component X to a separate release cycle would reduce cross-project blocking by 60%"

**Technical Feasibility:** Hard (but doable in 2 days). The core challenge is building the dependency graph from Jira links — needs `issuelinks` field access via MCP. LLM analysis on top is straightforward. Discovery of *implicit* dependencies is the risky part; focus on explicit links first.

**Quick Win Score:** 4/10

**Implementation Sketch:**
```
1. Fetch all Epics and linked issues across configured projects
2. Parse issuelinks to build directed graph of dependencies
3. LLM analyzes graph → identifies critical path, bottleneck projects, risk chain
4. Frontend: Interactive force-directed graph (d3.js or cytoscape) or simple table of "Top 5 Dependency Risks"
```

---

## Summary: Priority Matrix

| # | Idea | Impact | Effort | Quick Win Score | Best For |
|---|------|--------|--------|-----------------|----------|
| 1 | Weekly Narrative Digest | High | Easy | **9** | PM weekly reporting |
| 2 | Stale Issue Root-Cause | High | Medium | **8** | Daily standup prep |
| 3 | Cross-System Risk Radar | High | Medium | **8** | Morning check |
| 4 | Requirement Traceability | High | Medium | **7** | Feature delivery tracking |
| 5 | Code Review Health | High | Medium | **7** | Engineering excellence |
| 6 | Sprint Confidence Score | Medium | Medium-Hard | **6** | Sprint planning |
| 7 | CL-to-Issue Coverage | Medium | Medium | **6** | Process enforcement |
| 8 | Knowledge Gap Detector | Medium | Medium-Hard | **5** | Knowledge management |
| 9 | Release Readiness | High | Medium | **5** | Release gate review |
| 10 | Engineer Capacity | Medium | Medium-Hard | **5** | Resource planning |
| 11 | Activity Timeline | Medium | Medium | **5** | Async awareness |
| 12 | Cross-Project Dependency | High | Hard | **4** | Program management |

## Recommended Top 3 for Phase 2

1. **Weekly Narrative Digest (#1)** — Lowest effort, highest PM delight. Everyone hates writing weekly reports.
2. **Stale Issue Root-Cause (#2)** — Builds directly on existing stale-count metric. LLM adds real diagnostic value.
3. **Cross-System Risk Radar (#3)** — Most differentiated from traditional Jira dashboards. Proves the "AI agent" concept.

These three together could be implemented in ~3-4 days total and would give the dashboard a genuinely unique value proposition that no Jira-native dashboard can match.

---

## 承接状态（2026-08 整理时核对）

> 下表标注 12 个想法与当前代码/其它设计文档的承接关系，供后续取舍参考。

| # | 想法 | 承接状态 |
|---|------|---------|
| 1 | Weekly Narrative Digest | 未实施。`weekly_report` skill 是模板式周报（近似替代，非 LLM 叙事）。方向与 monitoring 方案的 LLM summary 重叠 |
| 2 | Stale Issue Root-Cause | 未实施。与 monitoring 方案第五节"LLM 精判"直接互补，仍未做 |
| 3 | Cross-System Risk Radar | 部分。现有 `alert_service` R1-R5 是简单计数，非 LLM 风险矩阵 |
| 4 | Requirement Traceability | 部分。`models/requirement.py` 数据模型在（2026-06-19），但无 API/服务/前端 |
| 5 | Code Review Health | 未实施 |
| 6 | Sprint Confidence Score | 未实施 |
| 7 | CL-to-Issue Coverage | 未实施（CL 监控已有雏形，未做覆盖率维度） |
| 8 | Knowledge Gap Detector | 未实施 |
| 9 | Release Readiness | 未实施 |
| 10 | Engineer Capacity | 未实施 |
| 11 | Activity Timeline | 未实施 |
| 12 | Cross-Project Dependency | 未实施 |

**结论**：#1 被 `monitoring_optimization.md`（主待办蓝图）的 LLM summary 方向承接；#2/#3 与 monitoring 方案第五节 LLM 精判互补；#4 有数据模型待落地；其余暂未承接。
