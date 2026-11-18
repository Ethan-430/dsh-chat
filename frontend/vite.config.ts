import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 生产：前端由 FastAPI 同端口托管，无需 proxy。
// 开发：vite dev server 把 /socket.io 与 /api 代理到后端 8000（含 ws）。
// PWA 用静态 manifest + 手写 Service Worker（public/sw.js），不依赖 workbox，构建更稳、供应链面更小。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/socket.io': { target: 'http://localhost:8000', ws: true },
      '/api': { target: 'http://localhost:8000' },
    },
  },
})
