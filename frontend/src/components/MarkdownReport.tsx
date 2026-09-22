/**
 * Markdown 报告渲染组件
 *
 * 使用 react-markdown + remark-gfm 渲染后端生成的批改报告,
 * 并通过 MUI 组件映射定制排版(标题、引用、列表、加粗高亮等),
 * 使报告在 Material Design 界面中保持精致可读。
 *
 * 结构化批改意见(2026-09-18):
 * - 后端按【原句/修正/错因解析(小提示)】输出独立引用块;
 * - 本组件按引用块首行粗体关键词渲染三色 Callout:
 *   修正=success,错因解析/小提示=info,原句/原文=primary;
 * - 明/暗主题均使用主题色 token,打印样式保持边框可辨。
 */

import { Box, alpha } from '@mui/material'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  markdown: string
}

/** 递归提取节点中的纯文本 */
function flattenText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(flattenText).join('')
  if (node && typeof node === 'object' && 'props' in (node as { props?: unknown })) {
    return flattenText((node as { props: { children?: ReactNode } }).props?.children)
  }
  return ''
}

/** 找到节点中第一个加粗文本(用于判定引用块类型) */
function firstStrongText(node: ReactNode): string {
  if (!node || typeof node !== 'object') return ''
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = firstStrongText(child)
      if (found) return found
    }
    return ''
  }
  const element = node as { type?: unknown; props?: { children?: ReactNode } }
  if (!element.props) return ''
  if (element.type === 'strong') return flattenText(element.props.children).trim()
  return firstStrongText(element.props.children ?? null)
}

/** 引用块 -> 章节化 Callout(三色语义:修正 / 解析 / 原句) */
function ReportBlockquote({ children }: { children?: ReactNode }) {
  const label = firstStrongText(children ?? null)
  const tone = label.includes('修正')
    ? 'success.main'
    : label.includes('错因解析') || label.includes('小提示')
      ? 'info.main'
      : 'primary.main'
  return (
    <Box
      sx={{
        borderLeft: '4px solid',
        borderColor: tone,
        borderRadius: '0 10px 10px 0',
        px: 2,
        py: 1,
        my: 1.25,
        mx: 0,
        backgroundColor: (theme) =>
          alpha(
            tone === 'success.main'
              ? theme.palette.success.main
              : tone === 'info.main'
                ? theme.palette.info.main
                : theme.palette.primary.main,
            theme.palette.mode === 'dark' ? 0.14 : 0.07,
          ),
        '& p': { my: 0.6, fontSize: 14, lineHeight: 1.75 },
        '& p:first-of-type': { fontWeight: 600 },
        '& ul, & ol': { my: 0.5, pl: 2.6 },
        '& li': { fontSize: 13.5, lineHeight: 1.75, mb: 0.4 },
        '& li strong': { color: 'text.secondary', fontWeight: 700 },
      }}
    >
      {children}
    </Box>
  )
}

export default function MarkdownReport({ markdown }: Props) {
  return (
    <Box
      sx={{
        '& h1': { fontSize: 22, fontWeight: 800, mb: 1.5, letterSpacing: '-0.01em' },
        '& h2': { fontSize: 18, fontWeight: 700, mt: 3, mb: 1 },
        '& h3': {
          fontSize: 16,
          fontWeight: 700,
          mt: 3,
          mb: 1.5,
          pb: 0.8,
          borderBottom: '1px solid',
          borderColor: 'divider',
        },
        '& h4': { fontSize: 14.5, fontWeight: 700, mt: 2, mb: 0.5 },
        '& p': { fontSize: 14.5, lineHeight: 1.8, my: 1 },
        '& strong': { fontWeight: 700 },
        '& ul, & ol': { pl: 3, my: 1 },
        '& li': { fontSize: 14.5, lineHeight: 1.9 },
        '& li > ul, & li > ol': { my: 0.25 },
        '& hr': {
          border: 0,
          borderTop: '1px dashed',
          borderColor: 'divider',
          my: 2.5,
        },
        '& code': {
          bgcolor: 'action.hover',
          borderRadius: 1,
          px: 0.6,
          py: 0.2,
          fontSize: 13,
          fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
        },
        '& table': {
          borderCollapse: 'collapse',
          width: '100%',
          my: 1.5,
          '& th, & td': { border: '1px solid', borderColor: 'divider', px: 1.2, py: 0.6, fontSize: 13.5 },
          '& th': { bgcolor: 'action.hover', fontWeight: 700 },
        },
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          blockquote: (props) => <ReportBlockquote>{props.children}</ReportBlockquote>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </Box>
  )
}
