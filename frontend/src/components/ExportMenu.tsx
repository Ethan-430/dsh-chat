import { useEffect, useRef, useState } from 'react'
import { downloadExport } from '../api'

/** D4：导出经 fetch + Bearer 头取回 Blob 再下载，令牌不出现在 URL 里。 */
export default function ExportMenu({ notify }: { notify: (msg: string) => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const go = async (format: 'json' | 'md') => {
    setOpen(false)
    setBusy(true)
    try {
      await downloadExport(format)
      notify(`已导出 ${format === 'json' ? 'JSON' : 'Markdown'}`)
    } catch (e) {
      notify(e instanceof Error ? e.message : '导出失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="export-wrap" ref={ref}>
      <button className="tool-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open} disabled={busy}>
        {busy ? '导出中…' : '⬇ 导出'}
      </button>
      {open && (
        <div className="export-menu">
          <button type="button" onClick={() => void go('json')}><span>📄</span> 导出 JSON</button>
          <button type="button" onClick={() => void go('md')}><span>📝</span> 导出 Markdown</button>
        </div>
      )}
    </div>
  )
}
