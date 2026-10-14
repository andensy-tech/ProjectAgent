import React, { useState, useRef, useEffect } from 'react'
import { Input, Button, Spin, Card, Tag, Typography, Tooltip, message } from 'antd'
import {
  SendOutlined,
  PaperClipOutlined,
  LoadingOutlined,
  LinkOutlined,
  CloseCircleOutlined,
  RobotOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import { useSearchParams, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  sendMessage,
  fetchConversation,
  uploadFile,
  fetchSkills,
  downloadFile,
  extractFileDownload,
  FileDownload,
  SkillInfo,
} from '../services/chatService'

const { TextArea } = Input
const { Text } = Typography

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  type: string
  data?: any
  actions?: any[]
  created_at?: string
  taskId?: string
  _task_done?: boolean
  _file_download?: FileDownload
}

/** 人类可读的文件大小。 */
const formatFileSize = (bytes?: number): string => {
  if (!bytes || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// --- Report Card Sub-components ---

const ReportSummaryBadge: React.FC<{ label: string; value: number; color: string }> = ({ label, value, color }) => (
  <div style={{ textAlign: 'center', padding: '8px 12px', background: '#fafafa', borderRadius: 8, minWidth: 80 }}>
    <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
    <div style={{ fontSize: 11, color: '#666', marginTop: 2 }}>{label}</div>
  </div>
)

const JiraReportCard: React.FC<{ title: string; data: any; actions?: any[] }> = ({ title, data, actions }) => {
  const hasRisks = (data?.p1_issues || 0) > 0 || (data?.eta_expiring || 0) > 0
  return (
    <Card
      size="small"
      title={<Text strong style={{ fontSize: 14 }}>{title}</Text>}
      style={{ maxWidth: 680, borderLeft: `4px solid ${hasRisks ? '#ff4d4f' : '#52c41a'}`, borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}
      actions={actions?.map((act, i) => (
        act.url ? (
          <Button key={i} type="link" size="small" href={act.url} target="_blank" icon={<LinkOutlined />}>
            {act.label}
          </Button>
        ) : (
          <Button key={i} type="link" size="small">{act.label}</Button>
        )
      ))}
    >
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {data?.total_issues !== undefined && <ReportSummaryBadge label="总计" value={data.total_issues} color="#1890ff" />}
        {data?.p1_issues !== undefined && <ReportSummaryBadge label="P1问题" value={data.p1_issues} color="#ff4d4f" />}
        {data?.eta_expiring !== undefined && <ReportSummaryBadge label="ETA到期" value={data.eta_expiring} color="#fa8c16" />}
        {data?.stale_issues !== undefined && <ReportSummaryBadge label="超过7天未更新" value={data.stale_issues} color="#722ed1" />}
        {data?.remaining !== undefined && <ReportSummaryBadge label="其他" value={data.remaining} color="#1890ff" />}
      </div>
      {data?.risks && (
        <div style={{ marginTop: 8, padding: 8, background: '#fff2f0', borderRadius: 6, fontSize: 12 }}>
          <Text type="danger" strong>风险概览</Text>
          {Object.entries(data.risks).map(([key, val]: any) =>
            val > 0 ? <div key={key} style={{ marginTop: 4 }}>• {riskLabel(key)}: <Text strong type="danger">{val}</Text></div> : null
          )}
        </div>
      )}
    </Card>
  )
}

const ClMergeReportCard: React.FC<{ title: string; data: any; actions?: any[] }> = ({ title, data, actions }) => {
  return (
    <Card
      size="small"
      title={<Text strong style={{ fontSize: 14 }}>{title}</Text>}
      style={{ maxWidth: 680, borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}
      actions={actions?.map((act, i) => (
        act.url ? (
          <Button key={i} type="link" size="small" href={act.url} target="_blank" icon={<LinkOutlined />}>
            {act.label}
          </Button>
        ) : (
          <Button key={i} type="link" size="small">{act.label}</Button>
        )
      ))}
    >
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {data?.verified_jiras !== undefined && <ReportSummaryBadge label="已验证Jira" value={data.verified_jiras} color="#1890ff" />}
        {data?.total_cls !== undefined && <ReportSummaryBadge label="关联CL" value={data.total_cls} color="#1890ff" />}
        {data?.merged !== undefined && <ReportSummaryBadge label="已合并" value={data.merged} color="#52c41a" />}
        {data?.unmerged !== undefined && <ReportSummaryBadge label="未合并" value={data.unmerged} color={data.unmerged > 0 ? '#ff4d4f' : '#52c41a'} />}
      </div>
      {data?.risks && (
        <div style={{ marginTop: 8, padding: 8, background: '#fff2f0', borderRadius: 6, fontSize: 12 }}>
          <Text type="danger" strong>风险告警</Text>
          {Object.entries(data.risks).map(([key, val]: any) =>
            val > 0 ? <div key={key} style={{ marginTop: 4 }}>• {riskLabel(key)}: <Text strong type="danger">{val}</Text></div> : null
          )}
        </div>
      )}
    </Card>
  )
}

function riskLabel(key: string): string {
  const labels: Record<string, string> = {
    unmerged_cl_blocking: 'P1阻塞未合并',
    long_pending_cl: '长期未合入',
    epic_convergence_risk: 'Epic收敛风险',
    too_many_cls: 'CL数量过多',
    missing_assignee: '缺少Assignee',
  }
  return labels[key] || key
}

// --- Reasoning Chain Component ---

const ReasoningChain: React.FC<{ steps: Array<{type: string, content: string, tool?: string}> }> = ({ steps }) => {
  const [collapsed, setCollapsed] = useState(false)

  if (steps.length === 0) return null

  return (
    <div style={{
      margin: '8px 0',
      borderRadius: 10,
      border: '1px solid #e8e8e8',
      background: '#fafbfc',
      overflow: 'hidden',
    }}>
      <div
        onClick={() => setCollapsed(!collapsed)}
        style={{
          padding: '8px 14px',
          fontSize: 12,
          color: '#888',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          userSelect: 'none',
        }}
      >
        <span style={{ fontWeight: 600, color: '#666' }}>推理过程</span>
        <span style={{ color: '#aaa' }}>({steps.length} 步)</span>
        <span style={{ marginLeft: 'auto', color: '#999', fontSize: 11 }}>
          {collapsed ? '展开' : '收起'} ▸
        </span>
      </div>
      {!collapsed && (
        <div style={{ padding: '0 14px 10px', fontSize: 12, lineHeight: 1.7, color: '#666' }}>
          {steps.map((step, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
              <span style={{
                flexShrink: 0,
                width: 16,
                textAlign: 'center',
                color: step.type === 'thought' ? '#4A90E2' : '#52c41a',
                fontWeight: 600,
                fontSize: 11,
              }}>
                {step.type === 'thought' ? '→' : '✓'}
              </span>
              <span>{step.content}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// --- File Delivery Components ---

/** 下载 Agent 产出的文件（带 JWT 拉 blob，<a href> 拿不到鉴权头）。 */
const FileDownloadButton: React.FC<{ file: FileDownload }> = ({ file }) => {
  const [downloading, setDownloading] = useState(false)

  const handleDownload = async () => {
    setDownloading(true)
    try {
      await downloadFile(file.download_url, file.file_name)
    } catch (e: any) {
      message.error(`下载失败：${e?.message || e}`)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <Button
      type="primary"
      size="small"
      icon={<DownloadOutlined />}
      loading={downloading}
      onClick={handleDownload}
      style={{ borderRadius: 6, background: '#4A90E2', borderColor: '#4A90E2' }}
    >
      下载文件：{file.file_name}
    </Button>
  )
}

/** 文件卡片：Agent 生成文件后回传，用户一键下载。 */
const FileDeliveryCard: React.FC<{ file: FileDownload }> = ({ file }) => {
  const size = formatFileSize(file.size)
  return (
    <Card
      size="small"
      style={{
        maxWidth: 520,
        borderLeft: '4px solid #4A90E2',
        borderRadius: 12,
        boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <PaperClipOutlined style={{ color: '#4A90E2', fontSize: 16 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#333', wordBreak: 'break-all' }}>
            {file.file_name}
          </div>
          <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
            {size}
            {size && file.skill_name ? ' · ' : ''}
            {file.skill_name ? `来自技能 ${file.skill_name}` : ''}
          </div>
        </div>
      </div>
      <FileDownloadButton file={file} />
    </Card>
  )
}

// --- Main Chat Component ---

/** Safe UUID generator — falls back to timestamp if crypto.randomUUID() unavailable */
const safeUUID = (): string => {
  try { return crypto.randomUUID() } catch { return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}` }
}

const ChatPage: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([])
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [attachedFile, setAttachedFile] = useState<{ fileId: string; fileName: string; fileSize: number } | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [isInputFocused, setIsInputFocused] = useState(false)
  const [showSkills, setShowSkills] = useState(false)
  const [availableSkills, setAvailableSkills] = useState<SkillInfo[]>([])
  const [reasoningSteps, setReasoningSteps] = useState<Array<{type: string, content: string, tool?: string}>>([])
  const [showReasoning, setShowReasoning] = useState(true)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const convId = searchParams.get('conv') ? parseInt(searchParams.get('conv')!, 10) : null

  const scrollToBottom = () => {
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight
    }
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // Load conversation when convId changes
  useEffect(() => {
    if (convId) {
      loadConversation(convId)
    } else {
      // New chat - show welcome
      setMessages([{
        id: 'welcome',
        role: 'assistant',
        content: '您好！我是您的项目管理AI助手。我可以帮您：\n\n- 📊 **汇总Jira状态** - 生成项目报告\n- ⚠️ **风险预警** - 识别项目风险\n- 📝 **记录需求** - 管理需求变更\n\n请问有什么可以帮您的？',
        type: 'text',
      }])
    }
  }, [convId])

  // Detect /skills command and fetch available skills
  useEffect(() => {
    const trimmed = inputValue.trim().toLowerCase()
    if (trimmed === '/skills') {
      fetchSkills()
        .then((skills) => {
          setAvailableSkills(skills)
          setShowSkills(true)
        })
        .catch(() => setShowSkills(false))
    } else if (showSkills && !trimmed.startsWith('/')) {
      setShowSkills(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputValue])

  // Cleanup: abort SSE on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort()
    }
  }, [])

  const loadConversation = async (id: number) => {
    try {
      const data = await fetchConversation(id)
      const loaded: Message[] = (data.messages || []).map((m: any, i: number) => ({
        id: `msg-${m.id || i}`,
        role: m.role,
        content: m.content,
        type: m.type,
        data: m.data,
        created_at: m.created_at,
      }))
      if (loaded.length === 0) {
        loaded.push({
          id: 'welcome',
          role: 'assistant',
          content: '您好！我是项目管理AI助手。有什么可以帮您的？',
          type: 'text',
        })
      }
      setMessages(loaded)
    } catch (e) {
      console.error('Failed to load conversation:', e)
    }
  }

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setIsUploading(true)
    try {
      const result = await uploadFile(file)
      setAttachedFile({ fileId: result.file_id, fileName: result.file_name, fileSize: file.size })
    } catch (err) {
      console.error('Upload failed:', err)
    } finally {
      setIsUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleRemoveFile = () => {
    setAttachedFile(null)
  }

  const handleSend = async (overrideText?: string) => {
    const textToSend = overrideText !== undefined ? overrideText : inputValue
    if ((!textToSend.trim() && !attachedFile) || isLoading) return

    // Abort previous SSE request if still in flight
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const userMessage: Message = {
      id: safeUUID(),
      role: 'user',
      content: textToSend,
      type: 'text',
      created_at: new Date().toISOString(),
    }

    setMessages((prev) => [...prev, userMessage])
    const sentMessage = textToSend
    setInputValue('')
    setReasoningSteps([])
    setIsLoading(true)

    try {
      const currentFileId = attachedFile?.fileId
      setAttachedFile(null) // Clear file chip after sending

      await sendMessage(sentMessage, (response) => {
        // Update URL if a new conversation was created
        if (response.conversation_id && response.conversation_id !== convId) {
          navigate(`/chat?conv=${response.conversation_id}`, { replace: true })
        }

        const newMessage: Message = {
          id: response.conversation_id
            ? `msg-${response.conversation_id}-${Date.now()}`
            : safeUUID(),
          role: 'assistant',
          content: response.content || '',
          type: response.type,
          data: response.data,
          actions: response.actions,
          created_at: response.type !== 'thinking' ? new Date().toISOString() : undefined,
        }

        if (response.type === 'thinking') {
          if (response.data?.reasoning_step) {
            // Accumulate reasoning chain steps
            if (showReasoning) {
              setReasoningSteps((prev) => [...prev, {
                type: response.data.step_type,
                content: response.content,
                tool: response.data.tool,
              }])
            }
          } else {
            setMessages((prev) => {
              const lastMsg = prev[prev.length - 1]
              if (lastMsg?.type === 'thinking' && lastMsg.role === 'assistant') {
                return [...prev.slice(0, -1), newMessage]
              }
              return [...prev, newMessage]
            })
          }
          setIsLoading(true)
        } else if (response.type === 'complete') {
          setIsLoading(false)
        } else if (response.type === 'ask_user') {
          // Phase 2: Agent 暂停等待用户答复——展示问题卡片，停止 loading
          setMessages((prev) => {
            const lastMsg = prev[prev.length - 1]
            if (lastMsg?.type === 'thinking' && lastMsg.role === 'assistant') {
              return [...prev.slice(0, -1), newMessage]
            }
            return [...prev, newMessage]
          })
          setIsLoading(false)
        } else if (response.type === 'error') {
          setMessages((prev) => {
            const lastMsg = prev[prev.length - 1]
            if (lastMsg?.type === 'thinking' && lastMsg.role === 'assistant') {
              return [...prev.slice(0, -1), newMessage]
            }
            return [...prev, newMessage]
          })
          setIsLoading(false)
        } else if (response.type === 'task_submitted') {
          const taskId = response.data?.task_id
          if (taskId) {
            setMessages((prev) => {
              const lastMsg = prev[prev.length - 1]
              const submitMsg = { ...newMessage, content: response.content || '任务已提交到后台执行', type: 'task_submitted' as const, taskId }
              if (lastMsg?.type === 'thinking' && lastMsg.role === 'assistant') {
                return [...prev.slice(0, -1), submitMsg]
              }
              return [...prev, submitMsg]
            })
            // 长任务轮询可能持续几十分钟（CI 构建下载+上传），单次瞬时失败
            // （502/503/网络抖动）不能直接放弃——容错若干次后再提示用户。
            let pollFails = 0
            const POLL_MAX_FAILS = 5
            const poll = setInterval(async () => {
              try {
                const res = await fetch("/api/tasks/" + taskId, { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') } })
                if (!res.ok) {
                  if (++pollFails < POLL_MAX_FAILS) return
                  clearInterval(poll)
                  setMessages((prev) => {
                    const copy = [...prev]
                    for (let i = copy.length - 1; i >= 0; i--) {
                      if (copy[i] && copy[i].type === 'task_submitted' && copy[i].taskId === taskId) {
                        copy[i] = { ...copy[i], content: '⚠️ 进度查询中断，任务仍在后台执行。完成后结果会出现在本会话聊天记录中（可刷新页面查看）。' }
                        break
                      }
                    }
                    return copy
                  })
                  setIsLoading(false)
                  return
                }
                pollFails = 0
                const data = await res.json()
                if (data.status === 'done' || data.status === 'failed' || data.status === 'cancelled') {
                  clearInterval(poll)
                  const statusLabel = data.status === 'done' ? '✅' : data.status === 'failed' ? '❌' : '⏹️'
                  const statusContent = data.result?.message || data.progress ||
                    (data.status === 'done' ? '任务已完成' : data.status === 'failed' ? '任务执行失败' : '任务已取消')
                  const contentText = `${statusLabel} ${statusContent}`
                  const fileDownload = data.status === 'done' ? extractFileDownload(data.result) : undefined
                  setMessages((prev) => {
                    const copy = [...prev]
                    for (let i = copy.length - 1; i >= 0; i--) {
                      if (copy[i] && copy[i].type === 'task_submitted' && copy[i].taskId === taskId) {
                        copy[i] = { ...copy[i], _task_done: true, content: contentText, _file_download: fileDownload }
                        break
                      }
                    }
                    return copy
                  })
                  setIsLoading(false)
                } else {
                  setMessages((prev) => {
                    const copy = [...prev]
                    const last = copy[copy.length - 1]
                    if (last && last.type === 'task_submitted') {
                      copy[copy.length - 1] = { ...last, content: data.progress || '任务执行中...' }
                    }
                    return copy
                  })
                }
              } catch {
                if (++pollFails < POLL_MAX_FAILS) return
                clearInterval(poll)
                setIsLoading(false)
              }
            }, 3000)
          }
        } else {
          setMessages((prev) => {
            const lastMsg = prev[prev.length - 1]
            if (lastMsg?.type === 'thinking' && lastMsg.role === 'assistant') {
              return [...prev.slice(0, -1), newMessage]
            }
            return [...prev, newMessage]
          })
        }
      }, convId ?? undefined, currentFileId, controller.signal)
        .finally(() => setIsLoading(false))
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: 'assistant',
          content: '抱歉，处理请求时出错。请稍后重试。',
          type: 'error',
          created_at: new Date().toISOString(),
        },
      ])
      setIsLoading(false)
    }
  }

  const handleSkillClick = async (skill: SkillInfo) => {
    setShowSkills(false)
    setInputValue('')
    setReasoningSteps([])

    // Submit skill to background task queue (Phase B)
    const skillMsg = `执行技能: ${skill.name}`
    setMessages((prev) => [...prev, {
      id: safeUUID(),
      role: 'user',
      content: skillMsg,
      type: 'text',
      created_at: new Date().toISOString(),
    }])
    setIsLoading(true)

    try {
      const res = await fetch('/api/tasks/submit', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + (localStorage.getItem('token') || ''),
        },
        body: JSON.stringify({ skill_name: skill.name, params: {} }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(errData.detail || `提交失败: ${res.status}`)
      }
      const data = await res.json()
      const taskId = data.task_id
      setMessages((prev) => [...prev, {
        id: safeUUID(),
        role: 'assistant',
        content: data.message || '任务已提交到后台执行',
        type: 'task_submitted',
        taskId,
        created_at: new Date().toISOString(),
      }])

      // Poll task status every 3 seconds
      const poll = setInterval(async () => {
        try {
          const statusRes = await fetch('/api/tasks/' + taskId, {
            headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') },
          })
          if (!statusRes.ok) { clearInterval(poll); setIsLoading(false); return }
          const statusData = await statusRes.json()
          if (statusData.status === 'done' || statusData.status === 'failed' || statusData.status === 'cancelled') {
            clearInterval(poll)
            const statusLabel = statusData.status === 'done' ? '✅' : statusData.status === 'failed' ? '❌' : '⏹️'
            const statusContent = statusData.result?.message || statusData.progress ||
              (statusData.status === 'done' ? '任务已完成' : statusData.status === 'failed' ? '任务执行失败' : '任务已取消')
            // Check if there's a downloadable file (后端在 result.files 中给出签名下载链接)
            const contentText = `${statusLabel} ${statusContent}`
            const fileDownload = statusData.status === 'done'
              ? extractFileDownload(statusData.result)
              : undefined
            setMessages((prev) => {
              const copy = [...prev]
              for (let i = copy.length - 1; i >= 0; i--) {
                if (copy[i] && copy[i].type === 'task_submitted' && copy[i].taskId === taskId) {
                  copy[i] = { ...copy[i], _task_done: true, content: contentText, _file_download: fileDownload }
                  break
                }
              }
              return copy
            })
            setIsLoading(false)
          } else {
            setMessages((prev) => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last && last.type === 'task_submitted') {
                copy[copy.length - 1] = { ...last, content: statusData.progress || '任务执行中...' }
              }
              return copy
            })
          }
        } catch { clearInterval(poll); setIsLoading(false) }
      }, 3000)
    } catch (e: any) {
      setMessages((prev) => [...prev, {
        id: safeUUID(),
        role: 'assistant',
        type: 'error',
        content: typeof e === 'string' ? e : (e?.message || '技能提交失败'),
        created_at: new Date().toISOString(),
      }])
      setIsLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Pass-through: backend `_format_tool_result` already produces clean GFM tables.
  // No sanitization needed — kept as a hook for future use.
  const sanitizeMarkdownTables = (text: string): string => {
    return text
  }

  const formatMsgTime = (iso?: string) => {
    if (!iso) return ''
    try {
      const d = new Date(iso)
      return d.toLocaleString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return ''
    }
  }

  const renderMessageContent = (msg: Message) => {
    if (msg.type === 'thinking') {
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#666', fontSize: 14 }}>
          <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} spin />} />
          <span>{msg.content}</span>
        </div>
      )
    }

    if (msg.type === 'card' && msg.data?.total_issues !== undefined) {
      return <JiraReportCard title={msg.content} data={msg.data} actions={msg.actions} />
    }

    if (msg.type === 'card' && msg.data?.verified_jiras !== undefined) {
      return <ClMergeReportCard title={msg.content} data={msg.data} actions={msg.actions} />
    }

    if (msg.type === 'card') {
      return (
        <Card size="small" title={msg.content} style={{ maxWidth: 680, borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}
          actions={msg.actions?.map((act, i) => (
            act.url ? (
              <Button key={i} type="link" size="small" href={act.url} target="_blank" icon={<LinkOutlined />}>
                {act.label}
              </Button>
            ) : (
              <Button key={i} type="link" size="small">{act.label}</Button>
            )
          ))}
        >
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
            {JSON.stringify(msg.data, null, 2)}
          </pre>
        </Card>
      )
    }

    if (msg.type === 'ask_user') {
      const opts = msg.actions || []
      return (
        <Card
          size="small"
          style={{ maxWidth: 680, borderLeft: '4px solid #faad14', borderRadius: 12, background: '#fffbe6', boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}
        >
          <Text strong style={{ fontSize: 14, color: '#ad6800' }}>❓ {msg.content}</Text>
          {opts.length > 0 && (
            <div style={{ marginTop: 12 }}>
              {opts.map((act, i) => (
                <Button
                  key={i}
                  type="primary"
                  size="small"
                  style={{ margin: '0 8px 8px 0', borderRadius: 16 }}
                  onClick={() => handleSend(act.value ?? act.label)}
                >
                  {act.label}
                </Button>
              ))}
            </div>
          )}
          <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>也可在下方直接输入回答后发送</div>
        </Card>
      )
    }

    if (msg.type === 'task_submitted') {
      const done = msg._task_done
      const dl = msg._file_download
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, color: done ? '#52c41a' : '#faad14', fontSize: 14 }}>
            {!done && <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} spin />} />}
            {done ? (
              // 任务结果是 markdown（如 CI 构建上传清单表格），完成态按 markdown 渲染
              <div style={{ color: 'inherit', flex: 1, minWidth: 0 }}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    table({ children, ...props }: any) {
                      return (
                        <div className="chat-table-wrap" style={{ overflowX: 'auto', margin: '8px 0', borderRadius: 8, border: '1px solid #e8e8e8' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px', lineHeight: 1.6 }} {...props}>{children}</table>
                        </div>
                      )
                    },
                    th({ children, ...props }: any) {
                      return (
                        <th style={{ background: '#f5f7fa', padding: '8px 12px', textAlign: 'left', fontWeight: 600, borderBottom: '2px solid #e8e8e8', whiteSpace: 'nowrap' }} {...props}>{children}</th>
                      )
                    },
                    td({ children, ...props }: any) {
                      return (
                        <td style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0', wordBreak: 'break-all' }} {...props}>{children}</td>
                      )
                    },
                  }}
                >
                  {msg.content}
                </ReactMarkdown>
              </div>
            ) : (
              <span>{msg.content}</span>
            )}
          </div>
          {msg.taskId && !done && (
            <div style={{ fontSize: 12, color: '#999', marginLeft: 22 }}>
              任务ID: {msg.taskId} · 后台执行中
            </div>
          )}
          {done && dl && (
            <div style={{ marginTop: 4 }}>
              <FileDownloadButton file={dl} />
            </div>
          )}
        </div>
      )
    }

    // Agent 产出的文件（技能产物 / 内置工具写出的文档）
    if (msg.type === 'file') {
      const file = msg.data as FileDownload | undefined
      if (!file?.download_url) {
        return <div>{msg.content}</div>
      }
      return <FileDeliveryCard file={file} />
    }

    if (msg.type === 'error') {
      return (
        <div>
          <Tag color="error" icon={<CloseCircleOutlined />}>错误</Tag>
          <span style={{ color: '#ff4d4f', marginLeft: 6 }}>{msg.content}</span>
        </div>
      )
    }

    return (
      <div>
        {msg.content && (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              table({ children, ...props }: any) {
                return (
                  <div className="chat-table-wrap" style={{ overflowX: 'auto', margin: '8px 0', borderRadius: 8, border: '1px solid #e8e8e8' }}>
                    <table style={{
                      width: '100%',
                      borderCollapse: 'collapse',
                      fontSize: '13px',
                      lineHeight: 1.6,
                    }} {...props}>{children}</table>
                  </div>
                )
              },
              th({ children, ...props }: any) {
                return (
                  <th style={{
                    background: '#f5f7fa',
                    padding: '8px 12px',
                    textAlign: 'left',
                    fontWeight: 600,
                    borderBottom: '2px solid #e8e8e8',
                    whiteSpace: 'nowrap',
                    fontSize: '12px',
                    color: '#555',
                    letterSpacing: '0.3px',
                  }} {...props}>{children}</th>
                )
              },
              td({ children, ...props }: any) {
                return (
                  <td style={{
                    padding: '8px 12px',
                    borderBottom: '1px solid #f0f0f0',
                    wordBreak: 'break-word',
                    fontSize: '13px',
                  }} {...props}>{children}</td>
                )
              },
              code({ node, inline, className, children, ...props }: any) {
                return inline ? (
                  <span style={{
                    fontFamily: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
                    fontSize: '90%',
                    background: '#f0f0f0',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    color: '#333',
                  }}>{children}</span>
                ) : (
                  <pre style={{
                    background: '#1e1e1e',
                    padding: 16,
                    borderRadius: 8,
                    overflow: 'auto',
                    fontSize: '13px',
                    fontFamily: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
                    lineHeight: 1.5,
                    border: '1px solid #2d2d2d',
                  }}>
                    <code style={{ color: '#d4d4d4', background: 'none' }} {...props}>{children}</code>
                  </pre>
                )
              },
            }}
          >
            {sanitizeMarkdownTables(msg.content)}
          </ReactMarkdown>
        )}
      </div>
    )
  }

  // Render a single message bubble with avatar and styling
  const renderMessage = (msg: Message) => (
    <div key={msg.id} style={{
      display: 'flex',
      justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
      alignItems: 'flex-start',
      gap: '12px',
    }}>
      {msg.role === 'assistant' && (
        <div style={{
          width: '32px',
          height: '32px',
          borderRadius: '50%',
          background: 'linear-gradient(135deg, #4A90E2 0%, #4A7DE4 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          marginTop: '4px',
          boxShadow: '0 2px 6px rgba(74, 144, 226, 0.2)',
        }}>
          <RobotOutlined style={{ color: '#fff', fontSize: '16px' }} />
        </div>
      )}
      <div style={{
        maxWidth: msg.type === 'card' ? '100%' : '90%',
        padding: msg.type === 'card' ? 0 : '12px 18px',
        borderRadius: msg.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
        background: msg.role === 'user'
          ? 'linear-gradient(135deg, #5B8DEF 0%, #4A90E2 100%)'
          : (msg.type === 'thinking' ? 'transparent' : '#f7f7f8'),
        color: msg.role === 'user' ? '#fff' : '#333',
        wordBreak: 'break-word',
        fontSize: '15px',
        lineHeight: '1.6',
        boxShadow: msg.role === 'user'
          ? '0 2px 8px rgba(91, 141, 239, 0.25)'
          : (msg.type === 'thinking' ? 'none' : '0 1px 3px rgba(0,0,0,0.04)'),
        border: msg.role === 'user' ? 'none' : (msg.type === 'thinking' ? 'none' : '1px solid rgba(229, 229, 229, 0.6)'),
      }}>
        {renderMessageContent(msg)}
        {msg.created_at && msg.type !== 'thinking' && (
          <div style={{
            fontSize: '11px',
            color: msg.role === 'user' ? 'rgba(255,255,255,0.6)' : '#aaa',
            marginTop: '4px',
            textAlign: msg.role === 'user' ? 'right' : 'left',
          }}>
            {formatMsgTime(msg.created_at)}
          </div>
        )}
      </div>
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 56px)' }}>
      {/* Messages Area */}
      <div ref={messagesContainerRef} style={{
        flex: 1,
        overflow: 'auto',
        display: 'flex',
        flexDirection: 'column',
        padding: '24px 0',
        background: 'linear-gradient(180deg, #fafafa 0%, #fff 20%)',
      }}>
        <div style={{ maxWidth: '960px', width: '100%', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '24px', padding: '0 24px' }}>
          {/* Table alternating row colors */}
          <style>{`
            .chat-table-wrap table tr:nth-child(even) td {
              background-color: #fafafc;
            }
            .chat-table-wrap table tr:hover td {
              background-color: #f0f4ff;
            }
            .chat-table-wrap table tr:last-child td {
              border-bottom: none;
            }
            /* Compact table mode for 4+ column tables */
            .chat-table-wrap table {
              table-layout: auto;
            }
            .chat-table-wrap table th,
            .chat-table-wrap table td {
              min-width: 40px;
            }
            /* Issue key column: keep narrow */
            .chat-table-wrap table td:first-child,
            .chat-table-wrap table th:first-child {
              white-space: nowrap;
              width: 1%;
            }
            /* Status column: keep narrow */
            .chat-table-wrap table td:nth-child(3),
            .chat-table-wrap table th:nth-child(3) {
              white-space: nowrap;
              width: 1%;
            }
          `}</style>
          {/* Messages + Reasoning chain inserted between last user message and its response */}
          {(() => {
            // Find the last user message — ReasoningChain should anchor here
            let lastUserIdx = -1
            for (let i = messages.length - 1; i >= 0; i--) {
              if (messages[i].role === 'user') {
                lastUserIdx = i
                break
              }
            }
            // Search for a non-thinking assistant message AFTER the last user
            let insertIdx = messages.length
            const searchFrom = lastUserIdx + 1
            for (let i = messages.length - 1; i >= searchFrom; i--) {
              if (messages[i].role === 'assistant' && messages[i].type !== 'thinking') {
                insertIdx = i
                break
              }
            }
            // Fallback: place right after the user message
            if (insertIdx === messages.length && lastUserIdx >= 0) {
              insertIdx = lastUserIdx + 1
            }
            return (
              <>
                {messages.slice(0, insertIdx).map(renderMessage)}
                {showReasoning && reasoningSteps.length > 0 && (
                  <div style={{
                    display: 'flex',
                    justifyContent: 'flex-start',
                    alignItems: 'flex-start',
                    gap: '12px',
                  }}>
                    <div style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '50%',
                      background: 'linear-gradient(135deg, #4A90E2 0%, #4A7DE4 100%)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                      marginTop: '4px',
                      boxShadow: '0 2px 6px rgba(74, 144, 226, 0.2)',
                    }}>
                      <RobotOutlined style={{ color: '#fff', fontSize: '16px' }} />
                    </div>
                    <div style={{
                      maxWidth: '90%',
                      padding: 0,
                      borderRadius: '18px 18px 18px 4px',
                      background: 'transparent',
                      color: '#333',
                    }}>
                      <ReasoningChain steps={reasoningSteps} />
                    </div>
                  </div>
                )}
                {messages.slice(insertIdx).map(renderMessage)}
              </>
            )
          })()}
          <div />
        </div>
      </div>

      {/* Input Area */}
      <div style={{
        padding: '16px 24px 24px',
        background: '#fff',
        display: 'flex',
        justifyContent: 'center',
        flexDirection: 'column',
        alignItems: 'center',
      }}>
        {/* File preview chip */}
        {attachedFile && (
          <div style={{
            maxWidth: '960px',
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 12px',
            marginBottom: 8,
            background: '#f0f5ff',
            borderRadius: '10px',
            border: '1px solid #d6e4ff',
            fontSize: 13,
          }}>
            <PaperClipOutlined style={{ color: '#4A90E2' }} />
            <span style={{ flex: 1, color: '#333', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {attachedFile.fileName}
            </span>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {(attachedFile.fileSize / 1024).toFixed(0)} KB
            </Text>
            <Button type="text" size="small" icon={<CloseCircleOutlined />} onClick={handleRemoveFile} style={{ color: '#999' }} />
          </div>
        )}

        {/* Skills Panel - shown when /skills command is active */}
        {showSkills && availableSkills.length > 0 && (
          <div style={{
            maxWidth: '960px',
            width: '100%',
            marginBottom: 12,
            padding: '12px 16px',
            background: '#fafbfc',
            borderRadius: '14px',
            border: '1px solid #e8e8e8',
          }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 10 }}>
              可用 Skills — 点击即可执行
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {availableSkills.map((skill) => (
                <div
                  key={skill.name}
                  onClick={() => handleSkillClick(skill)}
                  style={{
                    padding: '8px 14px',
                    background: '#fff',
                    borderRadius: '10px',
                    border: '1px solid #e0e0e0',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 2,
                    maxWidth: 220,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = '#4A90E2'
                    e.currentTarget.style.boxShadow = '0 2px 8px rgba(74,144,226,0.12)'
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = '#e0e0e0'
                    e.currentTarget.style.boxShadow = 'none'
                  }}
                >
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#4A90E2' }}>
                    {skill.display_name || skill.name}
                  </span>
                  <span style={{ fontSize: 11, color: '#999', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 180 }}>
                    {skill.description || '暂无描述'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{
          maxWidth: '768px',
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          background: '#f7f7f8',
          borderRadius: '16px',
          border: '1px solid',
          borderColor: isInputFocused ? '#4A90E2' : '#e5e5e5',
          transition: 'border-color 0.2s ease',
          overflow: 'hidden',
        }}>
          {/* Hidden file input */}
          <input
            type="file"
            ref={fileInputRef}
            style={{ display: 'none' }}
            accept=".doc,.docx,.pdf,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.gif,.bmp,.tiff,.tif,.webp,.csv,.json,.md,.txt,.log,.html,.htm,.eml"
            onChange={handleFileSelect}
          />
          {/* Main input row */}
          <div style={{
            display: 'flex',
            alignItems: 'flex-end',
            gap: '8px',
            padding: '8px 12px 0',
          }}>
            <TextArea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息..."
              autoSize={{ minRows: 1, maxRows: 6 }}
              style={{
                flex: 1,
                background: 'transparent',
                border: 'none',
                boxShadow: 'none',
                resize: 'none',
                fontSize: '15px',
                padding: '8px 0',
                color: '#333',
              }}
              disabled={isLoading}
              onFocus={() => setIsInputFocused(true)}
              onBlur={() => setIsInputFocused(false)}
            />
            <Button
              type="primary"
              shape="circle"
              icon={<SendOutlined />}
              size="middle"
              onClick={() => handleSend()}
              loading={isLoading}
              disabled={!inputValue.trim() && !attachedFile}
              style={{
                flexShrink: 0,
                marginBottom: 8,
                background: (inputValue.trim() || attachedFile) ? '#4A90E2' : '#d9d9d9',
                borderColor: (inputValue.trim() || attachedFile) ? '#4A90E2' : '#d9d9d9',
              }}
            />
          </div>
          {/* Bottom bar: file upload + thinking toggle */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 8px 6px',
          }}>
            <Tooltip title={attachedFile ? "替换附件" : "上传附件"}>
              <Button
                type="text"
                icon={isUploading ? <LoadingOutlined /> : <PaperClipOutlined />}
                size="small"
                style={{
                  color: attachedFile ? '#4A90E2' : '#bbb',
                  flexShrink: 0,
                  width: 24,
                  height: 24,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
              />
            </Tooltip>
            <Tooltip title={showReasoning ? "关闭推理过程显示" : "开启推理过程显示"}>
              <div
                onClick={() => {
                  if (showReasoning) setReasoningSteps([])
                  setShowReasoning(!showReasoning)
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  cursor: 'pointer',
                  padding: '2px 8px',
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 500,
                  color: showReasoning ? '#4A90E2' : '#bbb',
                  background: showReasoning ? '#eef4ff' : 'transparent',
                  border: showReasoning ? '1px solid #cddaff' : '1px solid transparent',
                  userSelect: 'none',
                  transition: 'all 0.2s ease',
                  lineHeight: '20px',
                }}
              >
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" style={{ flexShrink: 0 }}>
                  <path d="M8 1C4.686 1 2 3.686 2 7v2.5a1 1 0 001 1h.5l.5 3.5a1 1 0 001 1h2a1 1 0 001-1v-1h-1.5v1h-2l-.5-3.5H3V7c0-2.76 2.24-5 5-5s5 2.24 5 5v.5h1.5V7c0-3.314-2.686-6-6-6z" fill={showReasoning ? '#4A90E2' : '#bbb'} />
                  <path d="M11.5 10.5a1 1 0 011 1v.5a1 1 0 01-1 1h-1a1 1 0 01-1-1v-.5a1 1 0 011-1h1z" fill={showReasoning ? '#4A90E2' : '#bbb'} />
                  <path d="M12 12.5v1a1 1 0 01-1 1h-.5a1 1 0 01-1-1v-1a1 1 0 011-1h.5a1 1 0 011 1z" fill={showReasoning ? '#4A90E2' : '#bbb'} />
                </svg>
                <span>思考模式</span>
              </div>
            </Tooltip>
          </div>
        </div>
      </div>
    </div>
  )
}

export default ChatPage
