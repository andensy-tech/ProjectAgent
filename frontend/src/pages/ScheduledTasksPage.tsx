/**
 * 定时任务管理页面 — Agent 通用 scheduled tasks 看板（2026-09-09）。
 *
 * 列表：名称/类型/间隔/下次运行/上次状态/已跑次数/截止日/启停/删除。
 * 新建：表单映射常用 jira_comment 规则（JQL/停滞天数/排除状态/提醒文案/间隔/截止日）
 * → 组装声明式 config JSON 提交。也可在 web chat 用自然语言让 Agent 创建。
 */
import React, { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message as antMsg,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  ScheduledTask,
  createScheduledTask,
  deleteScheduledTask,
  fetchScheduledTasks,
  updateScheduledTask,
} from '../services/chatService'

const { Title, Text } = Typography

const TYPE_LABEL: Record<string, string> = {
  declarative_rule: '声明式规则',
}

const formatTime = (iso?: string | null) => {
  if (!iso) return '-'
  return String(iso).replace('T', ' ').slice(0, 19)
}

const ScheduledTasksPage: React.FC = () => {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null)
  const [updatingId, setUpdatingId] = useState<number | null>(null)
  const [form] = Form.useForm()

  const load = useCallback(async (showLoader = false) => {
    if (showLoader) setLoading(true)
    try {
      const data = await fetchScheduledTasks()
      setTasks(data || [])
      setError(null)
    } catch (e: any) {
      setError(e.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(true)
  }, [load])

  const handleToggle = async (task: ScheduledTask, active: boolean) => {
    setUpdatingId(task.id)
    try {
      await updateScheduledTask(task.id, { active })
      antMsg.success(active ? `已启用「${task.name}」` : `已停用「${task.name}」`)
      await load()
    } catch (e: any) {
      antMsg.error(e.message || '操作失败')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleDelete = async (task: ScheduledTask) => {
    setUpdatingId(task.id)
    try {
      await deleteScheduledTask(task.id)
      antMsg.success(`已删除「${task.name}」`)
      await load()
    } catch (e: any) {
      antMsg.error(e.message || '删除失败')
    } finally {
      setUpdatingId(null)
    }
  }

  // 从 task.config 解析回表单字段（编辑时预填）
  const fillFormFromTask = (task: ScheduledTask) => {
    const cfg = task.config || {}
    const source = cfg.source || {}
    const conditions = cfg.conditions || []
    const staleCond = conditions.find((c: any) => c.kind === 'stale_days')
    const statusCond = conditions.find((c: any) => c.kind === 'status_not_in')
    const action = cfg.action || {}
    form.setFieldsValue({
      name: task.name,
      jql: source.jql || '',
      staleDays: staleCond?.days ?? 3,
      excludeStatuses: (statusCond?.statuses || ['Verified', 'Closed']).join(','),
      template: action.template || '',
      remindIntervalDays: action.remind_interval_days ?? 3,
      intervalMinutes: task.interval_minutes,
      runUntil: task.run_until || '',
      description: task.description || '',
    })
  }

  const openCreate = () => {
    setEditingTask(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (task: ScheduledTask) => {
    setEditingTask(task)
    fillFormFromTask(task)
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    setCreating(true)
    try {
      const statuses = String(values.excludeStatuses || 'Verified,Closed')
        .split(/[,，\s]+/)
        .map((s: string) => s.trim())
        .filter(Boolean)
      const config = {
        source: { kind: 'jira_jql', jql: values.jql },
        conditions: [
          { kind: 'status_not_in', statuses },
          { kind: 'stale_days', days: values.staleDays, basis: 'last_comment' },
        ],
        action: {
          kind: 'jira_comment',
          template: values.template,
          remind_interval_days: values.remindIntervalDays || 3,
        },
      }
      const payload = {
        name: values.name,
        description: values.description,
        config,
        interval_minutes: values.intervalMinutes || 60,
        run_until: values.runUntil || null,
      } as Partial<ScheduledTask>
      if (editingTask) {
        await updateScheduledTask(editingTask.id, payload)
        antMsg.success(`已更新「${values.name}」`)
      } else {
        await createScheduledTask(payload)
        antMsg.success('定时任务已创建')
      }
      setModalOpen(false)
      setEditingTask(null)
      form.resetFields()
      await load()
    } catch (e: any) {
      if (e?.errorFields) return // 表单校验未过
      antMsg.error(e.message || (editingTask ? '更新失败' : '创建失败'))
    } finally {
      setCreating(false)
    }
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (v: string, r: ScheduledTask) => (
        <div>
          <div style={{ fontWeight: 500 }}>{v}</div>
          {r.description && <Text type="secondary" style={{ fontSize: 12 }}>{r.description}</Text>}
        </div>
      ),
    },
    {
      title: '类型',
      dataIndex: 'task_type',
      key: 'task_type',
      width: 110,
      render: (v: string) => <Tag>{TYPE_LABEL[v] || v}</Tag>,
    },
    {
      title: '间隔',
      dataIndex: 'interval_minutes',
      key: 'interval_minutes',
      width: 90,
      render: (v: number) => `每 ${v} 分钟`,
    },
    {
      title: '下次运行',
      dataIndex: 'next_run_at',
      key: 'next_run_at',
      width: 165,
      render: (v?: string | null) => formatTime(v),
    },
    {
      title: '上次状态',
      dataIndex: 'last_run_status',
      key: 'last_run_status',
      width: 220,
      render: (v?: string | null) =>
        v ? (
          <Text style={{ fontSize: 12, color: v.startsWith('error') ? '#ff4d4f' : 'inherit' }}>{v}</Text>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>未执行</Text>
        ),
    },
    {
      title: '已跑',
      dataIndex: 'run_count',
      key: 'run_count',
      width: 70,
      render: (v: number) => v || 0,
    },
    {
      title: '截止日',
      dataIndex: 'run_until',
      key: 'run_until',
      width: 105,
      render: (v?: string | null) => v || <Text type="secondary">-</Text>,
    },
    {
      title: '启用',
      dataIndex: 'active',
      key: 'active',
      width: 80,
      render: (v: boolean, r: ScheduledTask) => (
        <Switch
          size="small"
          checked={v}
          disabled={updatingId === r.id}
          onChange={(checked) => handleToggle(r, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_: unknown, r: ScheduledTask) => (
        <Space size={4}>
          <Button type="link" size="small" disabled={updatingId === r.id} onClick={() => handleEdit(r)}>
            编辑
          </Button>
          <Popconfirm
            title={`删除定时任务「${r.name}」？`}
            description="删除后不可恢复"
            onConfirm={() => handleDelete(r)}
            okText="删除"
            cancelText="取消"
          >
            <Button type="text" size="small" danger disabled={updatingId === r.id}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: '24px 32px', height: '100%', overflow: 'auto' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>定时任务</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => load(true)}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建任务</Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="也可在聊天中用自然语言创建（如「跟踪 filter=61364，连续3天没更新的自动comment提醒owner，9/20前」），Agent 会自动组装规则。"
      />

      {error && <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} />}

      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        dataSource={tasks}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: '暂无定时任务' }}
      />

      <Modal
        title={editingTask ? `编辑定时任务「${editingTask.name}」` : '新建定时任务（Jira 停滞提醒规则）'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => { setModalOpen(false); setEditingTask(null) }}
        confirmLoading={creating}
        okText={editingTask ? '保存' : '创建'}
        cancelText="取消"
        width={560}
      >
        <Form form={form} layout="vertical" initialValues={{
          staleDays: 3,
          excludeStatuses: 'Verified,Closed',
          intervalMinutes: 60,
          remindIntervalDays: 3,
          template: '[~{owner}] {key} 已 {stale_days} 天无更新，请尽快处理，{run_until} 前 fix 掉',
        }}>
          <Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}>
            <Input placeholder="如：TV 项目卡闭环跟踪" maxLength={100} />
          </Form.Item>
          <Form.Item name="jql" label="JQL（成员范围）" rules={[{ required: true, message: '请输入 JQL' }]}>
            <Input placeholder="如：filter = 61364" />
          </Form.Item>
          <Space size={16} style={{ display: 'flex' }}>
            <Form.Item name="staleDays" label="停滞天数（≥N 天无更新触发）" rules={[{ required: true }]}>
              <InputNumber min={1} max={90} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="excludeStatuses" label="排除状态（逗号分隔）">
              <Input style={{ width: 220 }} placeholder="Verified,Closed" />
            </Form.Item>
          </Space>
          <Form.Item name="template" label="提醒文案模板" rules={[{ required: true, message: '请输入提醒文案' }]}>
            <Input.TextArea rows={3} placeholder="变量：{key} {summary} {owner} {stale_days}" />
          </Form.Item>
          <Space size={16} style={{ display: 'flex' }}>
            <Form.Item name="intervalMinutes" label="执行间隔（分钟）" rules={[{ required: true }]}>
              <InputNumber min={5} max={1440} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="remindIntervalDays" label="同一 issue 提醒去重（天）">
              <InputNumber min={1} max={30} style={{ width: 160 }} />
            </Form.Item>
          </Space>
          <Form.Item name="runUntil" label="截止日（YYYY-MM-DD，可选；到期自动停止）">
            <Input placeholder="如：2026-09-20" style={{ width: 220 }} />
          </Form.Item>
          <Form.Item name="description" label="备注（可选）">
            <Input placeholder="任务背景说明" maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default ScheduledTasksPage
