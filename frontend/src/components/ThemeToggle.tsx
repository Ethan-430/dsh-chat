import { THEME_ICON, THEME_LABEL, nextTheme, type ThemeMode } from '../theme'

interface Props {
  mode: ThemeMode
  onChange: (m: ThemeMode) => void
  /** fab = 登录页等无边栏场景的悬浮按钮 */
  variant?: 'tool' | 'fab'
}

export default function ThemeToggle({ mode, onChange, variant = 'tool' }: Props) {
  const next = nextTheme(mode)
  return (
    <button
      type="button"
      className={variant === 'fab' ? 'theme-fab' : 'tool-btn theme-btn'}
      title={`主题：${THEME_LABEL[mode]}（点击切到${THEME_LABEL[next]}）`}
      aria-label={`主题：${THEME_LABEL[mode]}，点击切换到${THEME_LABEL[next]}`}
      onClick={() => onChange(next)}
    >
      <span aria-hidden="true">{THEME_ICON[mode]}</span>
    </button>
  )
}
