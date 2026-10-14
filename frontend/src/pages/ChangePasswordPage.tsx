import React, { useState } from 'react'
import { Form, Input, Button, Card, message, Typography } from 'antd'
import { LockOutlined, KeyOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { changePassword } from '../services/authService'
import { useAuthStore } from '../store/authStore'

const { Title, Text } = Typography

const ChangePasswordPage: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const { forcePasswordChange, clearForcePasswordChange, logout } = useAuthStore()

  const handleSubmit = async (values: { old_password?: string; new_password: string }) => {
    setLoading(true)
    try {
      // When forcePasswordChange is true, old_password is optional (backend doesn't verify it)
      await changePassword(values.new_password, values.old_password || undefined)
      message.success('密码修改成功')
      clearForcePasswordChange()
      navigate('/', { replace: true })
    } catch (e: any) {
      message.error(e.message || '修改密码失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        background: 'linear-gradient(135deg, #667eea 0%, #4A90E2 100%)',
      }}
    >
      <Card
        style={{
          width: 420,
          borderRadius: 12,
          boxShadow: '0 8px 32px rgba(0,0,0,0.15)',
        }}
        bodyStyle={{ padding: '40px 32px' }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <KeyOutlined style={{ fontSize: 48, color: '#4A90E2', marginBottom: 12 }} />
          <Title level={3} style={{ margin: 0, color: '#333' }}>
            {forcePasswordChange ? '首次登录请修改密码' : '修改密码'}
          </Title>
          {forcePasswordChange && (
            <Text type="warning">首次登录需要修改密码后才能继续使用</Text>
          )}
        </div>

        <Form
          onFinish={handleSubmit}
          layout="vertical"
          size="large"
          autoComplete="off"
        >
          {!forcePasswordChange && (
            <Form.Item
              name="old_password"
              label="当前密码"
              rules={[{ required: true, message: '请输入当前密码' }]}
            >
              <Input.Password
                prefix={<LockOutlined style={{ color: '#bfbfbf' }} />}
                placeholder="当前密码"
              />
            </Form.Item>
          )}

          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码至少6个字符' },
            ]}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: '#bfbfbf' }} />}
              placeholder="新密码"
            />
          </Form.Item>

          <Form.Item
            name="confirm_password"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请确认新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的密码不一致'))
                },
              }),
            ]}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: '#bfbfbf' }} />}
              placeholder="确认新密码"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              block
              style={{
                height: 44,
                borderRadius: 8,
                background: '#4A90E2',
                borderColor: '#4A90E2',
              }}
            >
              {forcePasswordChange ? '确认修改并继续' : '修改密码'}
            </Button>
          </Form.Item>

          {forcePasswordChange && (
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <Button type="link" danger onClick={() => { logout(); navigate('/login', { replace: true }) }}>
                返回登录
              </Button>
            </div>
          )}
        </Form>
      </Card>
    </div>
  )
}

export default ChangePasswordPage
