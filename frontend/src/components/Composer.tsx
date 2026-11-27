import { useCallback, useEffect, useRef, useState } from 'react'
import { uploadVoice } from '../api'

interface Props {
  onSend: (t: string) => void
  disabled?: boolean
  notify?: (msg: string) => void
}

const MAX_MS = 60_000
const MIN_MS = 500
const CANCEL_DRAG_PX = 50
const TAP_MS = 350

function pickMime(): string {
  const cands = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
  ]
  const R = (window as unknown as { MediaRecorder?: typeof MediaRecorder }).MediaRecorder
  if (!R?.isTypeSupported) return ''
  for (const c of cands) if (R.isTypeSupported(c)) return c
  return ''
}

function fmt(ms: number): string {
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export default function Composer({ onSend, disabled = false, notify }: Props) {
  const [text, setText] = useState('')
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [cancelArmed, setCancelArmed] = useState(false)

  const recRef = useRef<{
    rec: MediaRecorder
    chunks: Blob[]
    start: number
    stream: MediaStream
    send: boolean
  } | null>(null)
  const pressRef = useRef({ at: 0, y: 0, held: false })
  const tickRef = useRef<number | null>(null)

  const tell = useCallback((msg: string) => notify?.(msg), [notify])

  const cleanup = useCallback(() => {
    if (tickRef.current) {
      window.clearInterval(tickRef.current)
      tickRef.current = null
    }
    setRecording(false)
    setElapsed(0)
    setCancelArmed(false)
  }, [])

  const finish = useCallback((send: boolean) => {
    const cur = recRef.current
    if (!cur) return
    cur.send = send
    try {
      cur.rec.stop()
    } catch {
      cur.stream.getTracks().forEach((t) => t.stop())
      recRef.current = null
      cleanup()
    }
  }, [cleanup])

  const start = useCallback(async () => {
    if (disabled || recRef.current) return
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      tell('这个浏览器不支持录音，换 Chrome / Edge / Safari 试试')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      const mime = pickMime()
      const rec = mime
        ? new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 32_000 })
        : new MediaRecorder(stream)
      const entry = { rec, chunks: [] as Blob[], start: Date.now(), stream, send: true }
      rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) entry.chunks.push(e.data) }
      rec.onstop = () => {
        const spent = Date.now() - entry.start
        entry.stream.getTracks().forEach((t) => t.stop())
        recRef.current = null
        cleanup()
        if (!entry.send) return
        if (spent < MIN_MS) {
          tell('说话时间太短了')
          return
        }
        const blob = new Blob(entry.chunks, { type: rec.mimeType || 'audio/webm' })
        if (blob.size < 600) {
          tell('没录到声音，检查一下麦克风')
          return
        }
        void uploadVoice(blob, spent).catch((e: unknown) => {
          tell(e instanceof Error ? e.message : '语音发送失败')
        })
      }
      rec.start(200)
      recRef.current = entry
      setRecording(true)
      setElapsed(0)
      tickRef.current = window.setInterval(() => {
        const cur = recRef.current
        if (!cur) return
        const ms = Date.now() - cur.start
        setElapsed(ms)
        if (ms >= MAX_MS) finish(true)
      }, 100)
    } catch (e) {
      const name = (e as { name?: string })?.name
      tell(name === 'NotAllowedError' ? '麦克风被拒绝了，请在浏览器设置里允许' : '打不开麦克风')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled, tell, cleanup, finish])


  // 卸载时确保释放麦克风（否则标签页会一直亮着录音指示）
  useEffect(() => () => {
    const cur = recRef.current
    if (cur) {
      cur.send = false
      try { cur.rec.stop() } catch { /* 已停 */ }
      cur.stream.getTracks().forEach((t) => t.stop())
    }
    if (tickRef.current) window.clearInterval(tickRef.current)
  }, [])

  useEffect(() => {
    if (!recording) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') finish(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [recording, finish])

  const send = () => {
    const t = text.trim()
    if (!t || disabled) return
    onSend(t)
    setText('')
  }

  // ---- 麦克风：点一下开始、再点一下发送；按住说话松手发送；上滑取消 ----
  const onMicDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (recording) { finish(true); return }   // 第二次按下 = 立刻发送
    e.preventDefault()
    pressRef.current = { at: Date.now(), y: e.clientY, held: true }
    void start()
  }
  const onMicMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!recording || !pressRef.current.held) return
    setCancelArmed(pressRef.current.y - e.clientY > CANCEL_DRAG_PX)
  }
  const onMicUp = () => {
    if (!recording) {
      pressRef.current.held = false
      return
    }
    const held = Date.now() - pressRef.current.at
    pressRef.current.held = false
    if (cancelArmed) { finish(false); return }
    // 快速一点 -> 留在录音态（点击模式）；按住超过阈值 -> 松手即发
    if (held > TAP_MS) finish(true)
  }

  return (
    <div className={`composer ${recording ? 'recording' : ''}`}>
      <button
        type="button"
        className={`mic-btn ${recording ? 'active' : ''}`}
        title={recording ? '发送语音（Esc 取消）' : '按住说话 / 点一下开始'}
        aria-label={recording ? '发送语音' : '录语音'}
        disabled={disabled}
        onPointerDown={onMicDown}
        onPointerMove={onMicMove}
        onPointerUp={onMicUp}
        onPointerCancel={() => { if (recording) finish(false) }}
      >
        {recording ? '■' : '🎙'}
      </button>

      {recording ? (
        <div className={`rec-bar ${cancelArmed ? 'cancel' : ''}`}>
          <span className="rec-dot" aria-hidden="true" />
          <span className="rec-time">{fmt(elapsed)}</span>
          <span className="rec-hint">
            {cancelArmed ? '松开取消' : '松开发送 · 上滑取消'}
          </span>
          <button type="button" className="rec-cancel" onClick={() => finish(false)}>取消</button>
        </div>
      ) : (
        <input
          value={text}
          placeholder={disabled ? '你已被禁言，暂时无法发言' : '说点什么…'}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
        />
      )}

      <button onClick={send} disabled={disabled || recording}>发送</button>
    </div>
  )
}
