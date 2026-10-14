import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  List, Typography, Button, Modal, message as antMsg, Spin, Empty,
  Tag, Space, Tooltip, Input,
} from 'antd'
import {
  DeleteOutlined, MessageOutlined, CalendarOutlined,
  ArrowLeftOutlined, SearchOutlined, ReloadOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import { fetchConversations, fetchConversation, deleteConversation } from '../services/chatService'

const { Title, Text } = Typography
const { confirm } = Modal

interface Conversation {
  id: number
  title: string
  created_at: string
  updated_at: string
}

interface ChatMessage {
  id: number
  role: string
  content: string
  type: string
  data: Record<string, any>
  created_at: string
}

const HistoryPage: React.FC = () => {
  const navigate = useNavigate()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedConv, setSelectedConv] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [messagesLoading, setMessagesLoading] = useState(false)
  const [searchText, setSearchText] = useState('')

  const loadConversations = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchConversations()
      setConversations(data || [])
    } catch (e) {
      console.error('Failed to load conversations:', e)
      antMsg.error('加载历史记录失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadConversations()
  }, [loadConversations])

  const fetchMessages = async (convId: number) => {
    setMessagesLoading(true)
    try {
      const data = await fetchConversation(convId)
      setMessages(data.messages || [])
    } catch (e) {
      console.error('Failed to load messages:', e)
      antMsg.error('加载消息失败')
    } finally {
      setMessagesLoading(false)
    }
  }

  const handleSelectConv = (conv: Conversation) => {
    setSelectedConv(conv)
    fetchMessages(conv.id)
  }

  const handleDelete = (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    confirm({
      title: '确认删除',
      content: '删除后将无法恢复此对话记录',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteConversation(convId)
          antMsg.success('已删除')
          if (selectedConv?.id === convId) {
            setSelectedConv(null)
            setMessages([])
          }
          loadConversations()
        } catch {
          antMsg.error('删除失败')
        }
      },
    })
  }

  const handleBack = () => {
    setSelectedConv(null)
    setMessages([])
  }

  const formatDate = (dateStr: string) => {
    if (!dateStr) return ''
    try {
      const d = new Date(dateStr)
      return d.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return dateStr
    }
  }

  const filteredConversations = conversations.filter((c) =>
    !searchText || c.title?.toLowerCase().includes(searchText.toLowerCase()),
  )

  if (selectedConv) {
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column', padding: '24px' }}>
        <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
          <Button icon={<ArrowLeftOutlined />} onClick={handleBack}>
            返回列表
          </Button>
          <Title level={5} style={{ margin: 0, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {selectedConv.title}
          </Title>
        </div>
        <div
          style={{
            flex: 1,
            overflow: 'auto',
            padding: '16px',
            background: '#fafafa',
            borderRadius: 8,
          }}
        >
          {messagesLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin />
            </div>
          ) : messages.length === 0 ? (
            <Empty description="暂无消息" />
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                style={{
                  display: 'flex',
                  justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                  marginBottom: 16,
                }}
              >
                <div
                  style={{
                    maxWidth: '75%',
                    padding: '10px 14px',
                    borderRadius: 12,
                    background: msg.role === 'user'
                      ? 'linear-gradient(135deg, #5B8DEF 0%, #4A90E2 100%)'
                      : '#fff',
                    color: msg.role === 'user' ? '#fff' : '#333',
                    border: msg.role === 'user' ? 'none' : '1px solid #e8e8e8',
                    boxShadow: msg.role === 'user'
                      ? '0 2px 8px rgba(91, 141, 239, 0.25)'
                      : '0 1px 2px rgba(0,0,0,0.04)',
                    wordBreak: 'break-word',
                  }}
                >
                  {msg.role === 'assistant' && (
                    <Tag color="blue" style={{ marginBottom: 6, fontSize: 11 }}>
                      Agent
                    </Tag>
                  )}
                  {msg.type === 'card' ? (
                    <div>
                      <div style={{ fontWeight: 500, marginBottom: 8, fontSize: 14 }}>{msg.content}</div>
                      <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', margin: 0, background: '#f5f5f5', padding: 12, borderRadius: 6 }}>
                        {JSON.stringify(msg.data || msg, null, 2)}
                      </pre>
                    </div>
                  ) : (
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  )}
                  <div
                    style={{
                      fontSize: 11,
                      color: msg.role === 'user' ? 'rgba(255,255,255,0.7)' : '#999',
                      marginTop: 6,
                    }}
                  >
                    {formatDate(msg.created_at)}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    )
  }

  return (
    <div style={{ height: '100%', padding: '24px' }}>
      <div
        style={{
          marginBottom: 16,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <Title level={5} style={{ margin: 0 }}>
          历史记录
        </Title>
        <Space>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索对话..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            style={{ width: 200 }}
            allowClear
          />
          <Tooltip title="刷新">
            <Button icon={<ReloadOutlined />} onClick={loadConversations} />
          </Tooltip>
        </Space>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : filteredConversations.length === 0 ? (
        <Empty description={searchText ? '未找到匹配的对话' : '暂无历史记录'}>
          {!searchText && (
            <Button type="primary" onClick={() => navigate('/chat')}>
              开始新对话
            </Button>
          )}
        </Empty>
      ) : (
        <List
          dataSource={filteredConversations}
          renderItem={(conv) => (
            <List.Item
              onClick={() => handleSelectConv(conv)}
              style={{
                cursor: 'pointer',
                padding: '12px 16px',
                borderRadius: 8,
                transition: 'all 0.2s ease',
                borderLeft: '3px solid transparent',
              }}
              onMouseEnter={(e) => {
                const el = e.currentTarget as HTMLElement
                el.style.background = '#f7f7f7'
                el.style.borderLeftColor = '#4A90E2'
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget as HTMLElement
                el.style.background = 'transparent'
                el.style.borderLeftColor = 'transparent'
              }}
              actions={[
                <Button
                  key="delete"
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={(e) => handleDelete(conv.id, e)}
                />,
              ]}
            >
              <List.Item.Meta
                avatar={
                  <MessageOutlined style={{ fontSize: 20, color: '#4A90E2' }} />
                }
                title={
                  <Text strong ellipsis style={{ maxWidth: 400, fontSize: 14 }}>
                    {conv.title}
                  </Text>
                }
                description={
                  <Space size="small">
                    <CalendarOutlined />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {formatDate(conv.updated_at)}
                    </Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  )
}

export default HistoryPage
