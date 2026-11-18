import { useState } from 'react'

interface Props {
  onSend: (t: string) => void
  disabled?: boolean
}

export default function Composer({ onSend, disabled = false }: Props) {
  const [text, setText] = useState('')
  const send = () => {
    const t = text.trim()
    if (!t || disabled) return
    onSend(t)
    setText('')
  }
  return (
    <div className="composer">
      <input
        value={text}
        placeholder={disabled ? '你已被禁言，暂时无法发言' : '说点什么…'}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && send()}
      />
      <button onClick={send} disabled={disabled}>发送</button>
    </div>
  )
}
