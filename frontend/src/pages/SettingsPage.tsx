import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  Tabs, Form, Input, Button, Card, message as antMsg, Spin,
  Typography, Descriptions, Tag, Radio, Progress, Alert, Switch,
} from 'antd'
import {
  SaveOutlined, ApiOutlined, RobotOutlined, CloudServerOutlined,
  LinkOutlined, StopOutlined, CheckCircleOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { apiFetch } from '../services/chatService'

const { Title, Text } = Typography

const SettingsPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState('llm')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [llmConfig, setLlmConfig] = useState<any>(null)
  const [mcpConfig, setMcpConfig] = useState<any>(null)
  const [mcpJsonText, setMcpJsonText] = useState('')
  const [llmVerify, setLlmVerify] = useState<Record<string, {verified: boolean, message?: string}>>({})
  const [mcpStatus, setMcpStatus] = useState<Record<string, {status: string, tools?: number, message?: string}>>({})
  const [defaultLlm, setDefaultLlm] = useState('commercial')
  const [llmCommercialForm] = Form.useForm()
  const [llmLocalForm] = Form.useForm()

  const fetchConfigs = async () => {
    setLoading(true)
    try {
      const [llmData, , mcpData] = await Promise.all([
        apiFetch<any>('/api/config/llm'),
        apiFetch<any>('/api/config/project'),
        apiFetch<any>('/api/config/mcp'),
      ])
      setLlmConfig(llmData)
      setDefaultLlm(llmData?.default_llm || 'commercial')
      if (llmData?._verify) {
        setLlmVerify(llmData._verify)
        delete llmData._verify
      }
      setMcpConfig(mcpData)
      if (mcpData?._server_status) {
        setMcpStatus(mcpData._server_status)
        delete mcpData._server_status
      }
      // If MCP is empty, show default demo template
      const mcpKeys = Object.keys(mcpData || {})
      if (mcpKeys.length === 0 || (mcpKeys.length === 1 && mcpKeys[0] === 'mcpServers' && Object.keys(mcpData.mcpServers || {}).length === 0)) {
        setMcpJsonText(JSON.stringify({
          mcpServers: {
            "gerrit_gerrit2": {
              disabled: false,
              timeout: 60,
              type: "streamableHttp",
              url: "http://10.0.0.1:8118/mcp",
              headers: {
                Authorization: '{"server_url": "https://gerrit-1.example.com", "username": "${username}", "password": "${password}"}'
              },
              remoteConfigured: true
            }
          }
        }, null, 2))
      } else {
        setMcpJsonText(JSON.stringify(mcpData, null, 2))
      }
      llmCommercialForm.setFieldsValue({
        commercial_api_base: llmData.commercial?.api_base || '',
        commercial_model: llmData.commercial?.model || '',
        commercial_provider: llmData.commercial?.provider || '',
        commercial_api_key: llmData.commercial?.api_key || '',
      })
      llmLocalForm.setFieldsValue({
        local_api_base: llmData.local?.api_base || '',
        local_model: llmData.local?.model || '',
        local_api_key: llmData.local?.api_key || '',
      })
    } catch (e) {
      console.error('加载配置失败:', e)
      antMsg.error('加载配置失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchConfigs()
  }, [])

  const handleDefaultLlmChange = async (value: string) => {
    setDefaultLlm(value)
    // Auto-save default_llm immediately so it persists on refresh
    try {
      await apiFetch('/api/config/llm', {
        method: 'POST',
        body: JSON.stringify({ commercial: {}, local: {}, default_llm: value }),
      })
      antMsg.success(`已切换到${value === 'commercial' ? '商用' : '本地'}大模型`)
    } catch (e) {
      console.error('切换默认模型失败:', e)
      antMsg.error('切换默认模型失败')
    }
  }

  const handleSaveCommercialLlm = async (values: any) => {
    setSaving(true)
    try {
      const payload: any = { commercial: {}, local: {} }
      if (values.commercial_api_base) payload.commercial.api_base = values.commercial_api_base
      if (values.commercial_model) payload.commercial.model = values.commercial_model
      if (values.commercial_provider) payload.commercial.provider = values.commercial_provider
      if (values.commercial_api_key && values.commercial_api_key !== '***') {
        payload.commercial.api_key = values.commercial_api_key
      }

      payload.default_llm = defaultLlm
      const resp = await apiFetch('/api/config/llm', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setLlmVerify({})
      if (resp._verify?.commercial) {
        setLlmVerify({commercial: resp._verify.commercial})
      }
      antMsg.success('商用大模型配置已保存')
      fetchConfigs()
    } catch (e) {
      console.error('保存商用LLM配置失败:', e)
      antMsg.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleSaveLocalLlm = async (values: any) => {
    setSaving(true)
    try {
      const payload: any = { commercial: {}, local: {} }
      if (values.local_api_base) payload.local.api_base = values.local_api_base
      if (values.local_model) payload.local.model = values.local_model
      if (values.local_api_key && values.local_api_key !== '***') {
        payload.local.api_key = values.local_api_key
      }
      payload.default_llm = defaultLlm
      const resp2 = await apiFetch('/api/config/llm', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setLlmVerify({})
      if (resp2._verify?.local) {
        setLlmVerify({local: resp2._verify.local})
      }
      antMsg.success('本地大模型配置已保存')
      fetchConfigs()
    } catch (e) {
      console.error('保存本地LLM配置失败:', e)
      antMsg.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleSaveMcpJson = async () => {
    setSaving(true)
    try {
      // Validate JSON
      const parsed = JSON.parse(mcpJsonText)
      const mcpResp = await apiFetch('/api/config/mcp', {
        method: 'POST',
        body: JSON.stringify(parsed),
      })
      if (mcpResp._server_status) {
        setMcpStatus(mcpResp._server_status)
      }
      antMsg.success('MCP 配置已保存')
      fetchConfigs()
    } catch (e: any) {
      if (e instanceof SyntaxError) {
        antMsg.error('JSON 格式错误: ' + e.message)
      } else {
        antMsg.error('保存失败: ' + (e.message || e))
      }
    } finally {
      setSaving(false)
    }
  }

  if (loading && !llmConfig) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  const handleMcpJsonChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setMcpJsonText(e.target.value)
  }

  const tabItems = [
    {
      key: 'llm',
      label: <span><RobotOutlined /> 大模型配置</span>,
      children: (
        <div style={{ maxWidth: 700 }}>
          <Card
            title={<span style={{ fontSize: 15, fontWeight: 600 }}>默认模型选择</span>}
            size="small"
            style={{ marginBottom: 20, background: '#fafafa' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ color: '#555', fontSize: 13 }}>
                选择 Agent 默认使用的模型，未配置时自动回退到另一个
              </span>
              <Radio.Group
                value={defaultLlm}
                onChange={(e) => handleDefaultLlmChange(e.target.value)}
                optionType="button"
                buttonStyle="solid"
              >
                <Radio.Button value="commercial">商用大模型</Radio.Button>
                <Radio.Button value="local">本地大模型</Radio.Button>
              </Radio.Group>
            </div>
          </Card>

          <Card title="商用大模型配置" size="small" style={{ marginBottom: 24 }}>
            <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="状态">
                {llmConfig?.commercial?.configured ? (
                  <Tag color="green">已配置</Tag>
                ) : (
                  <Tag color="red">未配置</Tag>
                )}
                {llmVerify.commercial && (
                  llmVerify.commercial.verified ? (
                    <Tag color="blue" style={{ marginLeft: 4 }}>已验证</Tag>
                  ) : (
                    <Tag color="red" style={{ marginLeft: 4 }} title={llmVerify.commercial.message}>链接失败</Tag>
                  )
                )}
              </Descriptions.Item>
            </Descriptions>
            <Form form={llmCommercialForm} layout="vertical" onFinish={handleSaveCommercialLlm}>
              <Form.Item label="Provider" name="commercial_provider">
                <Input placeholder="deepseek / openai / custom" />
              </Form.Item>
              <Form.Item label="API Base URL" name="commercial_api_base">
                <Input placeholder="https://api.deepseek.com" />
              </Form.Item>
              <Form.Item label="Model" name="commercial_model">
                <Input placeholder="deepseek-chat / gpt-4o" />
              </Form.Item>
              <Form.Item label="API Key" name="commercial_api_key">
                <Input.Password placeholder="sk-..." visibilityToggle={false} />
              </Form.Item>
              <Form.Item>
                <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
                  保存商用大模型配置
                </Button>
              </Form.Item>
            </Form>
          </Card>

          <Card title="本地大模型配置" size="small" style={{ marginBottom: 24 }}>
            <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="状态">
                {llmConfig?.local?.configured ? (
                  <Tag color="green">已配置</Tag>
                ) : (
                  <Tag color="orange">未配置</Tag>
                )}
                {llmVerify.local && (
                  llmVerify.local.verified ? (
                    <Tag color="blue" style={{ marginLeft: 4 }}>已验证</Tag>
                  ) : (
                    <Tag color="red" style={{ marginLeft: 4 }} title={llmVerify.local.message}>链接失败</Tag>
                  )
                )}
              </Descriptions.Item>
            </Descriptions>
            <Form form={llmLocalForm} layout="vertical" onFinish={handleSaveLocalLlm}>
              <Form.Item label="API Base URL" name="local_api_base">
                <Input placeholder="http://localhost:<backend-port>/v1" />
              </Form.Item>
              <Form.Item label="Model" name="local_model">
                <Input placeholder="qwen2.5 / deepseek-v3" />
              </Form.Item>
              <Form.Item label="API Key" name="local_api_key">
                <Input.Password placeholder="API key (optional)" visibilityToggle={false} />
              </Form.Item>
              <Form.Item>
                <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
                  保存本地大模型配置
                </Button>
              </Form.Item>
            </Form>
          </Card>
        </div>
      ),
    },
    {
      key: 'mcp',
      label: <span><CloudServerOutlined /> MCP服务器</span>,
      children: (
        <div style={{ maxWidth: 1100 }}>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
            标准 MCP 格式（支持任意名称和数量的服务器），
            密码填 <code>***</code> 保留现有值不变。
            <br />示例：将服务器配置包裹在 <code>mcpServers</code> 键下，
            headers.Authorization 中存放认证 JSON 字符串。
          </Text>
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
            <div style={{ flex: 2, minWidth: 0 }}>
              <textarea
                value={mcpJsonText}
                onChange={handleMcpJsonChange}
                style={{
                  width: '100%',
                  minHeight: 400,
                  fontFamily: 'monospace',
                  fontSize: 13,
                  padding: 12,
                  border: '1px solid #d9d9d9',
                  borderRadius: 6,
                  resize: 'vertical',
                  lineHeight: 1.6,
                }}
                spellCheck={false}
              />
              <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
                <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSaveMcpJson}>
                  保存 JSON 配置
                </Button>
                <Button onClick={() => setMcpJsonText(JSON.stringify(mcpConfig, null, 2))}>
                  重置
                </Button>
                <Button onClick={() => setMcpJsonText(JSON.stringify({
                  mcpServers: {
                    "gerrit_gerrit2": {
                      disabled: false,
                      timeout: 60,
                      type: "streamableHttp",
                      url: "http://10.0.0.1:8118/mcp",
                      headers: {
                        Authorization: '{"server_url": "https://gerrit-1.example.com", "username": "${username}", "password": "${password}"}'
                      },
                      remoteConfigured: true
                    }
                  }
                }, null, 2))}>
                  加载示例
                </Button>
              </div>
            </div>
            <div style={{ width: 360, minHeight: 380, marginLeft: 16, paddingLeft: 16, borderLeft: '1px solid #f0f0f0' }}>
              <div style={{ fontWeight: 500, marginBottom: 12, fontSize: 14, color: '#333' }}>服务器连接状态</div>
              {saving ? (
                (() => {
                  try {
                    const parsed = JSON.parse(mcpJsonText)
                    const servers = parsed.mcpServers || parsed
                    return Object.keys(servers).filter(k => typeof servers[k] === 'object' && servers[k]?.url).map(name => (
                      <div key={name} style={{ marginBottom: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                          <span style={{ fontSize: 13, fontWeight: 500 }}>{name}</span>
                          {mcpStatus[name] ? (
                            mcpStatus[name].status === 'ok' ? (
                              <Tag color="green" style={{ margin: 0 }}>可达 ({mcpStatus[name].tools} 工具)</Tag>
                            ) : (
                              <Tag color="red" style={{ margin: 0 }} title={mcpStatus[name].message}>不可达</Tag>
                            )
                          ) : (
                            <Tag color="processing" style={{ margin: 0 }}>验证中...</Tag>
                          )}
                        </div>
                        <Progress
                          percent={mcpStatus[name] ? 100 : 0}
                          status={mcpStatus[name]?.status === 'ok' ? 'success' : mcpStatus[name]?.status === 'error' ? 'exception' : 'active'}
                          strokeColor={mcpStatus[name]?.status === 'ok' ? '#52c41a' : mcpStatus[name]?.status === 'error' ? '#ff4d4f' : '#1890ff'}
                          showInfo={false}
                          size="small"
                        />
                      </div>
                    ))
                  } catch { return <div style={{ fontSize: 13, color: '#999' }}>解析服务器列表...</div> }
                })()
              ) : Object.keys(mcpStatus).length > 0 ? (
                Object.entries(mcpStatus).map(([name, st]) => (
                  <div key={name} style={{ marginBottom: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{name}</span>
                      {st.status === 'ok' ? (
                        <Tag color="green" style={{ margin: 0 }}>可达 ({st.tools} 工具)</Tag>
                      ) : (
                        <Tag color="red" style={{ margin: 0 }} title={st.message}>不可达</Tag>
                      )}
                    </div>
                    <Progress
                      percent={100}
                      status={st.status === 'ok' ? 'success' : 'exception'}
                      strokeColor={st.status === 'ok' ? '#52c41a' : '#ff4d4f'}
                      showInfo={false}
                      size="small"
                    />
                  </div>
                ))
              ) : (
                <div style={{ fontSize: 13, color: '#999' }}>保存 MCP 配置后将在此显示连接状态</div>
              )}
            </div>
            </div>
          </div>
      ),
    },
    {
      key: 'ci_build',
      label: <span><CloudServerOutlined /> CI 构建</span>,
      children: <CiBuildSection />,
    },
    // ── 飞书绑定 Tab ──
    {
      key: 'feishu_bind',
      label: <span><RobotOutlined /> 飞书绑定</span>,
      children: <FeishuBindSection />,
    },
  ]

  return (
    <div style={{ padding: '24px', height: '100%', overflowY: 'auto' }}>
      <Title level={5} style={{ marginBottom: 24 }}>
        <ApiOutlined /> 系统设置
      </Title>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
    </div>
  )
}

// ── CI 构建子组件（Google Android Build 查询/下载/FTPS 上传配置）──

const CiBuildSection: React.FC = () => {
  const [ciBuildForm] = Form.useForm()
  const [ciSaving, setCiSaving] = useState(false)
  const [ciLoaded, setCiLoaded] = useState(false)

  const loadCiBuild = async () => {
    try {
      const data = await apiFetch<any>('/api/config/ci_build')
      ciBuildForm.setFieldsValue({
        branch: data?.branch || '',
        target: data?.target || '',
        ota_flavor: data?.ota_flavor || '',
        default_build_type: data?.default_build_type || 'userdebug',
        auth_enabled: data?.auth?.enabled !== false,
        client_id: data?.auth?.client_id || '',
        client_secret: data?.auth?.client_secret || '',
        project_id: data?.auth?.project_id || '',
        ftp_host: data?.ftp?.host || '',
        ftp_port: data?.ftp?.port || 21,
        ftp_username: data?.ftp?.username || '',
        ftp_password: data?.ftp?.password || '',
        ftp_remote_dir: data?.ftp?.remote_dir || '/',
      })
      setCiLoaded(true)
    } catch (e) {
      console.error('加载 CI 构建配置失败:', e)
      antMsg.error('加载 CI 构建配置失败')
    }
  }

  useEffect(() => {
    loadCiBuild()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSaveCiBuild = async (values: any) => {
    setCiSaving(true)
    try {
      const payload: any = {
        branch: values.branch || '',
        target: values.target || '',
        ota_flavor: values.ota_flavor || '',
        default_build_type: values.default_build_type || 'userdebug',
        auth: {
          enabled: values.auth_enabled !== false,
          client_id: values.client_id || '',
          // "***" 表示未修改，服务端保留旧值
          client_secret: values.client_secret || '',
          project_id: values.project_id || '',
        },
        ftp: {
          host: values.ftp_host || '',
          port: values.ftp_port || 21,
          username: values.ftp_username || '',
          password: values.ftp_password || '',
          remote_dir: values.ftp_remote_dir || '/',
        },
      }
      await apiFetch('/api/config/ci_build', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      antMsg.success('CI 构建配置已保存')
      loadCiBuild()
    } catch (e) {
      console.error('保存 CI 构建配置失败:', e)
      antMsg.error('保存失败')
    } finally {
      setCiSaving(false)
    }
  }

  return (
    <div style={{ maxWidth: 700 }}>
      {!ciLoaded && <div style={{ color: '#999', marginBottom: 16 }}><Spin size="small" /> 加载配置...</div>}
      {/* 单一 Form 包裹全部卡片（antd 一个 form 实例只能挂在一个 Form 上） */}
      <Form form={ciBuildForm} layout="vertical" onFinish={handleSaveCiBuild}>
        <Card title="关注分支" size="small" style={{ marginBottom: 24 }}>
          <Form.Item
            label="分支 (branch)"
            name="branch"
            extra="Google CI 分支名，如 your-android-release-branch"
          >
            <Input placeholder="留空表示只按 target 过滤" />
          </Form.Item>
          <Form.Item
            label="目标 (target)"
            name="target"
            extra="target 基础名，如 CHIPX1（不要带 -user/-userdebug 后缀）"
            rules={[{ required: true, message: '请填写 target 基础名' }]}
          >
            <Input placeholder="CHIPX1" />
          </Form.Item>
          <Form.Item label="OTA Flavor 前缀" name="ota_flavor" extra="user 构建 signed OTA 命名用，如 URT2">
            <Input placeholder="URT2" />
          </Form.Item>
          <Form.Item
            label="默认构建类型"
            name="default_build_type"
            extra="ALL = 同时下载 user + userdebug 两种版型的产物"
          >
            <Radio.Group optionType="button" buttonStyle="solid">
              <Radio.Button value="userdebug">userdebug</Radio.Button>
              <Radio.Button value="user">user</Radio.Button>
              <Radio.Button value="all">ALL</Radio.Button>
            </Radio.Group>
          </Form.Item>
        </Card>

        <Card title="Google OAuth 授权（私有分支必填）" size="small" style={{ marginBottom: 24 }}>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="首次使用需在服务器上运行一次性授权脚本（token 之后自动续期，无需重复授权）："
            description={<code>cd backend && source venv/bin/activate && python scripts/ci_build_auth.py</code>}
          />
          <Form.Item name="auth_enabled" label="分支类型" valuePropName="checked">
            <Switch checkedChildren="私有分支（需要 OAuth）" unCheckedChildren="公共分支（无需授权）" />
          </Form.Item>
          <Form.Item label="Client ID" name="client_id">
            <Input placeholder="Google Cloud Console OAuth 2.0 桌面应用 Client ID" />
          </Form.Item>
          <Form.Item label="Client Secret" name="client_secret">
            <Input.Password placeholder="留空或 *** 表示不修改" visibilityToggle={false} />
          </Form.Item>
          <Form.Item label="Project ID（可选，配额项目）" name="project_id">
            <Input placeholder="Google Cloud Project ID" />
          </Form.Item>
        </Card>

        <Card title="FTPS 上传目标" size="small" style={{ marginBottom: 24 }}>
          <Form.Item label="FTP Host" name="ftp_host" rules={[{ required: true, message: '请填写 FTP 服务器地址' }]}>
            <Input placeholder="ftp.example.com" />
          </Form.Item>
          <Form.Item label="端口" name="ftp_port">
            <Input type="number" placeholder="21" />
          </Form.Item>
          <Form.Item label="用户名" name="ftp_username" rules={[{ required: true, message: '请填写 FTP 用户名' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="密码" name="ftp_password">
            <Input.Password placeholder="留空或 *** 表示不修改" visibilityToggle={false} />
          </Form.Item>
          <Form.Item
            label="远程根目录"
            name="ftp_remote_dir"
            extra="构建产物上传到 {远程根目录}/{日期}-{build_id}/{user|userdebug}/ 下"
          >
            <Input placeholder="/" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={ciSaving}>
              保存 CI 构建配置
            </Button>
          </Form.Item>
        </Card>
      </Form>
    </div>
  )
}

// ── 飞书绑定子组件 ──

const FeishuBindSection: React.FC = () => {
  const [status, setStatus] = useState<'idle' | 'loading' | 'show_code' | 'verifying' | 'bound'>('idle')
  const [bindCode, setBindCode] = useState('')
  const [expiresIn, setExpiresIn] = useState(0)
  const [feishuId, setFeishuId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 清理轮询
  const clearPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  useEffect(() => {
    return clearPoll
  }, [clearPoll])

  // 检查绑定状态
  const checkStatus = useCallback(async (silent = false) => {
    try {
      const res = await apiFetch<any>('/api/feishu/bind/status', { method: 'POST' })
      if (res?.bound) {
        setStatus('bound')
        setFeishuId(res.feishu_open_id || null)
        clearPoll()
        return true
      }
      return false
    } catch {
      if (!silent) setError('查询绑定状态失败')
      return false
    }
  }, [clearPoll])

  // 加载时自动检查状态
  useEffect(() => {
    checkStatus(true)
  }, [checkStatus])

  // 发起绑定
  const handleBind = async () => {
    setError('')
    setStatus('loading')
    try {
      const res = await apiFetch<any>('/api/feishu/bind/initiate', { method: 'POST' })
      if (res?.code) {
        setBindCode(res.code)
        setExpiresIn(res.expires_in || 300)
        setStatus('show_code')
        // 开始轮询等待验证（每3秒检查一次）
        pollRef.current = setInterval(async () => {
          const bound = await checkStatus(true)
          if (bound) {
            setStatus('bound')
            antMsg.success('飞书账号绑定成功！')
          }
        }, 3000)
      } else {
        setError(res?.detail || '生成验证码失败')
        setStatus('idle')
      }
    } catch (e: any) {
      const detail = e?.detail || (typeof e === 'object' ? JSON.stringify(e) : String(e))
      if (detail.includes('已经绑定')) {
        setStatus('bound')
        checkStatus(true)
      } else {
        setError(detail || '发起绑定失败')
        setStatus('idle')
      }
    }
  }

  // 解绑
  const handleUnbind = async () => {
    setError('')
    try {
      await apiFetch<any>('/api/feishu/bind/unbind', { method: 'POST' })
      setStatus('idle')
      setBindCode('')
      setFeishuId(null)
      antMsg.success('已解绑飞书账号')
    } catch (e: any) {
      setError(e?.detail || '解绑失败')
    }
  }

  // 重新获取验证码
  const handleRetry = () => {
    clearPoll()
    setError('')
    setStatus('idle')
    setTimeout(() => handleBind(), 100)
  }

  // 剩余时间倒计时
  const expiresMinutes = Math.floor(expiresIn / 60)

  return (
    <div style={{ maxWidth: 550 }}>
      <Card style={{ background: '#fafafa' }}>
        {/* 已绑定状态 */}
        {status === 'bound' && (
          <div style={{ textAlign: 'center', padding: '16px 0' }}>
            <CheckCircleOutlined style={{ fontSize: 48, color: '#52c41a', marginBottom: 12 }} />
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, color: '#333' }}>
              已绑定飞书账号
            </div>
            <div style={{ fontSize: 13, color: '#888', marginBottom: 16 }}>
              {feishuId
                ? `账号：${feishuId.substring(0, 8)}****${feishuId.slice(-4)}`
                : '飞书账号已关联'}
            </div>
            <div style={{ fontSize: 12, color: '#aaa', marginBottom: 20 }}>
              你现在可以通过飞书机器人使用 AI 助手，数据与Web端同步
            </div>
            <Button danger onClick={handleUnbind} icon={<StopOutlined />}>
              解绑飞书账号
            </Button>
          </div>
        )}

        {/* 显示验证码状态 */}
        {(status === 'show_code' || status === 'verifying') && (
          <div style={{ textAlign: 'center', padding: '8px 0' }}>
            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 12, color: '#333' }}>
              请将以下验证码发送给飞书机器人
            </div>
            <div style={{
              fontSize: 36, fontWeight: 700, letterSpacing: 8,
              color: '#4A90E2', fontFamily: 'monospace',
              padding: '20px 0', margin: '8px auto',
              background: '#fff', borderRadius: 8,
              border: '2px dashed #d9d9d9',
              maxWidth: 300,
            }}>
              {bindCode}
            </div>
            <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
              验证码 {expiresMinutes} 分钟内有效 · 发送后等待系统确认
            </div>
            <div style={{ marginTop: 16 }}>
              <Spin size="small" style={{ marginRight: 8 }} />
              <span style={{ fontSize: 13, color: '#888' }}>等待验证中...</span>
            </div>
            <div style={{ marginTop: 16 }}>
              <Button size="small" onClick={handleRetry}>
                <ReloadOutlined /> 重新获取验证码
              </Button>
            </div>
          </div>
        )}

        {/* 未绑定状态 */}
        {(status === 'idle' || status === 'loading') && (
          <div style={{ textAlign: 'center', padding: '16px 0' }}>
            <RobotOutlined style={{ fontSize: 48, color: '#d9d9d9', marginBottom: 12 }} />
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, color: '#333' }}>
              飞书账号绑定
            </div>
            <div style={{ fontSize: 13, color: '#888', marginBottom: 20, lineHeight: 1.8 }}>
              绑定后，你可以通过飞书机器人使用 AI 助手，数据与 Web 端同步。
              <br />
              请先在飞书搜索并添加「Project Agent」机器人。
            </div>
            <Button
              type="primary"
              size="large"
              loading={status === 'loading'}
              onClick={handleBind}
              icon={<LinkOutlined />}
            >
              绑定飞书账号
            </Button>
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <Alert
            type="error"
            message={error}
            closable
            onClose={() => setError('')}
            style={{ marginTop: 12 }}
            showIcon
          />
        )}
      </Card>

      {/* 说明信息 */}
      <Card size="small" style={{ marginTop: 16, background: '#fff' }}>
        <div style={{ fontSize: 13, color: '#666', lineHeight: 1.8 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>绑定说明：</div>
          <div>1. 点击「绑定飞书账号」生成验证码</div>
          <div>2. 打开飞书，找到 Project Agent 机器人</div>
          <div>3. 将验证码数字发送给机器人</div>
          <div>4. 系统自动确认绑定，页面同步更新</div>
        </div>
      </Card>
    </div>
  )
}

export default SettingsPage
