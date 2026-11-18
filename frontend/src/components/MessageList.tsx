import { useEffect, useRef } from 'react'
import { Member, Message, User } from '../types'
import MessageItem from './MessageItem'
import DshTyping from './DshTyping'

const RECALL_WINDOW_MS = 120_000

interface Props {
  messages: Message[]
  mySessionId: string
  myAvatar: string
  me: User
  members: Member[]
  typingActive: boolean
  typingText: string
  onRecall: (id: number) => void
}

export default function MessageList({
  messages, mySessionId, myAvatar, me, members, typingActive, typingText, onRecall,
}: Props) {
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, typingActive, typingText])

  const roleOf = (m: Message) =>
    m.sender_type === 'human'
      ? members.find((u) => String(u.id) === String(m.sender_id))?.role
      : undefined

  const canRecall = (m: Message) => {
    if (me.role === 'owner') return true
    if (m.sender_type === 'human' && String(m.sender_id) === String(me.id)) {
      return Date.now() - m.created_at <= RECALL_WINDOW_MS
    }
    return me.role === 'admin' && m.sender_type === 'human' && roleOf(m) === 'member'
  }

  return (
    <div className="msg-list" ref={listRef}>
      {messages.map((m) => (
        <MessageItem
          key={m.id}
          m={m}
          mine={m.sender_type === 'human' && !!mySessionId && m.sender_id === mySessionId}
          myAvatar={myAvatar}
          senderRole={roleOf(m)}
          canRecall={canRecall(m)}
          onRecall={onRecall}
        />
      ))}
      <DshTyping active={typingActive} text={typingText} />
    </div>
  )
}
