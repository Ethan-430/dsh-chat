import { useEffect, useState } from 'react'
import {
  ApiError,
  changePassword,
  kickMember,
  muteMember,
  restoreMember,
  setAnnouncement,
  setRole,
  updateProfile,
} from '../api'
import { AVATARS, avatarBg, FALLBACK_AVATAR } from '../avatars'
import { Member, Role, User } from '../types'

const LEVEL: Record<Role, number> = { member: 1, admin: 2, owner: 3 }
const ROLE_LABEL: Record<Role, string> = { owner: '群主', admin: '管理员', member: '成员' }
const MUTE_OPTIONS = [10, 60, 1440]

interface Props {
  me: User
  members: Member[]
  onlineIds: number[]
  announcement: string
  onClose: () => void
  onChanged: () => void
  notify: (msg: string) => void
  onLogout: () => void
  onToken: (token: string) => void
  onOpenAi: () => void
}

export default function MembersPanel({
  me, members, onlineIds, announcement,
  onClose, onChanged, notify, onLogout, onToken, onOpenAi,
}: Props) {
  const isOwner = me.role === 'owner'
  const isStaff = LEVEL[me.role] >= 2
  const [busy, setBusy] = useState('')
  const [annText, setAnnText] = useState(announcement)
  const [nick, setNick] = useState(me.nickname)
  const [avatar, setAvatar] = useState(me.avatar || AVATARS[0].emoji)
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [muteMinutes, setMuteMinutes] = useState(10)

  useEffect(() => setAnnText(announcement), [announcement])
  useEffect(() => { setNick(me.nickname); setAvatar(me.avatar || AVATARS[0].emoji) }, [me])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const run = async (key: string, fn: () => Promise<unknown>, okMsg?: string) => {
    setBusy(key)
    try {
      await fn()
      if (okMsg) notify(okMsg)
      onChanged()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="panel-card" role="dialog" aria-label="群成员与设置">
        <div className="panel-head">
          <strong>群成员与设置</strong>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">✕</button>
        </div>

        <div className="panel-scroll">
          {/* 群公告 */}
          <section className="panel-section">
            <div className="section-title">
              群公告 <span className="section-hint">{isStaff ? '（群主/管理员可编辑）' : '（只读）'}</span>
            </div>
            <textarea
              className="ann-input"
              value={annText}
              maxLength={500}
              disabled={!isStaff}
              placeholder="暂无群公告"
              onChange={(e) => setAnnText(e.target.value)}
            />
            {isStaff && (
              <button
                className="mini-btn primary"
                disabled={busy === 'ann'}
                onClick={() => run('ann', () => setAnnouncement(annText), '群公告已更新')}
              >
                保存公告
              </button>
            )}
          </section>

          {/* 成员列表 */}
          <section className="panel-section">
            <div className="section-title">
              成员 <span className="section-hint">共 {members.length} 人 · {onlineIds.length} 人在线</span>
            </div>
            <div className="member-list">
              {members.map((m) => {
                const isMe = m.id === me.id
                const canManage = !isMe && m.status === 'active' && m.role !== 'owner' && LEVEL[me.role] > LEVEL[m.role]
                const online = onlineIds.includes(m.id)
                const muted = m.muted_until > Date.now()
                return (
                  <div className="member-row" key={m.id}>
                    <div className="member-avatar" style={{ background: avatarBg(m.avatar) }}>
                      {m.avatar || FALLBACK_AVATAR}
                      <span className={`online-dot ${online ? 'on' : ''}`} />
                    </div>
                    <div className="member-info">
                      <div className="member-name">
                        {m.nickname}
                        <span className={`role-badge role-${m.role}`}>{ROLE_LABEL[m.role]}</span>
                        {isMe && <span className="me-tag">我</span>}
                      </div>
                      <div className="member-sub">
                        @{m.username}
                        {muted && <span className="warn-tag">禁言中</span>}
                        {m.status === 'kicked' && <span className="warn-tag">已移出</span>}
                        {online && <span className="ok-tag">在线</span>}
                      </div>
                    </div>
                    <div className="member-actions">
                      {m.status === 'kicked' ? (
                        isOwner && (
                          <button
                            className="mini-btn"
                            disabled={busy === `restore-${m.id}`}
                            onClick={() => run(`restore-${m.id}`, () => restoreMember(m.id), '已恢复入群')}
                          >
                            恢复入群
                          </button>
                        )
                      ) : canManage && (
                        <>
                          {isOwner && (
                            <button
                              className="mini-btn"
                              disabled={busy === `role-${m.id}`}
                              onClick={() => run(
                                `role-${m.id}`,
                                () => setRole(m.id, m.role === 'admin' ? 'member' : 'admin'),
                                m.role === 'admin' ? '已撤消管理员' : '已设为管理员',
                              )}
                            >
                              {m.role === 'admin' ? '撤消管理员' : '设为管理员'}
                            </button>
                          )}
                          {muted ? (
                            <button
                              className="mini-btn"
                              disabled={busy === `mute-${m.id}`}
                              onClick={() => run(`mute-${m.id}`, () => muteMember(m.id, 0), '已解除禁言')}
                            >
                              解禁
                            </button>
                          ) : (
                            <span className="mute-wrap">
                              <select
                                value={muteMinutes}
                                onChange={(e) => setMuteMinutes(Number(e.target.value))}
                                aria-label="禁言时长"
                              >
                                {MUTE_OPTIONS.map((n) => (
                                  <option key={n} value={n}>{n < 60 ? `${n} 分钟` : `${n / 60} 小时`}</option>
                                ))}
                              </select>
                              <button
                                className="mini-btn"
                                disabled={busy === `mute-${m.id}`}
                                onClick={() => run(`mute-${m.id}`, () => muteMember(m.id, muteMinutes), '已禁言')}
                              >
                                禁言
                              </button>
                            </span>
                          )}
                          <button
                            className="mini-btn danger"
                            disabled={busy === `kick-${m.id}`}
                            onClick={() => {
                              if (window.confirm(`确定将 ${m.nickname} 移出群聊？`)) {
                                void run(`kick-${m.id}`, () => kickMember(m.id), '已移出群聊')
                              }
                            }}
                          >
                            移出
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
            <p className="permission-note">
              群主可管理所有人并任命管理员、调整 AI 参数；管理员只能管理普通成员；普通成员仅可发言、搜索、导出与撤回自己的消息。
            </p>
          </section>

          {/* 我的账号 */}
          <section className="panel-section">
            <div className="section-title">我的账号 <span className="section-hint">@{me.username}</span></div>
            <div className="profile-edit">
              <input value={nick} maxLength={32} onChange={(e) => setNick(e.target.value)} aria-label="修改昵称" />
              <select value={avatar} onChange={(e) => setAvatar(e.target.value)} aria-label="修改头像">
                {AVATARS.map((a) => <option key={a.emoji} value={a.emoji}>{a.emoji}</option>)}
              </select>
              <button
                className="mini-btn primary"
                disabled={busy === 'profile'}
                onClick={() => run('profile', () => updateProfile({ nickname: nick.trim(), avatar }), '资料已更新')}
              >
                保存资料
              </button>
            </div>
            <div className="profile-edit">
              <input type="password" placeholder="原密码" value={oldPw} onChange={(e) => setOldPw(e.target.value)} aria-label="原密码" />
              <input type="password" placeholder="新密码（8+ 位，含字母数字）" value={newPw} onChange={(e) => setNewPw(e.target.value)} aria-label="新密码" />
              <button
                className="mini-btn"
                disabled={busy === 'pw'}
                onClick={async () => {
                  setBusy('pw')
                  try {
                    const d = await changePassword({ old_password: oldPw, new_password: newPw })
                    onToken(d.token)
                    setOldPw(''); setNewPw('')
                    notify('密码已更新，其他设备的登录已失效')
                    onChanged()
                  } catch (e) {
                    notify(e instanceof ApiError ? e.message : '修改失败')
                  } finally {
                    setBusy('')
                  }
                }}
              >
                修改密码
              </button>
            </div>
            <div className="account-foot">
              {isOwner && (
                <button className="mini-btn" onClick={() => { onClose(); onOpenAi() }}>⚙ AI 参数</button>
              )}
              <button className="mini-btn danger" onClick={onLogout}>退出登录</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
