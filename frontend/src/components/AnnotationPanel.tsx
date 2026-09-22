/**
 * 批注核对面板(审阅页右栏"批注核对"视图,需求二)
 *
 * - 图例:按错因类别展示"颜色 + 线型样本 + 数量",点击筛选(其余类别淡化),
 *   再次点击取消;「全部」一键清除筛选;
 * - 错误清单:与左侧转录全文双向联动(点击清单 → 聚焦全文对应片段并弹批注;
 *   在全文点击片段 → 本清单自动高亮并滚动到对应行);未定位条目带标记;
 * - 导航:上一处 / 下一处(支持 Alt + ←/→ 快捷键,由审阅页统一注册);
 * - 工具:一键复制全部修正、按类别导出 Markdown(与打印视图配色一致)。
 *
 * 说明:全部数据来自既有 GET /tasks/{id}/annotation(区间定位后端计算),
 * 本组件零字符串匹配、纯呈现层;历史数据缺 canonical_type 时自动降级为兜底样式。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Button, Divider, Paper, Stack, Tooltip, Typography, alpha } from '@mui/material'
import ArrowDownwardOutlinedIcon from '@mui/icons-material/ArrowDownwardOutlined'
import ArrowUpwardOutlinedIcon from '@mui/icons-material/ArrowUpwardOutlined'
import CheckOutlinedIcon from '@mui/icons-material/CheckOutlined'
import ContentCopyOutlinedIcon from '@mui/icons-material/ContentCopyOutlined'
import DownloadOutlinedIcon from '@mui/icons-material/DownloadOutlined'
import { downloadBlob } from '../api/client'
import type { AnnotationResponse, TaskDetail } from '../types'
import { buildLegend, errorDecorationSx, getErrorStyle } from '../constants/errorStyle'

interface Props {
  task: TaskDetail
  annotations: AnnotationResponse
  filter: string | null
  onFilterChange: (filter: string | null) => void
  activeIndex: number | null
  onActiveChange: (index: number | null) => void
}

export default function AnnotationPanel({
  task,
  annotations,
  filter,
  onFilterChange,
  activeIndex,
  onActiveChange,
}: Props) {
  const [copiedAll, setCopiedAll] = useState(false)
  const rowRefs = useRef<Map<number, HTMLElement>>(new Map())

  const legend = useMemo(() => buildLegend(annotations.annotations), [annotations])
  const locatedSet = useMemo(
    () => new Set(annotations.segments.map((seg) => seg.error_index)),
    [annotations],
  )

  /** 当前聚焦条目在清单中自动高亮并滚动可见 */
  useEffect(() => {
    if (activeIndex == null) return
    const el = rowRefs.current.get(activeIndex)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [activeIndex])

  /** 复制全部修正(清单格式,便于粘贴到备课笔记) */
  const copyAll = async () => {
    const lines = annotations.annotations.map((item, i) =>
      `${i + 1}. [${item.category_label}] ${item.original_text}${item.corrected_text ? ` → ${item.corrected_text}` : ''}`,
    )
    await navigator.clipboard.writeText(lines.join('\n'))
    setCopiedAll(true)
    window.setTimeout(() => setCopiedAll(false), 2000)
  }

  /** 按类别导出 Markdown */
  const exportByCategory = () => {
    const groups = new Map<string, string[]>()
    annotations.annotations.forEach((item) => {
      const key = item.category_label
      const line = `- 原句:${item.original_text}${item.corrected_text ? ` → **修正:** ${item.corrected_text}` : ''}${
        item.explanation ? `(解析:${item.explanation})` : ''
      }`
      groups.set(key, [...(groups.get(key) ?? []), line])
    })
    const date = new Date().toISOString().slice(0, 10)
    const sections = [...groups.entries()].map(
      ([label, lines]) => `## ${label} · ${lines.length} 条\n${lines.join('\n')}`,
    )
    const content = [
      `# 批注核对导出 - ${task.student_name || '未知'}(任务 #${task.id})`,
      `导出日期:${date} · 共 ${annotations.annotations.length} 条 · 已定位 ${locatedSet.size} 条`,
      '',
      ...sections,
    ].join('\n')
    downloadBlob(
      new Blob([content], { type: 'text/markdown;charset=utf-8' }),
      `批注核对_${task.student_name || '未知'}_${task.id}.md`,
    )
  }

  const locatedIndexes = useMemo(
    () => [...new Set(annotations.segments.map((seg) => seg.error_index))],
    [annotations],
  )
  const currentPos = activeIndex == null ? -1 : locatedIndexes.indexOf(activeIndex)
  const goPrev = () => {
    if (locatedIndexes.length === 0) return
    const next = currentPos <= 0 ? locatedIndexes[locatedIndexes.length - 1] : locatedIndexes[currentPos - 1]
    onActiveChange(next)
  }
  const goNext = () => {
    if (locatedIndexes.length === 0) return
    const next = currentPos === -1 || currentPos >= locatedIndexes.length - 1 ? locatedIndexes[0] : locatedIndexes[currentPos + 1]
    onActiveChange(next)
  }

  return (
    <Box>
      {/* ---------- 工具条 ---------- */}
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<ArrowUpwardOutlinedIcon fontSize="small" />}
          disabled={locatedIndexes.length === 0}
          onClick={goPrev}
        >
          上一处
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={<ArrowDownwardOutlinedIcon fontSize="small" />}
          disabled={locatedIndexes.length === 0}
          onClick={goNext}
        >
          下一处
        </Button>
        <Typography variant="caption" color="text.disabled">快捷键 Alt + ←/→</Typography>
        <Box sx={{ flex: 1 }} />
        <Tooltip title="复制全部修正(原句 → 修正)">
          <Button
            size="small"
            startIcon={copiedAll ? <CheckOutlinedIcon fontSize="small" /> : <ContentCopyOutlinedIcon fontSize="small" />}
            onClick={() => void copyAll()}
          >
            {copiedAll ? '已复制' : '复制修正'}
          </Button>
        </Tooltip>
        <Tooltip title="按错因类别导出 Markdown">
          <Button size="small" startIcon={<DownloadOutlinedIcon fontSize="small" />} onClick={exportByCategory}>
            导出台账
          </Button>
        </Tooltip>
      </Stack>

      {/* ---------- 图例(类别 + 数量,点击筛选) ---------- */}
      <Paper variant="outlined" sx={{ p: 1.25, borderRadius: 2.5, mb: 1.5 }}>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
            图例(点击筛选):
          </Typography>
          <Box
            onClick={() => onFilterChange(null)}
            sx={{
              px: 1,
              py: 0.25,
              borderRadius: 1.5,
              cursor: 'pointer',
              border: 1,
              borderColor: filter == null ? 'primary.main' : 'divider',
              bgcolor: filter == null ? 'action.selected' : 'transparent',
            }}
          >
            <Typography variant="caption">全部</Typography>
          </Box>
          {legend.map((entry) => {
            const selected = filter === entry.canonicalType
            const style = getErrorStyle(entry.canonicalType)
            return (
              <Box
                key={entry.canonicalType}
                onClick={() => onFilterChange(selected ? null : entry.canonicalType)}
                sx={{
                  px: 1,
                  py: 0.25,
                  borderRadius: 1.5,
                  cursor: 'pointer',
                  border: 1,
                  borderColor: selected ? 'primary.main' : 'divider',
                  bgcolor: selected ? 'action.selected' : 'transparent',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.6,
                }}
              >
                <Box component="span" sx={{ fontSize: 13, fontWeight: 600, ...errorDecorationSx(style) }}>
                  {entry.label}
                </Box>
                <Typography variant="caption" color="text.secondary">
                  ×{entry.count}
                </Typography>
              </Box>
            )
          })}
        </Stack>
        <Divider sx={{ my: 1 }} />
        <Typography variant="caption" color="text.secondary">
          共 {annotations.annotations.length} 条错因 · 已定位 {locatedSet.size} 条
          {annotations.unlocated.length > 0 ? ` · 未定位 ${annotations.unlocated.length} 条(见左下角清单)` : ''}
          {filter ? ` · 当前筛选:${filter}` : ''}
        </Typography>
      </Paper>

      {/* ---------- 错误清单(与全文双向联动) ---------- */}
      <Stack spacing={0.6} sx={{ maxHeight: 420, overflowY: 'auto', pr: 0.5 }}>
        {annotations.annotations.map((item, index) => {
          const style = getErrorStyle(item.canonical_type)
          const located = locatedSet.has(index)
          const dimmed = Boolean(filter) && item.canonical_type !== filter
          const active = activeIndex === index
          return (
            <Box
              key={index}
              ref={(el: HTMLElement | null) => {
                if (el) rowRefs.current.set(index, el)
                else rowRefs.current.delete(index)
              }}
              onClick={() => onActiveChange(active ? null : index)}
              sx={{
                p: 1,
                borderRadius: 2,
                cursor: 'pointer',
                border: 1,
                borderColor: active ? 'primary.main' : 'divider',
                bgcolor: active ? 'action.selected' : 'transparent',
                opacity: dimmed ? 0.35 : 1,
                transition: 'opacity .15s',
                '&:hover': { bgcolor: 'action.hover' },
              }}
            >
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="caption" color="text.disabled" sx={{ width: 24 }}>
                  #{index + 1}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    px: 0.8,
                    py: 0.1,
                    borderRadius: 1,
                    bgcolor: style.color,
                    color: '#fff',
                    fontWeight: 600,
                    ...(style.decoration === 'highlight' ? { bgcolor: alpha(style.color, 0.75) } : {}),
                    printColorAdjust: 'exact',
                  }}
                >
                  {item.category_label}
                </Typography>
                {!located && (
                  <Typography variant="caption" color="warning.main">
                    (未定位)
                  </Typography>
                )}
                <Box sx={{ flex: 1 }} />
              </Stack>
              <Typography variant="body2" sx={{ mt: 0.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.original_text}
                {item.corrected_text ? (
                  <Typography component="span" variant="body2" color="success.main">
                    {' '}→ {item.corrected_text}
                  </Typography>
                ) : null}
              </Typography>
            </Box>
          )
        })}
      </Stack>
    </Box>
  )
}
