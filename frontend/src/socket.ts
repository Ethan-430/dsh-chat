import { io, Socket } from 'socket.io-client'

// 先完成 /api/auth/me 校验再 connect；连接成功后由 App 发送 join(token)。
// 生产同源（FastAPI 托管前端）；开发期经 vite proxy 转发到 8000。
export const socket: Socket = io({ autoConnect: false })
