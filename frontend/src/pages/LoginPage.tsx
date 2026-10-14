import React, { useState } from 'react'
import { Form, Input, Button, Card, message, Typography } from 'antd'
import { UserOutlined, LockOutlined, RobotOutlined } from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { login } from '../services/authService'
import { useAuthStore } from '../store/authStore'

const { Title, Text } = Typography

const LoginPage: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const setAuth = useAuthStore((s) => s.setAuth)

  const from = (location.state as any)?.from || '/'

  const handleSubmit = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const result = await login(values)
      setAuth(result.access_token, result.user, result.force_password_change)
      if (result.force_password_change) {
        message.info('首次登录或密码被重置，请修改密码后继续使用。')
        navigate('/change-password', { replace: true })
        return
      }
      message.success(`欢迎回来，${result.user.username}`)
      navigate(from, { replace: true })
    } catch (e: any) {
      message.error(e.message || '登录失败')
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
          width: 400,
          borderRadius: 12,
          boxShadow: '0 8px 32px rgba(0,0,0,0.15)',
        }}
        bodyStyle={{ padding: '40px 32px' }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <RobotOutlined style={{ fontSize: 48, color: '#4A90E2', marginBottom: 12 }} />
          <Title level={3} style={{ margin: 0, color: '#333' }}>Project Agent</Title>
          <Text type="secondary">项目管理AI助手</Text>
        </div>

        <Form
          onFinish={handleSubmit}
          layout="vertical"
          size="large"
          autoComplete="off"
        >
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input
              prefix={<UserOutlined style={{ color: '#bfbfbf' }} />}
              placeholder="用户名"
            />
          </Form.Item>

          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: '#bfbfbf' }} />}
              placeholder="密码"
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
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}

export default LoginPage
