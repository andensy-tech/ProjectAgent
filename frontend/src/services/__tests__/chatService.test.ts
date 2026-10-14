/**
 * chatService 测试 — 前端核心通信逻辑（SSE 流式解析）。
 *
 * 用户高频场景：web chat 发送消息 → 后端 SSE 流式返回。
 * 这里验证 sendMessage 对 SSE 事件流（data: JSON）的正确解析，
 * 是聊天流式渲染的前端基础。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchConversations, sendMessage } from '../chatService'

function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c))
      controller.close()
    },
  })
  return new Response(stream, { status })
}

describe('sendMessage SSE 流式解析', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token')
  })

  it('解析 thinking/text/complete 事件并按序回调', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      'data: {"type":"thinking","content":"思考中"}\n\n',
      'data: {"type":"text","content":"你好"}\n\n',
      'data: {"type":"complete","content":""}\n\n',
    ])))

    const received: any[] = []
    await sendMessage('你好', (r) => received.push(r))

    expect(received).toHaveLength(3)
    expect(received[0].type).toBe('thinking')
    expect(received[1].type).toBe('text')
    expect(received[1].content).toBe('你好')
    expect(received[2].type).toBe('complete')
    vi.unstubAllGlobals()
  })

  it('一条事件跨多个网络 chunk 时正确合并', async () => {
    // 模拟网络分包：一条 JSON 事件被切成两段
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      'data: {"type":"text","cont',
      'ent":"跨块内容"}\n\ndata: {"type":"complete","content":""}\n\n',
    ])))

    const received: any[] = []
    await sendMessage('测试', (r) => received.push(r))

    expect(received).toHaveLength(2)
    expect(received[0].content).toBe('跨块内容')
    vi.unstubAllGlobals()
  })

  it('忽略 [DONE] 结束标记', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      'data: {"type":"text","content":"ok"}\n\ndata: [DONE]\n\n',
    ])))

    const received: any[] = []
    await sendMessage('x', (r) => received.push(r))

    expect(received).toHaveLength(1)
    vi.unstubAllGlobals()
  })

  it('无终止符的末尾 data 也会 flush 解析', async () => {
    // 最后一个事件没有 \n\n 结尾
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      'data: {"type":"text","content":"尾部"}',
    ])))

    const received: any[] = []
    await sendMessage('x', (r) => received.push(r))

    expect(received).toHaveLength(1)
    expect(received[0].content).toBe('尾部')
    vi.unstubAllGlobals()
  })

  it('HTTP 错误抛出异常', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 500 })))
    await expect(sendMessage('x', () => {})).rejects.toThrow('HTTP error')
    vi.unstubAllGlobals()
  })
})

describe('downloadFile（Agent 产出文件下载）', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('带 JWT 拉取文件并触发浏览器下载', async () => {
    vi.useFakeTimers()
    const mockFetch = vi.fn().mockResolvedValue(
      new Response('file-content', { status: 200, headers: { 'Content-Type': 'text/html' } }),
    )
    vi.stubGlobal('fetch', mockFetch)
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const anchorDownload: string[] = []
    const appendSpy = vi.spyOn(document.body, 'appendChild')
    appendSpy.mockImplementation(((node: any) => {
      if (node?.download) anchorDownload.push(node.download)
      return node
    }) as any)

    const { downloadFile } = await import('../chatService')
    await downloadFile('/api/files/download?token=abc', '周报.html')

    // 鉴权头必须带上（<a href> 无法携带 JWT，故走 fetch + blob）
    const [url, options] = mockFetch.mock.calls[0]
    expect(url).toContain('/api/files/download?token=abc')
    expect(options.headers.Authorization).toBe('Bearer test-token')
    expect(createObjectURL).toHaveBeenCalled()
    expect(click).toHaveBeenCalled()
    expect(anchorDownload).toEqual(['周报.html'])
    // 延迟释放：立即 revoke 会让部分浏览器中断下载
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(10000)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock')

    click.mockRestore()
    appendSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('403（链接过期）时抛出后端提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: '下载链接无效或已过期，请在对话中重新生成文件' }), {
        status: 403, headers: { 'Content-Type': 'application/json' },
      }),
    ))

    const { downloadFile } = await import('../chatService')
    await expect(downloadFile('/api/files/download?token=bad')).rejects.toThrow('下载链接无效或已过期')
    vi.unstubAllGlobals()
  })

  it('401 时跳转登录', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 401 })))

    const { downloadFile } = await import('../chatService')
    await expect(downloadFile('/api/files/download?token=expired')).rejects.toThrow('未登录')
    vi.unstubAllGlobals()
  })
})

describe('extractFileDownload（任务结果 → 下载卡片数据）', () => {
  it('优先使用后端 result.files[]（含签名下载链接）', async () => {
    const { extractFileDownload } = await import('../chatService')
    const file = extractFileDownload({
      type: 'success',
      data: { file_path: '/server/side/x.html', skill_name: 'weekly_report' },
      files: [{ file_name: 'x.html', file_path: '/server/side/x.html', size: 12,
                skill_name: 'weekly_report', download_url: '/api/files/download?token=sig' }],
    })

    expect(file?.download_url).toBe('/api/files/download?token=sig')
    expect(file?.file_name).toBe('x.html')
  })

  it('旧结构（data.file_path + skill_name）退回技能目录下载接口', async () => {
    const { extractFileDownload } = await import('../chatService')
    const file = extractFileDownload({
      data: { file_path: '/home/u/skills/weekly_report/周报 09-04.html',
              skill_name: 'weekly_report' },
    })

    expect(file?.file_name).toBe('周报 09-04.html')
    expect(file?.download_url).toBe(
      '/api/skills/weekly_report/download?file=' + encodeURIComponent('周报 09-04.html'),
    )
  })

  it('无文件信息 / 只有路径无技能名 → 不返回可下载文件', async () => {
    const { extractFileDownload } = await import('../chatService')

    expect(extractFileDownload(undefined)).toBeUndefined()
    expect(extractFileDownload({ message: '已完成' })).toBeUndefined()
    expect(extractFileDownload({ data: { file_path: '/tmp/x.html' } })).toBeUndefined()
    expect(extractFileDownload({ files: [] })).toBeUndefined()
  })
})

describe('fetchConversations', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token')
  })

  it('获取会话列表成功', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ conversations: [{ id: 1, title: '会话1' }] }), { status: 200 }),
    ))

    const data = await fetchConversations()
    // fetchConversations 返回已解构的 conversations 数组
    expect(data).toHaveLength(1)
    expect(data[0].title).toBe('会话1')
    vi.unstubAllGlobals()
  })

  it('获取失败抛出异常', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 500 })))
    await expect(fetchConversations()).rejects.toThrow('Failed to fetch conversations')
    vi.unstubAllGlobals()
  })
})

describe('定时任务 API（scheduled tasks）', () => {
  const TASK = {
    id: 1,
    name: '卡闭环跟踪',
    task_type: 'declarative_rule',
    config: { source: { kind: 'jira_jql', jql: 'filter = 61364' } },
    interval_minutes: 30,
    run_until: '2026-09-20',
    active: true,
    next_run_at: '2026-09-09T12:00:00+08:00',
    last_run_status: 'ok: nudged=1',
    run_count: 3,
  }

  it('fetchScheduledTasks 返回任务列表', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify([TASK]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    ))
    const { fetchScheduledTasks } = await import('../chatService')
    const data = await fetchScheduledTasks()
    expect(data).toHaveLength(1)
    expect(data[0].name).toBe('卡闭环跟踪')
    vi.unstubAllGlobals()
  })

  it('createScheduledTask POST 到 /api/scheduled_tasks', async () => {
    const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(TASK), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', mock)
    const { createScheduledTask } = await import('../chatService')
    await createScheduledTask({ name: '卡闭环跟踪' } as any)
    expect(mock).toHaveBeenCalledWith(
      expect.stringContaining('/api/scheduled_tasks'),
      expect.objectContaining({ method: 'POST' }),
    )
    vi.unstubAllGlobals()
  })

  it('updateScheduledTask PUT 到 /api/scheduled_tasks/{id}', async () => {
    const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(TASK), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', mock)
    const { updateScheduledTask } = await import('../chatService')
    await updateScheduledTask(1, { active: false } as any)
    expect(mock).toHaveBeenCalledWith(
      expect.stringContaining('/api/scheduled_tasks/1'),
      expect.objectContaining({ method: 'PUT' }),
    )
    vi.unstubAllGlobals()
  })

  it('deleteScheduledTask DELETE 到 /api/scheduled_tasks/{id}', async () => {
    const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', mock)
    const { deleteScheduledTask } = await import('../chatService')
    await deleteScheduledTask(1)
    expect(mock).toHaveBeenCalledWith(
      expect.stringContaining('/api/scheduled_tasks/1'),
      expect.objectContaining({ method: 'DELETE' }),
    )
    vi.unstubAllGlobals()
  })
})
