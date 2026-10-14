import React, { useState, useEffect } from 'react'
import { Card, Descriptions, Tag, Spin, Typography } from 'antd'
import { InfoCircleOutlined } from '@ant-design/icons'
import { apiFetch } from '../services/chatService'

const { Title } = Typography

const AboutPage: React.FC = () => {
  const [sysInfo, setSysInfo] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    apiFetch<any>('/api/config/system')
      .then(setSysInfo)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  return (
    <div style={{ padding: '24px' }}>
      <Title level={5} style={{ marginBottom: 24 }}>
        <InfoCircleOutlined /> 关于
      </Title>
      <Card title="Project Agent" style={{ maxWidth: 600 }}>
        <Descriptions column={1} size="middle">
          <Descriptions.Item label="应用名称">
            {sysInfo?.app_name || 'Project Agent'}
          </Descriptions.Item>
          <Descriptions.Item label="版本号">
            {sysInfo?.app_version || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="调试模式">
            {sysInfo?.debug ? (
              <Tag color="red">开启</Tag>
            ) : (
              <Tag color="green">关闭</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  )
}

export default AboutPage
