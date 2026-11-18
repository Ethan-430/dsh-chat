import { useEffect, useState } from 'react'
import { ApiError, getAiParams, getAiStatus, putAiParams } from '../api'
import { AiParamsResponse, AiStatus } from '../types'

interface Props {
  onClose: () => void
  notify: (msg: string) => void
}

export default function AiParamsPanel({ onClose, notify }: Props) {
  const [data, setData] = useState<AiParamsResponse | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<AiStatus | null>(null)

  useEffect(() => {
    getAiParams()
      .then((d) => { setData(d); setDraft(d.params) })
      .catch((e) => setError(e instanceof ApiError ? e.message : '加载失败'))
    getAiStatus().then(setStatus).catch(() => setStatus(null))
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const changed = data ? Object.keys(draft).filter((k) => draft[k] !== data.params[k]) : []

  const save = async () => {
    if (!changed.length) { notify('没有需要保存的改动'); return }
    setBusy(true)
    setError('')
    try {
      const res = await putAiParams(Object.fromEntries(changed.map((k) => [k, draft[k]])))
      setData((prev) => prev ? { ...prev, params: res.params } : prev)
      setDraft(res.params)
      getAiStatus().then(setStatus).catch(() => {})
      notify(`已保存 ${changed.length} 项 AI 参数`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="panel-card" role="dialog" aria-label="AI 参数">
        <div className="panel-head">
          <strong>⚙ AI 参数（仅群主）</strong>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        <div className="panel-scroll">
          <p className="permission-note">
            这里配置 DSH 的触发、冷却与成本闸门。参数按白名单校验并持久化到 SQLite；
            D5–D7 调度器每次叫醒都会重新读表，保存后无需重启、立即生效。
          </p>
          {error && <div className="auth-error">{error}</div>}
          {!data && !error && <div className="empty">加载中…</div>}
          {status && (
            <div className="dsh-status">
              <div className="section-title">
                调度器状态 <span className="section-hint">D5–D7 · 实时快照</span>
              </div>
              <div className="dsh-status-grid">
                <span>运行状态</span><strong>{status.ready ? '已就绪' : '未就绪'}</strong>
                <span>适配器</span><strong>{status.adapter}</strong>
                <span>DSH 发言</span><strong>{status.ai_enabled ? '开启' : '关闭'}</strong>
                <span>今日调用</span><strong>{status.calls_today ?? 0} / {status.daily_call_limit ?? 0}</strong>
                <span>连续主动</span><strong>{status.consecutive_init ?? 0}</strong>
                <span>距上次发言消息数</span><strong>{status.since_dsh_count ?? 0}</strong>
              </div>
              <div className="param-hint">最近决策：{status.last_decision || '暂无'}</div>
            </div>
          )}
          {data && (
            <div className="param-list">
              {Object.entries(data.spec).map(([key, spec]) => (
                <div className="param-row" key={key}>
                  <div className="param-label">
                    <span>{spec.label}</span>
                    <code>{key}</code>
                  </div>
                  {spec.type === 'bool' ? (
                    <select
                      value={draft[key] ?? spec.default}
                      onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                    >
                      <option value="1">开启</option>
                      <option value="0">关闭</option>
                    </select>
                  ) : spec.type === 'str' ? (
                    <textarea
                      rows={4}
                      maxLength={spec.max ?? undefined}
                      value={draft[key] ?? spec.default}
                      onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                    />
                  ) : (
                    <input
                      type="number"
                      min={spec.min ?? undefined}
                      max={spec.max ?? undefined}
                      value={draft[key] ?? spec.default}
                      onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                    />
                  )}
                  <div className="param-hint">
                    默认 {spec.type === 'bool' ? (spec.default === '1' ? '开启' : '关闭') : spec.default}
                    {spec.type === 'int' && ` · 范围 ${spec.min}–${spec.max}`}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="panel-foot">
          <span className="section-hint">{changed.length ? `${changed.length} 项待保存` : '无改动'}</span>
          <button className="mini-btn primary" disabled={busy || !changed.length} onClick={save}>
            {busy ? '保存中…' : '保存改动'}
          </button>
        </div>
      </div>
    </div>
  )
}
