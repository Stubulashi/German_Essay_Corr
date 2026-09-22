/**
 * 全局错误边界(防白屏兜底)
 *
 * 任何渲染期异常被捕获后,整页白屏退化为“可读的错误提示卡片 + 刷新按钮”。
 * 刻意不依赖 MUI 主题(内联硬色样式),即使主题本身出错也能正常展示。
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 保留到控制台便于教师反馈排查(不弹窗打断)
    console.error('[ErrorBoundary] 页面渲染异常:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#F4F6FB',
          color: '#1A1D2B',
          fontFamily: '-apple-system, "Segoe UI", "Noto Sans SC", sans-serif',
          padding: 24,
        }}
      >
        <div
          style={{
            maxWidth: 560,
            width: '100%',
            background: '#FFFFFF',
            border: '1px solid rgba(63, 81, 181, 0.16)',
            borderRadius: 16,
            padding: '28px 28px 20px',
            boxShadow: '0 1px 2px rgba(26, 29, 43, 0.04), 0 8px 24px -12px rgba(63, 81, 181, 0.12)',
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>
            页面出现异常,未能正常渲染
          </div>
          <div style={{ fontSize: 13.5, lineHeight: 1.7, color: '#5A6072', marginBottom: 12 }}>
            可先点击下方按钮刷新重试;若反复出现,请双击「一键自检」检查运行环境,
            并将下面的错误信息反馈给维护人员。
          </div>
          <pre
            style={{
              fontSize: 12,
              lineHeight: 1.6,
              color: '#B3261E',
              background: '#FDF2F2',
              border: '1px solid rgba(179, 38, 30, 0.18)',
              borderRadius: 10,
              padding: '10px 12px',
              margin: 0,
              maxHeight: 180,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}
          >
            {String(error?.message || error)}
          </pre>
          <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
            <button
              onClick={() => window.location.reload()}
              style={{
                border: 'none',
                borderRadius: 10,
                padding: '9px 20px',
                fontSize: 14,
                fontWeight: 600,
                color: '#FFFFFF',
                background: '#3F51B5',
                cursor: 'pointer',
              }}
            >
              刷新页面
            </button>
            <button
              onClick={() => {
                this.setState({ error: null })
              }}
              style={{
                borderRadius: 10,
                padding: '9px 20px',
                fontSize: 14,
                fontWeight: 600,
                color: '#3F51B5',
                background: 'transparent',
                border: '1px solid rgba(63, 81, 181, 0.4)',
                cursor: 'pointer',
              }}
            >
              尝试继续
            </button>
          </div>
        </div>
      </div>
    )
  }
}
