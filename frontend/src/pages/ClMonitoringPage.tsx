import React, { useState, useEffect, useCallback } from 'react'
import {
  Row, Col, Card, Table, Button, Spin, Tag, Typography,
  Empty, Alert, Modal, Form, Input, Switch, Space, message as antMsg,
  Tabs, Popconfirm,
} from 'antd'
import {
  ReloadOutlined,
  WarningOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  SettingOutlined,
  PlusOutlined,
  DeleteOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  fetchClStatus, ProjectClStatus, JiraClGroup, ClItem,
  fetchClMonitoringConfig, saveClMonitoringConfig, ClMonitoringConfig,
  suppressCl, restoreCl, fetchSuppressedCls, ClSuppressItem,
  fetchClFocusSummary, ClFocusSummary, ClFocusItem,
} from '../services/chatService'

const { Text, Title } = Typography

// ── Helpers ──────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  MERGED: 'green',
  Merged: 'green',
  Merging: 'blue',
  NEW: 'orange',
  'In Code Review': 'processing',
  Verified: 'cyan',
  Resolved: 'lime',
  DRAFT: 'default',
  ABANDONED: 'default',
  Error: 'red',
}

function statusTag(status: string) {
  const color = STATUS_COLOR[status] || 'default'
  return <Tag color={color}>{status}</Tag>
}

// 部署时按实际环境修改（或改为从后端配置读取）
const JIRA_WEB_URL = 'https://jira.example.com'
const GERRIT_HOSTS: Record<string, string> = {
  gerrit1: 'gerrit-1.example.com',
  gerrit2: 'gerrit-2.example.com',
}

function jiraLink(key: string) {
  return `${JIRA_WEB_URL}/browse/${key}`
}

function gerritLink(instance: string, project: string, changeId: string) {
  const host = GERRIT_HOSTS[instance] || instance
  return `https://${host}/c/${project}/+/${changeId}`
}

// ── CL Table (per-Jira) ──────────────────────────────────────────

const CL_TABLE_COLUMNS = [
  {
    title: 'CL 编号', dataIndex: 'change_id', key: 'change_id', width: 100,
    render: (v: string, record: ClItem) => (
      <a
        href={gerritLink(record.instance, record.project, v)}
        target="_blank"
        rel="noopener noreferrer"
        style={{ fontWeight: 500 }}
      >
        {v}
      </a>
    ),
  },
  { title: '项目', dataIndex: 'project', key: 'project', width: 200, ellipsis: true },
  { title: '分支', dataIndex: 'branch', key: 'branch', width: 120, ellipsis: true },
  {
    title: '状态', dataIndex: 'status', key: 'status', width: 110,
    render: (v: string) => statusTag(v),
  },
  { title: 'Gerrit', dataIndex: 'instance', key: 'instance', width: 110 },
]

// 未合并 CL 表格：共享列 + 追加"操作"列（标记不关注）
const UNMERGED_TABLE_COLUMNS = (onSuppress: (it: ClItem) => void) => [
  ...CL_TABLE_COLUMNS,
  {
    title: '操作', key: 'action', width: 90,
    render: (_: any, record: ClItem) => (
      <Popconfirm
        title="标记为不需要关注？"
        description="标记后此 CL 将不再显示，也不再自动提醒。"
        okText="标记" cancelText="取消"
        onConfirm={() => onSuppress(record)}
      >
        <Button size="small" danger type="link" icon={<StopOutlined />}>不关注</Button>
      </Popconfirm>
    ),
  },
]

// ── Jira CL Group Card ───────────────────────────────────────────

const JiraGroupCard: React.FC<{ group: JiraClGroup; onSuppress: (it: ClItem) => void }> = ({ group, onSuppress }) => {
  const unmergedItems = group.items.filter(
    (it) => it.status !== 'MERGED' && it.status !== 'Merged' && it.status !== 'Merging',
  )
  const mergedItems = group.items.filter(
    (it) => it.status === 'MERGED' || it.status === 'Merged' || it.status === 'Merging',
  )
  // 未登记任何 CL 但仍在重点监控中：最常见的是"漏提 CL"，需要显式提示
  const unregistered = !!group.cl_unregistered

  return (
    <Card
      size="small"
      style={{
        marginBottom: 12,
        borderRadius: 8,
        border: unregistered
          ? '1px solid #ffe58f'
          : group.unmerged_cls > 0 ? '1px solid #ffccc7' : '1px solid #d9f7be',
      }}
      bodyStyle={{ padding: '12px 16px' }}
    >
      {/* Jira header */}
      <Row align="middle" wrap style={{ marginBottom: 8 }}>
        <Col flex="auto">
          <Space size={12} align="center" wrap>
            <a
              href={jiraLink(group.jira_key)}
              target="_blank"
              rel="noopener noreferrer"
              style={{ fontSize: 15, fontWeight: 600, color: '#0052cc' }}
            >
              {group.jira_key}
            </a>
            <Text style={{ fontSize: 13, color: '#333' }}>{group.summary}</Text>
            {group.prev_status && (
              <Tag>{group.prev_status} →</Tag>
            )}
            {statusTag(group.status)}
            {group.tracking && (
              <Tag color="purple" icon={<ExclamationCircleOutlined />}>持续跟踪</Tag>
            )}
            {group.queue_reason === 'skip_to_closed' && (
              <Tag color="volcano" icon={<WarningOutlined />}>跳过验证直接关闭</Tag>
            )}
            {unregistered && (
              <Tag color="gold" icon={<WarningOutlined />}>未登记 CL</Tag>
            )}
            {group.ack_at && (
              <Tag color="default" icon={<CheckCircleOutlined />}>已停止提醒</Tag>
            )}
          </Space>
        </Col>
        <Col>
          <Space size={12}>
            {group.status_changed_at && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                状态变更: {group.status_changed_at.slice(0, 10)}
              </Text>
            )}
            <Text type="secondary" style={{ fontSize: 12 }}>
              Owner: {group.assignee}
            </Text>
          </Space>
        </Col>
      </Row>

      {group.ack_at && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 8, padding: '4px 10px', fontSize: 12 }}
          message={`经办人已说明 CL 处理完毕，已停止提醒（${group.ack_at.slice(0, 10)}）。此处保留供复核。`}
          description={group.ack_reason}
        />
      )}

      {unregistered && !group.ack_at && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8, padding: '4px 10px', fontSize: 12 }}
          message="该 Jira 已进入重点监控，但评论中没有任何 Gerrit CL 链接 —— 请确认是否漏提 CL；若确实无需合并，可忽略本项。"
        />
      )}

      {/* CL stats */}
      <Row style={{ marginBottom: 8 }}>
        <Space size={16}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            CL 总计: <strong>{group.total_cls}</strong>
          </Text>
          <Text style={{ fontSize: 12, color: '#52c41a' }}>
            已合并: <strong>{group.merged_cls}</strong>
          </Text>
          {group.unmerged_cls > 0 && (
            <Text style={{ fontSize: 12, color: '#ff4d4f' }}>
              未合并: <strong>{group.unmerged_cls}</strong>
            </Text>
          )}
        </Space>
      </Row>

      {/* Unmerged CLs */}
      {unmergedItems.length > 0 && (
        <div style={{ marginBottom: mergedItems.length > 0 ? 10 : 0 }}>
          <Text type="danger" style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 4 }}>
            <ExclamationCircleOutlined style={{ marginRight: 4 }} />
            未合并 CL
          </Text>
          <Table
            dataSource={unmergedItems}
            columns={UNMERGED_TABLE_COLUMNS(onSuppress)}
            rowKey={(row) => `${row.instance}-${row.change_id}`}
            size="small"
            pagination={false}
            scroll={{ x: 700 }}
          />
        </div>
      )}

      {/* Merged CLs (collapsed by default when there are unmerged ones) */}
      {mergedItems.length > 0 && unmergedItems.length === 0 && (
        <div>
          <Text style={{ fontSize: 12, color: '#52c41a', fontWeight: 600, display: 'block', marginBottom: 4 }}>
            <CheckCircleOutlined style={{ marginRight: 4 }} />
            全部 CL 已合并
          </Text>
          <Table
            dataSource={mergedItems}
            columns={CL_TABLE_COLUMNS}
            rowKey={(row) => `${row.instance}-${row.change_id}`}
            size="small"
            pagination={false}
            scroll={{ x: 640 }}
          />
        </div>
      )}

      {mergedItems.length > 0 && unmergedItems.length > 0 && (
        <details style={{ marginTop: 4 }}>
          <summary style={{ fontSize: 12, color: '#8c8c8c', cursor: 'pointer' }}>
            <CheckCircleOutlined style={{ marginRight: 4 }} />
            已合并 CL ({mergedItems.length})
          </summary>
          <div style={{ marginTop: 6 }}>
            <Table
              dataSource={mergedItems}
              columns={CL_TABLE_COLUMNS}
              rowKey={(row) => `${row.instance}-${row.change_id}`}
              size="small"
              pagination={false}
              scroll={{ x: 640 }}
            />
          </div>
        </details>
      )}
    </Card>
  )
}

// ── Section: 重点监控汇总（PM review）────────────────────────────

/** 一条重点监控 Jira 的 CL 侧状态描述。 */
function clStateText(item: ClFocusItem): React.ReactNode {
  if (item.cl_unregistered) {
    return <Tag color="gold" icon={<WarningOutlined />}>未登记 CL</Tag>
  }
  if (item.cl_unmerged > 0) {
    return <Tag color="red">{item.cl_unmerged} 个未合并</Tag>
  }
  return <Tag color="green">全部已合并</Tag>
}

const FOCUS_TABLE_COLUMNS = [
  {
    title: 'Jira', dataIndex: 'jira_key', key: 'jira_key', width: 130,
    render: (v: string) => (
      <a href={jiraLink(v)} target="_blank" rel="noopener noreferrer" style={{ color: '#0052cc', fontWeight: 500 }}>
        {v}
      </a>
    ),
  },
  { title: '概要', dataIndex: 'summary', key: 'summary', ellipsis: true },
  { title: 'Owner', dataIndex: 'assignee', key: 'assignee', width: 120 },
  {
    title: '状态', key: 'status', width: 150,
    render: (_: unknown, r: ClFocusItem) => (
      <Space size={4}>
        {r.prev_status && <Tag>{r.prev_status} →</Tag>}
        {statusTag(r.status)}
      </Space>
    ),
  },
  {
    title: 'CL', key: 'cl', width: 150,
    render: (_: unknown, r: ClFocusItem) => (
      <Space size={8}>
        {clStateText(r)}
        {r.cl_total > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.cl_merged}/{r.cl_total}
          </Text>
        )}
      </Space>
    ),
  },
  {
    title: '入队天数', dataIndex: 'queue_age_days', key: 'queue_age_days', width: 90,
    render: (v: number | null) => (v ?? '-'),
  },
  {
    title: '提醒状态', key: 'remind', width: 190,
    render: (_: unknown, r: ClFocusItem) =>
      r.ack_at ? (
        <Space direction="vertical" size={0}>
          <Tag color="default" icon={<CheckCircleOutlined />}>已停止提醒</Tag>
          <Text type="secondary" style={{ fontSize: 11 }} title={r.ack_reason}>
            {r.ack_reason.slice(0, 24)}{r.ack_reason.length > 24 ? '…' : ''}
          </Text>
        </Space>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {r.last_reminded_at ? r.last_reminded_at.replace('T', ' ').slice(0, 16) : '—'}
        </Text>
      ),
  },
]

const FocusSummaryTable: React.FC<{ items: ClFocusItem[]; empty: string }> = ({ items, empty }) => (
  <Table
    dataSource={items}
    columns={FOCUS_TABLE_COLUMNS}
    rowKey="jira_key"
    size="small"
    pagination={items.length > 20 ? { pageSize: 20 } : false}
    scroll={{ x: 900 }}
    locale={{ emptyText: <Empty description={empty} image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
  />
)

// ── Section: All-Merged Jiras Table ─────────────────────────────

const ALL_MERGED_COLUMNS = (onSuppressJira: (group: JiraClGroup) => void) => [
  {
    title: 'Jira ID', dataIndex: 'jira_key', key: 'jira_key', width: 110,
    render: (v: string) => (
      <a href={jiraLink(v)} target="_blank" rel="noopener noreferrer" style={{ fontWeight: 500 }}>
        {v}
      </a>
    ),
  },
  { title: '概要', dataIndex: 'summary', key: 'summary', ellipsis: true },
  { title: 'Owner', dataIndex: 'assignee', key: 'assignee', width: 120 },
  { title: 'CL 数', dataIndex: 'total_cls', key: 'total_cls', width: 70 },
  {
    // 该组里的题 CL 已全部合并、按规则不催办，但 PM 仍可能要整条标不关注
    // （实测 TV-946422：客户沟通中，需要停止一切后续动作）
    title: '操作', key: 'action', width: 100,
    render: (_: unknown, record: JiraClGroup) => (
      <Popconfirm
        title="标记该 Jira 为不关注？"
        description="之后不再对其发任何提醒，可稍后恢复。"
        onConfirm={() => onSuppressJira(record)}
      >
        <Button size="small" type="link" danger icon={<StopOutlined />}>不关注</Button>
      </Popconfirm>
    ),
  },
]

// Verified 但未登记任何 CL 的 Jira — 可能漏登记，需确认是否有改动需要合并
const VERIFIED_NO_CL_COLUMNS = (onSuppressJira: (group: JiraClGroup) => void) => [
  {
    title: 'Jira ID', dataIndex: 'jira_key', key: 'jira_key', width: 110,
    render: (v: string) => (
      <a href={jiraLink(v)} target="_blank" rel="noopener noreferrer" style={{ fontWeight: 500 }}>
        {v}
      </a>
    ),
  },
  { title: '概要', dataIndex: 'summary', key: 'summary', ellipsis: true },
  { title: 'Owner', dataIndex: 'assignee', key: 'assignee', width: 120 },
  {
    title: '状态', dataIndex: 'status', key: 'status', width: 100,
    render: (v: string) => statusTag(v),
  },
  {
    title: '操作', key: 'action', width: 100,
    render: (_: unknown, record: JiraClGroup) => (
      <Popconfirm
        title="标记该 Jira 为不关注？"
        description="之后不再对其发任何提醒，可稍后恢复。"
        onConfirm={() => onSuppressJira(record)}
      >
        <Button size="small" type="link" danger icon={<StopOutlined />}>不关注</Button>
      </Popconfirm>
    ),
  },
]

// ── Project Config Card ──────────────────────────────────────────

const ProjectConfigCard: React.FC<{
  fieldName: number
  restField: any
  form: any
  onRemove: () => void
}> = ({ fieldName, restField, form, onRemove }) => {
  const projects = Form.useWatch('projects', form) || []
  const proj = projects[fieldName] || {}
  // 模式用本地 state 记录：切到 JQL 后 advanced_jql 会先被清空（等用户填），
  // 若用 `!!advanced_jql` 推导，开关会立刻弹回基本模式。
  const [isAdvanced, setIsAdvanced] = useState<boolean>(() => !!proj?.advanced_jql)

  const toggleMode = (checked: boolean) => {
    setIsAdvanced(checked)
    const base = ['projects', fieldName]
    if (checked) {
      // 切到 JQL 模式时清空基本模式字段：Form.List 默认 preserve，
      // 隐藏的 labels 仍会随表单提交，后端一旦回退到它就与 UI 显示不一致
      // （2026-09-16 排查：UI 是 JQL 模式，实际生效的却是残留的 label）
      form.setFieldValue([...base, 'jira_project_key'], '')
      form.setFieldValue([...base, 'labels'], '')
      form.setFieldValue([...base, 'epics'], '')
    } else {
      form.setFieldValue([...base, 'advanced_jql'], '')
    }
  }

  return (
    <Card
      size="small"
      style={{ marginBottom: 12, borderRadius: 8, background: '#fafafa' }}
      extra={<Button type="text" danger icon={<DeleteOutlined />} onClick={onRemove} />}
    >
      <Row gutter={12}>
        <Col span={8}>
          <Form.Item {...restField} name={[fieldName, 'name']} label="项目名称" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="如：长虹T6D" />
          </Form.Item>
        </Col>
        <Col span={3}>
          <Form.Item {...restField} name={[fieldName, 'cl_monitoring_enabled']} label="CL 监控" valuePropName="checked">
            <Switch defaultChecked />
          </Form.Item>
        </Col>
        <Col span={5}>
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
        <Form.Item
          {...restField}
          name={[fieldName, 'advanced_jql']}
          label="JQL 查询条件"
          extra="填完整 JQL，系统不会再追加任何条件（含状态过滤）。此处命中的 Jira 全部进入追踪队列。"
        >
          <Input.TextArea
            rows={3}
            placeholder={'status not in (Closed) AND "Project ID" = "PROJECTX-CHIP-X" AND issuetype not in (Epic)'}
          />
        </Form.Item>
      ) : (
        <Row gutter={12}>
          <Col span={4}>
            <Form.Item {...restField} name={[fieldName, 'jira_project_key']} label="Jira Key">
              <Input placeholder="如：TV" />
            </Form.Item>
          </Col>
          <Col span={10}>
            <Form.Item {...restField} name={[fieldName, 'labels']} label="Labels（逗号分隔）">
              <Input placeholder="如：oem_release_new" />
            </Form.Item>
          </Col>
          <Col span={10}>
            <Form.Item {...restField} name={[fieldName, 'epics']} label="Epic IDs（逗号分隔）">
              <Input placeholder="如：TV-918177,TV-918179" />
            </Form.Item>
          </Col>
        </Row>
      )}
    </Card>
  )
}

// ── Config Form ──────────────────────────────────────────────────

const ConfigForm: React.FC<{
  config: ClMonitoringConfig
  saving: boolean
  onSave: (values: any) => Promise<void>
  onCancel: () => void
}> = ({ config, saving, onSave, onCancel }) => {
  const [form] = Form.useForm()

  useEffect(() => {
    form.setFieldsValue({ projects: config?.projects || [] })
  }, [config, form])

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={onSave}
      initialValues={{ projects: config?.projects || [] }}
    >
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
                labels: '', advanced_jql: '', cl_monitoring_enabled: true,
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

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
        <Button onClick={onCancel}>取消</Button>
        <Button type="primary" htmlType="submit" loading={saving}>保存配置</Button>
      </div>
    </Form>
  )
}

// ── Per-Project Section ──────────────────────────────────────────

const ProjectSection: React.FC<{
  proj: ProjectClStatus
  onSuppress: (it: ClItem) => void
  onSuppressJira: (group: JiraClGroup) => void
}> = ({ proj, onSuppress, onSuppressJira }) => {
  // Normalize: error path returns "items", normal path returns "jira_groups"
  const groups = proj.jira_groups || proj.items || []
  // Verified jiras with unmerged CLs — needs attention (risk section)
  const verifiedUnmerged = groups.filter(
    (g) => g.status === 'Verified' && g.unmerged_cls > 0,
  )
  // Resolved jiras with unmerged CLs — anomalous, also needs attention
  const resolvedUnmerged = groups.filter(
    (g) => g.status === 'Resolved' && g.unmerged_cls > 0,
  )
  // Resolved jiras with all CLs merged — completed successfully
  const resolvedAllMerged = groups.filter(
    (g) => g.status === 'Resolved' && g.unmerged_cls === 0,
  )
  // 持续跟踪：曾进入 Verified 但 CL 未合并（即使状态已 Closed 仍跟踪）
  const trackedUnmerged = groups.filter(
    (g) => g.tracking === true && g.unmerged_cls > 0,
  )
  // Verified 但完全未登记 CL — 可能漏登记，需确认是否有改动需要合并
  const verifiedNoCl = groups.filter(
    (g) => g.status === 'Verified' && (g.total_cls ?? 0) === 0,
  )
  // Verified 且 CL 全部已合并 —— 无需催办，继续跟踪到 Closed。
  // 单独成区：此前这一档没有任何分区承接，题会从页面上"消失"
  // （实测 TV-952845 即如此）
  const verifiedAllMerged = groups.filter(
    (g) => g.status === 'Verified' && (g.total_cls ?? 0) > 0 && g.unmerged_cls === 0,
  )

  const riskGroups = [...verifiedUnmerged, ...resolvedUnmerged, ...trackedUnmerged, ...verifiedNoCl]


  return (
    <Card
      size="small"
      title={
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {proj.project}
          <Tag
            color={proj.unmerged_cls > 0 ? 'red' : 'green'}
            style={{ marginLeft: 8 }}
          >
            CL: {proj.unmerged_cls} 未合并 / {proj.total_cls} 总计
            {riskGroups.length > 0 && (
              <span style={{ marginLeft: 6, fontWeight: 400 }}>
                | {riskGroups.length} Jira 需关注
              </span>
            )}
          </Tag>
        </span>
      }
      style={{ borderRadius: 10, marginBottom: 16, boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}
    >
      {proj.error && (
        <Alert
          type="warning"
          showIcon
          message={proj.error}
          style={{ marginBottom: 12, borderRadius: 6 }}
          closable={false}
        />
      )}

      {groups.length === 0 && !proj.error && (
        <div style={{ textAlign: 'center', padding: '24px 0' }}>
          <CheckCircleOutlined style={{ fontSize: 24, color: '#52c41a', marginBottom: 8 }} />
          <div><Text type="secondary">未发现 CL 数据</Text></div>
        </div>
      )}

      {/* Section 1: Risk — Verified jiras with unmerged CLs */}
      {verifiedUnmerged.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Title level={5} style={{ color: '#ff4d4f', marginBottom: 8 }}>
            <ExclamationCircleOutlined style={{ marginRight: 6 }} />
            Verified 含未合并 CL（{verifiedUnmerged.length} 个）
          </Title>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
            Verified 状态的 Jira 有关联的 CL 尚未 merge，需要跟进处理
          </Text>
          {verifiedUnmerged.map((g) => (
            <JiraGroupCard key={g.jira_key} group={g} onSuppress={onSuppress} />
          ))}
        </div>
      )}

      {/* Section 1a: Verified 但完全未登记 CL — 可能漏登记，需确认 */}
      {verifiedNoCl.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Title level={5} style={{ color: '#fa8c16', marginBottom: 8 }}>
            <WarningOutlined style={{ marginRight: 6 }} />
            Verified 未登记 CL（{verifiedNoCl.length} 个）
          </Title>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
            Verified 状态但未在 Jira 评论中检测到关联 CL，可能漏登记，请确认是否有改动需要合并
          </Text>
          <Table
            dataSource={verifiedNoCl}
            columns={VERIFIED_NO_CL_COLUMNS(onSuppressJira)}
            rowKey="jira_key"
            size="small"
            pagination={verifiedNoCl.length > 20 ? { pageSize: 20, size: 'small' } : false}
            scroll={{ x: 500 }}
          />
        </div>
      )}

      {/* Section 1b: Anomaly — Resolved jiras with unmerged CLs */}
      {resolvedUnmerged.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Title level={5} style={{ color: '#ff7a00', marginBottom: 8 }}>
            <WarningOutlined style={{ marginRight: 6 }} />
            Resolved 但仍含未合并 CL（{resolvedUnmerged.length} 个）
          </Title>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
            Resolved 但有关联的 CL 尚未 merge，请确认是否遗漏
          </Text>
          {resolvedUnmerged.map((g) => (
            <JiraGroupCard key={g.jira_key} group={g} onSuppress={onSuppress} />
          ))}
        </div>
      )}

      {/* Section 1c: 持续跟踪 — 曾 Verified 但 CL 未合并（可能已 Closed） */}
      {trackedUnmerged.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Title level={5} style={{ color: '#722ed1', marginBottom: 8 }}>
            <ExclamationCircleOutlined style={{ marginRight: 6 }} />
            持续跟踪 · 曾 Verified（{trackedUnmerged.length} 个）
          </Title>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
            这些 Jira 曾进入 Verified 但关联 CL 尚未 merge（即使状态已改为 Closed 仍持续跟踪），请及时登记并合并 CL
          </Text>
          {trackedUnmerged.map((g) => (
            <JiraGroupCard key={g.jira_key} group={g} onSuppress={onSuppress} />
          ))}
        </div>
      )}

        {/* Section 1d: Verified 且 CL 全部已合并 —— 无需催办，等走 Closed */}
      {verifiedAllMerged.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Title level={5} style={{ color: '#13c2c2', marginBottom: 8 }}>
            <CheckCircleOutlined style={{ marginRight: 6 }} />
            Verified 且 CL 全部已合并（{verifiedAllMerged.length} 个）
          </Title>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
            Verified 状态的 Jira，其关联 CL 已全部 merge —— 无待办、不再提醒经办人，
            继续跟踪至状态推进到 Closed
          </Text>
          <Table
            dataSource={verifiedAllMerged}
            columns={ALL_MERGED_COLUMNS(onSuppressJira)}
            rowKey="jira_key"
            size="small"
            pagination={verifiedAllMerged.length > 20 ? { pageSize: 20, size: 'small' } : false}
            scroll={{ x: 500 }}
          />
        </div>
      )}

    {/* Section 2: All good — Resolved jiras with all CLs merged */}
      {resolvedAllMerged.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Title level={5} style={{ color: '#52c41a', marginBottom: 8 }}>
            <CheckCircleOutlined style={{ marginRight: 6 }} />
            Resolved 且 CL 全部已合并（{resolvedAllMerged.length} 个）
          </Title>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
            Resolved 状态的 Jira，其关联的 CL 已全部 merge
          </Text>
          <Table
            dataSource={resolvedAllMerged}
            columns={ALL_MERGED_COLUMNS(onSuppressJira)}
            rowKey="jira_key"
            size="small"
            pagination={resolvedAllMerged.length > 20 ? { pageSize: 20, size: 'small' } : false}
            scroll={{ x: 500 }}
          />
        </div>
      )}

    </Card>
  )
}

// ── Alerts Page ──────────────────────────────────────────────────

const ClMonitoringPage: React.FC = () => {
  const [data, setData] = useState<ProjectClStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<string>('all')

  // Config modal
  const [configVisible, setConfigVisible] = useState(false)
  const [config, setConfig] = useState<ClMonitoringConfig | null>(null)
  const [configLoading, setConfigLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  // 已标记不关注的 CL（恢复入口）
  const [suppressed, setSuppressed] = useState<ClSuppressItem[]>([])
  const [suppressedVisible, setSuppressedVisible] = useState(false)

  // 重点监控汇总（PM review）
  const [summary, setSummary] = useState<ClFocusSummary | null>(null)
  const [summaryVisible, setSummaryVisible] = useState(false)
  const [summaryLoading, setSummaryLoading] = useState(false)

  const loadData = useCallback(async (showLoader = false, forceRefresh = false) => {
    if (showLoader) setLoading(true)
    try {
      const result = await fetchClStatus(forceRefresh)
      setData(result)
      setError(null)
    } catch (e: any) {
      console.error('CL status fetch error:', e)
      setError(e.message || '加载 CL 状态失败')
    } finally {
      if (showLoader) setLoading(false)
    }
  }, [])

  const loadSummary = useCallback(async (showLoader = false) => {
    if (showLoader) setSummaryLoading(true)
    try {
      setSummary(await fetchClFocusSummary())
    } catch (e) {
      console.error('CL focus summary fetch error:', e)
    } finally {
      setSummaryLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData(true, false)
    // forceRefresh=false: 默认从缓存加载，不触发全量扫描
    // 追踪队列由后台每日扫描维护（CL_SCAN_INTERVAL_SECONDS，默认 24h）
    // 如需立即扫描，点击右上角"刷新"按钮
    fetchSuppressedCls().then(setSuppressed).catch(() => {})
    loadSummary()
  }, [loadData, loadSummary])

  // ── CL 不关注 handlers ─────────────────────────────────────────

  const handleSuppress = async (it: ClItem) => {
    try {
      await suppressCl([{
        instance: it.instance, change_id: it.change_id, jira_key: it.jira_key, reason: 'manual',
      }])
      antMsg.success('已标记为不关注')
      const list = await fetchSuppressedCls()
      setSuppressed(list)
      loadData(false, false) // 后端实时过滤，重拉后该行即消失
    } catch (e: any) {
      antMsg.error('标记失败: ' + (e.message || ''))
    }
  }

  // 整条 Jira 标不关注（用于 CL 已全部合并 / 未登记 CL 的分区，那里没有逐 CL 的
  // 明细可标，但 PM 仍可能要停止该题的一切后续动作，实测 TV-946422 即如此）
  const handleSuppressJira = async (group: JiraClGroup) => {
    try {
      await suppressCl([{
        instance: '_jira', change_id: '_all', jira_key: group.jira_key, reason: 'jira_ack',
      }])
      antMsg.success('已把该 Jira 整体标记为不关注')
      const list = await fetchSuppressedCls()
      setSuppressed(list)
      loadData(false, false)
    } catch (e: any) {
      antMsg.error('标记失败: ' + (e.message || ''))
    }
  }

  const handleRestore = async (item: ClSuppressItem) => {
    try {
      await restoreCl({ instance: item.instance, change_id: item.change_id, jira_key: item.jira_key })
      antMsg.success('已恢复关注')
      const list = await fetchSuppressedCls()
      setSuppressed(list)
      loadData(false, false)
    } catch (e: any) {
      antMsg.error('恢复失败: ' + (e.message || ''))
    }
  }

  // ── Config handlers ────────────────────────────────────────────

  const openConfig = async () => {
    setConfigLoading(true)
    setConfigVisible(true)
    try {
      const data = await fetchClMonitoringConfig()
      setConfig(data)
    } catch (e: any) {
      antMsg.error('加载配置失败: ' + e.message)
    } finally {
      setConfigLoading(false)
    }
  }

  const handleSaveConfig = async (values: any) => {
    setSaving(true)
    try {
      const payload: ClMonitoringConfig = {
        projects: values.projects || [],
      }
      await saveClMonitoringConfig(payload)
      antMsg.success('配置已保存')
      setConfigVisible(false)
      loadData(true, true)
    } catch (e: any) {
      antMsg.error('保存配置失败: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  // ── Render ─────────────────────────────────────────────────────

  // Filter data by active project tab
  const filteredData = activeTab === 'all' ? data : data.filter(p => p.project === activeTab)

  // Aggregate stats across filtered projects
  const totals = filteredData.reduce(
    (acc, p) => ({
      total_cls: acc.total_cls + (p.total_cls || 0),
      merged_cls: acc.merged_cls + (p.merged_cls || 0),
      unmerged_cls: acc.unmerged_cls + (p.unmerged_cls || 0),
      jiras_unmerged: acc.jiras_unmerged + (p.jiras_unmerged || 0),
      jiras_merged: acc.jiras_merged + (p.jiras_merged || 0),
    }),
    { total_cls: 0, merged_cls: 0, unmerged_cls: 0, jiras_unmerged: 0, jiras_merged: 0 },
  )

  if (loading && data.length === 0) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Spin size="large" tip="加载 CL 状态..." />
      </div>
    )
  }

  return (
    <div style={{ padding: '24px 32px', height: '100%', overflow: 'auto' }}>
      {/* Header */}
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <ExclamationCircleOutlined style={{ marginRight: 8, color: '#faad14' }} />
            CL 监控
          </Title>
        </Col>
        <Col>
          <Space size={8}>
            <Button
              icon={<ExclamationCircleOutlined />}
              onClick={() => { setSummaryVisible(true); loadSummary(true) }}
              disabled={(summary?.total ?? 0) === 0}
            >
              重点监控汇总 ({summary?.total ?? 0})
            </Button>
            <Button
              icon={<StopOutlined />}
              onClick={() => setSuppressedVisible(true)}
              disabled={suppressed.length === 0}
            >
              已标记不关注 ({suppressed.length})
            </Button>
            <Button icon={<SettingOutlined />} onClick={openConfig}>
              监控配置
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => loadData(true, true)} loading={loading}>
              刷新
            </Button>
          </Space>
        </Col>
      </Row>

      {/* Error banner */}
      {error && (
        <Alert
          type="error"
          showIcon
          message="加载失败"
          description={error}
          style={{ marginBottom: 16, borderRadius: 8 }}
          closable
          onClose={() => setError(null)}
        />
      )}

      {/* Project tabs */}
      {data.length > 0 && (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          style={{ marginBottom: 16 }}
          items={[
            { key: 'all', label: `全部项目 (${data.length})` },
            ...data.map(p => ({ key: p.project, label: p.project })),
          ]}
        />
      )}

      {/* Summary cards */}
      {filteredData.length > 0 && (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={12} sm={8} md={4}>
            <Card
              style={{ borderRadius: 10, borderLeft: '4px solid #1890ff' }}
              bodyStyle={{ padding: '16px 20px' }}
            >
              <Text type="secondary" style={{ fontSize: 13 }}>CL 总计</Text>
              <div style={{ fontSize: 28, fontWeight: 700, color: '#1890ff' }}>
                {totals.total_cls}
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <Card
              style={{ borderRadius: 10, borderLeft: '4px solid #52c41a' }}
              bodyStyle={{ padding: '16px 20px' }}
            >
              <Text type="secondary" style={{ fontSize: 13 }}>已合并</Text>
              <div style={{ fontSize: 28, fontWeight: 700, color: '#52c41a' }}>
                <CheckCircleOutlined style={{ marginRight: 6 }} />
                {totals.merged_cls}
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <Card
              style={{ borderRadius: 10, borderLeft: '4px solid #ff4d4f' }}
              bodyStyle={{ padding: '16px 20px' }}
            >
              <Text type="secondary" style={{ fontSize: 13 }}>未合并</Text>
              <div style={{
                fontSize: 28, fontWeight: 700,
                color: totals.unmerged_cls > 0 ? '#ff4d4f' : '#595959',
              }}>
                <CloseCircleOutlined style={{ marginRight: 6 }} />
                {totals.unmerged_cls}
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <Card
              style={{ borderRadius: 10, borderLeft: `4px solid ${totals.jiras_unmerged > 0 ? '#ff4d4f' : '#52c41a'}` }}
              bodyStyle={{ padding: '16px 20px' }}
            >
              <Text type="secondary" style={{ fontSize: 13 }}>Jira 有未合并</Text>
              <div style={{ fontSize: 28, fontWeight: 700, color: totals.jiras_unmerged > 0 ? '#ff4d4f' : '#52c41a' }}>
                {totals.jiras_unmerged}
                <Text type="secondary" style={{ fontSize: 14, fontWeight: 400, marginLeft: 4 }}>
                  / {totals.jiras_merged + totals.jiras_unmerged}
                </Text>
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <Card
              style={{ borderRadius: 10, borderLeft: '4px solid #722ed1' }}
              bodyStyle={{ padding: '16px 20px' }}
            >
              <Text type="secondary" style={{ fontSize: 13 }}>Jira 全部已合并</Text>
              <div style={{ fontSize: 28, fontWeight: 700, color: '#722ed1' }}>
                <CheckCircleOutlined style={{ marginRight: 6 }} />
                {totals.jiras_merged}
              </div>
            </Card>
          </Col>
        </Row>
      )}

      {/* Empty state (no data at all) */}
      {data.length === 0 && !error && (
        <div style={{ textAlign: 'center', padding: '80px 0' }}>
          <Empty description="暂无 CL 监控数据">
            <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
              请点击右上角「监控配置」设置项目过滤条件
            </Text>
          </Empty>
        </div>
      )}

      {/* Per-project sections with Jira-grouped CL data */}
      {filteredData.map((proj) => (
        <ProjectSection
          key={proj.project}
          proj={proj}
          onSuppress={handleSuppress}
          onSuppressJira={handleSuppressJira}
        />
      ))}

      {/* ── 重点监控汇总 Modal（PM review）──────────────────────── */}
      <Modal
        title={<span><ExclamationCircleOutlined style={{ marginRight: 8 }} />重点监控汇总</span>}
        open={summaryVisible}
        onCancel={() => setSummaryVisible(false)}
        footer={null}
        width={1100}
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
          追踪队列中所有处于 Resolved / Verified / Closed 的 Jira。Resolved / Verified
          每天自动 @owner 提醒；Closed 的不评论 owner，仅在此列出供 PM 确认 CL 是否已合并。
        </Text>
        {summaryLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : !summary || summary.total === 0 ? (
          <Empty description="暂无重点监控项" />
        ) : (
          <Tabs
            items={[
              {
                key: 'resolved',
                label: `Resolved (${summary.groups.Resolved?.length ?? 0})`,
                children: <FocusSummaryTable items={summary.groups.Resolved ?? []} empty="暂无" />,
              },
              {
                key: 'verified',
                label: `Verified (${summary.groups.Verified?.length ?? 0})`,
                children: <FocusSummaryTable items={summary.groups.Verified ?? []} empty="暂无" />,
              },
              {
                key: 'closed',
                label: `Closed 未合并 (${summary.groups.Closed?.length ?? 0})`,
                children: (
                  <FocusSummaryTable
                    items={summary.groups.Closed ?? []}
                    empty="暂无（关闭且 CL 已合并的题已自动退出队列）"
                  />
                ),
              },
              {
                key: 'unregistered',
                label: `未登记 CL (${summary.unregistered?.length ?? 0})`,
                children: (
                  <>
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 12 }}
                      message="这些 Jira 已进入重点监控，但评论中没有任何 Gerrit CL 链接 —— 最容易被遗漏，请确认是否漏提 CL。"
                    />
                    <FocusSummaryTable items={summary.unregistered ?? []} empty="暂无" />
                  </>
                ),
              },
              {
                key: 'unmerged',
                label: `CL 未合并 (${summary.unmerged?.length ?? 0})`,
                children: <FocusSummaryTable items={summary.unmerged ?? []} empty="暂无" />,
              },
            ]}
          />
        )}
      </Modal>

      {/* ── 已标记不关注 CL Modal ──────────────────────────────── */}
      <Modal
        title={<span><StopOutlined style={{ marginRight: 8 }} />已标记不关注的 CL</span>}
        open={suppressedVisible}
        onCancel={() => setSuppressedVisible(false)}
        footer={null}
        width={720}
      >
        {suppressed.length === 0 ? (
          <Empty description="暂无已标记的 CL" />
        ) : (
          <Table
            dataSource={suppressed}
            rowKey={(r) => `${r.instance}-${r.change_id}-${r.jira_key}`}
            size="small"
            pagination={false}
            scroll={{ x: 600 }}
            columns={[
              {
                title: 'Jira', dataIndex: 'jira_key', width: 130,
                render: (v: string) => (
                  <a href={jiraLink(v)} target="_blank" rel="noopener noreferrer" style={{ color: '#0052cc' }}>
                    {v}
                  </a>
                ),
              },
              { title: 'Gerrit', dataIndex: 'instance', width: 140 },
              { title: 'CL 编号', dataIndex: 'change_id', width: 100 },
              { title: '原因', dataIndex: 'reason', width: 100 },
              {
                title: '标记时间', dataIndex: 'marked_at', width: 170,
                render: (v: string) => (v ? v.replace('T', ' ').slice(0, 19) : ''),
              },
              {
                title: '操作', key: 'action', width: 100,
                render: (_: any, record: ClSuppressItem) => (
                  <Button size="small" type="link" onClick={() => handleRestore(record)}>
                    恢复关注
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Modal>

      {/* ── Config Modal ────────────────────────────────────────── */}
      <Modal
        title={<span><SettingOutlined style={{ marginRight: 8 }} />CL 监控配置</span>}
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
            onSave={handleSaveConfig}
            onCancel={() => setConfigVisible(false)}
          />
        )}
      </Modal>
    </div>
  )
}

export default ClMonitoringPage
