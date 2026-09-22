/**
 * 批改审阅页(左右分屏 · Human-in-the-Loop)
 *
 * 左侧:手写作文原图(支持缩放 / 拖拽 / 多页切换);
 * 右侧:根据任务状态展示——
 *   - 处理中:阶段进度;
 *   - 待人工复核(管线 B):OCR 转录校订表单,确认后继续评分;
 *   - 已完成:Markdown 报告(预览 / 编辑双模式,保存与下载);
 *   - 失败:错误信息 + 重试。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  ButtonGroup,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  Switch,
  Tab,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import ArrowBackOutlinedIcon from '@mui/icons-material/ArrowBackOutlined'
import ChevronLeftOutlinedIcon from '@mui/icons-material/ChevronLeftOutlined'
import ChevronRightOutlinedIcon from '@mui/icons-material/ChevronRightOutlined'
import CheckCircleOutlinedIcon from '@mui/icons-material/CheckCircleOutlined'
import DownloadOutlinedIcon from '@mui/icons-material/DownloadOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import ImageNotSupportedOutlinedIcon from '@mui/icons-material/ImageNotSupportedOutlined'
import RateReviewOutlinedIcon from '@mui/icons-material/RateReviewOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import RefreshOutlinedIcon from '@mui/icons-material/RefreshOutlined'
import SaveOutlinedIcon from '@mui/icons-material/SaveOutlined'
import VisibilityOutlinedIcon from '@mui/icons-material/VisibilityOutlined'
import ZoomInOutlinedIcon from '@mui/icons-material/ZoomInOutlined'
import ZoomOutOutlinedIcon from '@mui/icons-material/ZoomOutOutlined'
import RestartAltOutlinedIcon from '@mui/icons-material/RestartAltOutlined'
import { TransformComponent, TransformWrapper } from 'react-zoom-pan-pinch'
import { useNavigate, useParams } from 'react-router-dom'
import {
  confirmOcr,
  extractErrorMessage,
  fetchAnnotation,
  fetchTask,
  fetchTasks,
  retryTask,
  taskImageUrl,
  taskNameCropUrl,
  updateReport,
  updateTaskStudent,
  updateTeacherMessage,
} from '../api/client'
import AnnotationPanel from '../components/AnnotationPanel'
import MarkdownReport from '../components/MarkdownReport'
import RecorrectDialog from '../components/RecorrectDialog'
import TranscriptPanel from '../components/TranscriptPanel'
import StatusChip from '../components/StatusChip'
import type { AnnotationResponse, TaskBrief, TaskDetail } from '../types'
import { TEACHER_MESSAGE_PRESETS } from '../constants/teacherMessages'
import { DETAIL_LABEL, PIPELINE_META, STAGE_LABEL, STANDARD_LABEL } from '../types'

/** 轮询间隔(毫秒):处理中的任务定期刷新 */
const POLL_INTERVAL = 2000

export default function ReviewPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const id = Number(taskId)

  const [task, setTask] = useState<TaskDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  // 图片翻页
  const [pageIndex, setPageIndex] = useState(0)
  // 报告预览 / 编辑模式
  const [reportMode, setReportMode] = useState<'preview' | 'edit'>('preview')
  // 报告视图:教师版(全量分析)/ 学生版(订正单,#11)/ 批注核对(#13)
  const [reportVariant, setReportVariant] = useState<'teacher' | 'student' | 'annotate'>('teacher')
  // 批注数据(懒加载)
  const [annotationData, setAnnotationData] = useState<AnnotationResponse | null>(null)
  const [annotationLoading, setAnnotationLoading] = useState(false)
  const [annotationError, setAnnotationError] = useState<string | null>(null)
  // 报告编辑内容
  const [editedMarkdown, setEditedMarkdown] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  // OCR 复核表单
  const [reviewName, setReviewName] = useState('')
  const [reviewStudentId, setReviewStudentId] = useState('')
  const [reviewText, setReviewText] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  // 打印设置对话框(#12 方向二)
  const [printDialogOpen, setPrintDialogOpen] = useState(false)
  const [printVariant, setPrintVariant] = useState<'teacher' | 'student' | 'both'>('teacher')
  const [printIncludeImages, setPrintIncludeImages] = useState(false)
  const [printIncludeTranscript, setPrintIncludeTranscript] = useState(false)

  // 学生信息纠错(事后修正姓名/学号;同步错题本/档案/统计对齐)
  const [studentDialogOpen, setStudentDialogOpen] = useState(false)
  const [editStudentName, setEditStudentName] = useState('')
  const [editStudentId, setEditStudentId] = useState('')
  const [savingStudent, setSavingStudent] = useState(false)
  const [studentError, setStudentError] = useState<string | null>(null)

  // 重新批改(需求一:已完成任务原地重跑)
  const [recorrectOpen, setRecorrectOpen] = useState(false)
  // 教师寄语编辑(学生版报告;含 10 条场景化预设语)
  const [msgOpen, setMsgOpen] = useState(false)
  const [msgText, setMsgText] = useState('')
  const [msgError, setMsgError] = useState<string | null>(null)
  const [savingMsg, setSavingMsg] = useState(false)
  // 批注核对联动(左栏转录全文 ⇄ 右栏清单)
  const [annotationFilter, setAnnotationFilter] = useState<string | null>(null)
  const [activeErrorIndex, setActiveErrorIndex] = useState<number | null>(null)
  const spanRefs = useRef<Map<number, HTMLElement>>(new Map())

  const pollTimer = useRef<number | null>(null)
  // 连续审阅:兄弟列表(同批次优先,否则同班级+作业;范围键不变时缓存复用)
  const [siblings, setSiblings] = useState<TaskBrief[]>([])

  /** 拉取任务详情 */
  const loadTask = useCallback(async () => {
    try {
      const t = await fetchTask(id)
      setTask(t)
      setLoadError(null)
      // 任务未完成时清空批注缓存(重新批改重跑期间不展示旧定位)
      if (t.status !== 'COMPLETED') setAnnotationData(null)
      return t
    } catch (e) {
      setLoadError(extractErrorMessage(e))
      return null
    }
  }, [id])

  // 首次加载
  useEffect(() => {
    if (!Number.isFinite(id)) return
    loadTask()
  }, [id, loadTask])

  // 处理中 -> 轮询
  useEffect(() => {
    const shouldPoll =
      task !== null && (task.status === 'PENDING' || task.status === 'PROCESSING')
    if (shouldPoll) {
      pollTimer.current = window.setInterval(async () => {
        const t = await loadTask()
        if (t && t.status !== 'PENDING' && t.status !== 'PROCESSING') {
          if (pollTimer.current) window.clearInterval(pollTimer.current)
        }
      }, POLL_INTERVAL)
    }
    return () => {
      if (pollTimer.current) window.clearInterval(pollTimer.current)
    }
  }, [task?.status, loadTask, task])

  // 进入待复核 / 完成后:初始化表单与编辑区
  useEffect(() => {
    if (!task) return
    if (task.status === 'WAITING_REVIEW' && task.ocr_result) {
      setReviewName(task.ocr_result.student_name ?? '')
      setReviewStudentId(task.ocr_result.student_id ?? '')
      setReviewText(task.ocr_result.transcribed_text ?? '')
    }
    if (task.status === 'COMPLETED') {
      const markdown = task.edited_report ?? task.result?.markdown_report ?? ''
      setEditedMarkdown(markdown)
      setReportMode('preview')
    }
  }, [task?.status, task?.ocr_result, task?.edited_report, task])

  // 切换任务时重置全部视图状态(防止跨任务串数据)
  useEffect(() => {
    setAnnotationData(null)
    setAnnotationError(null)
    setAnnotationLoading(false)
    setActiveErrorIndex(null)
    setAnnotationFilter(null)
    setReportVariant('teacher')
    setReportMode('preview')
    setPageIndex(0)
    setSaveMsg(null)
    setConfirmError(null)
    setStudentError(null)
    setMsgError(null)
    setReviewName('')
    setReviewStudentId('')
    setReviewText('')
    setRecorrectOpen(false)
    setMsgOpen(false)
    setPrintDialogOpen(false)
    setStudentDialogOpen(false)
  }, [id])

  // 连续审阅:按范围键拉取兄弟列表(同批次 → 同班级+作业 → 全局)
  const rangeKey = task
    ? task.batch_id
      ? `batch:${task.batch_id}`
      : `scope:${task.class_id ?? 'x'}:${task.assignment_name ?? ''}`
    : ''
  useEffect(() => {
    if (!task || !rangeKey) return
    let cancelled = false
    setSiblings([]) // 范围变化时先清空,加载完成后填入
    void fetchTasks({
      batch_id: task.batch_id ?? undefined,
      class_id: task.batch_id ? undefined : (task.class_id ?? undefined),
      limit: 200,
    })
      .then((data) => {
        if (cancelled) return
        const ordered = [...data.items].sort((a, b) =>
          a.created_at === b.created_at ? a.id - b.id : a.created_at.localeCompare(b.created_at),
        )
        setSiblings(ordered)
      })
      .catch(() => {
        if (!cancelled) setSiblings([])
      })
    return () => {
      cancelled = true
    }
    // 仅随范围键/范围参数变化重新拉取;同范围切换任务不重复请求
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey, task?.batch_id, task?.class_id])

  // 当前位置与相邻目标(顺序:创建时间升序、id 升序)
  const siblingIndex = useMemo(() => siblings.findIndex((s) => s.id === id), [siblings, id])
  const prevId = siblingIndex > 0 ? siblings[siblingIndex - 1].id : null
  const nextId = siblingIndex >= 0 && siblingIndex < siblings.length - 1 ? siblings[siblingIndex + 1].id : null
  const goSibling = useCallback(
    (target: number | null) => {
      if (target === null) return
      // replace:连续切换不堆积浏览器历史,返回键直回队列页
      navigate(`/review/${target}`, { replace: true })
    },
    [navigate],
  )

  // 键盘快捷键:← / → 连续审阅(输入框聚焦或对话框打开时忽略)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      const target = e.target as HTMLElement | null
      if (target) {
        const tag = target.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) {
          return
        }
      }
      if (document.querySelector('[role="dialog"]')) return
      if (e.key === 'ArrowLeft') goSibling(prevId)
      else goSibling(nextId)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [goSibling, prevId, nextId])

  // 批注视图:懒加载定位数据(后端计算区间,前端零匹配)
  useEffect(() => {
    if (reportVariant !== 'annotate' || !task || task.status !== 'COMPLETED') return
    if (annotationData || annotationLoading) return
    setAnnotationLoading(true)
    setAnnotationError(null)
    fetchAnnotation(task.id)
      .then(setAnnotationData)
      .catch((e) => setAnnotationError(extractErrorMessage(e)))
      .finally(() => setAnnotationLoading(false))
 }, [reportVariant, task, annotationData, annotationLoading])

  /** 批注联动:当前处于"已定位"的错误索引(上一处/下一处导航用) */
  const locatedErrorIndexes = useMemo(
    () => [...new Set((annotationData?.segments ?? []).map((seg) => seg.error_index))],
    [annotationData],
  )
  const goPrevError = useCallback(() => {
    if (locatedErrorIndexes.length === 0) return
    const pos = activeErrorIndex == null ? -1 : locatedErrorIndexes.indexOf(activeErrorIndex)
    setActiveErrorIndex(pos <= 0 ? locatedErrorIndexes[locatedErrorIndexes.length - 1] : locatedErrorIndexes[pos - 1])
  }, [locatedErrorIndexes, activeErrorIndex])
  const goNextError = useCallback(() => {
    if (locatedErrorIndexes.length === 0) return
    const pos = activeErrorIndex == null ? -1 : locatedErrorIndexes.indexOf(activeErrorIndex)
    setActiveErrorIndex(
      pos === -1 || pos >= locatedErrorIndexes.length - 1 ? locatedErrorIndexes[0] : locatedErrorIndexes[pos + 1],
    )
  }, [locatedErrorIndexes, activeErrorIndex])

  // 键盘快捷键:Alt + ←/→ 上一处/下一处(输入框内不触发)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || !annotationData) return
      const tag = (document.activeElement?.tagName ?? '').toLowerCase()
      if (tag === 'input' || tag === 'textarea') return
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        goPrevError()
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault()
        goNextError()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [annotationData, goPrevError, goNextError])

  if (!Number.isFinite(id)) {
    return <Alert severity="error">无效的任务 ID</Alert>
  }
  if (loadError) {
    return (
      <Alert severity="error" sx={{ borderRadius: 3 }}>
        {loadError}
      </Alert>
    )
  }
  if (!task) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', py: 10 }}>
        <CircularProgress />
      </Box>
    )
  }

  const meta = PIPELINE_META[task.pipeline_used ?? task.pipeline_choice]
  const imageCount = task.image_paths.length

  /** 提交 OCR 复核 */
  const handleConfirmOcr = async () => {
    setConfirmError(null)
    if (!reviewText.trim()) {
      setConfirmError('转录文本不能为空')
      return
    }
    setConfirming(true)
    try {
      const updated = await confirmOcr(id, {
        student_name: reviewName.trim() || '未知',
        student_id: reviewStudentId.trim() || null,
        transcribed_text: reviewText,
      })
      setTask(updated)
    } catch (e) {
      setConfirmError(extractErrorMessage(e))
    } finally {
      setConfirming(false)
    }
  }

  /** 保存编辑后的报告 */
  const handleSaveReport = async () => {
    setSaving(true)
    setSaveMsg(null)
    try {
      const updated = await updateReport(id, editedMarkdown)
      setTask(updated)
      setSaveMsg('已保存教师编辑版报告')
      setReportMode('preview')
    } catch (e) {
      setSaveMsg(extractErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  /** 恢复系统原始报告(清除教师编辑版) */
  const handleRestoreReport = async () => {
    setSaving(true)
    setSaveMsg(null)
    try {
      const updated = await updateReport(id, null)
      setTask(updated)
      setEditedMarkdown(updated.result?.markdown_report ?? '')
      setSaveMsg('已恢复系统原始报告')
      setReportMode('preview')
    } catch (e) {
      setSaveMsg(extractErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  /** 当前展示的报告内容(按版本选择) */
  const currentReportMarkdown =
    reportVariant === 'student'
      ? (task?.student_report ?? '(暂无学生版报告)')
      : (task?.edited_report ?? task?.result?.markdown_report ?? '(暂无报告)')

  /** 下载报告为 .md 文件 */
  const handleDownload = () => {
    const content =
      reportVariant === 'student'
        ? (task?.student_report ?? '')
        : (task?.edited_report ?? task?.result?.markdown_report ?? '')
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download =
      reportVariant === 'student'
        ? `订正单_${task?.student_name ?? '未知'}_${task?.id}.md`
        : `批改报告_${task?.student_name ?? '未知'}_${task?.id}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  /** 重试失败任务 */
  const handleRetry = async () => {
    try {
      await retryTask(id)
      setAnnotationData(null)
      await loadTask()
    } catch (e) {
      setSaveMsg(extractErrorMessage(e))
    }
  }

  /** 重新批改已启动:刷新任务(进入轮询),清空旧批注与视图状态 */
  const handleRecorrectStarted = (updated: TaskDetail) => {
    setTask(updated)
    setAnnotationData(null)
    setActiveErrorIndex(null)
    setReportVariant('teacher')
    setReportMode('preview')
    setSaveMsg(null)
  }

  /** 转录原文保存后:刷新任务并清空批注缓存(下次进入批注核对按新文本重新定位) */
  const handleTranscriptSaved = (updated: TaskDetail) => {
    setTask(updated)
    setAnnotationData(null)
    setActiveErrorIndex(null)
  }

  /** 打开打印视图(新标签页,保留当前审阅状态)[#12] */
  const handleOpenPrint = () => {
    const params = new URLSearchParams({
      variant: printVariant,
      include_images: printIncludeImages ? '1' : '0',
      include_transcript: printIncludeTranscript ? '1' : '0',
    })
    window.open(`/print/${id}?${params.toString()}`, '_blank')
    setPrintDialogOpen(false)
  }

  /** 处理中的进度百分比(按阶段估算) */
  const STAGE_PERCENT: Record<string, number> = { UPLOADED: 5, OCR: 40, GRADING: 70, RENDERING: 92, DONE: 100 }
  const processingPercent = STAGE_PERCENT[task.stage] ?? 10

  return (
    <Box sx={{ maxWidth: 1440, mx: 'auto' }}>
      {/* ================= 顶部信息栏 ================= */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2, flexWrap: 'wrap' }}>
        <Button size="small" color="inherit" startIcon={<ArrowBackOutlinedIcon />} onClick={() => navigate(-1)}>
          返回
        </Button>
        {siblings.length > 1 && siblingIndex >= 0 && (
          <Tooltip title="← / → 键可快捷切换;范围:同批次(或同班级作业),按创建顺序">
            <ButtonGroup size="small" variant="outlined">
              <Button
                disabled={prevId === null}
                onClick={() => goSibling(prevId)}
                startIcon={<ChevronLeftOutlinedIcon fontSize="small" />}
              >
                上一份
              </Button>
              <Button
                disabled
                sx={{ pointerEvents: 'none', px: 1.5, color: 'text.secondary' }}
              >
                第 {siblingIndex + 1} / {siblings.length} 份
              </Button>
              <Button
                disabled={nextId === null}
                onClick={() => goSibling(nextId)}
                endIcon={<ChevronRightOutlinedIcon fontSize="small" />}
              >
                下一份
              </Button>
            </ButtonGroup>
          </Tooltip>
        )}
        <Typography variant="h6">
          任务 #{task.id}
          {task.student_name && task.student_name !== '未知' && ` · ${task.student_name}`}
          {task.student_id && ` (${task.student_id})`}
        </Typography>
        <Tooltip title="修正学生信息(识别姓名有误时;同步错题本与统计)">
          <IconButton
            size="small"
            onClick={() => {
              setEditStudentName(task.student_name === '未知' ? '' : task.student_name)
              setEditStudentId(task.student_id ?? '')
              setStudentError(null)
              setStudentDialogOpen(true)
            }}
          >
            <EditOutlinedIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        {task.status === 'COMPLETED' && (
          <Tooltip title="复用原图原地重跑;系统报告将被新结果覆盖,教师编辑版与寄语保留">
            <Button
              size="small"
              variant="outlined"
              startIcon={<RestartAltOutlinedIcon fontSize="small" />}
              onClick={() => setRecorrectOpen(true)}
            >
              重新批改
            </Button>
          </Tooltip>
        )}
        <StatusChip status={task.status} />
        {task.fallback_triggered && (
          <Tooltip title="管线 A 网络失败,已自动切换至管线 B">
            <Chip size="small" color="warning" variant="outlined" label="已故障转移 A→B" />
          </Tooltip>
        )}
        <Box sx={{ flex: 1 }} />
        <Chip size="small" variant="outlined" label={meta.title} />
        <Chip size="small" variant="outlined" label={STANDARD_LABEL[task.grading_standard]} />
        <Chip size="small" variant="outlined" label={DETAIL_LABEL[task.detail_level]} />
        {task.class_name && <Chip size="small" variant="outlined" color="secondary" label={task.class_name} />}
        {task.assignment_name && <Chip size="small" variant="outlined" label={task.assignment_name} />}
        {task.overall_score && (
          <Chip size="small" color="primary" label={`得分 ${task.overall_score}`} />
        )}
      </Box>

      {/* 重新批改差异提示(完成后的新旧对比) */}
      {task.status === 'COMPLETED' && (task.recorrect_count ?? 0) > 0 && (
        <Alert severity="info" variant="outlined" sx={{ mb: 2, borderRadius: 2.5 }}>
          本任务已重新批改 {task.recorrect_count} 次
          {task.last_recorrect_at ? `(最近:${new Date(task.last_recorrect_at).toLocaleString()})` : ''}
          。上次:{task.prev_overall_score ?? '—'}({task.prev_error_count ?? 0} 处错因)→ 本次:
          {task.result?.overall_score ?? '—'}({task.result?.errors?.length ?? 0} 处错因)。
          新系统报告已覆盖旧报告;教师编辑版与教师寄语保留。
        </Alert>
      )}

      {/* ================= 分屏主体 ================= */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 5fr) minmax(0, 6fr)' },
          gap: 2.5,
          alignItems: 'start',
        }}
      >
        {/* ---------- 左:转录原文(上) + 作文原图(下);仅调换上下顺序,功能与交互不变 ---------- */}
        <Stack spacing={2.5} sx={{ minWidth: 0 }}>
          {/* 转录原文(可编辑;批注核对模式下与右栏清单双向联动) */}
          <TranscriptPanel
            task={task}
            annotations={annotationData}
            annotated={reportVariant === 'annotate' && task.status === 'COMPLETED'}
            filter={annotationFilter}
            activeIndex={activeErrorIndex}
            onActiveChange={setActiveErrorIndex}
            registerSpan={(index, el) => {
              if (el) spanRefs.current.set(index, el)
              else spanRefs.current.delete(index)
            }}
            onSaved={handleTranscriptSaved}
          />

          <Card sx={{ overflow: 'hidden' }}>
          <Box
            sx={{
              px: 2,
              py: 1.2,
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              borderBottom: 1,
              borderColor: 'divider',
            }}
          >
            <Typography variant="subtitle2" sx={{ flex: 1 }}>
              作文原图
            </Typography>
            {imageCount === 0 && (
              <Typography variant="caption" color="text.secondary">
                无图片
              </Typography>
            )}
          </Box>

          {imageCount > 0 && (
            <Tabs
              value={Math.min(pageIndex, imageCount - 1)}
              onChange={(_, v) => setPageIndex(v)}
              variant="scrollable"
              sx={{ px: 1, minHeight: 36, '& .MuiTab-root': { minHeight: 36, py: 0.5 } }}
            >
              {Array.from({ length: imageCount }, (_, i) => (
                <Tab key={i} label={`第 ${i + 1} 页`} />
              ))}
            </Tabs>
          )}

          <Box sx={{ bgcolor: 'action.hover', minHeight: 480, position: 'relative' }}>
            {imageCount > 0 ? (
              <TransformWrapper
                key={pageIndex}
                initialScale={1}
                minScale={0.4}
                maxScale={5}
                doubleClick={{ mode: 'zoomIn' }}
              >
                {({ zoomIn, zoomOut, resetTransform }) => (
                  <>
                    {/* 缩放工具条 */}
                    <Box
                      sx={{
                        position: 'absolute',
                        top: 10,
                        right: 10,
                        zIndex: 10,
                        display: 'flex',
                        gap: 0.5,
                        bgcolor: 'background.paper',
                        borderRadius: 2,
                        p: 0.5,
                        boxShadow: 1,
                      }}
                    >
                      <Tooltip title="放大">
                        <IconButton size="small" onClick={() => zoomIn()}>
                          <ZoomInOutlinedIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="缩小">
                        <IconButton size="small" onClick={() => zoomOut()}>
                          <ZoomOutOutlinedIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="重置">
                        <IconButton size="small" onClick={() => resetTransform()}>
                          <RestartAltOutlinedIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Box>
                    <TransformComponent
                      wrapperStyle={{ width: '100%', height: '52vh', minHeight: 380 }}
                      contentStyle={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center' }}
                    >
                      <img
                        src={taskImageUrl(task.id, Math.min(pageIndex, imageCount - 1))}
                        alt={`作文第 ${pageIndex + 1} 页`}
                        style={{ maxWidth: '100%', maxHeight: '100%', display: 'block' }}
                      />
                    </TransformComponent>
                  </>
                )}
              </TransformWrapper>
            ) : (
              <Box sx={{ display: 'grid', placeItems: 'center', height: 480, color: 'text.disabled' }}>
                <Box sx={{ textAlign: 'center' }}>
                  <ImageNotSupportedOutlinedIcon sx={{ fontSize: 44, mb: 1 }} />
                  <Typography variant="body2">暂无图片</Typography>
                </Box>
              </Box>
            )}
          </Box>

          {/* 姓名/学号区大预览卡(识别成功后显示;任务进行中展示预识别状态) */}
          {imageCount > 0 && (
            <Box sx={{ px: 2, py: 1.5, borderTop: 1, borderColor: 'divider' }}>
              {task.student_name && task.student_name !== '未知' ? (
                <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Box
                    component="img"
                    src={taskNameCropUrl(task.id)}
                    alt="姓名/学号区"
                    onError={(event) => {
                      ;(event.currentTarget as HTMLImageElement).style.display = 'none'
                    }}
                    onClick={() => window.open(taskNameCropUrl(task.id), '_blank')}
                    sx={{
                      width: { xs: '100%', sm: 420 },
                      maxWidth: '100%',
                      borderRadius: 2,
                      border: 1,
                      borderColor: 'divider',
                      bgcolor: '#fff',
                      cursor: 'zoom-in',
                    }}
                  />
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="overline" color="text.secondary" sx={{ display: 'block' }}>
                      姓名/学号区(来自原图,点击看大图)
                    </Typography>
                    <Typography variant="h6" sx={{ fontWeight: 600 }} noWrap>
                      {task.student_name}
                      {task.student_id ? ` · ${task.student_id}` : ''}
                    </Typography>
                  </Box>
                </Box>
              ) : task.status === 'PENDING' || task.status === 'PROCESSING' ? (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                  <CircularProgress size={16} />
                  <Typography variant="caption" color="text.secondary">
                    正在预识别姓名…(完成后此处将显示姓名区大图)
                  </Typography>
                </Box>
              ) : null}
            </Box>
          )}
          </Card>
        </Stack>

        {/* ---------- 右:状态区 / 复核 / 报告 ---------- */}
        <Card sx={{ minHeight: 560 }}>
          <CardContent sx={{ p: 3 }}>
            {/* === 处理中 === */}
            {(task.status === 'PENDING' || task.status === 'PROCESSING') && (
              <Box sx={{ py: 6, textAlign: 'center' }}>
                <CircularProgress size={44} sx={{ mb: 2.5 }} />
                <Typography variant="h6" gutterBottom>
                  正在批改中...
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                  当前阶段:{STAGE_LABEL[task.stage]}
                </Typography>
                <Box sx={{ maxWidth: 360, mx: 'auto' }}>
                  <LinearProgress
                    variant="determinate"
                    value={processingPercent}
                    sx={{ height: 8, borderRadius: 4 }}
                  />
                </Box>
                <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1.5 }}>
                  {task.pipeline_choice === 'PIPELINE_A_LOCAL'
                    ? '本地模型推理可能需要数分钟,请耐心等待'
                    : '云端识别与评分通常在一分钟内完成'}
                </Typography>
              </Box>
            )}

            {/* === 待人工复核(管线 B) === */}
            {task.status === 'WAITING_REVIEW' && (
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                  <RateReviewOutlinedIcon color="warning" />
                  <Typography variant="h6">OCR 转录复核</Typography>
                </Box>
                <Alert severity="warning" variant="outlined" sx={{ mb: 2.5, borderRadius: 2.5 }}>
                  {task.pipeline_choice === 'PIPELINE_A_LOCAL' ? '本地 VLM' : '云端'}
                  已完成手写识别。请校对下方转录内容(可修正识别错误与姓名、学号),
                  确认后将继续完成评分与报告。
                </Alert>
                {task.ocr_result?.recognition_quality === 'low' && (
                  <Alert severity="error" variant="outlined" sx={{ mb: 2.5, borderRadius: 2.5 }}>
                    识别准确率较低,建议仔细核对
                    {task.ocr_result?.quality_note ? `:${task.ocr_result.quality_note}` : ''}。
                    转录中 [unsicher:…] 为低把握猜测、[unleserlich] 为无法辨认处,请对照原图逐一修正后再确认。
                  </Alert>
                )}

                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 2 }}>
                  <TextField
                    label="学生姓名"
                    size="small"
                    fullWidth
                    value={reviewName}
                    onChange={(e) => setReviewName(e.target.value)}
                  />
                  <TextField
                    label="学号(可选)"
                    size="small"
                    fullWidth
                    value={reviewStudentId}
                    onChange={(e) => setReviewStudentId(e.target.value)}
                  />
                </Stack>

                <TextField
                  label="作文转录文本(可编辑)"
                  multiline
                  minRows={12}
                  fullWidth
                  value={reviewText}
                  onChange={(e) => setReviewText(e.target.value)}
                  sx={{ mb: 2, '& textarea': { fontSize: 14, lineHeight: 1.8 } }}
                />

                {confirmError && (
                  <Alert severity="error" sx={{ mb: 2, borderRadius: 2.5 }}>
                    {confirmError}
                  </Alert>
                )}

                <Button
                  variant="contained"
                  size="large"
                  fullWidth
                  disabled={confirming}
                  startIcon={confirming ? <CircularProgress size={18} color="inherit" /> : <CheckCircleOutlinedIcon />}
                  onClick={handleConfirmOcr}
                  sx={{ py: 1.3, background: 'linear-gradient(135deg, #3F51B5 0%, #5C6BC0 100%)' }}
                >
                  {confirming ? '正在提交...' : '确认并继续评分'}
                </Button>
              </Box>
            )}

            {/* === 失败 === */}
            {task.status === 'FAILED' && (
              <Box sx={{ py: 4 }}>
                <Alert severity="error" sx={{ mb: 2.5, borderRadius: 2.5 }}>
                  批改失败:{task.error_message ?? '未知错误'}
                </Alert>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                  常见原因:本地模型服务未启动、网络不可达、API 密钥未配置。
                  修复后可直接重试本任务。
                </Typography>
                <Button variant="contained" startIcon={<RefreshOutlinedIcon />} onClick={handleRetry}>
                  重试任务
                </Button>
              </Box>
            )}

            {/* === 已完成:报告 === */}
            {task.status === 'COMPLETED' && (
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
                  <Typography variant="h6" sx={{ flex: 1 }}>
                    {reportVariant === 'student'
                      ? '学生版订正单'
                      : reportVariant === 'annotate'
                        ? '批注核对(转录全文)'
                        : '批改报告(教师版)'}
                  </Typography>
                  {/* 视图切换:教师版 / 学生版 / 批注核对(#11 / #13) */}
                  <ToggleButtonGroup
                    exclusive
                    size="small"
                    value={reportVariant}
                    onChange={(_, v) => {
                      if (v) {
                        setReportVariant(v)
                        if (v !== 'teacher') setReportMode('preview') // 学生版与批注视图均只读
                      }
                    }}
                  >
                    <ToggleButton value="teacher">教师版</ToggleButton>
                    <ToggleButton value="student">学生版</ToggleButton>
                    <ToggleButton value="annotate">批注核对</ToggleButton>
                  </ToggleButtonGroup>
                  {reportVariant === 'student' && (
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<RateReviewOutlinedIcon fontSize="small" />}
                      onClick={() => {
                        setMsgText(task.teacher_message ?? '')
                        setMsgError(null)
                        setMsgOpen(true)
                      }}
                    >
                      编辑寄语
                    </Button>
                  )}
                  {reportVariant === 'teacher' && (
                    <ToggleButtonGroup
                      exclusive
                      size="small"
                      value={reportMode}
                      onChange={(_, v) => v && setReportMode(v)}
                    >
                      <ToggleButton value="preview">
                        <VisibilityOutlinedIcon fontSize="small" sx={{ mr: 0.6 }} />
                        预览
                      </ToggleButton>
                      <ToggleButton value="edit">
                        <EditOutlinedIcon fontSize="small" sx={{ mr: 0.6 }} />
                        编辑
                      </ToggleButton>
                    </ToggleButtonGroup>
                  )}
                  <Tooltip title="下载当前版本 Markdown">
                    <span>
                      <IconButton
                        size="small"
                        onClick={handleDownload}
                        disabled={reportVariant === 'annotate'}
                      >
                        <DownloadOutlinedIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                  <Tooltip title="打印(可选版本与原图)">
                    <IconButton size="small" onClick={() => setPrintDialogOpen(true)}>
                      <PrintOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>

                {reportVariant === 'student' && (
                  <Alert severity="info" variant="outlined" sx={{ mb: 1.5, borderRadius: 2.5 }}>
                    学生版已弱化分数,聚焦订正清单与练习建议,可直接打印或发回学生。
                  </Alert>
                )}

                {task.edited_report && reportVariant === 'teacher' && reportMode === 'preview' && (
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
                    <Chip size="small" variant="outlined" color="info" label="当前展示教师编辑版" />
                    <Button
                      size="small"
                      color="warning"
                      startIcon={<RestartAltOutlinedIcon />}
                      disabled={saving}
                      onClick={handleRestoreReport}
                    >
                      恢复系统原始报告
                    </Button>
                  </Box>
                )}

                {saveMsg && (
                  <Alert severity={saveMsg.startsWith('已保存') ? 'success' : 'error'} sx={{ mb: 1.5, borderRadius: 2.5 }}>
                    {saveMsg}
                  </Alert>
                )}

                {reportVariant === 'annotate' ? (
                  <Box>
                    {annotationLoading && (
                      <Box sx={{ display: 'grid', placeItems: 'center', py: 6 }}>
                        <CircularProgress />
                      </Box>
                    )}
                    {annotationError && (
                      <Alert severity="error" sx={{ borderRadius: 2.5 }}>
                        {annotationError}
                      </Alert>
                    )}
                    {annotationData && annotationData.annotations.length === 0 && (
                      <Alert severity="success" variant="outlined" sx={{ borderRadius: 2.5 }}>
                        本次批改未发现错误条目,转录全文无需批注。
                      </Alert>
                    )}
                    {annotationData && annotationData.annotations.length > 0 && (
                      <AnnotationPanel
                        task={task}
                        annotations={annotationData}
                        filter={annotationFilter}
                        onFilterChange={setAnnotationFilter}
                        activeIndex={activeErrorIndex}
                        onActiveChange={setActiveErrorIndex}
                      />
                    )}
                  </Box>
                ) : reportMode === 'preview' ? (
                  <Paper
                    id="print-area"
                    variant="outlined"
                    sx={{ p: 2.5, borderRadius: 3, maxHeight: 640, overflowY: 'auto' }}
                  >
                    <MarkdownReport markdown={currentReportMarkdown} />
                  </Paper>
                ) : (
                  <Box>
                    <TextField
                      multiline
                      minRows={18}
                      fullWidth
                      value={editedMarkdown}
                      onChange={(e) => setEditedMarkdown(e.target.value)}
                      sx={{ mb: 1.5, '& textarea': { fontSize: 13.5, fontFamily: 'ui-monospace, Consolas, monospace', lineHeight: 1.7 } }}
                    />
                    <Stack direction="row" spacing={1.5} flexWrap="wrap">
                      <Button
                        variant="contained"
                        startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveOutlinedIcon />}
                        disabled={saving}
                        onClick={handleSaveReport}
                      >
                        保存报告
                      </Button>
                      <Button
                        color="inherit"
                        onClick={() => {
                          setEditedMarkdown(task.edited_report ?? task.result?.markdown_report ?? '')
                          setReportMode('preview')
                        }}
                      >
                        放弃修改
                      </Button>
                      {task.edited_report && (
                        <Button
                          color="warning"
                          startIcon={<RestartAltOutlinedIcon />}
                          disabled={saving}
                          onClick={handleRestoreReport}
                        >
                          恢复系统原始报告
                        </Button>
                      )}
                    </Stack>
                  </Box>
                )}

                {/* 转录原文(折叠展示;批注视图已内含转录全文,不再重复) */}
                {reportVariant !== 'annotate' && task.result?.transcribed_text && (
                  <>
                    <Divider sx={{ my: 2.5 }} />
                    <Typography variant="subtitle2" gutterBottom>
                      转录原文(供核对)
                    </Typography>
                    <Paper
                      variant="outlined"
                      sx={{ p: 2, borderRadius: 2.5, bgcolor: 'action.hover', maxHeight: 200, overflowY: 'auto' }}
                    >
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.9 }}>
                        {task.result.transcribed_text}
                      </Typography>
                    </Paper>
                  </>
                )}
              </Box>
            )}
          </CardContent>
        </Card>
      </Box>

      {/* ================= 打印设置对话框(#12) ================= */}
      <Dialog open={printDialogOpen} onClose={() => setPrintDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>打印设置</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            选择要打印的内容,确认后将在新标签页打开打印预览并自动调起打印对话框。
          </Typography>
          <Stack spacing={2.5}>
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                打印版本
              </Typography>
              <ToggleButtonGroup
                exclusive
                fullWidth
                size="small"
                value={printVariant}
                onChange={(_, v) => v && setPrintVariant(v)}
              >
                <ToggleButton value="teacher">教师版</ToggleButton>
                <ToggleButton value="student">学生版</ToggleButton>
                <ToggleButton value="both">两份都要</ToggleButton>
              </ToggleButtonGroup>
            </Box>
            <FormControlLabel
              control={
                <Switch
                  checked={printIncludeImages}
                  onChange={(e) => setPrintIncludeImages(e.target.checked)}
                />
              }
              label={
                <Box>
                  <Typography variant="body2">包含作文原图</Typography>
                  <Typography variant="caption" color="text.secondary">
                    每张原图单独占一页,便于纸面装订
                  </Typography>
                </Box>
              }
            />
            <FormControlLabel
              control={
                <Switch
                  checked={printIncludeTranscript}
                  onChange={(e) => setPrintIncludeTranscript(e.target.checked)}
                />
              }
              label={
                <Box>
                  <Typography variant="body2">附:作文转录原文</Typography>
                  <Typography variant="caption" color="text.secondary">
                    在报告后面附一页识别出的原文,便于核对
                  </Typography>
                </Box>
              }
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setPrintDialogOpen(false)}>
            取消
          </Button>
          <Button variant="contained" startIcon={<PrintOutlinedIcon />} onClick={handleOpenPrint}>
            打开打印预览
          </Button>
        </DialogActions>
      </Dialog>

      {/* ================= 学生信息纠错对话框 ================= */}
      <Dialog open={studentDialogOpen} onClose={() => setStudentDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>修正学生信息</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              修改后同步错题本/档案与台账对齐口径(历史统计按新姓名/学号重新聚合)。
            </Typography>
            {studentError && <Alert severity="error">{studentError}</Alert>}
            <TextField
              autoFocus
              size="small"
              label="学生姓名"
              value={editStudentName}
              onChange={(e) => setEditStudentName(e.target.value)}
            />
            <TextField
              size="small"
              label="学号(可选)"
              value={editStudentId}
              onChange={(e) => setEditStudentId(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setStudentDialogOpen(false)}>
            取消
          </Button>
          <Button
            variant="contained"
            disabled={savingStudent || !editStudentName.trim()}
            onClick={async () => {
              setSavingStudent(true)
              setStudentError(null)
              try {
                const updated = await updateTaskStudent(id, {
                  student_name: editStudentName.trim(),
                  student_id: editStudentId.trim() || null,
                })
                setTask(updated)
                setStudentDialogOpen(false)
              } catch (e) {
                setStudentError(extractErrorMessage(e))
              } finally {
                setSavingStudent(false)
              }
            }}
          >
            保存
          </Button>
        </DialogActions>
      </Dialog>

      {/* ================= 教师寄语编辑对话框(学生版报告) ================= */}
      <Dialog open={msgOpen} onClose={() => setMsgOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>编辑教师寄语(学生版报告)</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ pt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              点击预设一键填入(10 个常见场景),再按学生情况润色;保存后立即生效于学生版报告,
              留空或点「恢复默认寄语」即回到系统默认寄语。
            </Typography>
            {msgError && <Alert severity="error">{msgError}</Alert>}
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {TEACHER_MESSAGE_PRESETS.map((preset) => (
                <Chip
                  key={preset.scene}
                  size="small"
                  variant="outlined"
                  clickable
                  label={preset.scene}
                  onClick={() => setMsgText(preset.text)}
                />
              ))}
            </Stack>
            <TextField
              multiline
              minRows={3}
              maxRows={6}
              fullWidth
              label="寄语内容"
              value={msgText}
              onChange={(e) => setMsgText(e.target.value)}
              helperText={`${msgText.length}/500 字(保存后打印/导出学生版时同步生效)`}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setMsgOpen(false)} disabled={savingMsg}>
            取消
          </Button>
          <Button
            color="warning"
            disabled={savingMsg}
            onClick={async () => {
              setSavingMsg(true)
              setMsgError(null)
              try {
                const updated = await updateTeacherMessage(id, null)
                setTask(updated)
                setMsgOpen(false)
              } catch (e) {
                setMsgError(extractErrorMessage(e))
              } finally {
                setSavingMsg(false)
              }
            }}
          >
            恢复默认寄语
          </Button>
          <Button
            variant="contained"
            disabled={savingMsg}
            onClick={async () => {
              if (!msgText.trim()) {
                setMsgError('寄语不能为空(如需恢复默认请点「恢复默认寄语」)')
                return
              }
              setSavingMsg(true)
              setMsgError(null)
              try {
                const updated = await updateTeacherMessage(id, msgText.trim())
                setTask(updated)
                setMsgOpen(false)
              } catch (e) {
                setMsgError(extractErrorMessage(e))
              } finally {
                setSavingMsg(false)
              }
            }}
          >
            保存
          </Button>
        </DialogActions>
      </Dialog>

      {/* ================= 重新批改对话框(复用原图原地重跑) ================= */}
      <RecorrectDialog
        open={recorrectOpen}
        onClose={() => setRecorrectOpen(false)}
        task={task}
        onStarted={handleRecorrectStarted}
      />
    </Box>
  )
}
