import { ReactNode, useEffect, useRef, useState } from 'react'
import { searchMessages } from '../api'
import { Message } from '../types'
import { avatarBg, DSH_AVATAR, FALLBACK_AVATAR } from '../avatars'

interface Props {
  onClose: () => void
  onLocate: (id: number) => void
}

/** 纯字符串切分渲染高亮，不用 dangerouslySetInnerHTML（避免 XSS） */
function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>
  const lower = text.toLowerCase()
  const needle = q.toLowerCase()
  const parts: ReactNode[] = []
  let from = 0
  let key = 0
  for (;;) {
    const hit = lower.indexOf(needle, from)
    if (hit < 0) {
      parts.push(text.slice(from))
      break
    }
    if (hit > from) parts.push(text.slice(from, hit))
    parts.push(<mark key={key++}>{text.slice(hit, hit + needle.length)}</mark>)
    from = hit + needle.length
  }
  return <>{parts}</>
}

function fmtTime(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function SearchPanel({ onClose, onLocate }: Props) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Message[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 输入防抖 280ms；切换关键词时取消在途请求，避免旧结果覆盖新结果
  useEffect(() => {
    const keyword = q.trim()
    if (!keyword) {
      setResults([])
      setHasMore(false)
      setSearched(false)
      setError('')
      setLoading(false)
      return
    }
    const ctrl = new AbortController()
    const timer = window.setTimeout(async () => {
      setLoading(true)
      setError('')
      try {
        const data = await searchMessages(keyword, ctrl.signal)
        setResults(data.messages)
        setHasMore(data.has_more)
        setSearched(true)
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setError((e as Error).message)
      } finally {
        if (!ctrl.signal.aborted) setLoading(false)
      }
    }, 280)
    return () => {
      window.clearTimeout(timer)
      ctrl.abort()
    }
  }, [q])

  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="search-card" role="dialog" aria-label="搜索聊天记录">
        <div className="search-head">
          <input
            ref={inputRef}
            value={q}
            maxLength={100}
            placeholder="搜索聊天记录关键词…"
            onChange={(e) => setQ(e.target.value)}
          />
          <button className="icon-btn" onClick={onClose} aria-label="关闭搜索">
            ✕
          </button>
        </div>

        <div className="search-meta">
          {loading
            ? '搜索中…'
            : error
              ? <span className="err">{error}</span>
              : !searched
                ? '输入关键词自动搜索（匹配消息正文）'
                : `找到 ${results.length} 条${hasMore ? '（仅显示最新 50 条）' : ''}`}
        </div>

        <div className="search-results">
          {results.map((m) => {
            const isDsh = m.sender_type === 'ai'
            const emoji = isDsh ? DSH_AVATAR.emoji : m.avatar || FALLBACK_AVATAR
            const nick = isDsh ? 'DSH · AI' : m.nickname
            return (
              <button
                key={m.id}
                type="button"
                className="search-hit"
                onClick={() => {
                  onLocate(m.id)
                  onClose()
                }}
              >
                <div className="hit-avatar" style={{ background: isDsh ? DSH_AVATAR.bg : avatarBg(emoji) }}>
                  {emoji}
                </div>
                <div className="hit-body">
                  <div className="hit-top">
                    <span className="hit-nick">{nick}</span>
                    <span className="hit-time">{fmtTime(m.created_at)}</span>
                  </div>
                  <div className="hit-text">
                    <Highlight text={m.text} q={q.trim()} />
                  </div>
                </div>
              </button>
            )
          })}
          {searched && !loading && !error && results.length === 0 && (
            <div className="empty">没有找到匹配的消息</div>
          )}
        </div>

        <div className="search-foot">点击结果可定位到聊天中的位置 · Esc 关闭</div>
      </div>
    </div>
  )
}
