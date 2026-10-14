import React, { useCallback, useEffect, useState } from 'react'
import { Card, Spin, Typography } from 'antd'
import { BookOutlined } from '@ant-design/icons'
import { dismissFocusSet, fetchFocusSets } from '../../services/chatService'
import FocusSetCard from './FocusSetCard'

const { Text } = Typography

/**
 * PM 关注指令列表（P-C：看板状态图，方案 8.1）。
 * 展示所有关注集合的健康度卡片；无集合时不渲染（不打扰）。
 */
const FocusCommandList: React.FC<{ jiraWebUrl?: string }> = ({ jiraWebUrl }) => {
  const [focusSets, setFocusSets] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const data = await fetchFocusSets()
      setFocusSets(data)
    } catch (e) {
      console.error('加载关注指令失败', e)
      setFocusSets([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleDismiss = async (id: number) => {
    try {
      await dismissFocusSet(id)
      setFocusSets((prev) => prev.filter((f) => f.id !== id))
    } catch (e) {
      console.error('取消关注失败', e)
    }
  }

  if (loading) {
    return <Spin size="small" style={{ display: 'block', margin: '12px auto' }} />
  }
  if (focusSets.length === 0) return null

  return (
    <Card size="small" style={{ marginBottom: 24, borderRadius: 8 }}>
      <div style={{ marginBottom: 12, fontSize: 14, fontWeight: 500, color: '#333' }}>
        <BookOutlined style={{ marginRight: 6, color: '#4A90E2' }} />
        我的关注指令
        <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
          {focusSets.length} 个进行中
        </Text>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {focusSets.map((fs) => (
          <FocusSetCard key={fs.id} focusSet={fs} onDismiss={handleDismiss} jiraWebUrl={jiraWebUrl} />
        ))}
      </div>
    </Card>
  )
}

export default FocusCommandList
