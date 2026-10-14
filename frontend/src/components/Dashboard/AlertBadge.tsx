import React, { useState, useEffect } from 'react'
import { Card, Tag, Typography, Button } from 'antd'
import { useNavigate } from 'react-router-dom'
import {
  BellOutlined,
  ExclamationCircleOutlined,
  WarningOutlined,
  InfoCircleOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { apiFetch } from '../../services/chatService'

const { Text } = Typography

interface AlertItem {
  id: number
  level: string
  title: string
  status: string
  created_at: string | null
}

const LEVEL_CONFIG: Record<string, { color: string; icon: React.ReactNode }> = {
  HIGH: { color: '#ff4d4f', icon: <ExclamationCircleOutlined /> },
  MEDIUM: { color: '#fa8c16', icon: <WarningOutlined /> },
  LOW: { color: '#1890ff', icon: <InfoCircleOutlined /> },
}

const AlertBadge: React.FC = () => {
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await apiFetch<{ alerts: AlertItem[]; total: number }>(
          '/api/alerts?status=ACTIVE&limit=5'
        )
        if (!cancelled) setAlerts(data.alerts || [])
      } catch {
        // Silent fail
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  const formatTime = (iso: string | null) => {
    if (!iso) return ''
    try {
      const d = new Date(iso)
      return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
    } catch { return '' }
  }

  if (loading) return null

  return (
    <Card
      size="small"
      title={
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          <BellOutlined style={{ marginRight: 6, color: alerts.length > 0 ? '#ff4d4f' : '#52c41a' }} />
          风险预警
        </span>
      }
      extra={
        <Button type="link" size="small" onClick={() => navigate('/alerts')}>
          查看全部 <RightOutlined />
        </Button>
      }
      style={{ borderRadius: 8, marginBottom: 24 }}
    >
      {alerts.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '12px 0' }}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            ✅ 无风险预警
          </Text>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {alerts.map((a) => {
            const cfg = LEVEL_CONFIG[a.level] || { color: '#999', icon: null }
            return (
              <div
                key={a.id}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '4px 0', cursor: 'pointer',
                }}
                onClick={() => navigate('/alerts')}
              >
                <span style={{ color: cfg.color, fontSize: 14 }}>{cfg.icon}</span>
                <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.title}
                </span>
                <Tag color={a.status === 'ACTIVE' ? 'red' : 'default'} style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                  {a.status === 'ACTIVE' ? '待处理' : a.status}
                </Tag>
                <span style={{ fontSize: 11, color: '#999', flexShrink: 0 }}>
                  {formatTime(a.created_at)}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

export default AlertBadge
