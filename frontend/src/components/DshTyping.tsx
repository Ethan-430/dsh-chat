import { DSH_AVATAR } from '../avatars'

interface Props {
  active: boolean
  text: string
}

/** D5–D7：DSH 正在输入 + 逐字追加的临时气泡（不落库，done 后由真实消息替换）。 */
export default function DshTyping({ active, text }: Props) {
  if (!active) return null
  return (
    <div className="msg-row theirs dsh-typing" aria-live="polite">
      <div className="avatar" style={{ background: DSH_AVATAR.bg }} title="DSH · AI">
        {DSH_AVATAR.emoji}
      </div>
      <div className="msg-body">
        <div className="nick">DSH · AI</div>
        <div className="bubble bubble-theirs dsh-typing-bubble">
          {text ? (
            <>
              {text}
              <span className="typing-caret" />
            </>
          ) : (
            <span className="typing-dots" aria-label="DSH 正在输入">
              <i /><i /><i />
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
