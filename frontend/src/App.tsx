import { useCallback, useEffect, useRef, useState } from 'react'
import { socket } from './socket'
import {
  clearToken, fetchMe, getMembers, getToken, logout as logoutApi,
  recallMessage, setToken,
} from './api'
import {
  JoinedPayload, Member, Message, Presence, Role, User, AuthResponse,
  DshTypingPayload, DshDeltaPayload,
} from './types'
import MessageList from './components/MessageList'
import Composer from './components/Composer'
import OnlineBadge from './components/OnlineBadge'
import SearchPanel from './components/SearchPanel'
import ExportMenu from './components/ExportMenu'
import AuthScreen from './components/AuthScreen'
import MembersPanel from './components/MembersPanel'
import AiParamsPanel from './components/AiParamsPanel'

const ROLE_LABEL: Record<Role, string> = { owner: '群主', admin: '管理员', member: '成员' }
type Phase = 'loading' | 'auth' | 'chat'

export default function App() {
  const [phase, setPhase] = useState<Phase>('loading')
  const [me, setMe] = useState<User | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [online, setOnline] = useState(0)
  const [onlineIds, setOnlineIds] = useState<number[]>([])
  const [announcement, setAnnouncement] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [membersOpen, setMembersOpen] = useState(false)
  const [aiOpen, setAiOpen] = useState(false)
  const [toast, setToast] = useState('')
  // D5–D7：DSH 逐字流式草稿（未落库）
  const [dshTyping, setDshTyping] = useState(false)
  const [dshDraft, setDshDraft] = useState('')
  const toastTimer = useRef<number | null>(null)

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(''), 2600)
  }, [])

  const refreshAll = useCallback(async () => {
    try {
      const [m, self] = await Promise.all([getMembers(), fetchMe()])
      setMembers(m.members)
      setOnlineIds(m.online_ids)
      setAnnouncement(m.announcement)
      setMe(self.user)
    } catch {
      // 401 由 apiRequest 统一派发 dsh:unauthorized
    }
  }, [])

  const connectSocket = useCallback(() => {
    const token = getToken()
    if (!token) return
    if (socket.connected) socket.emit('join', { token })
    else socket.connect()
  }, [])

  const resetToAuth = useCallback(() => {
    clearToken()
    socket.disconnect()
    setMe(null)
    setMessages([])
    setMembers([])
    setDshTyping(false)
    setDshDraft('')
    setOnlineIds([])
    setOnline(0)
    setMembersOpen(false)
    setAiOpen(false)
    setPhase('auth')
  }, [])

  // 所有 Socket.IO 监听只注册一次，处理器内部只读取 localStorage / setState，不捕获易变状态
  useEffect(() => {
    const onConnect = () => {
      const token = getToken()
      if (token) socket.emit('join', { token })
    }
    const onJoined = (p: JoinedPayload) => {
      setMe(p.user)
      setAnnouncement(p.announcement || '')
    }
    const onHistory = (p: { messages: Message[] }) => setMessages(p.messages)
    const onNew = (m: Message) => setMessages((prev) => [...prev, m])
    const onRecalled = (p: { id: number; by: string }) => {
      setMessages((prev) => prev.filter((m) => m.id !== p.id))
      showToast(`${p.by} 撤回了一条消息`)
    }
    const onPresence = (p: Presence) => {
      setOnline(p.online_count)
      setOnlineIds(p.online_ids || [])
    }
    const onMembersChanged = () => { void refreshAll() }
    const onNotice = (p: { text: string }) => showToast(p.text)
    const onAnnouncement = (p: { text: string }) => setAnnouncement(p.text)
    const onComposeError = (p: { message: string }) => showToast(p.message)
    // D5–D7：DSH 正在输入 / 逐字片段 / 完整消息 / 已读不回
    const onDshTyping = (p: DshTypingPayload) => {
      setDshTyping(!!p?.active)
      setDshDraft('')
    }
    const onDshDelta = (p: DshDeltaPayload) => setDshDraft((prev) => prev + (p?.text || ''))
    const onDshDone = (m: Message) => {
      setMessages((prev) => [...prev, m])
      setDshTyping(false)
      setDshDraft('')
    }
    const onDshSilent = () => { setDshTyping(false); setDshDraft('') }
    const onAuthInvalid = (p: { reason?: string }) => {
      resetToAuth()
      showToast(p?.reason || '登录已失效，请重新登录')
    }

    socket.on('connect', onConnect)
    socket.on('joined', onJoined)
    socket.on('history', onHistory)
    socket.on('message:new', onNew)
    socket.on('message:recalled', onRecalled)
    socket.on('presence', onPresence)
    socket.on('members:changed', onMembersChanged)
    socket.on('system:notice', onNotice)
    socket.on('announcement', onAnnouncement)
    socket.on('compose:error', onComposeError)
    socket.on('dsh:typing', onDshTyping)
    socket.on('dsh:delta', onDshDelta)
    socket.on('dsh:done', onDshDone)
    socket.on('dsh:silent', onDshSilent)
    socket.on('auth:invalid', onAuthInvalid)
    return () => {
      socket.off('connect', onConnect)
      socket.off('joined', onJoined)
      socket.off('history', onHistory)
      socket.off('message:new', onNew)
      socket.off('message:recalled', onRecalled)
      socket.off('presence', onPresence)
      socket.off('members:changed', onMembersChanged)
      socket.off('system:notice', onNotice)
      socket.off('announcement', onAnnouncement)
      socket.off('compose:error', onComposeError)
      socket.off('dsh:typing', onDshTyping)
      socket.off('dsh:delta', onDshDelta)
      socket.off('dsh:done', onDshDone)
      socket.off('dsh:silent', onDshSilent)
      socket.off('auth:invalid', onAuthInvalid)
    }
  }, [showToast, refreshAll, resetToAuth])

  // REST 401 统一回到登录页
  useEffect(() => {
    window.addEventListener('dsh:unauthorized', resetToAuth)
    return () => window.removeEventListener('dsh:unauthorized', resetToAuth)
  }, [resetToAuth])

  // 启动时校验已保存的令牌；顺手清掉匿名时代遗留的本地身份
  useEffect(() => {
    for (const k of ['dsh_client_id', 'dsh_nickname', 'dsh_avatar']) localStorage.removeItem(k)
    if (!getToken()) {
      setPhase('auth')
      return
    }
    void (async () => {
      const self = await fetchMe().catch(() => null)
      if (!self) {
        resetToAuth()
        return
      }
      setMe(self.user)
      setAnnouncement(self.announcement || '')
      setPhase('chat')
      await refreshAll()
      connectSocket()
    })()
  }, [refreshAll, connectSocket, resetToAuth])

  const locate = (id: number) => {
    const el = document.getElementById(`msg-${id}`)
    if (!el) {
      showToast('该消息不在当前已加载的历史中')
      return
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.remove('flash')
    void el.offsetWidth
    el.classList.add('flash')
    window.setTimeout(() => el.classList.remove('flash'), 1600)
  }

  const recall = async (id: number) => {
    try {
      await recallMessage(id)
      setMessages((prev) => prev.filter((m) => m.id !== id))
    } catch (e) {
      showToast(e instanceof Error ? e.message : '撤回失败')
    }
  }

  const onAuthed = (d: AuthResponse) => {
    setMe(d.user)
    setMessages([])
    setDshTyping(false)
    setDshDraft('')
    setPhase('chat')
    void refreshAll()
    connectSocket()
  }

  const handleLogout = async () => {
    try { await logoutApi() } catch { /* 服务端可能已断开连接 */ }
    resetToAuth()
    showToast('已退出登录')
  }

  const onToken = (token: string) => {
    setToken(token)
    connectSocket() // 用新令牌重新入房，替换连接里旧的 token_hash
  }

  if (phase === 'loading') return <div className="loading">正在校验登录状态…</div>
  if (phase === 'auth' || !me) return <AuthScreen onAuthed={onAuthed} />

  const muted = me.muted_until > Date.now()

  return (
    <div className="chat">
      <header>
        <div className="header-left">
          <h2>DSH Chat</h2>
          <span className={`role-badge role-${me.role}`}>{ROLE_LABEL[me.role]}</span>
        </div>
        <div className="toolbar">
          <OnlineBadge count={online} />
          {me.role === 'owner' && (
            <button className="tool-btn" onClick={() => setAiOpen(true)}>⚙ AI 参数</button>
          )}
          <button className="tool-btn" onClick={() => { setMembersOpen(true); void refreshAll() }}>
            👥 成员
          </button>
          <button className="tool-btn" onClick={() => setSearchOpen(true)}>🔍 搜索</button>
          <ExportMenu notify={showToast} />
        </div>
      </header>

      {announcement && <div className="announcement-bar">📢 {announcement}</div>}

      <MessageList
        messages={messages}
        mySessionId={String(me.id)}
        myAvatar={me.avatar || ''}
        me={me}
        members={members}
        typingActive={dshTyping}
        typingText={dshDraft}
        onRecall={(id) => void recall(id)}
      />
      <Composer
        disabled={muted}
        onSend={(text) => socket.emit('message', { text })}
      />

      {searchOpen && <SearchPanel onClose={() => setSearchOpen(false)} onLocate={locate} />}
      {membersOpen && (
        <MembersPanel
          me={me}
          members={members}
          onlineIds={onlineIds}
          announcement={announcement}
          onClose={() => setMembersOpen(false)}
          onChanged={() => void refreshAll()}
          notify={showToast}
          onLogout={() => void handleLogout()}
          onToken={onToken}
          onOpenAi={() => setAiOpen(true)}
        />
      )}
      {aiOpen && <AiParamsPanel onClose={() => setAiOpen(false)} notify={showToast} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
