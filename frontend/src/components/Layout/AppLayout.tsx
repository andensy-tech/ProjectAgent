import React, { useState, useEffect } from 'react'
import { Layout, Menu, Button, Empty, Popconfirm, Badge, Dropdown, Avatar, message } from 'antd'
import {
  MessageOutlined,
  WarningOutlined,
  SettingOutlined,
  PlusOutlined,
  MessageFilled,
  DeleteOutlined,
  DashboardOutlined,
  AlertOutlined,
  ClockCircleOutlined,
  ToolOutlined,
  UserOutlined,
  LogoutOutlined,
  KeyOutlined,
  InfoCircleOutlined,
  BellOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { fetchConversations, deleteConversation, fetchAlertSummary, fetchNotifications, fetchUnreadCount, markAllNotificationsRead, markNotificationRead } from '../../services/chatService'
import { useAuthStore } from '../../store/authStore'

const { Sider, Header } = Layout

interface Conversation {
  id: number
  title: string
  created_at: string
  updated_at: string
}

interface AppLayoutProps {
  children: React.ReactNode
}

const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const [collapsed] = useState(false)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [alertHighCount, setAlertHighCount] = useState(0)
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthStore()

  // Parse active conversation id from URL query param
  const searchParams = new URLSearchParams(location.search)
  const activeConvId = searchParams.get('conv')
    ? parseInt(searchParams.get('conv')!, 10)
    : null

  const isChatActive = location.pathname === '/' || location.pathname === '/chat'

  const loadConversations = async () => {
    try {
      const convs = await fetchConversations()
      setConversations(convs)
    } catch (e) {
      console.error('Failed to load conversations:', e)
    }
  }

  const loadAlertCount = async () => {
    try {
      const data = await fetchAlertSummary()
      setAlertHighCount(data?.active_high ?? 0)
    } catch (e) {
      // Silently fail — alert summary is non-critical
    }
  }

  // ── 站内消息（P-F）──────────────────────────────────────────────
  const [notifications, setNotifications] = useState<any[]>([])
  const [unreadCount, setUnreadCount] = useState(0)

  const loadNotifications = async () => {
    try {
      const [list, count] = await Promise.all([
        fetchNotifications(20),
        fetchUnreadCount(),
      ])
      setNotifications(list)
      setUnreadCount(count)
    } catch (e) {
      // Silently fail — notifications are non-critical
    }
  }

  const handleNotificationClick = async (item: any) => {
    if (!item.read) {
      try {
        await markNotificationRead(item.id)
        setUnreadCount((c) => Math.max(0, c - 1))
        setNotifications((prev) => prev.map((n) => (n.id === item.id ? { ...n, read: true } : n)))
      } catch (e) { /* ignore */ }
    }
    if (item.link) navigate(item.link)
  }

  const handleMarkAllRead = async () => {
    try {
      await markAllNotificationsRead()
      setUnreadCount(0)
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })))
    } catch (e) { /* ignore */ }
  }

  useEffect(() => {
    loadConversations()
    loadAlertCount()
    loadNotifications()
    // Refresh conversation list every 10 seconds; notifications every 60s
    const timer = setInterval(loadConversations, 10000)
    const notifTimer = setInterval(loadNotifications, 60000)
    return () => {
      clearInterval(timer)
      clearInterval(notifTimer)
    }
  }, [location.pathname])

  const handleNewChat = () => {
    navigate('/chat')
  }

  const handleSelectConversation = (convId: number) => {
    navigate(`/chat?conv=${convId}`)
  }

  const handleDeleteConversation = async (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await deleteConversation(convId)
      loadConversations()
      if (activeConvId === convId) {
        navigate('/chat')
      }
    } catch (e) {
      console.error('Failed to delete conversation:', e)
    }
  }

  const formatTime = (iso: string) => {
    try {
      const d = new Date(iso)
      return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
    } catch {
      return ''
    }
  }

  const groupConversations = (convs: Conversation[]) => {
    const now = new Date()
    const todayStr = now.toDateString()
    const yesterday = new Date(now)
    yesterday.setDate(yesterday.getDate() - 1)
    const yesterdayStr = yesterday.toDateString()

    const groups: Record<string, Conversation[]> = {}

    for (const conv of convs) {
      const d = new Date(conv.updated_at || conv.created_at)
      const dateStr = d.toDateString()

      let key: string
      if (dateStr === todayStr) {
        key = '今天'
      } else if (dateStr === yesterdayStr) {
        key = '昨天'
      } else {
        key = `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
      }

      if (!groups[key]) groups[key] = []
      groups[key].push(conv)
    }

    // Sort groups by date descending
    const sortedKeys = Object.keys(groups).sort((a, b) => {
      const order = ['今天', '昨天']
      const aIdx = order.indexOf(a)
      const bIdx = order.indexOf(b)
      if (aIdx !== -1 && bIdx !== -1) return aIdx - bIdx
      if (aIdx !== -1) return -1
      if (bIdx !== -1) return 1
      // Parse dates like "2026年5月24日"
      const parse = (s: string) => {
        const m = s.match(/(\d+)年(\d+)月(\d+)日/)
        if (m) return new Date(parseInt(m[1]), parseInt(m[2]) - 1, parseInt(m[3]))
        return new Date(0)
      }
      return parse(b).getTime() - parse(a).getTime()
    })

    return sortedKeys.map((key) => ({ key, items: groups[key] }))
  }

  const menuItems = [
    {
      key: '/chat',
      icon: <MessageOutlined />,
      label: '聊天',
    },
    {
      key: '/dashboard',
      icon: <DashboardOutlined />,
      label: '看板',
    },
    {
      key: '/alerts',
      icon: (
        <Badge count={alertHighCount} size="small" offset={[4, -2]}>
          <WarningOutlined style={{ fontSize: 16 }} />
        </Badge>
      ),
      label: '预警',
    },
    {
      key: '/cl-monitoring',
      icon: <AlertOutlined />,
      label: '监控',
    },
    {
      key: '/scheduled-tasks',
      icon: <ClockCircleOutlined />,
      label: '定时任务',
    },
    {
      key: '/skills-config',
      icon: <ToolOutlined />,
      label: 'skills',
    },
    {
      key: '/settings',
      icon: <SettingOutlined />,
      label: '设置',
    },
    {
      key: '/about',
      icon: <InfoCircleOutlined />,
      label: '关于',
    },
  ]

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden', fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif" }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        theme="light"
        width={260}
        style={{
          background: 'linear-gradient(180deg, #fafafa 0%, #f7f7f8 100%)',
          borderRight: '1px solid #e8e8e8',
        }}
      >
        {/* Inner wrapper for flex layout — use 100vh to bypass Ant Design Sider's internal wrapper which breaks height:100% propagation */}
        <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
        {/* Logo / Title */}
        <div
          style={{
            height: '56px',
            display: 'flex',
            alignItems: 'center',
            padding: '0 16px',
            fontSize: '18px',
            fontWeight: 700,
            color: '#4A90E2',
            borderBottom: '1px solid #e8e8e8',
            flexShrink: 0,
            letterSpacing: '0.5px',
          }}
        >
          {collapsed ? 'PA' : 'Project Agent'}
        </div>

        {/* Navigation Menu */}
        <Menu
          mode="inline"
          selectedKeys={[location.pathname === '/' ? '/chat' : location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{
            background: 'transparent',
            borderRight: 0,
            padding: '4px 8px',
            fontSize: '14px',
          }}
          theme="light"
        />

        {/* Divider */}
        <div style={{ margin: '0 16px', borderTop: '1px solid #e8e8e8', flexShrink: 0 }} />

        {/* New Chat Button */}
        <div style={{ padding: '12px 16px', flexShrink: 0 }}>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleNewChat}
            block
            style={{
              borderRadius: '8px',
              height: '40px',
              fontSize: '14px',
              fontWeight: 500,
              background: '#4A90E2',
              borderColor: '#4A90E2',
              boxShadow: '0 2px 6px rgba(74, 144, 226, 0.2)',
            }}
          >
            新对话
          </Button>
        </div>

        {/* Conversation History */}
        <div
          style={{
            flex: 1,
            overflow: 'auto',
            minHeight: 0,
            padding: '0 12px 12px',
          }}
        >
          {conversations.length === 0 ? (
            <Empty
              description=""
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              style={{ marginTop: 20, opacity: 0.5 }}
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {groupConversations(conversations).map((group) => (
                <div key={group.key}>
                  <div
                    style={{
                      fontSize: '12px',
                      color: '#999',
                      padding: '8px 8px 4px',
                      fontWeight: 600,
                      letterSpacing: '0.3px',
                    }}
                  >
                    {group.key}
                  </div>
                  {group.items.map((conv) => {
                    const isActive = isChatActive && activeConvId === conv.id
                    return (
                      <div
                        key={conv.id}
                        onClick={() => handleSelectConversation(conv.id)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '10px',
                          padding: '8px 10px 8px 7px',
                          borderRadius: '8px',
                          cursor: 'pointer',
                          background: isActive ? '#eef3fc' : 'transparent',
                          borderLeft: isActive ? '3px solid #4A90E2' : '3px solid transparent',
                          transition: 'all 0.15s ease',
                          position: 'relative',
                        }}
                        onMouseEnter={(e) => {
                          if (!isActive) {
                            e.currentTarget.style.background = '#f0f0f0'
                          }
                          const delBtn = e.currentTarget.querySelector('.del-btn') as HTMLElement
                          if (delBtn) delBtn.style.opacity = '1'
                        }}
                        onMouseLeave={(e) => {
                          if (!isActive) {
                            e.currentTarget.style.background = 'transparent'
                          }
                          const delBtn = e.currentTarget.querySelector('.del-btn') as HTMLElement
                          if (delBtn) delBtn.style.opacity = '0'
                        }}
                      >
                        <MessageFilled
                          style={{
                            fontSize: '14px',
                            color: isActive ? '#4A90E2' : '#999',
                            flexShrink: 0,
                          }}
                        />
                        <span
                          style={{
                            flex: 1,
                            fontSize: '13px',
                            color: isActive ? '#4A90E2' : '#333',
                            fontWeight: isActive ? 500 : 400,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {conv.title || '新对话'}
                        </span>
                        <span
                          style={{
                            fontSize: '11px',
                            color: '#bbb',
                            flexShrink: 0,
                          }}
                        >
                          {formatTime(conv.updated_at || conv.created_at)}
                        </span>
                        <Popconfirm
                          title="删除对话"
                          description="删除后将无法恢复"
                          onConfirm={(e) => handleDeleteConversation(conv.id, e as unknown as React.MouseEvent)}
                          onCancel={(e) => e?.stopPropagation()}
                          okText="删除"
                          cancelText="取消"
                          okButtonProps={{ danger: true }}
                        >
                          <Button
                            type="text"
                            size="small"
                            danger
                            icon={<DeleteOutlined style={{ fontSize: '12px' }} />}
                            className="del-btn"
                            style={{
                              opacity: 0,
                              transition: 'opacity 0.15s ease',
                              padding: '0 4px',
                              minWidth: 'auto',
                              height: 'auto',
                            }}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </Popconfirm>
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
        </div>  {/* end inner flex wrapper */}
      </Sider>

      <Layout style={{ background: '#fff' }}>
        <Header
          style={{
            padding: '0 24px',
            background: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
            height: '56px',
            position: 'sticky',
            top: 0,
            zIndex: 10,
            boxShadow: '0 1px 4px rgba(0,0,0,0.02)',
          }}
        >
          <span style={{ fontSize: '15px', fontWeight: 500, color: '#333' }}>
            项目管理AI助手
          </span>
          {/* ── 右侧：消息铃铛 + 用户（P-F）──────────────────────── */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <Dropdown
            dropdownRender={() => (
              <div style={{ width: 320, background: '#fff', borderRadius: 8, boxShadow: '0 3px 6px rgba(0,0,0,0.15)', padding: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 8px' }}>
                  <span style={{ fontWeight: 500 }}>消息通知</span>
                  {unreadCount > 0 && (
                    <Button type="link" size="small" onClick={handleMarkAllRead}>全部已读</Button>
                  )}
                </div>
                {notifications.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无消息" style={{ padding: '16px 0' }} />
                ) : (
                  <div style={{ maxHeight: 360, overflow: 'auto' }}>
                    {notifications.map((n) => (
                      <div
                        key={n.id}
                        onClick={() => handleNotificationClick(n)}
                        style={{
                          padding: '8px 10px',
                          cursor: 'pointer',
                          borderBottom: '1px solid #f5f5f5',
                          opacity: n.read ? 0.6 : 1,
                        }}
                      >
                        <div style={{ fontSize: 13, fontWeight: n.read ? 400 : 500 }}>{n.title}</div>
                        {n.content && <div style={{ fontSize: 12, color: '#888' }}>{n.content}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            trigger={['click']}
          >
            <Badge count={unreadCount} size="small" offset={[4, 0]}>
              <BellOutlined style={{ fontSize: 16, cursor: 'pointer', color: '#333' }} />
            </Badge>
          </Dropdown>
          <Dropdown
            menu={{
              items: [
                {
                  key: 'user',
                  label: `${user?.username || '用户'}`,
                  disabled: true,
                },
                { type: 'divider' },
                ...(user?.is_superuser ? [
                  {
                    key: 'admin-users',
                    icon: <UserOutlined />,
                    label: '用户管理',
                  },
                  { type: 'divider' } as any,
                ] : []),
                {
                  key: 'change-password',
                  icon: <KeyOutlined />,
                  label: '修改密码',
                },
                { type: 'divider' },
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  danger: true,
                },
              ],
              onClick: ({ key }) => {
                if (key === 'logout') {
                  logout()
                  message.success('已退出登录')
                  navigate('/login', { replace: true })
                } else if (key === 'change-password') {
                  navigate('/change-password')
                } else if (key === 'admin-users') {
                  navigate('/admin/users')
                }
              },
            }}
            placement="bottomRight"
          >
            <span
              style={{
                color: '#4A90E2',
                fontSize: '13px',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                cursor: 'pointer',
              }}
            >
              <Avatar
                size={24}
                icon={<UserOutlined />}
                style={{ background: '#4A90E2' }}
              />
              {user?.username || '用户'}
            </span>
          </Dropdown>
          </div>
        </Header>
        <div
          style={{
            background: '#fff',
            height: 'calc(100vh - 56px)',
            overflow: 'hidden',
          }}
        >
          {children}
        </div>
      </Layout>
    </Layout>
  )
}

export default AppLayout
