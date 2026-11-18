export interface AvatarOption {
  emoji: string
  bg: string
}

// 用户可自选的头像；不含 🤖，避免与 DSH 混淆
export const AVATARS: AvatarOption[] = [
  { emoji: '🐱', bg: '#fef3c7' },
  { emoji: '🐶', bg: '#dbeafe' },
  { emoji: '🦊', bg: '#ffedd5' },
  { emoji: '🐼', bg: '#e5e7eb' },
  { emoji: '🐯', bg: '#fee2e2' },
  { emoji: '🦁', bg: '#fef9c3' },
  { emoji: '🐸', bg: '#dcfce7' },
  { emoji: '🐵', bg: '#fce7f3' },
  { emoji: '🐰', bg: '#f3e8ff' },
  { emoji: '🐻', bg: '#ede9fe' },
  { emoji: '🐧', bg: '#cffafe' },
  { emoji: '🦄', bg: '#fae8ff' },
]

// DSH · AI 专属头像（规格第八节：仅头像 + 昵称区分，不做角标/消息底色）
export const DSH_AVATAR: AvatarOption = { emoji: '🤖', bg: '#e0f2fe' }

export const FALLBACK_AVATAR = '🙂'

export function avatarBg(emoji?: string | null): string {
  return AVATARS.find((a) => a.emoji === emoji)?.bg ?? '#e5e7eb'
}
