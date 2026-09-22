/**
 * 转录原文面板(审阅页左侧「作文原图」下方,需求一)
 *
 * 能力:
 * - 展示转录全文;处于"批注核对"模式时按错因类别渲染"颜色 + 线型"装饰
 *   (与右栏图例/清单双向联动,点击片段弹出内联批注);
 * - 悬停预览(类别 + 原句 → 修正),点击打开完整 Popover(含复制修正);
 * - 教师可就地编辑转录原文(保存 → 后端写回并重渲染系统报告;取消恢复当前值);
 * - 未能定位的错误在底部以兜底清单展示(信息不丢失)。
 *
 * 说明:后端的区间定位算法保持不变,本组件仅做呈现层增强;
 * 缺少 canonical_type 等新字段的历史数据自动降级为兜底样式,不会报错。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
  alpha,
} from '@mui/material'
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined'
import CheckOutlinedIcon from '@mui/icons-material/CheckOutlined'
import ContentCopyOutlinedIcon from '@mui/icons-material/ContentCopyOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import SaveOutlinedIcon from '@mui/icons-material/SaveOutlined'
import { extractErrorMessage, updateTranscript } from '../api/client'
import type { AnnotationResponse, TaskDetail } from '../types'
import { errorDecorationSx, getErrorStyle } from '../constants/errorStyle'
import { splitExplanation } from '../utils/explanation'

/** 转录不确定标记:[unleserlich] / [unleserlich×n] / [unsicher:猜测文本] */
const MARKER_RE = /\[unleserlich(?:×\d+)?\]|\[unsicher:([^\]]*)\]/g

/** 把不确定标记渲染为醒目样式:红=无法辨认;警告色=低把握猜测(悬停提示对照原图) */
function renderWithMarkers(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  MARKER_RE.lastIndex = 0
  while ((match = MARKER_RE.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const raw = match[0]
    const guess = match[1]
    nodes.push(
      <Tooltip
        key={`${keyPrefix}-m${match.index}`}
        placement="top"
        title={
          guess !== undefined
            ? '低把握度识别,请对照原图核对'
            : '此处无法辨认,请对照原图核对'
        }
      >
        <Box
          component="span"
          sx={
            guess !== undefined
              ? {
                  bgcolor: 'warning.main',
                  color: 'warning.contrastText',
                  borderRadius: 1,
                  px: 0.8,
                  py: 0.1,
                  fontWeight: 600,
                  textDecoration: 'underline dotted',
                  cursor: 'help',
                }
              : {
                  bgcolor: 'error.main',
                  color: 'error.contrastText',
                  borderRadius: 1,
                  px: 0.8,
                  py: 0.1,
                  fontWeight: 600,
                  cursor: 'help',
                }
          }
        >
          {guess !== undefined ? guess || '?' : raw}
        </Box>
      </Tooltip>,
    )
    last = match.index + raw.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

interface Props {
  task: TaskDetail
  annotations: AnnotationResponse | null
  /** 是否处于"批注核对"模式(开启装饰与交互) */
  annotated: boolean
  /** 类别筛选(命中类别高亮,其余淡化) */
  filter: string | null
  activeIndex: number | null
  onActiveChange: (index: number | null) => void
  registerSpan: (index: number, el: HTMLElement | null) => void
  onSaved: (task: TaskDetail) => void
}

export default function TranscriptPanel({
  task,
  annotations,
  annotated,
  filter,
  activeIndex,
  onActiveChange,
  registerSpan,
  onSaved,
}: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)
  const [copied, setCopied] = useState(false)
  const localRefs = useRef<Map<number, HTMLElement>>(new Map())

  const transcribed = task.result?.transcribed_text ?? ''

  /** 按定位区间把转录全文切分为"普通段 + 错误段" */
  const parts = useMemo(() => {
    if (!annotations) return [{ text: transcribed, errorIndex: null as number | null }]
    const result: Array<{ text: string; errorIndex: number | null }> = []
    let cursor = 0
    for (const seg of annotations.segments) {
      if (seg.start > cursor) {
        result.push({ text: transcribed.slice(cursor, seg.start), errorIndex: null })
      }
      result.push({ text: transcribed.slice(seg.start, seg.end), errorIndex: seg.error_index })
      cursor = seg.end
    }
    if (cursor < transcribed.length) {
      result.push({ text: transcribed.slice(cursor), errorIndex: null })
    }
    return result
  }, [transcribed, annotations])

  /** 外部聚焦(右栏清单/上一处下一处)→ 滚动到片段并打开内联批注 */
  useEffect(() => {
    if (activeIndex == null || !annotated) {
      setAnchorEl(null)
      return
    }
    const el = localRefs.current.get(activeIndex)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setAnchorEl(el)
    setCopied(false)
  }, [activeIndex, annotated])

  const activeItem =
    annotated && activeIndex != null && annotations ? annotations.annotations[activeIndex] : null
  const activeStyle = getErrorStyle(activeItem?.canonical_type)

  /** 进入编辑态(草稿 = 当前值) */
  const openEdit = () => {
    setDraft(transcribed)
    setError(null)
    setNotice(null)
    setEditing(true)
  }

  /** 取消编辑(恢复为当前值) */
  const cancelEdit = () => {
    setEditing(false)
    setDraft('')
    setError(null)
  }

  /** 保存转录(后端写回并重渲染系统报告;教师编辑版不受影响) */
  const saveEdit = async () => {
    const text = draft.trim()
    if (!text) {
      setError('转录原文不能为空')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await updateTranscript(task.id, text)
      onSaved(updated)
      setEditing(false)
      setNotice('转录原文已保存;系统报告与批注核对已同步刷新')
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const handleCopyFix = async () => {
    if (!activeItem?.corrected_text) return
    await navigator.clipboard.writeText(activeItem.corrected_text)
    setCopied(true)
  }

  return (
    <Paper variant="outlined" sx={{ borderRadius: 3, overflow: 'hidden' }}>
      {/* 头部:标题 + 编辑操作 */}
      <Box
        sx={{
          px: 2,
          py: 1.2,
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          borderBottom: 1,
          borderColor: 'divider',
          flexWrap: 'wrap',
        }}
      >
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          转录原文
        </Typography>
        {annotated && annotations && <Chip size="small" color="primary" variant="outlined" label="批注核对中" />}
        {!editing ? (
          <Button size="small" startIcon={<EditOutlinedIcon fontSize="small" />} onClick={openEdit} disabled={!transcribed}>
            编辑转录
          </Button>
        ) : (
          <>
            <Button
              size="small"
              variant="contained"
              startIcon={saving ? <CircularProgress size={14} color="inherit" /> : <SaveOutlinedIcon fontSize="small" />}
              disabled={saving}
              onClick={() => void saveEdit()}
            >
              保存
            </Button>
            <Button size="small" color="inherit" startIcon={<CancelOutlinedIcon fontSize="small" />} disabled={saving} onClick={cancelEdit}>
              取消
            </Button>
          </>
        )}
      </Box>

      <Box sx={{ p: 2 }}>
        {notice && (
          <Alert severity="success" sx={{ mb: 1.5 }} onClose={() => setNotice(null)}>
            {notice}
          </Alert>
        )}
        {error && (
          <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {task.ocr_result?.recognition_quality === 'low' && (
          <Alert severity="warning" sx={{ mb: 1.5 }}>
            识别准确率较低,建议人工核对
            {task.ocr_result?.quality_note ? `(${task.ocr_result.quality_note})` : ''}。
            不确定处已用 [unsicher:…] / [unleserlich] 标注,请对照原图复查;也可点「编辑转录」直接修正。
          </Alert>
        )}

        {editing ? (
          <Box>
            <TextField
              multiline
              minRows={10}
              maxRows={22}
              fullWidth
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              sx={{ mb: 1, '& textarea': { fontSize: 14, lineHeight: 1.9 } }}
              helperText={`${draft.length} 字符;保存后将重渲染系统报告(教师编辑版不被覆盖),批注核对按新文本重新定位`}
            />
          </Box>
        ) : annotated && annotations ? (
          <Box sx={{ whiteSpace: 'pre-wrap', lineHeight: 2.3, fontSize: 15, minHeight: 120 }}>
            {parts.map((part, i) =>
              part.errorIndex === null ? (
                <span key={i}>{renderWithMarkers(part.text, `p${i}`)}</span>
              ) : (
                (() => {
                  const item = annotations.annotations[part.errorIndex as number]
                  const style = getErrorStyle(item?.canonical_type)
                  const dimmed = Boolean(filter) && item?.canonical_type !== filter
                  return (
                    <Tooltip
                      key={i}
                      placement="top"
                      enterDelay={350}
                      title={
                        item
                          ? `${item.category_label}:${item.original_text}${item.corrected_text ? ` → ${item.corrected_text}` : ''}`
                          : ''
                      }
                    >
                      <Box
                        component="span"
                        ref={(el: HTMLElement | null) => {
                          const index = part.errorIndex as number
                          if (el) localRefs.current.set(index, el)
                          else localRefs.current.delete(index)
                          registerSpan(index, el)
                        }}
                        onClick={() => onActiveChange(part.errorIndex as number)}
                        sx={{
                          cursor: 'pointer',
                          opacity: dimmed ? 0.32 : 1,
                          transition: 'opacity .15s',
                          ...errorDecorationSx(style),
                          ...(activeIndex === part.errorIndex
                            ? { backgroundColor: alpha(style.color, 0.2) }
                            : { '&:hover': { backgroundColor: alpha(style.color, 0.12) } }),
                        }}
                      >
                        {part.text}
                      </Box>
                    </Tooltip>
                  )
                })()
              ),
            )}
          </Box>
        ) : (
          <Box sx={{ whiteSpace: 'pre-wrap', lineHeight: 2, fontSize: 14.5, minHeight: 120, color: transcribed ? 'text.primary' : 'text.disabled' }}>
            {transcribed
              ? renderWithMarkers(transcribed, 't')
              : '(暂无转录原文 —— 任务完成批改后可查看并编辑)'}
          </Box>
        )}

        {/* 未定位错误兜底清单(批注模式下展示) */}
        {annotated && annotations && annotations.unlocated.length > 0 && (
          <Box sx={{ mt: 2, p: 1.5, bgcolor: 'action.hover', borderRadius: 2 }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
              以下 {annotations.unlocated.length} 条错误未能定位到原文片段(可能因转录与错误片段不一致),以清单形式列出:
            </Typography>
            <Stack spacing={0.5} sx={{ mt: 0.8 }}>
              {annotations.unlocated.map((idx) => {
                const item = annotations.annotations[idx]
                if (!item) return null
                const style = getErrorStyle(item.canonical_type)
                return (
                  <Box
                    key={idx}
                    onClick={() => onActiveChange(idx)}
                    sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap', cursor: 'pointer' }}
                  >
                    <Box component="span" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: style.color, flexShrink: 0, alignSelf: 'center' }} />
                    <Typography variant="body2" color="text.secondary">
                      <b>{item.category_label}</b>:{item.original_text}
                      {item.corrected_text ? ` → ${item.corrected_text}` : ''}
                    </Typography>
                  </Box>
                )
              })}
            </Stack>
          </Box>
        )}
      </Box>

      {/* 内联批注 Popover */}
      <Popover
        open={Boolean(anchorEl) && Boolean(activeItem)}
        anchorEl={anchorEl}
        onClose={() => onActiveChange(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{ paper: { sx: { borderRadius: 3, mt: 0.5 } } }}
      >
        {activeItem && (
          <Box sx={{ p: 2, maxWidth: 400 }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <Chip size="small" label={activeItem.category_label} sx={{ bgcolor: activeStyle.color, color: '#fff', fontWeight: 600 }} />
              <Typography variant="caption" color="text.secondary">
                错误 #{activeItem.index + 1} · {activeItem.error_type}
              </Typography>
            </Stack>
            <Typography variant="caption" sx={{ fontWeight: 700, color: 'text.secondary' }}>
              原文
            </Typography>
            <Typography variant="body2" sx={{ mb: 0.6 }}>
              ❌ {activeItem.original_text}
            </Typography>
            {activeItem.corrected_text && (
              <>
                <Typography variant="caption" sx={{ fontWeight: 700, color: 'text.secondary' }}>
                  修正
                </Typography>
                <Typography variant="body2" sx={{ mb: 0.6 }}>
                  ✅ {activeItem.corrected_text}
                </Typography>
              </>
            )}
            {activeItem.explanation && (() => {
              const parts = splitExplanation(activeItem.explanation)
              return (
                <Box sx={{ mt: 0.75, pt: 0.75, borderTop: '1px dashed', borderColor: 'divider' }}>
                  <Typography variant="caption" sx={{ fontWeight: 700, color: 'text.secondary', display: 'block', mb: 0.4 }}>
                    错因解析
                  </Typography>
                  {parts.rule && (
                    <Typography variant="body2" sx={{ mb: 0.4 }}>
                      <b>规则:</b>
                      {parts.rule}
                    </Typography>
                  )}
                  {parts.steps.map((step, i) => (
                    <Typography key={`s${i}`} variant="body2" sx={{ mb: 0.4 }}>
                      <b>推理:</b>
                      {step}
                    </Typography>
                  ))}
                  {parts.notes.map((note, i) => (
                    <Typography key={`n${i}`} variant="body2" color="text.secondary" sx={{ mb: 0.4 }}>
                      <b>说明:</b>
                      {note}
                    </Typography>
                  ))}
                </Box>
              )
            })()}
            {activeItem.corrected_text && (
              <Button
                size="small"
                startIcon={copied ? <CheckOutlinedIcon /> : <ContentCopyOutlinedIcon />}
                onClick={() => void handleCopyFix()}
                sx={{ mt: 0.5 }}
              >
                {copied ? '已复制' : '复制修正'}
              </Button>
            )}
          </Box>
        )}
      </Popover>
    </Paper>
  )
}
