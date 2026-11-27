import { useState } from 'react'
import { Message, Role } from '../types'
import { avatarBg, DSH_AVATAR, FALLBACK_AVATAR } from '../avatars'
import VoiceBubble from './VoiceBubble'

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
  // 语音消息默认只听不读（微信习惯），点「转文字」才摊开原文
  const [showText, setShowText] = useState(false)
  // 规格第八节：DSH 仅靠头像 + 昵称「DSH · AI」区分，不做角标、不做消息底色
  const emoji = isDsh
    ? DSH_AVATAR.emoji
    : mine
      ? myAvatar || m.avatar || FALLBACK_AVATAR
      : m.avatar || FALLBACK_AVATAR
  const bg = isDsh ? DSH_AVATAR.bg : avatarBg(emoji)
  const nick = isDsh ? 'DSH · AI' : m.nickname

  const status = m.audio_status || 'none'
  const isVoice = status === 'ready' || status === 'pending'
  const voiceFailed = status === 'failed'
  const hasText = !!m.text?.trim()

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

        {status === 'ready' && (
          <>
            <VoiceBubble m={m} mine={mine} />
            {hasText && (
              <button
                type="button"
                className="voice-text-toggle"
                onClick={() => setShowText((v) => !v)}
              >
                {showText ? '收起文字' : '转文字'}
              </button>
            )}
            {showText && hasText && (
              <div className={`bubble ${mine ? 'bubble-mine' : 'bubble-theirs'} voice-transcript`}>
                {m.text}
              </div>
            )}
          </>
        )}

        {status === 'pending' && (
          <div className={`bubble ${mine ? 'bubble-mine' : 'bubble-theirs'} voice-pending`}>
            <span className="typing-dots voice-dots"><i /><i /><i /></span>
            <span className="voice-pending-label">正在说话…</span>
          </div>
        )}

        {!isVoice && (
          <>
            <div className={`bubble ${mine ? 'bubble-mine' : 'bubble-theirs'}`}>
              {hasText ? m.text : '（语音）'}
            </div>
            {voiceFailed && (
              <div className="voice-fallback" title="本机 TTS 没能在 15 秒内返回音频">
                语音没合成出来，已转文字
              </div>
            )}
          </>
        )}

        {canRecall && (
          <button className="recall-btn" type="button" onClick={() => onRecall(m.id)}>
            撤回
          </button>
        )}
      </div>
    </div>
  )
}
