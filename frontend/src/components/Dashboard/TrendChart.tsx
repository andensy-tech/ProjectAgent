import React, { useState, useEffect } from 'react'
import { Card, Select, Spin, Empty, Typography, Row, Col, Statistic, Alert } from 'antd'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts'
import { apiFetch } from '../../services/chatService'

const { Text } = Typography

// ── Types ─────────────────────────────────────────────────────────────────

interface TrendPoint {
  snapshot_time: string
  total_issues: number
  p0_count: number
  stale_count: number
  eta_expiring: number
  unmerged_cls: number
}

interface TrendData {
  project: string
  days: number
  points: TrendPoint[]
}

interface MetricSummary {
  direction: string
  percent: number
  start: number
  end: number
}

interface TrendSummaryData {
  project: string
  days: number
  point_count: number
  metrics: Record<string, MetricSummary>
}

// ── Color config ──────────────────────────────────────────────────────────

const SERIES_CONFIG: Record<string, { label: string; color: string }> = {
  p0_count: { label: 'P0', color: '#ff4d4f' },
  stale_count: { label: '超过7天未更新', color: '#722ed1' },
  eta_expiring: { label: 'ETA 到期', color: '#fa8c16' },
  unmerged_cls: { label: '未合并 CL', color: '#595959' },
  total_issues: { label: '总问题', color: '#1890ff' },
}

const SERIES_ORDER = ['p0_count', 'stale_count', 'eta_expiring', 'unmerged_cls']

const DAY_OPTIONS = [
  { value: 7, label: '7天' },
  { value: 14, label: '14天' },
  { value: 30, label: '30天' },
]

// ── Helpers ───────────────────────────────────────────────────────────────

const formatTime = (iso: string) => {
  try {
    const d = new Date(iso)
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getDate().toString().padStart(2, '0')}`
  } catch { return iso }
}

const formatTooltipTime = (iso: string) => {
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

interface CustomTooltipProps {
  active?: boolean
  payload?: any[]
  label?: string
}

const CustomTooltip: React.FC<CustomTooltipProps> = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8,
      padding: '10px 14px', boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    }}>
      <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 12 }}>
        {formatTooltipTime(label || '')}
      </div>
      {payload.map((entry: any) => {
        const cfg = SERIES_CONFIG[entry.dataKey] || { label: entry.dataKey, color: '#999' }
        return (
          <div key={entry.dataKey} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: entry.color || cfg.color, display: 'inline-block' }} />
            <span style={{ fontSize: 12, color: '#666' }}>{cfg.label}:</span>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{entry.value}</span>
          </div>
        )
      })}
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────

const TrendChart: React.FC<{ projectNames?: string[]; defaultProject?: string }> = ({ projectNames, defaultProject = '' }) => {
  const [days, setDays] = useState(7)
  const [project, setProject] = useState(defaultProject)
  const [trend, setTrend] = useState<TrendData | null>(null)
  const [summary, setSummary] = useState<TrendSummaryData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const params = new URLSearchParams({ days: String(days) })
        if (project) params.set('project', project)

        const [trendData, summaryData] = await Promise.all([
          apiFetch<TrendData>(`/api/trends?${params.toString()}`),
          apiFetch<TrendSummaryData>(`/api/trends/summary?${params.toString()}`),
        ])

        if (!cancelled) {
          setTrend(trendData)
          setSummary(summaryData)
        }
      } catch (e: any) {
        if (!cancelled) setError(e.message || '加载趋势数据失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [days, project])

  const points = trend?.points || []
  const metrics = summary?.metrics || {}

  return (
    <Card
      size="small"
      title={
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          趋势分析
        </span>
      }
      extra={
        <div style={{ display: 'flex', gap: 8 }}>
          {projectNames && projectNames.length > 0 && (
            <Select
              size="small"
              value={project}
              onChange={setProject}
              style={{ width: 140 }}
              options={[
                { value: '', label: '全部项目' },
                ...projectNames.map(n => ({ value: n, label: n })),
              ]}
            />
          )}
          <Select
            size="small"
            value={days}
            onChange={setDays}
            style={{ width: 80 }}
            options={DAY_OPTIONS}
          />
        </div>
      }
      style={{ borderRadius: 8, marginBottom: 24 }}
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 0' }}>
          <Spin />
        </div>
      ) : error ? (
        <Alert type="error" showIcon message="加载失败" description={error} />
      ) : points.length === 0 ? (
        <Empty
          description={
            <span style={{ fontSize: 13, color: '#999' }}>
              数据采集中，请稍后再看
            </span>
          }
          style={{ padding: '40px 0', margin: 0 }}
        >
          <Text type="secondary" style={{ fontSize: 12 }}>
            指标快照每 5 分钟采集一次，首次采集后即可查看趋势
          </Text>
        </Empty>
      ) : (
        <>
          {/* ── Trend direction indicators ───────────────────────── */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            {SERIES_ORDER.map(key => {
              const m = metrics[key]
              const cfg = SERIES_CONFIG[key]
              if (!m || !cfg) return null
              const colorVal = m.direction === '↑' ? '#ff4d4f'
                : m.direction === '↓' ? '#52c41a'
                : '#8c8c8c'
              return (
                <Col key={key} span={6}>
                  <Statistic
                    title={<span style={{ fontSize: 11, color: '#999' }}>{cfg.label}</span>}
                    value={`${m.direction} ${Math.abs(m.percent)}%`}
                    valueStyle={{ fontSize: 16, fontWeight: 600, color: colorVal }}
                    suffix={
                      <span style={{ fontSize: 11, color: '#999', fontWeight: 400 }}>
                        ({m.start} → {m.end})
                      </span>
                    }
                  />
                </Col>
              )
            })}
          </Row>

          {/* ── Chart ────────────────────────────────────────────── */}
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={points} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                {SERIES_ORDER.map(key => (
                  <linearGradient key={key} id={`gradient_${key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={SERIES_CONFIG[key].color} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={SERIES_CONFIG[key].color} stopOpacity={0.02} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis
                dataKey="snapshot_time"
                tickFormatter={formatTime}
                tick={{ fontSize: 11, fill: '#999' }}
                axisLine={{ stroke: '#f0f0f0' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 11, fill: '#999' }}
                axisLine={false}
                tickLine={false}
                allowDecimals={false}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend
                formatter={(value: string) => {
                  const cfg = SERIES_CONFIG[value]
                  return cfg ? cfg.label : value
                }}
                wrapperStyle={{ fontSize: 12 }}
              />
              {SERIES_ORDER.map(key => (
                <Area
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={SERIES_CONFIG[key].color}
                  fill={`url(#gradient_${key})`}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 1 }}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </>
      )}
    </Card>
  )
}

export default TrendChart
