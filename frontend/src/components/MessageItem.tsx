import { Message, Role } from '../types'
import { avatarBg, DSH_AVATAR, FALLBACK_AVATAR } from '../avatars'

const ROLE_LABEL: Record<Role, string> = { owner: '群主', admin: '管理员', member: '成员' }

interface Props {
  m: Message
  mine: boolean
  myAvatar: string
  senderRole?: Role
  canRecall: boolean
  onRecall: (id: number) => void
}

export default function MessageItem({ m, mine, myAvatar, senderRole, canRecall, onRecall }: Props) {
  const isDsh = m.sender_type === 'ai'
  // 规格第八节：DSH 仅靠头像 + 昵称「DSH · AI」区分，不做角标、不做消息底色
  const emoji = isDsh
    ? DSH_AVATAR.emoji
    : mine
      ? myAvatar || m.avatar || FALLBACK_AVATAR
      : m.avatar || FALLBACK_AVATAR
  const bg = isDsh ? DSH_AVATAR.bg : avatarBg(emoji)
  const nick = isDsh ? 'DSH · AI' : m.nickname

  return (
    <div id={`msg-${m.id}`} className={`msg-row ${mine ? 'mine' : 'theirs'}`}>
      <div className="avatar" style={{ background: bg }} title={nick}>
        {emoji}
      </div>
      <div className="msg-body">
        {!mine && (
          <div className="nick">
            {nick}
            {!isDsh && senderRole && senderRole !== 'member' && (
              <span className={`role-badge role-${senderRole}`}>{ROLE_LABEL[senderRole]}</span>
            )}
          </div>
        )}
        <div className={`bubble ${mine ? 'bubble-mine' : 'bubble-theirs'}`}>{m.text}</div>
        {canRecall && (
          <button className="recall-btn" type="button" onClick={() => onRecall(m.id)}>
            撤回
          </button>
        )}
      </div>
    </div>
  )
}
