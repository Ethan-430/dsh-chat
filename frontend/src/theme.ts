/** 主题：三档（跟随系统 / 浅色 / 深色）。
 *
 * 真值来源有两处，必须保持一致：
 *  1. index.html 里的一段内联脚本（首屏前执行，避免深色用户看到一帧白闪）；
 *  2. 这里（运行时切换、跟随系统变化）。
 * 存储键固定为 dsh_theme，值为 auto | light | dark。
 */
export type ThemeMode = 'auto' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_KEY = 'dsh_theme'

const ORDER: ThemeMode[] = ['auto', 'light', 'dark']

const META_COLOR: Record<ResolvedTheme, string> = {
  light: '#f5f5f7',
  dark: '#181b22',
}

export const THEME_LABEL: Record<ThemeMode, string> = {
  auto: '跟随系统',
  light: '浅色',
  dark: '深色',
}

export const THEME_ICON: Record<ThemeMode, string> = {
  auto: '🌗',
  light: '☀️',
  dark: '🌙',
}

export function getStoredTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light' || v === 'dark' || v === 'auto') return v
  } catch {
    /* 隐私模式下 localStorage 可能抛错，按默认处理 */
  }
  return 'auto'
}

export function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia
    && window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === 'auto') return systemPrefersDark() ? 'dark' : 'light'
  return mode
}

export function applyTheme(mode: ThemeMode): ResolvedTheme {
  const resolved = resolveTheme(mode)
  const root = document.documentElement
  root.dataset.theme = resolved
  root.style.colorScheme = resolved
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META_COLOR[resolved])
  return resolved
}

export function setTheme(mode: ThemeMode): void {
  try {
    localStorage.setItem(THEME_KEY, mode)
  } catch {
    /* 存不下就只管当前会话 */
  }
  applyTheme(mode)
}

export function nextTheme(mode: ThemeMode): ThemeMode {
  return ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length]
}

/** 只在「跟随系统」时才需要监听系统主题变化。 */
export function watchSystemTheme(onChange: () => void): () => void {
  if (!window.matchMedia) return () => {}
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const handler = () => onChange()
  mq.addEventListener('change', handler)
  return () => mq.removeEventListener('change', handler)
}
