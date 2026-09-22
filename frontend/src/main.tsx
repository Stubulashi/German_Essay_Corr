import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './print.css'  // 打印样式(#12)

// 应用入口:挂载 React 根节点(ErrorBoundary 捕获渲染异常,避免整页白屏)
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  </React.StrictMode>,
)

// 启动看门狗:极端情况下(如渲染前脚本异常)根节点为空时,给出可读提示而非纯白页
window.setTimeout(() => {
  const root = document.getElementById('root')
  if (root && root.childElementCount === 0) {
    root.innerHTML =
      '<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;' +
      'font-family:-apple-system,Segoe UI,Noto Sans SC,sans-serif;color:#1A1D2B;background:#F4F6FB;">' +
      '<div style="max-width:520px;padding:28px;background:#fff;border:1px solid rgba(63,81,181,.16);' +
      'border-radius:16px;line-height:1.8;">' +
      '<b>页面未能正常渲染。</b><br/>请刷新页面重试;若反复出现,请双击「一键自检」检查运行环境,' +
      '或查看浏览器控制台的错误信息。</div></div>'
  }
}, 3000)
