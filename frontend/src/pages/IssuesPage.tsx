import React, { useState, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Table, Typography, Spin, Alert, Tag, Button, Empty } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { fetchProjectIssues, fetchDashboardConfig, DashboardIssue } from '../services/chatService'

const { Text, Title } = Typography

const ISSUE_TYPE_COLORS: Record<string, string> = {
  'Sub-task': '#ebecf0',
  Task: '#e3f0ff',
  Bug: '#ffe7e7',
  Story: '#e0f7e0',
}

const useColumns = (jiraWebUrl: string) => {
  const baseUrl = jiraWebUrl ? jiraWebUrl.replace(/\/+$/, '') : ''
  const keyLink = (v: string) => {
    const href = baseUrl ? `${baseUrl}/browse/${v}` : undefined
    return href
      ? <a href={href} target="_blank" rel="noopener noreferrer" style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 600 }}>{v}</a>
      : <span style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 600 }}>{v}</span>
  }

  return [
    {
      title: 'Key', dataIndex: 'key', key: 'key', width: 130,
      render: keyLink,
    },
    {
      title: '类型', dataIndex: 'issuetype', key: 'issuetype', width: 90,
      render: (v: string) => (
        <Tag color={ISSUE_TYPE_COLORS[v] || '#f0f0f0'} style={{ color: '#333', fontSize: 11 }}>{v || '-'}</Tag>
      ),
    },
    {
      title: '概要', dataIndex: 'summary', key: 'summary',
      render: (v: string) => (
        <div style={{ wordBreak: 'break-word', whiteSpace: 'normal', lineHeight: 1.4, fontSize: 13 }}>{v || '-'}</div>
      ),
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
    },
    {
      title: '优先级', dataIndex: 'priority', key: 'priority', width: 80,
      render: (v: string) => {
        const color = v === 'P1' ? '#ff4d4f' : v === 'P2' ? '#fa8c16' : v === 'P3' ? '#1890ff' : '#8c8c8c'
        return <span style={{ color, fontWeight: 600, fontSize: 12 }}>{v || '-'}</span>
      },
    },
    {
      title: '经办人', dataIndex: 'assignee', key: 'assignee', width: 120, ellipsis: true,
    },
    {
      title: '更新时间', dataIndex: 'updated', key: 'updated', width: 170,
      render: (v: string) => {
        if (!v) return '-'
        try {
          const d = new Date(v)
          return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
        } catch { return v }
      },
    },
  ]
}

const IssuesPage: React.FC = () => {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const project = searchParams.get('project') || ''
  const filter = searchParams.get('filter') || ''

  const filterLabel = filter === 'p0' ? 'P0 问题'
    : filter === 'eta_expiring' ? 'ETA 到期'
    : filter === 'stale' ? '超过 7 天未更新'
    : '问题列表'

  const [issues, setIssues] = useState<DashboardIssue[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [jiraWebUrl, setJiraWebUrl] = useState('')

  useEffect(() => {
    fetchDashboardConfig().then(cfg => {
      setJiraWebUrl((cfg as any)?.jira_web_url || '')
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!project) {
      setLoading(false)
      setError('未指定项目')
      return
    }
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const data = await fetchProjectIssues(project, 200, filter || undefined)
        if (!cancelled) setIssues(data)
      } catch (e: any) {
        if (!cancelled) setError(e.message || '加载失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [project, filter])

  return (
    <div style={{ padding: '24px 32px', height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/dashboard')} />
        <Title level={4} style={{ margin: 0 }}>
          {project === 'all' ? '全部项目' : project} — {filterLabel}
        </Title>
        {!loading && (
          <Text type="secondary" style={{ fontSize: 13 }}>共 {issues.length} 条</Text>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}><Spin size="large" /></div>
      ) : error ? (
        <Alert type="error" showIcon message="加载失败" description={error} style={{ borderRadius: 8 }} />
      ) : issues.length === 0 ? (
        <Empty description="暂无数据" style={{ padding: '80px 0' }} />
      ) : (
        <Table
          dataSource={issues}
          columns={useColumns(jiraWebUrl)}
          rowKey="key"
          size="small"
          pagination={{ pageSize: 50, showTotal: (t) => `共 ${t} 条` }}
          scroll={{ x: 900 }}
        />
      )}
    </div>
  )
}

export default IssuesPage
