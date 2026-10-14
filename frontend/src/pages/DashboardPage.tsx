import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Row, Col, Card, Statistic, Button, Spin, Alert, Modal, Form,
  Input, InputNumber, Switch, message as antMsg, Typography, Tag,
  Divider, Empty, Tooltip, Table, Collapse, Tabs,
} from 'antd'
import {
  SettingOutlined,
  ReloadOutlined,
  PlusOutlined,
  DeleteOutlined,
  WarningOutlined,
  DashboardOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import {
  fetchDashboardSummary,
  fetchDashboardConfig,
  updateDashboardConfig,
  apiFetch,
  DashboardSummary,
  DashboardConfig,
  DashboardTrend,
} from '../services/chatService'
import TrendChart from '../components/Dashboard/TrendChart'
import FocusCommandList from '../components/Dashboard/FocusCommandList'

const { Text, Title } = Typography

// ── Statistic Card ──────────────────────────────────────────────────────────

interface MetricCardProps {
  label: string
  value: number
  color: string
  icon?: React.ReactNode
  suffix?: string
  onClick?: () => void
}

const MetricCard: React.FC<MetricCardProps> = ({ label, value, color, icon, suffix, onClick }) => (
  <Card
    hoverable
    style={{
      borderRadius: 12,
      borderLeft: `4px solid ${color}`,
      boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
      height: '100%',
      cursor: onClick ? 'pointer' : undefined,
    }}
    bodyStyle={{ padding: '20px 24px' }}
    onClick={onClick}
  >
    <Statistic
      title={
        <span style={{ fontSize: 13, color: '#666' }}>
          {icon} {label}
        </span>
      }
      value={value}
      valueStyle={{
        fontSize: 32,
        fontWeight: 700,
        color: value > 0 ? color : '#595959',
      }}
      suffix={suffix}
    />
  </Card>
)

// ── Status badge ────────────────────────────────────────────────────────────

const MCPStatusBadge: React.FC<{ hasErrors: boolean }> = ({ hasErrors }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
    <span
      style={{
        width: 8, height: 8, borderRadius: '50%', display: 'inline-block',
        background: hasErrors ? '#ff4d4f' : '#52c41a',
        boxShadow: hasErrors
          ? '0 0 6px rgba(255,77,79,0.4)'
          : '0 0 6px rgba(82,196,26,0.4)',
      }}
    />
    {hasErrors ? '连接异常' : '连接正常'}
  </span>
)

// ── Time display helper ─────────────────────────────────────────────────────

const formatTime = (iso: string) => {
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch { return iso }
}

// ── Trend Section ────────────────────────────────────────────────────────────

const TrendSection: React.FC<{ trend: DashboardTrend; jiraWebUrl?: string }> = ({ trend, jiraWebUrl }) => {
  const baseUrl = jiraWebUrl ? jiraWebUrl.replace(/\/+$/, '') : ''
  const newCount = trend.new_jiras?.length || 0
  const removedCount = trend.removed_jiras?.length || 0
  const changedCount = trend.status_changes?.length || 0

  if (newCount === 0 && removedCount === 0 && changedCount === 0) {
    return null
  }

  const linkRender = (key: string) => {
    const href = baseUrl ? `${baseUrl}/browse/${key}` : undefined
    return href
      ? <a href={href} target="_blank" rel="noopener noreferrer" style={{ fontFamily: 'monospace', fontSize: 12 }}>{key}</a>
      : <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{key}</span>
  }

  const itemColumns = [
    { title: 'Key', dataIndex: 'key', key: 'key', width: 130, render: linkRender },
    { title: '概要', dataIndex: 'summary', key: 'summary', width: 300, render: (v: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100 },
    { title: '优先级', dataIndex: 'priority', key: 'priority', width: 80 },
    { title: '类型', dataIndex: 'issuetype', key: 'issuetype', width: 80 },
  ]

  const changeColumns = [
    { title: 'Key', dataIndex: 'key', key: 'key', width: 130, render: linkRender },
    { title: '概要', dataIndex: 'summary', key: 'summary', width: 300, render: (v: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
    { title: '原状态', dataIndex: 'from', key: 'from', width: 120, render: (v: string) => <Tag color="default">{v}</Tag> },
    { title: '新状态', dataIndex: 'to', key: 'to', width: 120, render: (v: string) => <Tag color="blue">{v}</Tag> },
  ]

  const items = []
  if (newCount > 0) {
    items.push({
      key: 'new',
      label: <span><ArrowUpOutlined style={{ color: '#52c41a', marginRight: 6 }} />新增 Jira ({newCount})</span>,
      children: <Table dataSource={trend.new_jiras} columns={itemColumns} rowKey="key" size="small" pagination={false} scroll={{ x: 700 }} />,
    })
  }
  if (removedCount > 0) {
    items.push({
      key: 'removed',
      label: <span><ArrowDownOutlined style={{ color: '#ff4d4f', marginRight: 6 }} />已关闭/移除 Jira ({removedCount})</span>,
      children: <Table dataSource={trend.removed_jiras} columns={itemColumns} rowKey="key" size="small" pagination={false} scroll={{ x: 700 }} />,
    })
  }
  if (changedCount > 0) {
    items.push({
      key: 'changed',
      label: <span><SwapOutlined style={{ color: '#fa8c16', marginRight: 6 }} />状态变更 ({changedCount})</span>,
      children: <Table dataSource={trend.status_changes} columns={changeColumns} rowKey="key" size="small" pagination={false} scroll={{ x: 700 }} />,
    })
  }

  return (
    <div style={{ marginBottom: 24 }}>
      <Divider orientation="left" style={{ fontSize: 13, color: '#999' }}>
        趋势对比（与上次更新相比）
      </Divider>
      <Card size="small" style={{ borderRadius: 8, background: '#fafafa' }}>
        <div style={{ display: 'flex', gap: 32, marginBottom: items.length > 0 ? 12 : 0 }}>
          <Statistic title="新增" value={newCount} valueStyle={{ color: '#52c41a', fontSize: 24 }} prefix={<ArrowUpOutlined />} />
          <Statistic title="已关闭" value={removedCount} valueStyle={{ color: '#ff4d4f', fontSize: 24 }} prefix={<ArrowDownOutlined />} />
          <Statistic title="状态变更" value={changedCount} valueStyle={{ color: '#fa8c16', fontSize: 24 }} prefix={<SwapOutlined />} />
        </div>
        {items.length > 0 && (
          <Collapse items={items} ghost size="small" />
        )}
      </Card>
    </div>
  )
}

// ── Main Dashboard Page ─────────────────────────────────────────────────────

const DashboardPage: React.FC = () => {
  const navigate = useNavigate()
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [alertCount, setAlertCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [configVisible, setConfigVisible] = useState(false)
  const [config, setConfig] = useState<DashboardConfig | null>(null)
  const [configLoading, setConfigLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState<string>('all')
  const intervalRef = useRef<number | null>(null)

  // ── Load summary ──────────────────────────────────────────────────────

  const loadSummary = useCallback(async (showLoader = false, forceRefresh = false) => {
    if (showLoader) setLoading(true)
    try {
      const data = await fetchDashboardSummary(forceRefresh)
      setSummary(data)
      setError(null)
    } catch (e: any) {
      console.error('Dashboard summary error:', e)
      setError(e.message || '加载看板数据失败')
    } finally {
      if (showLoader) setLoading(false)
    }
  }, [])

  // ── Auto refresh ──────────────────────────────────────────────────────

  useEffect(() => {
    loadSummary(true)
    intervalRef.current = window.setInterval(() => loadSummary(false), 30_000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [loadSummary])

  // ── Fetch alert count per project tab ──────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const params = new URLSearchParams()
        if (activeTab !== 'all') params.set('project_name', activeTab)
        const qs = params.toString()
        const res = await apiFetch<{ by_status: Record<string, number> }>(
          `/api/alerts/summary${qs ? '?' + qs : ''}`
        )
        setAlertCount(res.by_status?.ACTIVE ?? 0)
      } catch { /* silent */ }
    })()
  }, [activeTab])

  // ── Config ────────────────────────────────────────────────────────────

  const openConfig = async () => {
    setConfigLoading(true)
    setConfigVisible(true)
    try {
      const data = await fetchDashboardConfig()
      setConfig(data)
    } catch (e: any) {
      antMsg.error('加载配置失败: ' + e.message)
    } finally {
      setConfigLoading(false)
    }
  }

  const handleConfigSave = async (values: any) => {
    setSaving(true)
    try {
      const payload: DashboardConfig = {
        projects: values.projects || [],
        thresholds: {
          eta_expiring_days: values.eta_expiring_days ?? 7,
          stale_days: values.stale_days ?? 7,
        },
        refresh_interval_seconds: values.refresh_interval_seconds ?? 30,
        jira_web_url: values.jira_web_url || '',
      }
      await updateDashboardConfig(payload)
      antMsg.success('配置已保存')
      setConfigVisible(false)
      loadSummary(true, true)
    } catch (e: any) {
      antMsg.error('保存配置失败: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────

  if (loading && !summary) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Spin size="large" tip="加载看板..." />
      </div>
    )
  }

  const total = summary?.total || { issues: 0, p0: 0, eta_expiring: 0, stale: 0, epic_total: 0 }
  const hasErrors = summary?.errors && summary.errors.length > 0
  const selectedProj = activeTab !== 'all' && summary
    ? summary.projects.find(p => p.name === activeTab) || null
    : null
  const metricData = selectedProj
    ? { issues: selectedProj.issues, p0: selectedProj.p0, eta_expiring: selectedProj.eta_expiring, stale: selectedProj.stale }
    : total

  return (
    <div style={{ padding: '24px 32px', height: '100%', overflow: 'auto' }}>
      {/* ── Header ──────────────────────────────────────────────────── */}
      <Row justify="space-between" align="middle" style={{ marginBottom: 20 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <DashboardOutlined style={{ marginRight: 8, color: '#4A90E2' }} />
            看板
          </Title>
        </Col>
        <Col>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <MCPStatusBadge hasErrors={!!hasErrors} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              更新: {summary?.updated_at ? formatTime(summary.updated_at) : '--'}
            </Text>
            <Tooltip title="手动刷新">
              <Button icon={<ReloadOutlined />} size="small" onClick={() => loadSummary(true, true)} />
            </Tooltip>
            <Button icon={<SettingOutlined />} onClick={openConfig}>
              配置
            </Button>
          </div>
        </Col>
      </Row>

      {/* ── Error banner ────────────────────────────────────────────── */}
      {hasErrors && (
        <Alert
          type="warning"
          showIcon
          message="部分数据获取异常"
          description={summary!.errors!.join('; ')}
          style={{ marginBottom: 16, borderRadius: 8 }}
          closable
        />
      )}

      {/* ── Alert Badge (removed, now shown as metric card) ──────── */}
      {error && (
        <Alert
          type="error"
          showIcon
          message="加载失败"
          description={error}
          style={{ marginBottom: 16, borderRadius: 8 }}
          closable
        />
      )}

      {/* ── Project Tabs ──────────────────────────────────────────── */}
      {summary && summary.projects.length > 0 && (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          style={{ marginBottom: 0 }}
          items={[
            { key: 'all', label: '全部' },
            ...summary.projects.map(p => ({ key: p.name, label: p.name })),
          ]}
        />
      )}

      {/* ── Empty state ─────────────────────────────────────────────── */}
      {summary && summary.projects.length === 0 && !error && (
        <div style={{ textAlign: 'center', padding: '80px 0' }}>
          <Empty description="尚未配置监控项目">
            <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
              点击右上角「配置」按钮，添加需要监控的 Jira 项目
            </Text>
            <Button type="primary" icon={<SettingOutlined />} onClick={openConfig}>
              立即配置
            </Button>
          </Empty>
        </div>
      )}

      {/* ── Metric cards ────────────────────────────────────────────── */}
      {summary && summary.projects.length > 0 && (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={12} sm={12} md={6} lg={4}>
              <MetricCard label="总问题" value={metricData.issues} color="#1890ff"
                onClick={() => {
                  const project = activeTab === 'all' ? 'all' : activeTab
                  navigate(`/issues?project=${encodeURIComponent(project)}`)
                }}
              />
            </Col>
            <Col xs={12} sm={12} md={6} lg={4}>
              <MetricCard label="P0 问题" value={metricData.p0} color={metricData.p0 > 0 ? '#ff4d4f' : '#52c41a'} icon={<WarningOutlined />}
                onClick={() => {
                  const project = activeTab === 'all' ? 'all' : activeTab
                  navigate(`/issues?project=${encodeURIComponent(project)}&filter=p0`)
                }}
              />
            </Col>
            <Col xs={12} sm={12} md={6} lg={4}>
              <MetricCard
                label="ETA 到期"
                value={metricData.eta_expiring}
                color={metricData.eta_expiring > 0 ? '#fa8c16' : '#52c41a'}
                onClick={() => {
                  const project = activeTab === 'all' ? 'all' : activeTab
                  navigate(`/issues?project=${encodeURIComponent(project)}&filter=eta_expiring`)
                }}
              />
            </Col>
            <Col xs={12} sm={12} md={6} lg={4}>
              <MetricCard
                label="超过 7 天未更新"
                value={metricData.stale}
                color={metricData.stale > 0 ? '#722ed1' : '#52c41a'}
                onClick={() => {
                  const project = activeTab === 'all' ? 'all' : activeTab
                  navigate(`/issues?project=${encodeURIComponent(project)}&filter=stale`)
                }}
              />
            </Col>
            <Col xs={12} sm={12} md={6} lg={4}>
              <MetricCard
                label="风险预警"
                value={alertCount}
                color={alertCount > 0 ? '#ff4d4f' : '#52c41a'}
                icon={<WarningOutlined />}
                onClick={() => {
                  const target = activeTab === 'all' ? '/alerts' : `/alerts?project=${encodeURIComponent(activeTab)}`
                  navigate(target)
                }}
              />
            </Col>
          </Row>

          {/* ── Trend Chart (Phase C) ────────────────────────────────── */}
          <Collapse
            ghost
            size="small"
            items={[{
              key: 'trends',
              label: <span style={{ fontSize: 13, color: '#666' }}>趋势分析</span>,
              children: <TrendChart
                key={activeTab}
                projectNames={activeTab === 'all' ? summary.projects.map(p => p.name) : [activeTab]}
                defaultProject={activeTab === 'all' ? '' : activeTab}
              />,
            }]}
            style={{ marginBottom: 16 }}
          />

          {/* ── Trend comparison (per-project when tab selected) ─────── */}
          {(summary as any)._trend && (() => {
            const projectTrends = (summary as any).project_trends || {}
            const trend = activeTab !== 'all' && projectTrends[activeTab]
              ? projectTrends[activeTab]
              : (summary as any)._trend
            return <TrendSection trend={trend} jiraWebUrl={summary.jira_web_url} />
          })()}

        </>
      )}

      {/* ── 我的关注指令（P-B/P-C：PM 关注集合状态图，位于项目卡片区域之下）── */}
      <div style={{ marginTop: 24 }}>
        <FocusCommandList jiraWebUrl={summary?.jira_web_url} />
      </div>

      {/* ── Config Modal ────────────────────────────────────────────── */}
      <Modal
        title={
          <span>
            <SettingOutlined style={{ marginRight: 8 }} />
            看板配置
          </span>
        }
        open={configVisible}
        onCancel={() => setConfigVisible(false)}
        footer={null}
        width={720}
        destroyOnClose
      >
        {configLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : (
          <ConfigForm
            config={config!}
            saving={saving}
            onSave={handleConfigSave}
            onCancel={() => setConfigVisible(false)}
          />
        )}
      </Modal>
    </div>
  )
}

// ── Project Config Card (with basic/advanced JQL toggle) ────────────────────

const ProjectConfigCard: React.FC<{
  fieldName: number
  restField: any
  form: any
  onRemove: () => void
}> = ({ fieldName, restField, form, onRemove }) => {
  const projects = Form.useWatch('projects', form) || []
  const proj = projects[fieldName] || {}
  const isAdvanced = !!(proj?.advanced_jql)

  const toggleMode = (checked: boolean) => {
    const path = ['projects', fieldName, 'advanced_jql']
    if (checked) {
      form.setFieldValue(path, 'project = TV AND labels = oem_release_new AND status not in (Closed, Verified, Done, Resolved)')
    } else {
      form.setFieldValue(path, '')
    }
  }

  return (
    <Card
      size="small"
      style={{ marginBottom: 12, borderRadius: 8, background: '#fafafa' }}
      extra={<Button type="text" danger icon={<DeleteOutlined />} onClick={onRemove} />}
    >
      <Row gutter={12}>
        <Col span={6}>
          <Form.Item {...restField} name={[fieldName, 'name']} label="项目名称" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="如：长虹T6D" />
          </Form.Item>
        </Col>
        <Col span={3}>
          <Form.Item {...restField} name={[fieldName, 'enabled']} label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item label="检索模式">
            <Switch
              checked={isAdvanced}
              onChange={toggleMode}
              checkedChildren="JQL"
              unCheckedChildren="基本"
            />
          </Form.Item>
        </Col>
      </Row>

      {isAdvanced ? (
        <>
          <Form.Item {...restField} name={[fieldName, 'advanced_jql']} label="JQL 查询条件">
            <Input.TextArea rows={3} placeholder="project = TV AND labels = oem_release_new AND status not in (Closed, Verified, Done)" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item {...restField} name={[fieldName, 'eta_custom_field']} label="ETA 自定义字段（选填，用于 ETA 到期预警）">
                <Input placeholder="如：customfield_12345" />
              </Form.Item>
            </Col>
          </Row>
        </>
      ) : (
        <>
          <Row gutter={12}>
            <Col span={4}>
              <Form.Item {...restField} name={[fieldName, 'jira_project_key']} label="Jira Key">
                <Input placeholder="如：TV" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item {...restField} name={[fieldName, 'labels']} label="Labels（逗号分隔）">
                <Input placeholder="如：oem_release_new" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item {...restField} name={[fieldName, 'epics']} label="Epic IDs（逗号分隔）">
                <Input placeholder="如：TV-900001,TV-900002" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item {...restField} name={[fieldName, 'eta_custom_field']} label="ETA 自定义字段（选填）">
                <Input placeholder="如：customfield_12345" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item {...restField} name={[fieldName, 'exclude_statuses']} label="排除状态（逗号分隔）">
                <Input placeholder="Closed,Verified,Done" />
              </Form.Item>
            </Col>
          </Row>

        </>
      )}
    </Card>
  )
}

// ── Config Form (separate component to keep things clean) ───────────────────

const ConfigForm: React.FC<{
  config: DashboardConfig
  saving: boolean
  onSave: (values: any) => Promise<void>
  onCancel: () => void
}> = ({ config, saving, onSave, onCancel }) => {
  const [form] = Form.useForm()

  useEffect(() => {
    form.setFieldsValue({
      projects: config?.projects || [],
      eta_expiring_days: config?.thresholds?.eta_expiring_days ?? 7,
      stale_days: config?.thresholds?.stale_days ?? 7,
      refresh_interval_seconds: config?.refresh_interval_seconds ?? 30,
      jira_web_url: (config as any)?.jira_web_url || '',
    })
  }, [config, form])

  const handleFinish = (values: any) => {
    onSave(values)
  }

  return (
    <Form form={form} layout="vertical" onFinish={handleFinish} initialValues={{
      projects: config?.projects || [],
      eta_expiring_days: config?.thresholds?.eta_expiring_days ?? 7,
      stale_days: config?.thresholds?.stale_days ?? 7,
      refresh_interval_seconds: config?.refresh_interval_seconds ?? 30,
      jira_web_url: (config as any)?.jira_web_url || '',
    }}>
      <Text strong style={{ fontSize: 13, color: '#666' }}>监控项目</Text>

      <Form.List name="projects">
        {(fields, { add, remove }) => (
          <div style={{ marginTop: 8 }}>
            {fields.length === 0 && (
              <Text type="secondary" style={{ display: 'block', textAlign: 'center', padding: 20 }}>
                暂无项目，点击下方按钮添加
              </Text>
            )}

            {fields.map(({ key, name: fieldName, ...restField }) => (
              <ProjectConfigCard
                key={key}
                fieldName={fieldName}
                restField={restField}
                form={form}
                onRemove={() => remove(fieldName)}
              />
            ))}

            <Button
              type="dashed"
              onClick={() => add({
                name: '', enabled: true, jira_project_key: '', epics: '',
                labels: '', eta_custom_field: '', exclude_statuses: 'Closed,Verified,Done',
                advanced_jql: '',
              })}
              block
              icon={<PlusOutlined />}
              style={{ marginBottom: 16, borderRadius: 8 }}
            >
              添加项目
            </Button>
          </div>
        )}
      </Form.List>

      <Divider />

      <Text strong style={{ fontSize: 13, color: '#666' }}>全局阈值</Text>

      <Row gutter={16} style={{ marginTop: 12 }}>
        <Col span={8}>
          <Form.Item name="stale_days" label="超过 N 天未更新视为异常">
            <InputNumber min={1} max={90} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item name="eta_expiring_days" label="ETA 到期预警天数">
            <InputNumber min={1} max={90} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item name="refresh_interval_seconds" label="自动刷新间隔（秒）">
            <InputNumber min={10} max={300} step={10} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Form.Item name="jira_web_url" label="Jira Web URL（用于问题链接跳转）">
            <Input placeholder="如：https://jira.example.com" />
          </Form.Item>
        </Col>
      </Row>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
        <Button onClick={onCancel}>取消</Button>
        <Button type="primary" htmlType="submit" loading={saving}>
          保存配置
        </Button>
      </div>
    </Form>
  )
}

export default DashboardPage
