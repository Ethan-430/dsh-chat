import {
  AiParamsResponse,
  AiStatus,
  AuthResponse,
  AuthStatus,
  MeResponse,
  MembersResponse,
  SearchResponse,
  User,
} from './types'

const TOKEN_KEY = 'dsh_token'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
  // 清掉匿名时代遗留的本地身份，避免旧标签页残留
  for (const k of ['dsh_client_id', 'dsh_nickname', 'dsh_avatar']) localStorage.removeItem(k)
}

/** 统一注入 Bearer 令牌；令牌不放 Cookie，天然免疫 CSRF。 */
export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = {
    ...((init.headers as Record<string, string>) || {}),
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, { ...init, headers })
  if (res.status === 401 && !path.startsWith('/api/auth/login')) {
    clearToken()
    window.dispatchEvent(new CustomEvent('dsh:unauthorized'))
  }
  return res
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiRequest(path, init)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError((data as { detail?: string }).detail || `HTTP ${res.status}`, res.status)
  }
  return data as T
}

function withBody(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

const post = (p: unknown) => withBody('POST', p)
const patch = (p: unknown) => withBody('PATCH', p)
const put = (p: unknown) => withBody('PUT', p)

// ---------- 认证 ----------
export const authStatus = () => apiJson<AuthStatus>('/api/auth/status')
export const register = (payload: {
  username: string
  password: string
  nickname: string
  avatar: string
}) => apiJson<AuthResponse>('/api/auth/register', post(payload))
export const login = (payload: { username: string; password: string }) =>
  apiJson<AuthResponse>('/api/auth/login', post(payload))
export const fetchMe = () => apiJson<MeResponse>('/api/auth/me')
export const logout = () => apiJson<{ ok: boolean }>('/api/auth/logout', post({}))
export const changePassword = (payload: { old_password: string; new_password: string }) =>
  apiJson<AuthResponse>('/api/auth/password', post(payload))
export const updateProfile = (payload: { nickname: string; avatar: string }) =>
  apiJson<{ user: User }>('/api/me', patch(payload))

// ---------- 成员 / 群资料 ----------
export const getMembers = () => apiJson<MembersResponse>('/api/members')
export const setRole = (userId: number, role: 'admin' | 'member') =>
  apiJson('/api/admin/role', post({ user_id: userId, role }))
export const muteMember = (userId: number, minutes: number) =>
  apiJson('/api/admin/mute', post({ user_id: userId, minutes }))
export const kickMember = (userId: number) =>
  apiJson('/api/admin/kick', post({ user_id: userId }))
export const restoreMember = (userId: number) =>
  apiJson('/api/admin/restore', post({ user_id: userId }))
export const setAnnouncement = (text: string) =>
  apiJson('/api/group/announcement', put({ text }))

// ---------- AI 参数（仅群主可改） ----------
export const getAiParams = () => apiJson<AiParamsResponse>('/api/ai/params')
export const putAiParams = (params: Record<string, string | boolean | number>) =>
  apiJson<{ params: Record<string, string> }>('/api/ai/params', put({ params }))
/** D5–D7：调度器 / 适配器 / 冷却状态快照 */
export const getAiStatus = () => apiJson<AiStatus>('/api/ai/status')

// ---------- 消息 ----------
export const recallMessage = (id: number) =>
  apiJson<{ ok: boolean }>(`/api/messages/${id}`, { method: 'DELETE' })

// ---------- D3–D4 ----------
export async function searchMessages(q: string, signal?: AbortSignal): Promise<SearchResponse> {
  return apiJson<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}&limit=50`, { signal })
}

/** 导出改为 fetch + Blob：令牌留在请求头里，不出现在 URL / 浏览器历史中。 */
export async function downloadExport(format: 'json' | 'md'): Promise<void> {
  const res = await apiRequest(`/api/export?format=${format}`)
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new ApiError((data as { detail?: string }).detail || `导出失败（HTTP ${res.status}）`, res.status)
  }
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = /filename="?([^";]+)"?/.exec(disposition)
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = match?.[1] || `dsh-chat.${format}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
