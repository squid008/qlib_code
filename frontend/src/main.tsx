import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// 过滤 recharts 的已知良性错误：
// recharts 2.15.4 的 ResponsiveContainer 在 ResizeObserver 回调触发时，若容器 ref 尚未赋值，
// 会抛 "Cannot read properties of null (reading 'getBoundingClientRect')"。
// 该错误不影响图表最终渲染，这里仅在全局层面忽略它，避免 WebView/控制台刷屏。
window.addEventListener('error', (e) => {
  const msg = e.message || ''
  if (msg.includes("getBoundingClientRect") && msg.includes("null")) {
    e.preventDefault()
    e.stopPropagation()
  }
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
