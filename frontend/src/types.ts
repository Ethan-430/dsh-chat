export type Role = 'owner' | 'admin' | 'member'
export type UserStatus = 'active' | 'kicked'

export type AudioStatus = 'none' | 'pending' | 'ready' | 'failed'

export interface Message {
  id: number
  room_id?: string
  sender_type: 'human' | 'ai'
  sender_id?: string | null
  nickname: string
  avatar?: string | null
  text: string
  created_at: number
  /** 语音状态：pending 合成中 / ready 可播 / failed 已降级为文字 */
  audio_status?: AudioStatus
  audio_duration_ms?: number
  audio_transcript?: string | null
}

/** 服务端 message:audio 事件：只带变化的字段 */
export interface MessageAudioPayload {
  id: number
  audio_status: AudioStatus
  audio_duration_ms?: number
}

export interface User {
  id: number
  username: string
  nickname: string
  avatar: string | null
  role: Role
  status: UserStatus
  muted_until: number
  created_at: number
}

export type Member = User

export interface Presence {
  online_count: number
  online_ids: number[]
}

export interface JoinedPayload {
  session_id: string
  user: User
  announcement: string
}

export interface AuthResponse {
  token: string
  user: User
  role_label: string
}

export interface MeResponse {
  user: User
  announcement: string
  role_label: string
}

export interface MembersResponse {
  members: Member[]
  online_ids: number[]
  announcement: string
  me: User
}

export interface AuthStatus {
  user_count: number
  has_owner: boolean
}

export interface AiParamSpecItem {
  type: 'bool' | 'int' | 'str'
  min: number | null
  max: number | null
  default: string
  label: string
}

export interface AiParamsResponse {
  params: Record<string, string>
  editable: boolean
  spec: Record<string, AiParamSpecItem>
}

/** GET /api/search?q= 的响应 */
export interface SearchResponse {
  query: string
  messages: Message[]
  has_more: boolean
}

// ---------- D5–D7：DSH 流式事件 ----------
export interface DshTypingPayload {
  active: boolean
  trigger?: string
}

export interface DshDeltaPayload {
  text: string
}

export interface DshSilentPayload {
  trigger: string
  reason?: string
}

/** GET /api/ai/status：调度器运行状态（只读） */
export interface AiStatus {
  ready: boolean
  adapter: string
  room?: string
  busy?: boolean
  ai_enabled: boolean
  daily_call_limit?: number
  calls_today?: number
  last_speak_ts?: number
  consecutive_init?: number
  since_dsh_count?: number
  recent_decisions?: string[]
  last_decision?: string
  reason?: string
}
