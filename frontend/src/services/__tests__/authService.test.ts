/**
 * authService 测试 — 前端认证流程。
 *
 * 覆盖登录成功/失败、未登录保护、改密请求体构造，
 * 验证前端与后端 /api/auth/* 的契约。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { changePassword, getMe, login } from '../authService'

describe('authService.login', () => {
  it('登录成功返回 access_token 与用户信息', async () => {
    const mockUser = {
      access_token: 'jwt-token',
      token_type: 'bearer',
      user: { user_id: 1, username: 'admin', is_superuser: true },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify(mockUser), { status: 200 }),
    ))

    const res = await login({ username: 'admin', password: 'admin123' })
    expect(res.access_token).toBe('jwt-token')
    expect(res.user.username).toBe('admin')
    vi.unstubAllGlobals()
  })

  it('登录失败抛出后端错误消息', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: '用户名或密码错误' }), { status: 401 }),
    ))

    await expect(login({ username: 'a', password: 'b' })).rejects.toThrow('用户名或密码错误')
    vi.unstubAllGlobals()
  })
})

describe('authService.getMe', () => {
  beforeEach(() => localStorage.clear())

  it('无 token 时抛出未登录错误', async () => {
    await expect(getMe()).rejects.toThrow('未登录')
  })

  it('带 token 返回当前用户信息', async () => {
    localStorage.setItem('token', 'jwt')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 1, username: 'admin' }), { status: 200 }),
    ))

    const me = await getMe()
    expect(me.username).toBe('admin')
    vi.unstubAllGlobals()
  })
})

describe('authService.changePassword', () => {
  beforeEach(() => localStorage.clear())

  it('主动改密时携带旧密码', async () => {
    localStorage.setItem('token', 'jwt')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ message: '密码已修改' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await changePassword('new-pass', 'old-pass')
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.new_password).toBe('new-pass')
    expect(body.old_password).toBe('old-pass')
    vi.unstubAllGlobals()
  })

  it('强制改密场景不携带旧密码', async () => {
    localStorage.setItem('token', 'jwt')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ message: '密码已修改' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await changePassword('first-login-pass')
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.new_password).toBe('first-login-pass')
    expect(body.old_password).toBeUndefined()
    vi.unstubAllGlobals()
  })
})
