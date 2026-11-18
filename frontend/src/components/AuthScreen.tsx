import { useEffect, useRef, useState } from 'react'
import { ApiError, authStatus, login, register, setToken } from '../api'
import { AVATARS } from '../avatars'
import { AuthResponse, AuthStatus } from '../types'

type Mode = 'login' | 'register'

export default function AuthScreen({ onAuthed }: { onAuthed: (d: AuthResponse) => void }) {
  const [mode, setMode] = useState<Mode>('register')
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [avatar, setAvatar] = useState(AVATARS[0].emoji)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const decided = useRef(false)

  useEffect(() => {
    authStatus()
      .then((s) => {
        setStatus(s)
        if (!decided.current) {
          decided.current = true
          setMode(s.has_owner ? 'login' : 'register')
        }
      })
      .catch(() => {})
  }, [])

  const submit = async () => {
    setError('')
    setBusy(true)
    try {
      const data =
        mode === 'login'
          ? await login({ username: username.trim(), password })
          : await register({
              username: username.trim(),
              password,
              nickname: nickname.trim(),
              avatar,
            })
      setToken(data.token)
      onAuthed(data)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '网络错误，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const switchMode = (m: Mode) => {
    setMode(m)
    setError('')
  }

  return (
    <div className="join auth-join">
      <h1>DSH Chat</h1>
      <p className="sub">4~N 人 + DSH · AI 同处一室闲聊</p>

      {status && !status.has_owner && (
        <div className="auth-callout">
          🌱 群里还没有成员：<strong>第一个注册的账号将成为群主</strong>，可任命管理员并调整 AI 参数。
        </div>
      )}

      <div className="auth-tabs">
        <button className={mode === 'login' ? 'active' : ''} onClick={() => switchMode('login')}>
          登录
        </button>
        <button className={mode === 'register' ? 'active' : ''} onClick={() => switchMode('register')}>
          注册
        </button>
      </div>

      <input
        type="text"
        autoComplete="username"
        placeholder="用户名（3–32 位字母/数字/下划线）"
        value={username}
        maxLength={32}
        onChange={(e) => setUsername(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
      />
      <input
        type="password"
        autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
        placeholder="密码（至少 8 位，含字母和数字）"
        value={password}
        maxLength={128}
        onChange={(e) => setPassword(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
      />

      {mode === 'register' && (
        <>
          <input
            type="text"
            placeholder="昵称（群内显示）"
            value={nickname}
            maxLength={32}
            onChange={(e) => setNickname(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
          />
          <label className="field-label">选个头像</label>
          <div className="avatar-picker">
            {AVATARS.map((a) => (
              <button
                key={a.emoji}
                type="button"
                className={`avatar-option ${avatar === a.emoji ? 'selected' : ''}`}
                style={{ background: a.bg }}
                onClick={() => setAvatar(a.emoji)}
                aria-label={`头像 ${a.emoji}`}
              >
                {a.emoji}
              </button>
            ))}
          </div>
        </>
      )}

      {error && <div className="auth-error">{error}</div>}
      <button className="auth-submit" onClick={submit} disabled={busy}>
        {busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并进入'}
      </button>
      <p className="auth-note">
        密码以 scrypt 加盐哈希存储；服务端不会保存明文，也不会把密码哈希返回给客户端。
      </p>
    </div>
  )
}
