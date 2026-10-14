import React from 'react'
import { Button, Card, Collapse, Progress, Table, Tag, Tooltip, Typography } from 'antd'
import { CloseOutlined } from '@ant-design/icons'

const { Text } = Typography

const LEVEL_COLOR: Record<string, string> = {
  red: '#ff4d4f',
  yellow: '#faad14',
  green: '#52c41a',
}

const LEVEL_LABEL: Record<string, string> = {
  red: '🔴 紧急',
  yellow: '🟠 关注',
  green: '🟢 正常',
}

// Jira ISO 时间 → YYYY-MM-DD（如 2026-08-01T10:00:00.000+0000 → 2026-08-01）
const formatDate = (iso?: string) => {
  if (!iso) return '-'
  const d = String(iso).slice(0, 10)
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : String(iso)
}

interface FocusSetCardProps {
  focusSet: any
  onDismiss: (id: number) => void
  jiraWebUrl?: string
}

/**
 * 单个关注集合的健康度卡片（P-C：看板状态图，方案 8.3）。
 * 展示 PM 关心的有效信息：目标/达成/倒计时 → 状态分布 → 停滞需推动明细
 * （Jira ID / 描述 / Owner / 创建时间 / 停滞天数）。
 */
const FocusSetCard: React.FC<FocusSetCardProps> = ({ focusSet, onDismiss, jiraWebUrl }) => {
  const h = focusSet.health || {}
  const statusColor = LEVEL_COLOR[h.status] || '#8c8c8c'
  const progressPercent = Math.round((h.progress || 0) * 100)
  const targetPercent = (focusSet.metric?.target || 0) * 100
  const metric = focusSet.metric
  const stagnant = h.stagnant_members || []
  const statusDist = h.status_dist || {}
  const baseJiraUrl = jiraWebUrl ? jiraWebUrl.replace(/\/+$/, '') : ''

  return (
    <Card
      size="small"
      style={{
        width: '100%',
        borderRadius: 8,
        borderTop: `3px solid ${statusColor}`,
      }}
    >
      {/* 头部 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <div style={{ minWidth: 0 }}>
          <Text strong style={{ fontSize: 14, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {focusSet.name}
          </Text>
          <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>
            {focusSet.type}/{focusSet.identifier}
            {focusSet.priority_semantics ? ` · ${focusSet.priority_semantics}` : ''}
          </div>
        </div>
        <div style={{ flexShrink: 0, display: 'flex', gap: 4 }}>
          <Tag color={statusColor}>{LEVEL_LABEL[h.status] || h.status}</Tag>
          <Tag>{focusSet.type}</Tag>
        </div>
      </div>

      {/* 目标 + 达成 */}
      <div style={{ marginBottom: 6, fontSize: 13, color: '#333' }}>
        {h.days_left !== null && h.days_left !== undefined && (
          <span style={{ color: h.days_left < 0 ? '#ff4d4f' : undefined }}>
            ⏰ {h.days_left < 0 ? `已超期 ${-h.days_left} 天` : `距截止 ${h.days_left} 天`}
          </span>
        )}
        {metric && <span style={{ marginLeft: 8 }}>🎯 {metric.type === 'fix_rate' ? `fix率≥${Math.round(targetPercent)}%` : `目标 ${metric.target}`}</span>}
        <span style={{ marginLeft: 8, fontWeight: 500 }}>
          {h.done}/{h.baseline_total ?? h.total} 达成
          {h.diff_to_target !== null && h.diff_to_target !== undefined && h.diff_to_target > 0 && (
            <span style={{ color: '#ff4d4f' }}>（差 {h.diff_to_target} 个达标）</span>
          )}
        </span>
      </div>
      <Progress
        percent={progressPercent}
        size="small"
        strokeColor={statusColor}
        status={progressPercent >= targetPercent ? 'success' : 'active'}
      />

      {/* Epic 周基线信息 */}
      {h.baseline_total !== null && h.baseline_total !== undefined && (
        <div style={{ marginTop: 8, fontSize: 12, color: '#666' }}>
          <span>📅 本周起始 {h.baseline_total} 个</span>
          <span style={{ marginLeft: 12 }}>📊 当前 {h.total} 个</span>
          <span style={{ marginLeft: 12 }}>✅ 已完成 {h.done} 个</span>
          {h.done_delta !== null && h.done_delta !== undefined && h.done_delta !== 0 && (
            <span style={{ marginLeft: 12, color: h.done_delta > 0 ? '#52c41a' : '#ff4d4f' }}>
              {h.done_delta > 0 ? `↗ 今日新增完成 ${h.done_delta} 个` : `↘ 今日回退 ${Math.abs(h.done_delta)} 个`}
            </span>
          )}
          {h.baseline_date && (
            <span style={{ marginLeft: 12 }}>基线 {h.baseline_date}</span>
          )}
        </div>
      )}

      {/* 本周变化 */}
      <WeeklyChanges changes={h.weekly_changes || {}} baseJiraUrl={baseJiraUrl} />

      {/* 状态分布 */}
      {Object.keys(statusDist).length > 0 && (
        <div style={{ margin: '8px 0' }}>
          {Object.entries(statusDist).map(([s, n]) => (
            <Tag key={s} style={{ marginBottom: 4 }}>{s}: {String(n)}</Tag>
          ))}
        </div>
      )}

      {/* 停滞需推动明细（Jira ID / 描述 / Owner / 创建时间 / 停滞天数） */}
      {stagnant.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <Text strong style={{ fontSize: 12, color: '#ff4d4f' }}>⚠️ 停滞需推动（{stagnant.length}）</Text>
          <Table
            size="small"
            rowKey="key"
            dataSource={stagnant}
            pagination={false}
            style={{ marginTop: 6 }}
            columns={[
              {
                title: 'Jira ID', dataIndex: 'key', width: 130,
                render: (k: string) => baseJiraUrl
                  ? (
                    <a
                      href={`${baseJiraUrl}/browse/${k}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ fontFamily: 'monospace', fontSize: 12 }}
                    >
                      {k}
                    </a>
                  )
                  : <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{k}</span>,
              },
              {
                title: '描述', dataIndex: 'summary',
                render: (v: string) => (
                  <span style={{ fontSize: 12, wordBreak: 'break-word', whiteSpace: 'normal' }}>
                    {v || '-'}
                  </span>
                ),
              },
              {
                title: 'Owner', dataIndex: 'assignee', width: 130,
                render: (v: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span>,
              },
              {
                title: '创建时间', dataIndex: 'created', width: 105,
                render: (v: string) => <span style={{ fontSize: 12 }}>{formatDate(v)}</span>,
              },
              {
                title: '停滞', dataIndex: 'stagnant_days', width: 70,
                render: (d: number) => <span style={{ fontSize: 12, color: '#ff4d4f' }}>{d}天</span>,
              },
            ]}
          />
        </div>
      )}

      {/* 操作 */}
      <div style={{ textAlign: 'right', marginTop: 8 }}>
        <Tooltip title="取消关注">
          <Button type="text" size="small" icon={<CloseOutlined />} onClick={() => onDismiss(focusSet.id)}>
            取消关注
          </Button>
        </Tooltip>
      </div>
    </Card>
  )
}

interface WeeklyChangesProps {
  changes: {
    newly_done?: Array<{ key: string; summary: string; status: string }>
    reopened?: Array<{ key: string; summary: string; status: string }>
    added?: Array<{ key: string; summary: string; status: string }>
    removed?: Array<{ key: string; summary: string; status: string }>
  }
  baseJiraUrl: string
}

const KeyLink: React.FC<{ issueKey: string; baseJiraUrl: string }> = ({ issueKey, baseJiraUrl }) => {
  if (!baseJiraUrl) {
    return <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{issueKey}</span>
  }
  return (
    <a
      href={`${baseJiraUrl}/browse/${issueKey}`}
      target="_blank"
      rel="noopener noreferrer"
      style={{ fontFamily: 'monospace', fontSize: 12 }}
    >
      {issueKey}
    </a>
  )
}

const WeeklyChanges: React.FC<WeeklyChangesProps> = ({ changes, baseJiraUrl }) => {
  const newlyDone = changes.newly_done || []
  const reopened = changes.reopened || []
  const added = changes.added || []
  const removed = changes.removed || []
  const total = newlyDone.length + reopened.length + added.length + removed.length
  if (total === 0) return null

  const section = (title: string, items: Array<{ key: string; summary: string; status: string }>, color: string) => {
    if (items.length === 0) return null
    return (
      <div style={{ marginBottom: 6 }}>
        <Text strong style={{ fontSize: 12, color }}>{title}（{items.length}）</Text>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
          {items.map((item) => (
            <Tooltip key={item.key} title={item.summary || item.status}>
              <Tag style={{ margin: 0, fontSize: 12 }}>
                <KeyLink issueKey={item.key} baseJiraUrl={baseJiraUrl} />
              </Tag>
            </Tooltip>
          ))}
        </div>
      </div>
    )
  }

  return (
    <Collapse ghost size="small" style={{ marginTop: 8 }}>
      <Collapse.Panel header={<span style={{ fontSize: 12, color: '#666' }}>📈 本周变化（{total}）</span>} key="changes">
        {section('🟢 本周新完成', newlyDone, '#52c41a')}
        {section('🟠 重新打开', reopened, '#fa8c16')}
        {section('🔵 新增到 Epic', added, '#1890ff')}
        {section('⚪ 从 Epic 移除', removed, '#8c8c8c')}
      </Collapse.Panel>
    </Collapse>
  )
}

export default FocusSetCard
