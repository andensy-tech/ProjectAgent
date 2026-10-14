import React, { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Table, Button, Tag, Typography, Empty, Spin, Alert, Tabs,
  Space, message as antMsg, Tooltip, Row, Col,
} from 'antd'
import {
  WarningOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  BellOutlined,
} from '@ant-design/icons'
import { apiFetch, fetchDashboardConfig } from '../services/chatService'

const { Text, Title } = Typography

// ── Types ─────────────────────────────────────────────────────────────────

interface AlertItem {
  id: number
  rule_id: string
  rule_name: string
  category: string
  level: string
  title: string
  description: string
  impact: string
  suggestion: string
  evidence: any[]
  status: string
  created_at: string | null
  updated_at: string | null
  resolved_at: string | null
  resolved_by: string | null
  resolution_note: string | null
}

interface AlertSummary {
  total: number
  by_status: Record<string, number>
  by_level: Record<string, number>
  by_category: Record<string, number>
  active_high: number
}

// ── Helpers ──────────────────────────────────────────────────────────────

const LEVEL_CONFIG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  HIGH: { color: '#ff4d4f', icon: <ExclamationCircleOutlined />, label: '高' },
  MEDIUM: { color: '#fa8c16', icon: <WarningOutlined />, label: '中' },
  LOW: { color: '#1890ff', icon: <InfoCircleOutlined />, label: '低' },
}

const STATUS_TAG_COLOR: Record<string, string> = {
  ACTIVE: 'red',
  ACKNOWLEDGED: 'orange',
  RESOLVED: 'green',
  IGNORED: 'default',
}

const formatTime = (iso: string | null) => {
  if (!iso) return '--'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

// ── Main Alerts Page ─────────────────────────────────────────────────────

const AlertsPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams()
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [loading, setLoading] = useState(true)
  const [summary, setSummary] = useState<AlertSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeStatusTab, setActiveStatusTab] = useState('all')
  const [activeProjectTab, setActiveProjectTab] = useState(searchParams.get('project') || 'all')
  const [projectNames, setProjectNames] = useState<string[]>([])
  const [scanning, setScanning] = useState(false)
  const [updatingId, setUpdatingId] = useState<number | null>(null)

  const statusFilter = activeStatusTab === 'all' ? '' : activeStatusTab.toUpperCase()
  const projectFilter = activeProjectTab === 'all' ? '' : activeProjectTab

  const loadAlerts = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: '100' })
      if (statusFilter) params.set('status', statusFilter)
      if (projectFilter) params.set('project_name', projectFilter)
      const data = await apiFetch<{ alerts: AlertItem[]; total: number }>(
        `/api/alerts?${params.toString()}`
      )
      setAlerts(data.alerts || [])
      setError(null)
    } catch (e: any) {
      console.error('Failed to load alerts:', e)
      setError(e.message || '加载告警失败')
    }
  }, [statusFilter, projectFilter])

  const loadSummary = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      if (projectFilter) params.set('project_name', projectFilter)
      const qs = params.toString()
      const data = await apiFetch<AlertSummary>(`/api/alerts/summary${qs ? '?' + qs : ''}`)
      setSummary(data)
    } catch (e) {
      console.error('Failed to load alert summary:', e)
    }
  }, [projectFilter])

  const loadAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([
      loadAlerts(),
      loadSummary(),
      fetchDashboardConfig().then(cfg => {
        const names = cfg.projects?.filter(p => p.enabled).map(p => p.name) || []
        setProjectNames(names)
        // If URL has a project param but it's not in the list, reset to 'all'
        if (activeProjectTab !== 'all' && !names.includes(activeProjectTab)) {
          setActiveProjectTab('all')
        }
      }).catch(() => {}),
    ])
    setLoading(false)
  }, [loadAlerts, loadSummary, activeProjectTab])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  const handleScan = async () => {
    setScanning(true)
    try {
      const result = await apiFetch<{ created: number; alert_ids: number[] }>('/api/alerts/scan', {
        method: 'POST',
      })
      antMsg.success(`扫描完成，新增 ${result.created} 条告警`)
      await loadAll()
    } catch (e: any) {
      antMsg.error('扫描失败: ' + (e.message || ''))
    } finally {
      setScanning(false)
    }
  }

  const handleStatusUpdate = async (id: number, status: string) => {
    setUpdatingId(id)
    try {
      await apiFetch(`/api/alerts/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      })
      antMsg.success(`告警已${status === 'ACKNOWLEDGED' ? '确认' : status === 'RESOLVED' ? '解决' : '忽略'}`)
      await loadAll()
    } catch (e: any) {
      antMsg.error('操作失败: ' + (e.message || ''))
    } finally {
      setUpdatingId(null)
    }
  }

  const expandedRowRender = (record: AlertItem) => (
    <div style={{ padding: '8px 0', maxWidth: 700 }}>
      <div style={{ marginBottom: 8 }}>
        <Text strong style={{ fontSize: 13, color: '#666' }}>描述：</Text>
        <Text style={{ fontSize: 13 }}>{record.description || '--'}</Text>
      </div>
      {record.impact && (
        <div style={{ marginBottom: 8 }}>
          <Text strong style={{ fontSize: 13, color: '#666' }}>影响：</Text>
          <Text style={{ fontSize: 13 }}>{record.impact}</Text>
        </div>
      )}
      {record.suggestion && (
        <div style={{ marginBottom: 8 }}>
          <Text strong style={{ fontSize: 13, color: '#666' }}>建议：</Text>
          <Text style={{ fontSize: 13 }}>{record.suggestion}</Text>
        </div>
      )}
      {record.evidence && record.evidence.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Text strong style={{ fontSize: 13, color: '#666' }}>证据：</Text>
          <Text style={{ fontSize: 13, fontFamily: 'monospace' }}>
            {JSON.stringify(record.evidence)}
          </Text>
        </div>
      )}
      {record.resolution_note && (
        <div style={{ marginBottom: 8 }}>
          <Text strong style={{ fontSize: 13, color: '#666' }}>处理备注：</Text>
          <Text style={{ fontSize: 13 }}>{record.resolution_note}</Text>
        </div>
      )}
      <div style={{ marginTop: 8 }}>
        <Space size="small">
          {record.status === 'ACTIVE' && (
            <>
              <Button size="small" type="primary" ghost
                onClick={() => handleStatusUpdate(record.id, 'ACKNOWLEDGED')}
                loading={updatingId === record.id}>
                确认
              </Button>
              <Button size="small" type="primary" danger ghost
                onClick={() => handleStatusUpdate(record.id, 'IGNORED')}
                loading={updatingId === record.id}>
                忽略
              </Button>
            </>
          )}
          {['ACTIVE', 'ACKNOWLEDGED'].includes(record.status) && (
            <Button size="small" type="primary"
              onClick={() => handleStatusUpdate(record.id, 'RESOLVED')}
              loading={updatingId === record.id}>
              标记解决
            </Button>
          )}
        </Space>
      </div>
    </div>
  )

  const columns = [
    {
      title: '级别', dataIndex: 'level', key: 'level', width: 60,
      render: (level: string) => {
        const cfg = LEVEL_CONFIG[level] || { color: '#999', icon: null, label: level }
        return (
          <Tooltip title={cfg.label}>
            <span style={{ color: cfg.color, fontSize: 16 }}>{cfg.icon}</span>
          </Tooltip>
        )
      },
    },
    {
      title: '规则', dataIndex: 'rule_name', key: 'rule_name', width: 140,
      render: (v: string) => <span style={{ fontSize: 12 }}>{v || '--'}</span>,
    },
    {
      title: '标题', dataIndex: 'title', key: 'title',
      render: (v: string) => <span style={{ fontSize: 13 }}>{v || '--'}</span>,
    },
    {
      title: '分类', dataIndex: 'category', key: 'category', width: 90,
      render: (v: string) => {
        const colors: Record<string, string> = {
          PROGRESS: 'purple', QUALITY: 'cyan', SCOPE: 'geekblue',
        }
        const labels: Record<string, string> = {
          PROGRESS: '进度', QUALITY: '质量', SCOPE: '范围',
        }
        return <Tag color={colors[v] || 'default'} style={{ fontSize: 11 }}>{labels[v] || v}</Tag>
      },
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => {
        const labels: Record<string, string> = {
          ACTIVE: '待处理', ACKNOWLEDGED: '已确认', RESOLVED: '已解决', IGNORED: '已忽略',
        }
        return <Tag color={STATUS_TAG_COLOR[v] || 'default'}>{labels[v] || v}</Tag>
      },
    },
    {
      title: '时间', dataIndex: 'created_at', key: 'created_at', width: 120,
      render: (v: string | null) => (
        <span style={{ fontSize: 11, color: '#999' }}>{formatTime(v)}</span>
      ),
    },
  ]

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Spin size="large" tip="加载告警列表..." />
      </div>
    )
  }

  const activeCount = summary?.by_status?.ACTIVE ?? 0

  return (
    <div style={{ padding: '24px 32px', height: '100%', overflow: 'auto' }}>
      {/* ── Header ──────────────────────────────────────────────── */}
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <BellOutlined style={{ marginRight: 8, color: '#ff4d4f' }} />
            预警中心
          </Title>
        </Col>
        <Col>
          <Space size="small">
            <Tooltip title="手动运行一次告警扫描">
              <Button icon={<ReloadOutlined />} size="small" onClick={handleScan} loading={scanning}>
                扫描
              </Button>
            </Tooltip>
          </Space>
        </Col>
      </Row>

      {/* ── Error ───────────────────────────────────────────────── */}
      {error && (
        <Alert type="error" showIcon message="加载失败" description={error}
          style={{ marginBottom: 16, borderRadius: 8 }} closable />
      )}

      {/* ── Summary bar ─────────────────────────────────────────── */}
      <div style={{
        display: 'flex', gap: 24, marginBottom: 16, padding: '12px 16px',
        background: '#fafafa', borderRadius: 8, border: '1px solid #f0f0f0',
      }}>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>全部</Text>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#333' }}>{summary?.total ?? 0}</div>
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>待处理</Text>
          <div style={{ fontSize: 22, fontWeight: 700, color: activeCount > 0 ? '#ff4d4f' : '#52c41a' }}>
            {activeCount}
          </div>
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>HIGH 级别</Text>
          <div style={{ fontSize: 22, fontWeight: 700, color: (summary?.by_level?.HIGH ?? 0) > 0 ? '#ff4d4f' : '#333' }}>
            {summary?.by_level?.HIGH ?? 0}
          </div>
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>MEDIUM</Text>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#fa8c16' }}>
            {summary?.by_level?.MEDIUM ?? 0}
          </div>
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>LOW</Text>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#1890ff' }}>
            {summary?.by_level?.LOW ?? 0}
          </div>
        </div>
      </div>

      {/* ── Project Tabs ────────────────────────────────────────── */}
      {projectNames.length > 0 && (
        <Tabs
          size="small"
          activeKey={activeProjectTab}
          onChange={(key) => {
            setActiveProjectTab(key)
            // Sync project filter to URL
            const params = new URLSearchParams(searchParams)
            if (key === 'all') params.delete('project')
            else params.set('project', key)
            setSearchParams(params, { replace: true })
          }}
          style={{ marginBottom: 0 }}
          items={[
            { key: 'all', label: '全部项目' },
            ...projectNames.map(n => ({ key: n, label: n })),
          ]}
        />
      )}

      {/* ── Status Tabs ─────────────────────────────────────────── */}
      <Tabs
        activeKey={activeStatusTab}
        onChange={setActiveStatusTab}
        items={[
          { key: 'all', label: `全部 (${summary?.total ?? 0})` },
          { key: 'active', label: `待处理 (${summary?.by_status?.ACTIVE ?? 0})` },
          { key: 'acknowledged', label: `已确认 (${summary?.by_status?.ACKNOWLEDGED ?? 0})` },
          { key: 'resolved', label: `已解决 (${summary?.by_status?.RESOLVED ?? 0})` },
          { key: 'ignored', label: `已忽略 (${summary?.by_status?.IGNORED ?? 0})` },
        ]}
        style={{ marginBottom: 0 }}
      />

      <Table<AlertItem>
        dataSource={alerts}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
        expandable={{
          expandedRowRender,
          rowExpandable: () => true,
        }}
        locale={{
          emptyText: (
            <div style={{ padding: '40px 0' }}>
              <Empty description={
                <span>
                  <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 6 }} />
                  一切正常，没有需要关注的告警
                </span>
              } />
            </div>
          ),
        }}
      />
    </div>
  )
}

export default AlertsPage
