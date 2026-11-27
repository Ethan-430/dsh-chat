import { useEffect, useMemo, useState } from 'react'
import { Message } from '../types'
import { voiceUrl } from '../api'
import { audioState, subscribeAudio, toggleAudio } from '../audioBus'

interface Props {
  m: Message
  mine: boolean
}

/** 微信式语音气泡：宽窄随时长变化，点一下播放。 */
export default function VoiceBubble({ m, mine }: Props) {
  const [playing, setPlaying] = useState(false)
  const [progress, setProgress] = useState(0)

  useEffect(() => subscribeAudio((s) => {
    const active = s.id === m.id
    setPlaying(active && s.playing)
    setProgress(active && s.progress > 0 ? s.progress : 0)
  }), [m.id])

  // 卸载时若正在播放这条，停掉，避免"看不见的地方还在响"
  useEffect(() => () => {
    const s = audioState()
    if (s.id === m.id && s.playing) toggleAudio(m.id, voiceUrl(m.id))
  }, [m.id])

  const seconds = Math.max(1, Math.round((m.audio_duration_ms || 0) / 1000))
  // 微信的宽度规律：1 秒最窄，60 秒最宽
  const width = useMemo(() => 96 + Math.min(seconds, 60) * 3.4, [seconds])
  const bars = useMemo(() => {
    // 固定的伪波形，避免每次渲染跳动；进度用颜色覆盖
    const n = Math.max(8, Math.min(20, Math.round(width / 12)))
    return Array.from({ length: n }, (_, i) => 6 + ((i * 37) % 11))
  }, [width])

  return (
    <button
      type="button"
      className={`voice-bubble ${mine ? 'voice-mine' : 'voice-theirs'} ${playing ? 'playing' : ''}`}
      style={{ width }}
      onClick={() => toggleAudio(m.id, voiceUrl(m.id))}
      aria-label={playing ? '暂停语音' : `播放 ${seconds} 秒语音`}
      title={playing ? '暂停' : '播放'}
    >
      <span className="voice-play" aria-hidden="true">{playing ? '❚❚' : '▶'}</span>
      <span className="voice-wave" aria-hidden="true">
        {bars.map((h, i) => (
          <i
            key={i}
            style={{ height: h }}
            className={progress > 0 && i / bars.length <= progress ? 'on' : ''}
          />
        ))}
      </span>
      <span className="voice-dur">{seconds}"</span>
    </button>
  )
}
