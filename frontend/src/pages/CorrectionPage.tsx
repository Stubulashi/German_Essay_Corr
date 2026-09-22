/**
 * 批改工作台(首页)
 *
 * 功能:
 * - 单篇 / 批量两种上传模式(拖拽或点击选择,支持 ZIP);
 * - 动态控制矩阵:管线选择、评分标准、细致度、OCR 人工复核开关;
 * - 提交后:单篇跳转审阅页,批量跳转队列页。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  LinearProgress,
  CircularProgress,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Paper,
  Radio,
  Select,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
// MUI v6 新版 Grid(支持 size 响应式属性)
import Grid from '@mui/material/Grid2'
import AutoAwesomeOutlinedIcon from '@mui/icons-material/AutoAwesomeOutlined'
import CloseOutlinedIcon from '@mui/icons-material/CloseOutlined'
import CloudQueueOutlinedIcon from '@mui/icons-material/CloudQueueOutlined'
import DeleteSweepOutlinedIcon from '@mui/icons-material/DeleteSweepOutlined'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined'
import HelpOutlineOutlinedIcon from '@mui/icons-material/HelpOutlineOutlined'
import ImageOutlinedIcon from '@mui/icons-material/ImageOutlined'
import InsertDriveFileOutlinedIcon from '@mui/icons-material/InsertDriveFileOutlined'
import MemoryOutlinedIcon from '@mui/icons-material/MemoryOutlined'
import RestartAltOutlinedIcon from '@mui/icons-material/RestartAltOutlined'
import RocketLaunchOutlinedIcon from '@mui/icons-material/RocketLaunchOutlined'
import SortByAlphaOutlinedIcon from '@mui/icons-material/SortByAlphaOutlined'
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined'
import { useNavigate } from 'react-router-dom'
import {
  createBatchCorrection,
  createSingleCorrection,
  extractErrorMessage,
  fetchClasses,
  fetchClassRoster,
  fetchSettings,
  fetchStyleStatus,
  probeIdentity,
} from '../api/client'
import type { IdentityProbeItem } from '../api/client'
import { useHealth } from '../hooks/useHealth'
import PageHeader from '../components/PageHeader'
import ImageEditorDialog from '../components/ImageEditorDialog'
import { compressImageFiles } from '../utils/imageCompress'
import type {
  AssignMode,
  CorrectionConfigPayload,
  DetailLevel,
  GradingStandard,
  PipelineChoice,
  RosterMember,
  SchoolClass,
} from '../types'
import { PIPELINE_META } from '../types'

/** 上传模式:单篇(多页=一份作文)/ 批量(每张=一篇) */
type UploadMode = 'single' | 'batch'

/** 允许的图片扩展名(可浏览器预览) */
const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.webp', '.bmp']

/** 允许上传的扩展名(含 HEIC 与扫描 PDF:浏览器不能预览,但后端可解码/逐页展开) */
const UPLOAD_EXTS = [...IMAGE_EXTS, '.heic', '.heif', '.pdf']

/** 批量分片提交单片文件数(兼顾进度反馈与服务端单请求护栏) */
const CHUNK_SIZE = 20

/** 按文件名自然排序(数字感知;用于"按名单顺序指派"前的手动排序) */
function sortFilesByName(files: File[], direction: 'asc' | 'desc'): File[] {
  const sorted = [...files].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
  return direction === 'asc' ? sorted : sorted.reverse()
}

/** 判断文件是否为可预览图片 */
function isImageFile(name: string): boolean {
  const lower = name.toLowerCase()
  return IMAGE_EXTS.some((ext) => lower.endsWith(ext))
}

/** 判断是否为 ZIP */
function isZipFile(name: string): boolean {
  return name.toLowerCase().endsWith('.zip')
}

/** 个人信息探测状态(「强制性姓名识别」;key = 文件名:大小) */
type ProbeState = { loading?: boolean; items?: IdentityProbeItem[]; error?: string }

const probeKey = (f: File) => `${f.name}:${f.size}`

/** 识别结果行文案(单条:姓名 · 年龄 N;无信息→未识别) */
function probeItemText(item: IdentityProbeItem): string {
  if (item.status === 'error') return '识别失败'
  const parts: string[] = []
  if (item.name) parts.push(item.name)
  if (item.age) parts.push(`年龄 ${item.age}`)
  if (!parts.length) return '未识别到姓名'
  return parts.join(' · ')
}

export default function CorrectionPage() {
  const navigate = useNavigate()
  const health = useHealth()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ---------- 上传状态 ----------
  const [mode, setMode] = useState<UploadMode>('single')
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)

  // ---------- 强制性姓名识别:选文件即时探测(本地引擎;设置开启时生效) ----------
  const [forceProbeActive, setForceProbeActive] = useState(false)
  const [probeMap, setProbeMap] = useState<Record<string, ProbeState>>({})
  /** 在途探测去重(避免补探测 effect 与新增触发重复发起) */
  const probeInflight = useRef<Set<string>>(new Set())

  // ---------- 配置状态(动态控制矩阵) ----------
  const [pipeline, setPipeline] = useState<PipelineChoice>('PIPELINE_A_LOCAL')
  const [standard, setStandard] = useState<GradingStandard>('GAOKAO')
  const [detail, setDetail] = useState<DetailLevel>('MEDIUM')
  const [requireReview, setRequireReview] = useState(false)

  // ---------- 班级与作业元数据 ----------
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [classId, setClassId] = useState<number | ''>('')
  const [assignmentName, setAssignmentName] = useState('')
  const [topic, setTopic] = useState('')

  // ---------- 标准答题卷打印(绑定学生二维码) ----------
  const [sheetDialogOpen, setSheetDialogOpen] = useState(false)
  const [sheetClassId, setSheetClassId] = useState<number | ''>('')
  const [sheetRoster, setSheetRoster] = useState<RosterMember[]>([])
  const [sheetRosterLoading, setSheetRosterLoading] = useState(false)
  const [sheetStudentId, setSheetStudentId] = useState<number | ''>('')

  /** 标准答题卷:加载指定班级花名册(选择学生用;失败静默为空列表) */
  const loadSheetRoster = async (cid: number) => {
    setSheetRosterLoading(true)
    try {
      setSheetRoster(await fetchClassRoster(cid))
    } catch {
      setSheetRoster([])
    } finally {
      setSheetRosterLoading(false)
    }
  }

  /** 标准答题卷:打开"绑定学生"对话框(预填当前班级) */
  const openSheetDialog = () => {
    const preset = classId !== '' ? classId : ''
    setSheetClassId(preset)
    setSheetStudentId('')
    setSheetRoster([])
    setSheetDialogOpen(true)
    if (preset !== '') void loadSheetRoster(Number(preset))
  }

  // ---------- 学生指派(上传优化) ----------
  const [assignMode, setAssignMode] = useState<AssignMode>('recognize')
  const [assignOrderStart, setAssignOrderStart] = useState('')
  const [singleName, setSingleName] = useState('')
  const [singleId, setSingleId] = useState('')
  const [roster, setRoster] = useState<RosterMember[]>([])

  // ---------- 客户端压缩与上传进度 ----------
  const [compress, setCompress] = useState(true)
  const [compressing, setCompressing] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)

  // ---------- 图片编辑(灰度/黑白/裁剪/旋转;参数化操作栈可撤销/重置) ----------
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorIndex, setEditorIndex] = useState(0)
  /** 当前列表项 -> 初次编辑前的原图(未编辑的项不在 Map 中) */
  const editedOriginals = useRef<WeakMap<File, File>>(new WeakMap())

  // ---------- 示范学习生效风格(工作台提示) ----------
  const [styleName, setStyleName] = useState<string | null>(null)

  // ---------- 提交状态 ----------
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /** 加载班级列表 */
  const loadClasses = () => {
    fetchClasses()
      .then(setClasses)
      .catch(() => setClasses([]))
  }
  useEffect(() => {
    loadClasses()
  }, [])

  /** 选择班级后加载花名册(供指派模式使用与人数提示) */
  useEffect(() => {
    if (classId === '') {
      setRoster([])
      return
    }
    fetchClassRoster(classId)
      .then(setRoster)
      .catch(() => setRoster([]))
  }, [classId])

  /** 生效中的示范学习风格(仅提示,注入由后端完成) */
  useEffect(() => {
    fetchStyleStatus()
      .then((status) => setStyleName(status.active?.name ?? null))
      .catch(() => undefined)
  }, [])

  /** 工作台默认值(设置中心「基础运行」可配置):首次拿到 health 后应用一次 */
  const appliedDefaults = useRef(false)
  useEffect(() => {
    if (appliedDefaults.current || !health) return
    appliedDefaults.current = true
    if (health.default_grading_standard === 'DSD' || health.default_grading_standard === 'GAOKAO') {
      setStandard(health.default_grading_standard)
    }
    if (health.default_detail_level === 'LOW' || health.default_detail_level === 'MEDIUM' || health.default_detail_level === 'HIGH') {
      setDetail(health.default_detail_level)
    }
  }, [health])

  /** 文件预览信息(图片显示缩略图 URL) */
  const fileEntries = useMemo(
    () =>
      files.map((f, idx) => ({
        file: f,
        idx,
        isImage: isImageFile(f.name),
        previewUrl: isImageFile(f.name) ? URL.createObjectURL(f) : null,
      })),
    [files],
  )

  /** 卸载/列表变化时释放 objectURL(避免内存泄漏) */
  useEffect(() => {
    const urls = fileEntries.map((entry) => entry.previewUrl).filter(Boolean) as string[]
    return () => urls.forEach((url) => URL.revokeObjectURL(url))
  }, [fileEntries])

  /** 读取「强制性姓名识别」开关(实时;读取失败返回 null → 不探测,保持现状) */
  const fetchForceProbeFlag = async (): Promise<boolean | null> => {
    try {
      const view = await fetchSettings()
      const field = view.groups
        .flatMap((group) => group.fields)
        .find((item) => item.key === 'FORCE_NAME_RECOGNITION')
      return field?.value === true
    } catch {
      return null
    }
  }

  /** 对目标文件执行探测(限并发 2;逐张渐进更新为"识别中…"→结果) */
  const runProbeQueue = async (targets: File[]) => {
    if (targets.length === 0) return
    const active = await fetchForceProbeFlag()
    if (active === null) return // 开关读取失败:静默保持现状
    setForceProbeActive(active)
    if (!active) return
    const queue = [...targets]
    const worker = async () => {
      while (queue.length) {
        const file = queue.shift()
        if (!file) break
        const key = probeKey(file)
        if (probeInflight.current.has(key)) continue
        probeInflight.current.add(key)
        setProbeMap((prev) => ({ ...prev, [key]: { loading: true } }))
        try {
          const { items } = await probeIdentity(file)
          setProbeMap((prev) => ({ ...prev, [key]: { items } }))
        } catch (e) {
          setProbeMap((prev) => ({ ...prev, [key]: { error: extractErrorMessage(e) } }))
        } finally {
          probeInflight.current.delete(key)
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(2, queue.length) }, () => worker()))
  }

  /** 添加文件(自动去重同名同大小);开启「强制性姓名识别」→ 新增文件立即本地探测 */
  const addFiles = (incoming: FileList | File[]) => {
    const list = Array.from(incoming)
    const key = (f: File) => `${f.name}:${f.size}`
    const snapshotKeys = new Set(files.map(key))
    const added = list.filter((f) => !snapshotKeys.has(key(f)))
    setFiles((prev) => {
      const existing = new Set(prev.map(key))
      const merged = [...prev]
      list.forEach((f) => {
        if (!existing.has(key(f))) {
          merged.push(f)
          existing.add(key(f))
        }
      })
      return merged
    })
    void runProbeQueue(added)
  }

  /** 移除单个文件(连同其探测状态) */
  const removeFile = (idx: number) => {
    const target = files[idx]
    setFiles((prev) => prev.filter((_, i) => i !== idx))
    if (target) {
      setProbeMap((prev) => {
        const next = { ...prev }
        delete next[probeKey(target)]
        return next
      })
    }
  }

  /** 清空全部(连同探测状态) */
  const clearFiles = () => {
    setFiles([])
    setProbeMap({})
  }

  /** 图片编辑:应用结果写回列表(记录初次编辑前的原图,供重置) */
  const applyEditedFile = (index: number, newFile: File) => {
    setFiles((prev) => {
      const current = prev[index]
      if (!current) return prev
      const original = editedOriginals.current.get(current) ?? current
      editedOriginals.current.set(newFile, original)
      const next = [...prev]
      next[index] = newFile
      return next
    })
    // 图片内容已变更:重新探测(开关关闭时内部静默跳过)
    void runProbeQueue([newFile])
  }

  /** 图片编辑:重置某页为原图(撤销全部编辑) */
  const resetEditedFile = (index: number) => {
    setFiles((prev) => {
      const current = prev[index]
      const original = current ? editedOriginals.current.get(current) : null
      if (!current || !original) return prev
      editedOriginals.current.delete(current)
      const next = [...prev]
      next[index] = original
      return next
    })
  }

  /** 强制性姓名识别:页面挂载时读取开关(开启则用于展示与补齐) */
  useEffect(() => {
    let cancelled = false
    void fetchForceProbeFlag().then((flag) => {
      if (!cancelled && flag === true) setForceProbeActive(true)
    })
    return () => {
      cancelled = true
    }
    // 仅挂载执行一次
  }, [])

  /** 开关开启后,对已选但尚未探测的文件补齐(含开关刚被打开的情况;收敛于 probeMap 更新) */
  useEffect(() => {
    if (!forceProbeActive) return
    const pending = files.filter(
      (f) => !probeMap[probeKey(f)] && !probeInflight.current.has(probeKey(f)),
    )
    if (pending.length) void runProbeQueue(pending)
  }, [forceProbeActive, files, probeMap])

  /** 渲染探测结果(开关开启时;替代文件名展示位置) */
  const renderProbeStatus = (entry: { file: File }) => {
    const state = probeMap[probeKey(entry.file)]
    if (!state || state.loading) {
      return (
        <Stack direction="row" spacing={1} alignItems="center">
          <CircularProgress size={14} />
          <Typography variant="body2" color="text.secondary">
            识别中…
          </Typography>
        </Stack>
      )
    }
    if (state.error) {
      return (
        <Typography variant="body2" color="warning.main" noWrap>
          {`识别失败(${state.error})`}
        </Typography>
      )
    }
    const items = state.items ?? []
    if (items.length === 0) {
      return (
        <Typography variant="body2" color="text.secondary">
          未识别到姓名
        </Typography>
      )
    }
    if (items.length === 1) {
      return (
        <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
          {probeItemText(items[0])}
        </Typography>
      )
    }
    return (
      <Stack spacing={0.25}>
        {items.map((item, i) => (
          <Typography key={`${i}-${item.source_name}`} variant="caption" noWrap sx={{ display: 'block' }}>
            {`第 ${i + 1} 页 · ${probeItemText(item)}`}
          </Typography>
        ))}
      </Stack>
    )
  }

  /** 图片编辑:取某页原图(未编辑返回 null) */
  const originalOf = (index: number): File | null => {
    const current = files[index]
    return current ? editedOriginals.current.get(current) ?? null : null
  }

  /** 提交批改 */
  const handleSubmit = async () => {
    setError(null)
    if (files.length === 0) {
      setError('请先上传作文图片')
      return
    }
    // 单篇模式下不允许混入 ZIP
    if (mode === 'single' && files.some((f) => isZipFile(f.name))) {
      setError('单篇模式不支持 ZIP,请切换为批量模式或改为上传图片')
      return
    }
    // 指派模式依赖(给出提前提示,避免创建后才发现未匹配)
    if (mode === 'batch' && assignMode !== 'recognize' && classId === '' && assignMode === 'order') {
      setError('「按名单顺序指派」需要先选择归属班级并导入花名册')
      return
    }

    const baseConfig: CorrectionConfigPayload = {
      pipeline_choice: pipeline,
      grading_standard: standard,
      detail_level: detail,
      require_ocr_review: requireReview,
      class_id: classId === '' ? null : classId,
      assignment_name: assignmentName.trim() || null,
      topic: topic.trim() || null,
    }

    setSubmitting(true)
    setProgress(null)
    try {
      // 客户端压缩(增强项:失败自动回退原图;ZIP/HEIC 自动跳过)
      let uploadFiles = files
      if (compress) {
        setCompressing(true)
        uploadFiles = await compressImageFiles(files)
        setCompressing(false)
      }
      if (mode === 'single') {
        const resp = await createSingleCorrection(uploadFiles, {
          ...baseConfig,
          student_name: singleName.trim() || null,
          student_id: singleId.trim() || null,
        })
        navigate(`/review/${resp.task_id}`)
      } else {
        // 分片提交:每片 ≤ CHUNK_SIZE 个文件,复用同一批次 ID,叠加总体进度
        const chunks: File[][] = []
        for (let i = 0; i < uploadFiles.length; i += CHUNK_SIZE) {
          chunks.push(uploadFiles.slice(i, i + CHUNK_SIZE))
        }
        let batchId: string | null = null
        for (let index = 0; index < chunks.length; index += 1) {
          const resp = await createBatchCorrection(
            chunks[index],
            {
              ...baseConfig,
              assign_mode: assignMode,
              assign_order_start: assignOrderStart.trim() ? Number(assignOrderStart) : null,
              batch_id: batchId,
            },
            (percent) =>
              setProgress(Math.round(((index + percent / 100) / chunks.length) * 100)),
          )
          batchId = resp.batch_id
        }
        setProgress(100)
        navigate(`/queue?batch=${batchId}`)
      }
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setCompressing(false)
      setSubmitting(false)
    }
  }

  return (
    <Box sx={{ maxWidth: 1280, mx: 'auto' }}>
      {/* ================= 顶部标题区(统一规范) ================= */}
      <PageHeader
        title="智能批改工作台"
        subtitle="上传手写德语作文,选择管线与评分标准,系统自动完成识别、评分与报告生成。双管线热切换:本地隐私优先,或云端精度优先。"
        actions={
          <Button
            size="small"
            color="inherit"
            startIcon={<HelpOutlineOutlinedIcon />}
            onClick={() => navigate('/guide')}
          >
            使用指南
          </Button>
        }
      />

      {/* 演示模式提示 */}
      {health?.mock_mode && (
        <Alert severity="warning" variant="outlined" sx={{ mb: 2, borderRadius: 3 }}>
          当前后端处于演示模式(MOCK_MODE),批改结果为样例数据,不调用真实模型。
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* ================= 左:上传区 ================= */}
        <Grid size={{ xs: 12, md: 7 }}>
          <Card>
            <CardContent sx={{ p: 3 }}>
              {/* 模式切换 */}
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 1.5, flexWrap: 'wrap' }}>
                <ToggleButtonGroup
                  exclusive
                  size="small"
                  value={mode}
                  onChange={(_, v) => {
                    if (v) {
                      setMode(v)
                      setFiles([])
                    }
                  }}
                >
                  <ToggleButton value="single" sx={{ px: 2 }}>
                    <DescriptionOutlinedIcon fontSize="small" sx={{ mr: 0.8 }} />
                    单篇批改
                  </ToggleButton>
                  <ToggleButton value="batch" sx={{ px: 2 }}>
                    <InsertDriveFileOutlinedIcon fontSize="small" sx={{ mr: 0.8 }} />
                    批量批改
                  </ToggleButton>
                </ToggleButtonGroup>
                <Typography variant="caption" color="text.secondary">
                  {mode === 'single'
                    ? '同一学生的多页图片 = 一份作文'
                    : '每张图片 = 一篇作文;可直接上传 ZIP 压缩包或扫描 PDF(逐页展开)'}
                </Typography>
              </Box>

              {/* 花名册与生效风格(上传指派基础) */}
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
                {classId !== '' && (
                  <>
                    <Chip
                      size="small"
                      variant="outlined"
                      icon={<GroupsOutlinedIcon />}
                      color={roster.length > 0 ? 'success' : 'default'}
                      label={`花名册 ${roster.length} 人`}
                    />
                    <Button size="small" color="inherit" onClick={() => navigate('/classes/roster')}>
                      {roster.length > 0 ? '管理花名册' : '导入花名册'}
                    </Button>
                  </>
                )}
                {styleName && (
                  <Chip
                    size="small"
                    color="success"
                    variant="outlined"
                    icon={<AutoAwesomeOutlinedIcon />}
                    label={`示范学习生效:${styleName}`}
                  />
                )}
              </Stack>

              {/* 拖拽上传区 */}
              <Paper
                variant="outlined"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  addFiles(e.dataTransfer.files)
                }}
                sx={{
                  border: '2px dashed',
                  borderColor: dragging ? 'primary.main' : 'divider',
                  bgcolor: dragging ? 'action.hover' : 'transparent',
                  borderRadius: 4,
                  py: 5,
                  px: 3,
                  textAlign: 'center',
                  cursor: 'pointer',
                  transition: 'all .2s',
                  '&:hover': { borderColor: 'primary.light', bgcolor: 'action.hover' },
                }}
              >
                <UploadFileOutlinedIcon sx={{ fontSize: 44, color: 'primary.main', mb: 1 }} />
                <Typography variant="subtitle1" gutterBottom>
                  点击选择或拖拽文件到此处
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {mode === 'single'
                    ? '支持 PNG / JPG / JPEG / WEBP / BMP / PDF(扫描件逐页=同一篇多页),同一学生的多页按顺序上传'
                    : '支持图片、扫描 PDF(每页=一篇)与 ZIP 压缩包,压缩包内图片/PDF 每张自动创建一份作文任务'}
                </Typography>
                <input
                  ref={fileInputRef}
                  type="file"
                  hidden
                  multiple
                  accept={mode === 'single' ? UPLOAD_EXTS.join(',') : [...UPLOAD_EXTS, '.zip'].join(',')}
                  onChange={(e) => {
                    if (e.target.files) addFiles(e.target.files)
                    e.target.value = ''
                  }}
                />
              </Paper>

              {/* 学生指派(批量) / 手动指派(单篇) */}
              {mode === 'batch' ? (
                <Box sx={{ mt: 2, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 3 }}>
                  <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
                    <Typography variant="subtitle2">学生指派:</Typography>
                    <ToggleButtonGroup
                      exclusive
                      size="small"
                      value={assignMode}
                      onChange={(_, v) => v && setAssignMode(v as AssignMode)}
                    >
                      <ToggleButton value="recognize" sx={{ px: 1.5 }}>自动识别</ToggleButton>
                      <ToggleButton value="filename" sx={{ px: 1.5 }}>文件名匹配</ToggleButton>
                      <ToggleButton value="order" sx={{ px: 1.5 }}>按名单顺序</ToggleButton>
                    </ToggleButtonGroup>
                    {assignMode === 'order' && (
                      <TextField
                        size="small"
                        type="number"
                        label="起始序号"
                        value={assignOrderStart}
                        onChange={(e) => setAssignOrderStart(e.target.value)}
                        sx={{ width: 110 }}
                      />
                    )}
                    {assignMode !== 'recognize' && roster.length === 0 && (
                      <Typography variant="caption" color="warning.main">
                        尚未导入花名册 —— 先在「4. 班级与作业」选择班级并导入
                      </Typography>
                    )}
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.75 }}>
                    {assignMode === 'recognize' && '从图片内容识别姓名(默认;最稳妥)'}
                    {assignMode === 'filename' && '文件名包含姓名/学号时精确匹配花名册,未命中回退自动识别'}
                    {assignMode === 'order' && `按文件排序依次匹配花名册顺序(当前花名册 ${roster.length} 人;可用下方排序按钮调整)`}
                  </Typography>
                </Box>
              ) : (
                <Stack direction="row" spacing={1.5} sx={{ mt: 2 }}>
                  <TextField
                    size="small"
                    label="学生姓名(可选,留空自动识别)"
                    value={singleName}
                    onChange={(e) => setSingleName(e.target.value)}
                    sx={{ flex: 1 }}
                  />
                  <TextField
                    size="small"
                    label="学号(可选)"
                    value={singleId}
                    onChange={(e) => setSingleId(e.target.value)}
                    sx={{ width: 170 }}
                  />
                </Stack>
              )}

              {/* 已选文件列表 */}
              {fileEntries.length > 0 && (
                <>
                  <Box sx={{ display: 'flex', alignItems: 'center', mt: 2.5, mb: 0.5 }}>
                    <Typography variant="subtitle2" sx={{ flex: 1 }}>
                      已选择 {files.length} 个文件
                      {mode === 'single' && '(按上传顺序作为页序)'}
                    </Typography>
                    {mode === 'batch' && files.length > 1 && (
                      <>
                        <Tooltip title="按名称正序(数字感知,便于按名单次序) ">
                          <IconButton size="small" onClick={() => setFiles((prev) => sortFilesByName(prev, 'asc'))}>
                            <SortByAlphaOutlinedIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="按名称倒序">
                          <IconButton size="small" onClick={() => setFiles((prev) => sortFilesByName(prev, 'desc'))}>
                            <SortByAlphaOutlinedIcon fontSize="small" sx={{ transform: 'scaleY(-1)' }} />
                          </IconButton>
                        </Tooltip>
                      </>
                    )}
                    <Button size="small" color="inherit" startIcon={<DeleteSweepOutlinedIcon />} onClick={clearFiles}>
                      清空
                    </Button>
                  </Box>
                  <List dense sx={{ maxHeight: 460, overflowY: 'auto' }}>
                    {fileEntries.map((entry) => (
                      <ListItem
                        key={`${entry.file.name}-${entry.idx}`}
                        sx={{ border: 1, borderColor: 'divider', borderRadius: 2.5, mb: 0.8 }}
                        secondaryAction={
                          <Stack direction="row" spacing={0.25} alignItems="center">
                            {entry.isImage && (
                              <Tooltip title="编辑图片(灰度/黑白/裁剪/旋转,可重置原图)">
                                <IconButton
                                  edge="end"
                                  size="small"
                                  onClick={() => {
                                    setEditorIndex(entry.idx)
                                    setEditorOpen(true)
                                  }}
                                >
                                  <EditOutlinedIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            {editedOriginals.current.has(entry.file) && (
                              <Tooltip title="重置为原图(撤销全部编辑)">
                                <IconButton edge="end" size="small" color="warning" onClick={() => resetEditedFile(entry.idx)}>
                                  <RestartAltOutlinedIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            <Tooltip title="移除">
                              <IconButton edge="end" size="small" onClick={() => removeFile(entry.idx)}>
                                <CloseOutlinedIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </Stack>
                        }
                      >
                        {entry.previewUrl ? (
                          <Box
                            sx={{
                              width: 150,
                              flexShrink: 0,
                              mr: 1.5,
                              borderRadius: 1.5,
                              border: 1,
                              borderColor: 'divider',
                              bgcolor: '#fff',
                              display: 'grid',
                              placeItems: 'center',
                              overflow: 'hidden',
                              minHeight: 110,
                            }}
                          >
                            <Box
                              component="img"
                              src={entry.previewUrl}
                              alt={entry.file.name}
                              loading="lazy"
                              sx={{ width: '100%', maxHeight: 175, objectFit: 'contain', display: 'block' }}
                            />
                          </Box>
                        ) : (
                          <Box sx={{ width: 150, flexShrink: 0, mr: 1.5, display: 'grid', placeItems: 'center' }}>
                            <InsertDriveFileOutlinedIcon sx={{ color: 'text.secondary' }} />
                          </Box>
                        )}
                        {forceProbeActive ? (
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Tooltip title={entry.file.name} placement="top">
                              <Box sx={{ minWidth: 0 }}>{renderProbeStatus(entry)}</Box>
                            </Tooltip>
                            <Typography variant="caption" color="text.secondary">
                              {(entry.file.size / 1024).toFixed(0)} KB
                              {editedOriginals.current.has(entry.file) ? ' · 已编辑(重置可恢复原图)' : ''}
                            </Typography>
                          </Box>
                        ) : (
                          <ListItemText
                            primary={entry.file.name}
                            secondary={`${(entry.file.size / 1024).toFixed(0)} KB${
                              editedOriginals.current.has(entry.file) ? ' · 已编辑(重置可恢复原图)' : ''
                            }`}
                            primaryTypographyProps={{ fontSize: 13.5, noWrap: true }}
                            secondaryTypographyProps={{ fontSize: 12 }}
                          />
                        )}
                      </ListItem>
                    ))}
                  </List>
                </>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* ================= 右:参数配置区(动态控制矩阵) ================= */}
        <Grid size={{ xs: 12, md: 5 }}>
          <Card>
            <CardContent sx={{ p: 3 }}>
              <Typography variant="h6" gutterBottom>
                批改参数
              </Typography>
              <Typography variant="caption" color="text.secondary">
                动态控制矩阵:切换管线不会改变输出结构与界面渲染
              </Typography>
              <Divider sx={{ my: 2 }} />

              {/* 1. 管线选择 */}
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                1. 选择批改管线
              </Typography>
              <Stack spacing={1.2} sx={{ mb: 2.5 }}>
                {(Object.keys(PIPELINE_META) as PipelineChoice[]).map((key) => {
                  const meta = PIPELINE_META[key]
                  const selected = pipeline === key
                  const isLocal = key === 'PIPELINE_A_LOCAL'
                  return (
                    <Paper
                      key={key}
                      variant="outlined"
                      onClick={() => setPipeline(key)}
                      sx={{
                        p: 1.8,
                        borderRadius: 3,
                        cursor: 'pointer',
                        borderWidth: 2,
                        borderColor: selected ? 'primary.main' : 'divider',
                        bgcolor: selected ? 'action.selected' : 'transparent',
                        transition: 'all .15s',
                        '&:hover': { borderColor: 'primary.light' },
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'flex-start' }}>
                        {isLocal ? (
                          <MemoryOutlinedIcon sx={{ color: 'primary.main', mr: 1.2, mt: 0.2 }} />
                        ) : (
                          <CloudQueueOutlinedIcon sx={{ color: 'primary.main', mr: 1.2, mt: 0.2 }} />
                        )}
                        <Box sx={{ flex: 1 }}>
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Typography variant="subtitle2">{meta.title}</Typography>
                            <Typography variant="caption" color="secondary.main" fontWeight={600}>
                              {meta.subtitle}
                            </Typography>
                          </Box>
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.4 }}>
                            {meta.description}
                          </Typography>
                          <Box sx={{ mt: 0.8, display: 'flex', gap: 0.6, flexWrap: 'wrap' }}>
                            {meta.tags.map((t) => (
                              <Chip key={t} size="small" variant="outlined" label={t} sx={{ fontSize: 11, height: 22 }} />
                            ))}
                          </Box>
                        </Box>
                        <Radio checked={selected} size="small" sx={{ mt: -0.5 }} />
                      </Box>
                    </Paper>
                  )
                })}
              </Stack>

              {/* 2. 评分标准 */}
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                2. 评分标准
              </Typography>
              <ToggleButtonGroup
                exclusive
                fullWidth
                size="small"
                value={standard}
                onChange={(_, v) => v && setStandard(v)}
                sx={{ mb: 2.5 }}
              >
                <ToggleButton value="GAOKAO">高考 25 分制</ToggleButton>
                <ToggleButton value="DSD">DSD / CEFR 等级</ToggleButton>
              </ToggleButtonGroup>

              {/* 3. 细致度 */}
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                3. 批改细致度
              </Typography>
              <ToggleButtonGroup
                exclusive
                fullWidth
                size="small"
                value={detail}
                onChange={(_, v) => v && setDetail(v)}
                sx={{ mb: 1 }}
              >
                <ToggleButton value="LOW">低 · 标错</ToggleButton>
                <ToggleButton value="MEDIUM">中 · 标错+修改</ToggleButton>
                <ToggleButton value="HIGH">高 · 保姆级</ToggleButton>
              </ToggleButtonGroup>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
                {detail === 'LOW' && '仅标注错误位置,适合快速浏览'}
                {detail === 'MEDIUM' && '标注错误并给出修正表达,日常批改推荐'}
                {detail === 'HIGH' && '附详细中文语法解析(如"mit 需接 Dativ,故 dein 应改为 deinem")'}
              </Typography>

              {/* 班级与作业元数据(可选) */}
              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                4. 班级与作业(可选)
              </Typography>
              <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
                <FormControl size="small" fullWidth>
                  <InputLabel>归属班级</InputLabel>
                  <Select
                    label="归属班级"
                    value={classId}
                    onChange={(e) => setClassId(e.target.value as number | '')}
                  >
                    <MenuItem value="">未指定班级</MenuItem>
                    {classes.map((c) => (
                      <MenuItem key={c.id} value={c.id}>
                        {c.name}
                        {c.task_count > 0 ? ` (${c.task_count})` : ''}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <Tooltip title="班级管理(新建 / 花名册 / 数据包)">
                  <Button
                    variant="outlined"
                    size="small"
                    sx={{ minWidth: 40, px: 1 }}
                    onClick={() => navigate('/classes')}
                  >
                    <GroupsOutlinedIcon fontSize="small" />
                  </Button>
                </Tooltip>
              </Stack>
              <TextField
                size="small"
                fullWidth
                label="作业名称(如:第一次月考作文)"
                value={assignmentName}
                onChange={(e) => setAssignmentName(e.target.value)}
                sx={{ mb: 1.5 }}
              />
              <TextField
                size="small"
                fullWidth
                label="作文题目/要求(可选)"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                multiline
                minRows={1}
                maxRows={3}
                sx={{ mb: 2 }}
              />

              {/* 答题卷入口(新窗口打印;选择班级/学生后绑定二维码,见对话框) */}
              <Box sx={{ mb: 1.5, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                <Button size="small" variant="text" onClick={openSheetDialog}>
                  打印标准答题卷(绑定学生二维码)
                </Button>
                <Button
                  size="small"
                  variant="text"
                  onClick={() => window.open('/print/answer-sheet-continuation', '_blank')}
                >
                  打印续写纸(自动并页)
                </Button>
              </Box>

              {/* OCR 复核开关(两条管线行为一致) */}
              <FormControlLabel
                  sx={{ mb: 1.5 }}
                  control={
                    <Switch
                      checked={requireReview}
                      onChange={(e) => setRequireReview(e.target.checked)}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">开启 OCR 人工复核</Typography>
                      <Typography variant="caption" color="text.secondary">
                        识别完成后暂停,教师校对转录文本再继续评分
                      </Typography>
                    </Box>
                  }
                />

              {/* 客户端压缩开关 */}
              <FormControlLabel
                sx={{ mb: 1 }}
                control={<Switch checked={compress} onChange={(e) => setCompress(e.target.checked)} />}
                label={
                  <Box>
                    <Typography variant="body2">上传前压缩图片</Typography>
                    <Typography variant="caption" color="text.secondary">
                      长边≤2200px,大幅减少上传时间;ZIP/HEIC 自动跳过,失败回退原图
                    </Typography>
                  </Box>
                }
              />

              {/* 压缩/上传进度 */}
              {(compressing || (submitting && progress != null)) && (
                <Box sx={{ mb: 1.5 }}>
                  <LinearProgress
                    variant={progress == null ? 'indeterminate' : 'determinate'}
                    value={progress ?? undefined}
                  />
                  <Typography variant="caption" color="text.secondary">
                    {compressing ? '正在压缩图片…' : `上传进度 ${progress}%`}
                  </Typography>
                </Box>
              )}

              {/* 错误提示 */}
              {error && (
                <Alert severity="error" sx={{ mb: 2, borderRadius: 2.5 }}>
                  {error}
                </Alert>
              )}

              {/* 提交按钮 */}
              <Button
                fullWidth
                size="large"
                variant="contained"
                disabled={submitting || files.length === 0}
                startIcon={submitting ? undefined : <RocketLaunchOutlinedIcon />}
                onClick={handleSubmit}
                sx={{
                  py: 1.4,
                  fontSize: 16,
                  background: 'linear-gradient(135deg, #3F51B5 0%, #5C6BC0 100%)',
                }}
              >
                {submitting
                  ? '正在创建任务...'
                  : mode === 'single'
                    ? '开始批改'
                    : `开始批量批改(${files.length} 篇)`}
              </Button>

              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1.5 }}>
                <AutoAwesomeOutlinedIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
                <Typography variant="caption" color="text.disabled">
                  报告由后端统一渲染,双管线输出结构完全一致
                </Typography>
              </Box>
            </CardContent>
          </Card>

          {/* 流程说明小卡片 */}
          <Card sx={{ mt: 3 }}>
            <CardContent sx={{ p: 2.5 }}>
              <Typography variant="subtitle2" gutterBottom>
                工作流程
              </Typography>
              <Stack spacing={0.8}>
                {[
                  '上传手写作文图片(支持多页 / 批量 / ZIP / PDF)',
                  pipeline === 'PIPELINE_A_LOCAL'
                    ? requireReview
                      ? '本地 VLM 两段式:识别 → 人工复核 → 评分'
                      : '本地 VLM 单次执行:识别 + 评分一次完成'
                    : '云端两段式:OCR 转录 → DeepSeek 评分',
                  requireReview
                    ? '人工复核转录文本后继续评分'
                    : '全自动流水线,无需人工介入',
                  '分屏审阅报告,可编辑、保存与导出',
                ].map((step, i) => (
                  <Box key={i} sx={{ display: 'flex', gap: 1.2, alignItems: 'flex-start' }}>
                    <Box
                      sx={{
                        minWidth: 20,
                        height: 20,
                        borderRadius: '50%',
                        bgcolor: 'primary.main',
                        color: '#fff',
                        fontSize: 11,
                        display: 'grid',
                        placeItems: 'center',
                        fontWeight: 700,
                        mt: 0.2,
                      }}
                    >
                      {i + 1}
                    </Box>
                    <Typography variant="body2" color="text.secondary">
                      {step}
                    </Typography>
                  </Box>
                ))}
              </Stack>
              <Box sx={{ mt: 1.5, display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <ImageOutlinedIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
                <Typography variant="caption" color="text.disabled">
                  图片仅保存在本机(backend/data/uploads)
                </Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* ================= 图片编辑器(灰度/黑白/裁剪/旋转;可撤销/重置,支持翻页) ================= */}
      <ImageEditorDialog
        open={editorOpen}
        files={files}
        startIndex={Math.min(editorIndex, Math.max(files.length - 1, 0))}
        getOriginal={originalOf}
        onApplyIndex={applyEditedFile}
        onResetIndex={resetEditedFile}
        onClose={() => setEditorOpen(false)}
      />

      {/* 标准答题卷打印:选择班级/学生并绑定其身份二维码后打开打印页 */}
      <Dialog open={sheetDialogOpen} onClose={() => setSheetDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ fontSize: 16 }}>打印标准答题卷(绑定学生)</DialogTitle>
        <DialogContent dividers>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
            选择学生后,其身份二维码将打印在卷面右上区;上传该卷照片时系统将本地扫码直接获取身份
            (未扫到则自动回退手写识别)。未绑定的旧版空白卷仍可正常使用。
          </Typography>
          <FormControl size="small" fullWidth sx={{ mb: 1.5 }}>
            <InputLabel>班级</InputLabel>
            <Select
              label="班级"
              value={sheetClassId}
              onChange={(e) => {
                const value = e.target.value as number | ''
                setSheetClassId(value)
                setSheetStudentId('')
                setSheetRoster([])
                if (value !== '') void loadSheetRoster(Number(value))
              }}
            >
              {classes.map((cls) => (
                <MenuItem key={cls.id} value={cls.id}>
                  {cls.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" fullWidth disabled={sheetClassId === '' || sheetRosterLoading}>
            <InputLabel>学生</InputLabel>
            <Select
              label="学生"
              value={sheetStudentId}
              onChange={(e) => setSheetStudentId(e.target.value as number | '')}
            >
              {sheetRoster.map((member) => (
                <MenuItem key={member.id} value={member.id}>
                  {member.name}
                  {member.student_id ? ` (${member.student_id})` : ''}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {sheetRosterLoading && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1.5 }}>
              <CircularProgress size={14} />
              <Typography variant="caption" color="text.secondary">
                花名册加载中…
              </Typography>
            </Stack>
          )}
          {sheetClassId !== '' && !sheetRosterLoading && sheetRoster.length === 0 && (
            <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 1.5 }}>
              该班级暂无花名册成员;请先在「班级管理」导入花名册。
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setSheetDialogOpen(false)}>
            取消
          </Button>
          <Button
            variant="contained"
            disabled={sheetClassId === '' || sheetStudentId === ''}
            onClick={() => {
              setSheetDialogOpen(false)
              window.open(
                `/print/answer-sheet?classId=${sheetClassId}&rosterId=${sheetStudentId}`,
                '_blank',
              )
            }}
          >
            打开打印页
          </Button>
        </DialogActions>
      </Dialog>

      {/* 花名册与班级管理已迁移至「班级管理」功能区(/classes);工作台仅保留名单数据的只读加载(指派模式) */}
    </Box>
  )
}
