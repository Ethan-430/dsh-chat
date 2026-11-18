// 仅在生产构建注册 Service Worker（开发期跳过，避免干扰 HMR）。
// 当前 sw.js 是「自毁型」：它的作用只是清掉历史版本缓存的中毒页面并注销自身，
// 不做任何请求拦截。因此这里不需要 updatefound / reload 逻辑。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // 非 HTTPS 环境不支持 SW；聊天功能不受影响
    })
  })
}
