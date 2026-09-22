/**
 * 批改队列页
 *
 * 功能:
 * - 任务列表(状态 / 管线 / 标准 / 得分)自动轮询刷新;
 * - 多条件筛选:状态 / 批次 / 创建日期范围;
 * - 多选批量操作:批量重试(失败/待复核/中断)、批量删除(含文件清理)、一键重试全部失败;
 * - 单任务操作:查看报告 / 待复核跳转 / 单条重试。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import CloseOutlinedIcon from '@mui/icons-material/CloseOutlined'
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined'
import ExpandMoreOutlinedIcon from '@mui/icons-material/ExpandMoreOutlined'
import FileDownloadOutlinedIcon from '@mui/icons-material/FileDownloadOutlined'
import FilterAltOffOutlinedIcon from '@mui/icons-material/FilterAltOffOutlined'
import OpenInNewOutlinedIcon from '@mui/icons-material/OpenInNewOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import RefreshOutlinedIcon from '@mui/icons-material/RefreshOutlined'
import RestartAltOutlinedIcon from '@mui/icons-material/RestartAltOutlined'
import TableViewOutlinedIcon from '@mui/icons-material/TableViewOutlined'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  batchDeleteTasks,
  batchRetryTasks,
  downloadBlob,
  exportGradesBlob,
  exportReportsBlob,
  extractBlobError,
  extractErrorMessage,
  fetchClasses,
  fetchTasks,
  retryTask,
} from '../api/client'
import StatusChip from '../components/StatusChip'
import PageHeader from '../components/PageHeader'
import type { SchoolClass, TaskBrief, TaskStatus } from '../types'
import { DETAIL_LABEL, PIPELINE_META, STAGE_LABEL, STANDARD_LABEL } from '../types'

/** 轮询间隔(毫秒) */
const POLL_INTERVAL = 3000

/** 阶段 -> 进度百分比估算 */
const STAGE_PERCENT: Record<string, number> = {
  UPLOADED: 5,
  OCR: 40,
  GRADING: 70,
  RENDERING: 92,
  DONE: 100,
}

/** 状态筛选选项 */
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '排队中' },
  { value: 'PROCESSING', label: '批改中' },
  { value: 'WAITING_REVIEW', label: '待人工复核' },
  { value: 'COMPLETED', label: '已完成' },
  { value: 'FAILED', label: '失败' },
]

/** 可批量重试的状态 */
const RETRYABLE: TaskStatus[] = ['FAILED', 'WAITING_REVIEW', 'PROCESSING']

/** 格式化时间(ISO -> 本地简短格式) */
function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function QueuePage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const batchFilter = searchParams.get('batch')

  const [tasks, setTasks] = useState<TaskBrief[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ---------- 筛选条件 ----------
  const [statusFilter, setStatusFilter] = useState('')
  const [classFilter, setClassFilter] = useState<number | ''>('')
  const [createdFrom, setCreatedFrom] = useState('')
  const [createdTo, setCreatedTo] = useState('')
  const [classes, setClasses] = useState<SchoolClass[]>([])

  // ---------- 选择与批量操作 ----------
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [retrying, setRetrying] = useState<number | null>(null)
  const [batchBusy, setBatchBusy] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [notice, setNotice] = useState<{ severity: 'success' | 'warning' | 'error'; text: string } | null>(null)

  // ---------- 批量打印(#12 方向二) ----------
  const [printDialogOpen, setPrintDialogOpen] = useState(false)
  const [printVariant, setPrintVariant] = useState<'student' | 'teacher'>('student')
  const [printIncludeImages, setPrintIncludeImages] = useState(false)

  const timerRef = useRef<number | null>(null)

  // 加载班级列表(筛选器选项)
  useEffect(() => {
    fetchClasses()
      .then(setClasses)
      .catch(() => setClasses([]))
  }, [])

  /** 拉取任务列表(带当前筛选条件) */
  const loadTasks = useCallback(
    async (showLoading = false) => {
      if (showLoading) setLoading(true)
      try {
        const data = await fetchTasks({
          status: statusFilter || undefined,
          batch_id: batchFilter ?? undefined,
          class_id: classFilter === '' ? undefined : classFilter,
          created_from: createdFrom || undefined,
          created_to: createdTo || undefined,
          limit: 200,
        })
        setTasks(data.items)
        setTotal(data.total)
        setError(null)
        // 清理已不在列表中的选中项
        setSelected((prev) => {
          const visible = new Set(data.items.map((t) => t.id))
          const next = new Set([...prev].filter((id) => visible.has(id)))
          return next.size === prev.size ? prev : next
        })
      } catch (e) {
        setError(extractErrorMessage(e))
      } finally {
        if (showLoading) setLoading(false)
      }
    },
    [statusFilter, batchFilter, classFilter, createdFrom, createdTo],
  )

  // 首次加载 + 筛选条件变化
  useEffect(() => {
    loadTasks(true)
  }, [loadTasks])

  // 自动轮询:只要有处理中的任务就持续刷新
  useEffect(() => {
    const hasActive = tasks.some((t) => t.status === 'PENDING' || t.status === 'PROCESSING')
    if (hasActive) {
      timerRef.current = window.setInterval(() => loadTasks(false), POLL_INTERVAL)
    }
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [tasks, loadTasks])

  // ---------- 选择逻辑 ----------
  const selectableIds = useMemo(() => tasks.map((t) => t.id), [tasks])
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id))
  const someSelected = selected.size > 0 && !allSelected

  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(selectableIds))
  }
  const toggleOne = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // ---------- 分组展示(仅渲染层;筛选/多选/批量操作不受影响) ----------
  // 组键:作业名称(含班级)→ 批次 → 其他;组间按组内最新任务时间倒序
  const groupedTasks = useMemo(() => {
    const groups = new Map<
      string,
      { key: string; title: string; className?: string | null; items: TaskBrief[] }
    >()
    for (const task of tasks) {
      let key: string
      let title: string
      if (task.assignment_name) {
        key = `assign:${task.class_id ?? 'x'}:${task.assignment_name}`
        title = task.assignment_name
      } else if (task.batch_id) {
        key = `batch:${task.batch_id}`
        title = `批次 ${task.batch_id}`
      } else {
        key = 'misc'
        title = '其他 / 历史任务'
      }
      const group = groups.get(key)
      if (group) group.items.push(task)
      else groups.set(key, { key, title, className: task.class_name ?? null, items: [task] })
    }
    return [...groups.values()].sort((a, b) =>
      (b.items[0]?.created_at ?? '').localeCompare(a.items[0]?.created_at ?? ''),
    )
  }, [tasks])

  // 折叠状态(会话内;默认全部展开)
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const toggleGroupCollapsed = (key: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  /** 组内全选/取消(与全局 selected 集合同一语义,支持跨组多选) */
  const toggleGroupAll = (ids: number[]) => {
    setSelected((prev) => {
      const next = new Set(prev)
      const allIn = ids.length > 0 && ids.every((id) => next.has(id))
      for (const id of ids) {
        if (allIn) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }

  // ---------- 操作 ----------
  /** 单任务重试 */
  const handleRetry = async (taskId: number) => {
    setRetrying(taskId)
    setNotice(null)
    try {
      await retryTask(taskId)
      await loadTasks(false)
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setRetrying(null)
    }
  }

  /** 批量重试选中任务 */
  const handleBatchRetry = async () => {
    if (selected.size === 0) return
    setBatchBusy(true)
    setNotice(null)
    try {
      const resp = await batchRetryTasks([...selected])
      setNotice({
        severity: resp.skipped.length > 0 ? 'warning' : 'success',
        text: `批量重试:已重新入队 ${resp.retried.length} 个任务${
          resp.skipped.length > 0 ? `,跳过 ${resp.skipped.length} 个(状态不允许)` : ''
        }`,
      })
      setSelected(new Set())
      await loadTasks(false)
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBatchBusy(false)
    }
  }

  /** 一键重试全部失败任务 */
  const handleRetryAllFailed = async () => {
    const failedIds = tasks.filter((t) => t.status === 'FAILED').map((t) => t.id)
    if (failedIds.length === 0) {
      setNotice({ severity: 'warning', text: '当前筛选范围内没有失败任务' })
      return
    }
    setBatchBusy(true)
    setNotice(null)
    try {
      const resp = await batchRetryTasks(failedIds)
      setNotice({ severity: 'success', text: `已重新入队 ${resp.retried.length} 个失败任务` })
      await loadTasks(false)
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBatchBusy(false)
    }
  }

  /** 导出目标:有选中用选中,否则用当前筛选范围内的全部任务 */
  const exportTargetIds = (): number[] =>
    selected.size > 0 ? [...selected] : tasks.map((t) => t.id)

  /** 批量导出报告(ZIP)[#12] */
  const handleExportReports = async () => {
    const ids = exportTargetIds()
    if (ids.length === 0) {
      setNotice({ severity: 'warning', text: '当前没有可导出的任务' })
      return
    }
    setBatchBusy(true)
    setNotice(null)
    try {
      const blob = await exportReportsBlob(ids)
      downloadBlob(blob, `批改报告_${new Date().toISOString().slice(0, 10)}.zip`)
      setNotice({ severity: 'success', text: `已导出 ${ids.length} 个任务的报告(ZIP)` })
    } catch (e) {
      setError(await extractBlobError(e))
    } finally {
      setBatchBusy(false)
    }
  }

  /** 批量导出成绩表(CSV)[#12] */
  const handleExportGrades = async () => {
    const ids = exportTargetIds()
    if (ids.length === 0) {
      setNotice({ severity: 'warning', text: '当前没有可导出的任务' })
      return
    }
    setBatchBusy(true)
    setNotice(null)
    try {
      const blob = await exportGradesBlob(ids)
      downloadBlob(blob, `成绩表_${new Date().toISOString().slice(0, 10)}.csv`)
      setNotice({ severity: 'success', text: `已导出 ${ids.length} 条成绩记录(CSV)` })
    } catch (e) {
      setError(await extractBlobError(e))
    } finally {
      setBatchBusy(false)
    }
  }

  /** 批量打印(仅勾选中已完成的报告)[#12] */
  const handleOpenBatchPrint = () => {
    const completedIds = tasks
      .filter((t) => selected.has(t.id) && t.status === 'COMPLETED')
      .map((t) => t.id)
    if (completedIds.length === 0) {
      setNotice({ severity: 'warning', text: '请先勾选至少一个已完成的批改任务再打印' })
      setPrintDialogOpen(false)
      return
    }
    const params = new URLSearchParams({
      task_ids: completedIds.join(','),
      variant: printVariant,
      include_images: printIncludeImages ? '1' : '0',
    })
    window.open(`/print/batch?${params.toString()}`, '_blank')
    setPrintDialogOpen(false)
  }

  /** 批量删除(经确认对话框) */
  const handleBatchDelete = async () => {
    setConfirmDeleteOpen(false)
    if (selected.size === 0) return
    setBatchBusy(true)
    setNotice(null)
    try {
      const resp = await batchDeleteTasks([...selected])
      setNotice({
        severity: resp.skipped.length > 0 ? 'warning' : 'success',
        text: `批量删除:已删除 ${resp.deleted.length} 个任务(含图片与错题记录)${
          resp.skipped.length > 0 ? `,跳过 ${resp.skipped.length} 个(处理中/排队中)` : ''
        }`,
      })
      setSelected(new Set())
      await loadTasks(false)
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBatchBusy(false)
    }
  }

  /** 清空所有筛选 */
  const clearFilters = () => {
    setStatusFilter('')
    setClassFilter('')
    setCreatedFrom('')
    setCreatedTo('')
    setSearchParams({})
  }

  const hasFilters = Boolean(statusFilter || classFilter !== '' || createdFrom || createdTo || batchFilter)

  return (
    <Box sx={{ maxWidth: 1440, mx: 'auto' }}>
      {/* ================= 标题栏(统一规范) ================= */}
      <PageHeader
        title="批改队列"
        subtitle="实时跟踪全部批改任务;支持按状态 / 班级 / 创建时间筛选,勾选任务后可批量重试、导出、打印或删除。"
        chips={
          <>
            <Chip size="small" variant="outlined" label={`共 ${total} 个任务`} />
            {batchFilter && (
              <Chip
                size="small"
                color="primary"
                label={`批次:${batchFilter}`}
                onDelete={() => setSearchParams({})}
                deleteIcon={<CloseOutlinedIcon />}
              />
            )}
          </>
        }
        actions={
          <>
            <Button
              size="small"
              color="inherit"
              startIcon={<FileDownloadOutlinedIcon />}
              onClick={handleExportReports}
              disabled={batchBusy}
            >
              导出报告
            </Button>
            <Button
              size="small"
              color="inherit"
              startIcon={<TableViewOutlinedIcon />}
              onClick={handleExportGrades}
              disabled={batchBusy}
            >
              导出成绩表
            </Button>
            <Button
              size="small"
              color="inherit"
              startIcon={<PrintOutlinedIcon />}
              onClick={() => setPrintDialogOpen(true)}
              disabled={batchBusy}
            >
              打印选中
            </Button>
            <Button
              size="small"
              color="inherit"
              startIcon={<RestartAltOutlinedIcon />}
              onClick={handleRetryAllFailed}
              disabled={batchBusy}
            >
              重试全部失败
            </Button>
            <Button
              size="small"
              color="inherit"
              startIcon={loading ? <CircularProgress size={14} /> : <RefreshOutlinedIcon />}
              onClick={() => loadTasks(true)}
              disabled={loading}
            >
              刷新
            </Button>
          </>
        }
      />

      {/* ================= 筛选栏 ================= */}
      <Card sx={{ mb: 2 }}>
        <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
          <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>状态</InputLabel>
              <Select
                label="状态"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                {STATUS_OPTIONS.map((opt) => (
                  <MenuItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>班级</InputLabel>
              <Select
                label="班级"
                value={classFilter}
                onChange={(e) => setClassFilter(e.target.value as number | '')}
              >
                <MenuItem value="">全部班级</MenuItem>
                {classes.map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {c.name}
                    {c.task_count > 0 ? ` (${c.task_count})` : ''}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              size="small"
              label="创建起始日"
              type="date"
              value={createdFrom}
              onChange={(e) => setCreatedFrom(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              sx={{ width: 170 }}
            />
            <TextField
              size="small"
              label="创建截止日"
              type="date"
              value={createdTo}
              onChange={(e) => setCreatedTo(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              sx={{ width: 170 }}
            />
            {hasFilters && (
              <Button
                size="small"
                color="inherit"
                startIcon={<FilterAltOffOutlinedIcon />}
                onClick={clearFilters}
              >
                清除筛选
              </Button>
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* ================= 批量操作工具栏 ================= */}
      {selected.size > 0 && (
        <Alert
          severity="info"
          variant="outlined"
          sx={{ mb: 2, borderRadius: 3, alignItems: 'center' }}
          action={
            <Stack direction="row" spacing={1}>
              <Button
                size="small"
                startIcon={<RestartAltOutlinedIcon />}
                onClick={handleBatchRetry}
                disabled={batchBusy}
              >
                批量重试
              </Button>
              <Button
                size="small"
                color="error"
                startIcon={<DeleteOutlinedIcon />}
                onClick={() => setConfirmDeleteOpen(true)}
                disabled={batchBusy}
              >
                批量删除
              </Button>
            </Stack>
          }
        >
          已选择 <b>{selected.size}</b> 个任务
        </Alert>
      )}

      {/* 操作结果通知 */}
      {notice && (
        <Alert severity={notice.severity} onClose={() => setNotice(null)} sx={{ mb: 2, borderRadius: 3 }}>
          {notice.text}
        </Alert>
      )}

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2, borderRadius: 3 }}>
          {error}
        </Alert>
      )}

      {/* ================= 任务表格 ================= */}
      <Card>
        <TableContainer>
          <Table size="small" sx={{ minWidth: 1080 }}>
            <TableHead>
              <TableRow>
                <TableCell padding="checkbox">
                  <Checkbox
                    size="small"
                    checked={allSelected}
                    indeterminate={someSelected}
                    onChange={toggleAll}
                    disabled={tasks.length === 0}
                  />
                </TableCell>
                <TableCell width={70}>任务</TableCell>
                <TableCell>学生</TableCell>
                <TableCell>管线</TableCell>
                <TableCell>标准 / 细致度</TableCell>
                <TableCell width={130}>状态</TableCell>
                <TableCell width={170}>进度阶段</TableCell>
                <TableCell width={100}>得分</TableCell>
                <TableCell width={130}>创建时间</TableCell>
                <TableCell width={120} align="right">
                  操作
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {tasks.length === 0 && !loading && (
                <TableRow>
                  <TableCell colSpan={10} align="center" sx={{ py: 8 }}>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      {hasFilters ? '当前筛选条件下没有任务' : '暂无批改任务'}
                    </Typography>
                    {hasFilters ? (
                      <Button size="small" onClick={clearFilters}>
                        清除筛选条件
                      </Button>
                    ) : (
                      <Button size="small" onClick={() => navigate('/')}>
                        去工作台创建第一个任务
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              )}

              {groupedTasks.map((group) => {
                const groupIds = group.items.map((t) => t.id)
                const groupAll = groupIds.length > 0 && groupIds.every((gid) => selected.has(gid))
                const groupSome = groupIds.some((gid) => selected.has(gid))
                const collapsed = collapsedGroups.has(group.key)
                const doneCount = group.items.filter((t) => t.status === 'COMPLETED').length
                return (
                  <Fragment key={group.key}>
                    {/* 分组标题行:三态全选 + 折叠 + 标题/班级/计数 */}
                    <TableRow sx={{ bgcolor: 'action.hover' }}>
                      <TableCell padding="checkbox">
                        <Checkbox
                          size="small"
                          checked={groupAll}
                          indeterminate={!groupAll && groupSome}
                          onChange={() => toggleGroupAll(groupIds)}
                        />
                      </TableCell>
                      <TableCell colSpan={9} sx={{ py: 0.75 }}>
                        <Stack
                          direction="row"
                          spacing={1}
                          alignItems="center"
                          sx={{ cursor: 'pointer', flexWrap: 'wrap', rowGap: 0.5 }}
                          onClick={() => toggleGroupCollapsed(group.key)}
                        >
                          <IconButton size="small" aria-label={collapsed ? '展开分组' : '收起分组'}
                            onClick={(e) => {
                              e.stopPropagation()
                              toggleGroupCollapsed(group.key)
                            }}
                          >
                            <ExpandMoreOutlinedIcon
                              fontSize="small"
                              sx={{ transform: collapsed ? 'rotate(-90deg)' : 'none', transition: 'transform .15s' }}
                            />
                          </IconButton>
                          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                            {group.title}
                          </Typography>
                          {group.className && (
                            <Chip size="small" variant="outlined" color="secondary" label={group.className} />
                          )}
                          <Chip size="small" variant="outlined" label={`共 ${group.items.length} · 已完成 ${doneCount}`} />
                        </Stack>
                      </TableCell>
                    </TableRow>
                    {!collapsed &&
                      group.items.map((task) => {
                const meta = PIPELINE_META[task.pipeline_used ?? task.pipeline_choice]
                const percent = STAGE_PERCENT[task.stage] ?? 0
                const active = task.status === 'PENDING' || task.status === 'PROCESSING'
                const checked = selected.has(task.id)
                return (
                  <TableRow key={task.id} hover selected={checked}>
                    <TableCell padding="checkbox">
                      <Checkbox size="small" checked={checked} onChange={() => toggleOne(task.id)} />
                    </TableCell>
                    <TableCell>#{task.id}</TableCell>
                    <TableCell>
                      <Typography variant="body2" fontWeight={500}>
                        {task.student_name || '未知'}
                      </Typography>
                      {task.student_id && (
                        <Typography variant="caption" color="text.secondary">
                          {task.student_id}
                        </Typography>
                      )}
                      {(task.class_name || task.assignment_name) && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }} noWrap>
                          {[task.class_name, task.assignment_name].filter(Boolean).join(' · ')}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Tooltip title={task.fallback_triggered ? '管线 A 失败后自动切换至 B' : meta.description}>
                        <Stack direction="row" spacing={0.5} alignItems="center">
                          <Typography variant="body2" noWrap>
                            {task.pipeline_used === 'PIPELINE_B_CLOUD'
                              ? 'B · 云端'
                              : task.pipeline_used === 'PIPELINE_A_LOCAL'
                                ? 'A · 本地'
                                : task.pipeline_choice === 'PIPELINE_B_CLOUD'
                                  ? 'B · 云端'
                                  : 'A · 本地'}
                          </Typography>
                          {task.fallback_triggered && (
                            <Chip size="small" color="warning" variant="outlined" label="转移" sx={{ height: 20, fontSize: 10.5 }} />
                          )}
                        </Stack>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" noWrap>
                        {STANDARD_LABEL[task.grading_standard]}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {DETAIL_LABEL[task.detail_level]}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <StatusChip status={task.status} />
                    </TableCell>
                    <TableCell>
                      {active ? (
                        <Box>
                          <Typography variant="caption" color="text.secondary">
                            {STAGE_LABEL[task.stage]}
                          </Typography>
                          <LinearProgress
                            variant="determinate"
                            value={percent}
                            sx={{ mt: 0.5, height: 6, borderRadius: 3 }}
                          />
                        </Box>
                      ) : (
                        <Typography variant="caption" color="text.secondary">
                          {STAGE_LABEL[task.stage]}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      {task.overall_score ? (
                        <Chip size="small" color="primary" variant="outlined" label={task.overall_score} />
                      ) : (
                        <Typography variant="caption" color="text.disabled">
                          —
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {formatTime(task.created_at)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                        <Tooltip title={task.status === 'WAITING_REVIEW' ? '去复核' : '查看报告'}>
                          <IconButton size="small" onClick={() => navigate(`/review/${task.id}`)}>
                            <OpenInNewOutlinedIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        {RETRYABLE.includes(task.status) && (
                          <Tooltip title="重新处理">
                            <IconButton
                              size="small"
                              color="warning"
                              disabled={retrying === task.id}
                              onClick={() => handleRetry(task.id)}
                            >
                              {retrying === task.id ? (
                                <CircularProgress size={16} />
                              ) : (
                                <RestartAltOutlinedIcon fontSize="small" />
                              )}
                            </IconButton>
                          </Tooltip>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                )
                      })}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Card>

      <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1.5 }}>
        处理中的任务每 {POLL_INTERVAL / 1000} 秒自动刷新;勾选任务后可批量重试或删除(删除会同时清理图片与错题记录)。
      </Typography>

      {/* ================= 删除确认对话框 ================= */}
      <Dialog open={confirmDeleteOpen} onClose={() => setConfirmDeleteOpen(false)}>
        <DialogTitle>确认删除 {selected.size} 个任务?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            将同时删除所选任务的批改结果、错题记录与上传图片文件,此操作不可撤销。
            处理中或排队中的任务会被自动跳过。
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setConfirmDeleteOpen(false)}>
            取消
          </Button>
          <Button color="error" variant="contained" onClick={handleBatchDelete} disabled={batchBusy}>
            确认删除
          </Button>
        </DialogActions>
      </Dialog>

      {/* ================= 批量打印设置对话框(#12) ================= */}
      <Dialog open={printDialogOpen} onClose={() => setPrintDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>批量打印选中任务</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            将在新标签页打开合并的打印预览(每个学生从新页开始),仅供已完成批改的任务。
          </DialogContentText>
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
                <ToggleButton value="student">学生版订正单</ToggleButton>
                <ToggleButton value="teacher">教师版报告</ToggleButton>
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
                    仅建议少量任务时勾选(每张原图占一页)
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
          <Button variant="contained" startIcon={<PrintOutlinedIcon />} onClick={handleOpenBatchPrint}>
            打开打印预览
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

