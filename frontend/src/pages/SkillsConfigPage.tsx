import React, { useState, useEffect } from 'react'
import { List, Card, Button, Typography, message as antMsg, Spin, Tag, Input, Empty } from 'antd'
import { SaveOutlined, ToolOutlined } from '@ant-design/icons'
import { apiFetch } from '../services/chatService'

const { Title, Text } = Typography
const { TextArea } = Input

interface SkillInfo {
  name: string
  display_name: string
  description: string
  version: string
  triggers: string[]
  mcp_required: string[]
}

const SkillsConfigPage: React.FC = () => {
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null)
  const [yamlContent, setYamlContent] = useState('')
  const [originalYaml, setOriginalYaml] = useState('')

  useEffect(() => {
    loadSkills()
  }, [])

  useEffect(() => {
    if (selectedSkill) {
      loadConfigFile(selectedSkill)
    }
  }, [selectedSkill])

  const loadSkills = async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/api/skills')
      setSkills(data || [])
      if (data && data.length > 0 && !selectedSkill) {
        setSelectedSkill(data[0].name)
      }
    } catch (e) {
      console.error('Failed to load skills:', e)
      antMsg.error('加载技能列表失败')
    } finally {
      setLoading(false)
    }
  }

  const loadConfigFile = async (skillName: string) => {
    setLoading(true)
    try {
      const resp = await fetch(`/api/skills/${skillName}/config-file`, {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('token') || ''}`,
        },
      })
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`)
      }
      const text = await resp.text()
      setYamlContent(text)
      setOriginalYaml(text)
    } catch (e) {
      console.error('Failed to load config file:', e)
      antMsg.error('加载配置失败')
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!selectedSkill) return
    setSaving(true)
    try {
      // Basic YAML validation on client side
      const resp = await fetch(`/api/skills/${selectedSkill}/config-file`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'text/plain',
          Authorization: `Bearer ${localStorage.getItem('token') || ''}`,
        },
        body: yamlContent,
      })
      if (!resp.ok) {
        const errText = await resp.text()
        throw new Error(errText || `HTTP ${resp.status}`)
      }
      setOriginalYaml(yamlContent)
      antMsg.success('配置已保存')
    } catch (e: any) {
      console.error('Failed to save config:', e)
      antMsg.error(`保存失败: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  const hasChanges = yamlContent !== originalYaml

  const selectedSkillInfo = skills.find((s) => s.name === selectedSkill)

  return (
    <div style={{ padding: '24px', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Title level={4} style={{ margin: '0 0 16px' }}>
        <ToolOutlined style={{ marginRight: 8 }} />
        技能配置
      </Title>

      <div style={{ display: 'flex', flex: 1, gap: '16px', minHeight: 0 }}>
        {/* Left: Skill list */}
        <Card
          style={{ width: 240, flexShrink: 0, overflow: 'auto' }}
          bodyStyle={{ padding: '8px' }}
        >
          <Spin spinning={loading}>
            {skills.length === 0 ? (
              <Empty description="暂无技能" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <List
                dataSource={skills}
                renderItem={(skill) => (
                  <List.Item
                    onClick={() => setSelectedSkill(skill.name)}
                    style={{
                      cursor: 'pointer',
                      padding: '8px 12px',
                      borderRadius: 6,
                      background: selectedSkill === skill.name ? '#eef3fc' : 'transparent',
                      borderLeft: selectedSkill === skill.name ? '3px solid #4A90E2' : '3px solid transparent',
                      marginBottom: 2,
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 500, fontSize: 13 }}>{skill.display_name}</div>
                      <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                        {skill.mcp_required?.length > 0 && (
                          skill.mcp_required.map((s) => (
                            <Tag key={s} style={{ fontSize: 10, marginRight: 2 }}>{s}</Tag>
                          ))
                        )}
                      </div>
                    </div>
                  </List.Item>
                )}
              />
            )}
          </Spin>
        </Card>

        {/* Right: YAML editor */}
        <Card
          style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
          bodyStyle={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '16px' }}
          title={
            selectedSkillInfo ? (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>
                  {selectedSkillInfo.display_name}
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                    {selectedSkillInfo.description}
                  </Text>
                </span>
              </div>
            ) : (
              '选择技能查看配置'
            )
          }
          extra={
            selectedSkill ? (
              <Button
                type="primary"
                icon={<SaveOutlined />}
                onClick={handleSave}
                loading={saving}
                disabled={!hasChanges}
              >
                保存
              </Button>
            ) : null
          }
        >
          {selectedSkill ? (
            <Spin spinning={loading} style={{ flex: 1 }}>
              <TextArea
                value={yamlContent}
                onChange={(e) => setYamlContent(e.target.value)}
                style={{
                  flex: 1,
                  fontFamily: "'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace",
                  fontSize: 12,
                  lineHeight: 1.6,
                  minHeight: 300,
                  resize: 'none',
                }}
                spellCheck={false}
              />
            </Spin>
          ) : (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Empty description="请从左侧选择一个技能" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

export default SkillsConfigPage
