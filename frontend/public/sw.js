// 自毁型 Service Worker（kill switch）。
//
// 历史问题：旧版 SW 用「缓存优先」托管静态资源，并把根路径响应（含 404 页面）
// 缓存成离线壳；一旦缓存了错误页，用户之后每次打开都只看到缓存的 "Not Found"，
// 网络恢复也不会自愈。
//
// 本项目是可实时聊天的单页应用，离线能力没有价值、错误缓存却会致命。
// 因此这里故意不注册任何 fetch 处理：装好即清空所有缓存、注销自身，页面完全走网络。
// 浏览器更新 SW 时会绕过 SW 直接拉取本文件，所以即使旧 SW 还在也能被替换掉。

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      try {
        const keys = await caches.keys()
        await Promise.all(keys.map((k) => caches.delete(k)))
      } catch (_) {}
      try {
        await self.registration.unregister()
      } catch (_) {}
      try {
        const cs = await self.clients.matchAll({ type: 'window' })
        cs.forEach((c) => {
          if (typeof c.navigate === 'function') c.navigate(c.url)
        })
      } catch (_) {}
    })()
  )
})
