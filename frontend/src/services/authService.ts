const API_BASE = import.meta.env.VITE_API_URL || ''

const headers = () => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${localStorage.getItem('token')}`,
})

export interface LoginParams {
  username: string
  password: string
}

export interface LoginResult {
  access_token: string
  token_type: string
  force_password_change?: boolean
  user: {
    user_id: number
    username: string
    is_superuser: boolean
  }
}

export async function login(params: LoginParams): Promise<LoginResult> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '登录失败' }))
    throw new Error(err.detail || '登录失败')
  }
  return res.json()
}

export async function getMe(): Promise<any> {
  const token = localStorage.getItem('token')
  if (!token) throw new Error('未登录')
  const res = await fetch(`${API_BASE}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('获取用户信息失败')
  return res.json()
}

export async function changePassword(newPassword: string, oldPassword?: string): Promise<void> {
  const body: any = { new_password: newPassword }
  if (oldPassword) {
    body.old_password = oldPassword
  }
  const res = await fetch(`${API_BASE}/api/auth/change-password`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '修改密码失败' }))
    throw new Error(err.detail || '修改密码失败')
  }
}

export async function listUsers(): Promise<any[]> {
  const res = await fetch(`${API_BASE}/api/auth/users`, { headers: headers() })
  if (!res.ok) throw new Error('获取用户列表失败')
  return res.json()
}

export async function deleteUser(targetId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/users/${targetId}`, {
    method: 'DELETE',
    headers: headers(),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '删除用户失败' }))
    throw new Error(err.detail || '删除用户失败')
  }
}

export async function toggleUserActive(targetId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/users/${targetId}/toggle-active`, {
    method: 'PUT',
    headers: headers(),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '操作失败' }))
    throw new Error(err.detail || '操作失败')
  }
}

export async function adminResetPassword(targetId: number, newPassword: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/users/${targetId}/reset-password`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ new_password: newPassword }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '重置密码失败' }))
    throw new Error(err.detail || '重置密码失败')
  }
}

export async function registerUser(params: {
  username: string
  password: string
  email?: string
  full_name?: string
}): Promise<any> {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '创建用户失败' }))
    throw new Error(err.detail || '创建用户失败')
  }
  return res.json()
}
