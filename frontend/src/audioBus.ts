/** 全局唯一播放器：同一时刻只播一条语音，切换时自动停上一条。
 *
 * 为什么不用每气泡一个 <audio>：iOS Safari 对同时存在的多个 audio 实例很挑，
 * 而且用户连续点两条时会叠着响。集中一个实例最省心。
 */
export interface AudioState {
  id: number | null
  playing: boolean
  /** 0~1，播放进度；未知时 -1 */
  progress: number
}

let audio: HTMLAudioElement | null = null
let state: AudioState = { id: null, playing: false, progress: -1 }
const subs = new Set<(s: AudioState) => void>()

function emit() {
  for (const fn of subs) fn(state)
}

function ensure(): HTMLAudioElement {
  if (audio) return audio
  const el = new Audio()
  el.preload = 'none'
  el.addEventListener('ended', () => {
    state = { id: null, playing: false, progress: -1 }
    emit()
  })
  el.addEventListener('play', () => {
    state = { ...state, playing: true }
    emit()
  })
  el.addEventListener('pause', () => {
    if (state.playing) {
      state = { ...state, playing: false }
      emit()
    }
  })
  el.addEventListener('timeupdate', () => {
    if (!el.duration || !isFinite(el.duration)) return
    state = { ...state, progress: el.currentTime / el.duration }
    emit()
  })
  audio = el
  return el
}

export function toggleAudio(id: number, url: string): void {
  const el = ensure()
  if (state.id === id) {
    if (el.paused) void el.play().catch(() => {})
    else el.pause()
    return
  }
  el.pause()
  el.src = url
  el.currentTime = 0
  state = { id, playing: false, progress: 0 }
  emit()
  void el.play().catch(() => {
    state = { id, playing: false, progress: -1 }
    emit()
  })
}

export function stopAudio(): void {
  if (audio && !audio.paused) audio.pause()
}

export function subscribeAudio(fn: (s: AudioState) => void): () => void {
  subs.add(fn)
  return () => { subs.delete(fn) }
}

export function audioState(): AudioState {
  return state
}
