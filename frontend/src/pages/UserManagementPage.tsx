import React, { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Modal, Form, Input, message, Tag, Space, Typography,
  Popconfirm, Card, Row, Col, Statistic,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, LockOutlined, ReloadOutlined,
  UserOutlined, CheckCircleOutlined, CloseCircleOutlined,
  CrownOutlined, TeamOutlined,
} from '@ant-design/icons'
import {
  listUsers, deleteUser, toggleUserActive, adminResetPassword,
  registerUser,
} from '../services/authService'

const { Title, Text } = Typography

interface UserRecord {
  id: number
  username: string
  email: string
  full_name: string
  is_superuser: boolean
  is_active: boolean
  force_password_change: boolean
  feishu_open_id: string | null
  created_at: string | null
}

const UserManagementPage: React.FC = () => {
  const [users, setUsers] = useState<UserRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [resetPwdModalOpen, setResetPwdModalOpen] = useState(false)
  const [resetTarget, setResetTarget] = useState<UserRecord | null>(null)
  const [addForm] = Form.useForm()
  const [resetForm] = Form.useForm()

  const loadUsers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listUsers()
      setUsers(data)
    } catch (e: any) {
      setError(e.message || '加载失败')
      message.error('加载用户列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadUsers()
  }, [loadUsers])

  const handleAddUser = async (values: any) => {
    try {
      const result = await registerUser(values)
      message.success(`用户 ${result.username} 创建成功，初始密码为设置的密码，用户首次登录后需修改密码。`)
      setAddModalOpen(false)
      addForm.resetFields()
      loadUsers()
    } catch (e: any) {
      message.error(e.message || '创建失败')
    }
  }

  const handleToggleActive = async (record: UserRecord) => {
    try {
      await toggleUserActive(record.id)
      message.success(`用户 ${record.username} 已${record.is_active ? '禁用' : '启用'}`)
      loadUsers()
    } catch (e: any) {
      message.error(e.message || '操作失败')
    }
  }

  const handleDelete = async (record: UserRecord) => {
    try {
      await deleteUser(record.id)
      message.success(`用户 ${record.username} 已删除`)
      loadUsers()
    } catch (e: any) {
      message.error(e.message || '删除失败')
    }
  }

  const handleResetPassword = async (values: { new_password: string }) => {
    if (!resetTarget) return
    try {
      await adminResetPassword(resetTarget.id, values.new_password)
      message.success(`用户 ${resetTarget.username} 密码已重置，下次登录需修改密码`)
      setResetPwdModalOpen(false)
      setResetTarget(null)
      resetForm.resetFields()
    } catch (e: any) {
      message.error(e.message || '重置失败')
    }
  }

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 60,
    },
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (text: string, record: UserRecord) => (
        <Space>
          {text}
          {record.is_superuser && (
            <Tag icon={<CrownOutlined />} color="gold" style={{ margin: 0 }}>管理员</Tag>
          )}
          {record.force_password_change && (
            <Tag color="warning" style={{ margin: 0 }}>待改密</Tag>
          )}
        </Space>
      ),
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      render: (v: string) => v || '-',
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (v: boolean) => v
        ? <Tag icon={<CheckCircleOutlined />} color="success">正常</Tag>
        : <Tag icon={<CloseCircleOutlined />} color="error">禁用</Tag>,
    },
    {
      title: '飞书绑定',
      dataIndex: 'feishu_open_id',
      key: 'feishu_bind',
      width: 100,
      render: (v: string | null) => v
        ? <Tag icon={<CheckCircleOutlined />} color="blue">已绑定</Tag>
        : <Tag style={{ color: '#999' }}>未绑定</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string | null) => v ? new Date(v).toLocaleDateString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 280,
      render: (_: any, record: UserRecord) => (
        <Space size="small" wrap>
          <Button
            size="small"
            icon={record.is_active ? <CloseCircleOutlined /> : <CheckCircleOutlined />}
            onClick={() => handleToggleActive(record)}
          >
            {record.is_active ? '禁用' : '启用'}
          </Button>
          <Button
            size="small"
            icon={<LockOutlined />}
            onClick={() => { setResetTarget(record); setResetPwdModalOpen(true) }}
          >
            重置密码
          </Button>
          <Popconfirm
            title="确认删除"
            description={`确定要删除用户 ${record.username} 吗？`}
            onConfirm={() => handleDelete(record)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const activeCount = users.filter(u => u.is_active).length
  const adminCount = users.filter(u => u.is_superuser).length

  return (
    <div style={{ padding: 24, height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Stats Cards */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="用户总数" value={users.length} prefix={<TeamOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="活跃用户" value={activeCount} prefix={<UserOutlined />} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="管理员" value={adminCount} prefix={<CrownOutlined />} valueStyle={{ color: '#faad14' }} />
          </Card>
        </Col>
      </Row>

      {/* Toolbar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <Title level={5} style={{ margin: 0, lineHeight: '32px' }}>用户列表</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadUsers} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddModalOpen(true)}>
            添加用户
          </Button>
        </Space>
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {error && !loading && (
          <Card style={{ marginBottom: 16, background: '#fff2f0', border: '1px solid #ffccc7' }}>
            <Text type="danger">{error}</Text>
            <Button type="link" onClick={loadUsers}>重试</Button>
          </Card>
        )}
        <Table
          dataSource={users}
          columns={columns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 个用户` }}
          locale={{ emptyText: '暂无用户数据' }}
        />
      </div>

      {/* Add User Modal */}
      <Modal
        title="添加用户"
        open={addModalOpen}
        onCancel={() => { setAddModalOpen(false); addForm.resetFields() }}
        footer={null}
        destroyOnClose
      >
        <Form
          form={addForm}
          layout="vertical"
          onFinish={handleAddUser}
          autoComplete="off"
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="用户名" />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[
            { required: true, message: '请输入密码' },
            { min: 6, message: '密码至少6个字符' },
          ]}>
            <Input.Password placeholder="初始密码" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="邮箱（可选）" />
          </Form.Item>
          <Form.Item name="full_name" label="姓名">
            <Input placeholder="姓名（可选）" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => { setAddModalOpen(false); addForm.resetFields() }}>取消</Button>
              <Button type="primary" htmlType="submit">创建</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* Reset Password Modal */}
      <Modal
        title={`重置密码 — ${resetTarget?.username || ''}`}
        open={resetPwdModalOpen}
        onCancel={() => { setResetPwdModalOpen(false); setResetTarget(null); resetForm.resetFields() }}
        footer={null}
        destroyOnClose
      >
        <Form
          form={resetForm}
          layout="vertical"
          onFinish={handleResetPassword}
          autoComplete="off"
        >
          <Form.Item name="new_password" label="新密码" rules={[
            { required: true, message: '请输入新密码' },
            { min: 6, message: '密码至少6个字符' },
          ]}>
            <Input.Password placeholder="新密码" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => { setResetPwdModalOpen(false); setResetTarget(null); resetForm.resetFields() }}>取消</Button>
              <Button type="primary" htmlType="submit">确认重置</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default UserManagementPage
