/**
 * 练习卷:依据历史作业错因数据生成练习卷 + 标准答案
 *
 * - 生成对话框:来源多选(一次/多次/全部;可按班级筛选)+ 题型多选 + 题数;
 * - 列表:标题 / 时间 / 班级 / 题型 chips / 题数;可打开详情、打印、删除(二次确认);
 * - 数据来源为已批改任务(与错题本/班级分析同源,只读)。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Snackbar,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import AddOutlinedIcon from '@mui/icons-material/AddOutlined'
import DeleteOutlineOutlinedIcon from '@mui/icons-material/DeleteOutlineOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import QuizOutlinedIcon from '@mui/icons-material/QuizOutlined'
import { useNavigate } from 'react-router-dom'
import {
  deletePracticeSheet,
  extractErrorMessage,
  fetchClasses,
  fetchPracticeOptions,
  fetchPracticeSheets,
  fetchPracticeSources,
  generatePracticeSheet,
} from '../api/client'
import type { PracticeOptions, PracticeSheetBrief, PracticeSourceItem, SchoolClass } from '../types'

/** 来源项唯一键(class_id 可能为空) */
function sourceKey(item: Pick<PracticeSourceItem, 'class_id' | 'name'>): string {
  return `${item.class_id ?? 'null'}::${item.name ?? ''}`
}

export default function PracticePage() {
  const navigate = useNavigate()
  const [options, setOptions] = useState<PracticeOptions | null>(null)
  const [sheets, setSheets] = useState<PracticeSheetBrief[]>([])
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')

  // ---------- 生成对话框 ----------
  const [dialogOpen, setDialogOpen] = useState(false)
  const [sources, setSources] = useState<PracticeSourceItem[]>([])
  const [sourcesLoading, setSourcesLoading] = useState(false)
  const [filterClassId, setFilterClassId] = useState<number | ''>('')
  const [scope, setScope] = useState<'selected' | 'all'>('selected')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [types, setTypes] = useState<string[]>([])
  const [count, setCount] = useState(10)
  const [classId, setClassId] = useState<number | ''>('')
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState('')

  // ---------- 删除确认 ----------
  const [deleting, setDeleting] = useState<PracticeSheetBrief | null>(null)

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [opts, list, classList] = await Promise.all([
        fetchPracticeOptions(),
        fetchPracticeSheets(),
        fetchClasses(),
      ])
      setOptions(opts)
      setSheets(list)
      setClasses(classList)
      setCount(opts.default_count)
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  const loadSources = useCallback(async (classIdFilter: number | '') => {
    setSourcesLoading(true)
    try {
      const data = await fetchPracticeSources(classIdFilter === '' ? null : classIdFilter)
      setSources(data.assignments)
    } catch (e) {
      setGenerateError(extractErrorMessage(e))
    } finally {
      setSourcesLoading(false)
    }
  }, [])

  const openDialog = () => {
    setDialogOpen(true)
    setGenerateError('')
    setScope('selected')
    setSelected(new Set())
    setTypes(options ? options.question_types.slice(0, 2).map((t) => t.key) : [])
    setCount(options?.default_count ?? 10)
    setClassId('')
    setFilterClassId('')
    void loadSources('')
  }

  const toggleType = (key: string) => {
    setTypes((prev) => (prev.includes(key) ? prev.filter((t) => t !== key) : [...prev, key]))
  }

  const toggleSource = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const labelOf = useMemo(() => {
    const map = new Map<string, string>()
    options?.question_types.forEach((t) => map.set(t.key, t.label))
    return map
  }, [options])

  const handleGenerate = async () => {
    if (!options) return
    if (types.length === 0) {
      setGenerateError('请至少选择一种题型。')
      return
    }
    if (scope === 'selected' && selected.size === 0) {
      setGenerateError('请至少勾选一次作业,或将范围切换为「全部作业」。')
      return
    }
    setGenerating(true)
    setGenerateError('')
    try {
      const payload = {
        scope,
        assignments:
          scope === 'selected'
            ? sources
                .filter((item) => selected.has(sourceKey(item)))
                .map((item) => ({ class_id: item.class_id ?? null, name: item.name ?? null }))
            : [],
        question_types: types,
        count,
        class_id: classId === '' ? null : classId,
      }
      const sheet = await generatePracticeSheet(payload)
      setDialogOpen(false)
      setToast(`练习卷「${sheet.title}」已生成(${sheet.question_count} 题)`)
      navigate(`/practice/${sheet.id}`)
    } catch (e) {
      setGenerateError(extractErrorMessage(e))
    } finally {
      setGenerating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleting) return
    try {
      await deletePracticeSheet(deleting.id)
      setToast(`已删除「${deleting.title}」`)
      setDeleting(null)
      await loadAll()
    } catch (e) {
      setError(extractErrorMessage(e))
      setDeleting(null)
    }
  }

  return (
    <Stack spacing={2.5}>
      {/* ---------- 页头 ---------- */}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'center' }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h5">练习卷</Typography>
          <Typography variant="body2" color="text.secondary">
            依据已批改作业的真实错因出一份针对性练习(含标准答案),可打印、可导出。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddOutlinedIcon />} onClick={openDialog} disabled={!options}>
          生成练习卷
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ borderRadius: 2.5 }}>
          {error}
        </Alert>
      )}

      {/* ---------- 列表 ---------- */}
      {loading ? (
        <Box sx={{ py: 8, textAlign: 'center' }}>
          <CircularProgress />
        </Box>
      ) : sheets.length === 0 ? (
        <Paper sx={{ p: 4, borderRadius: 3, textAlign: 'center', color: 'text.secondary' }}>
          <QuizOutlinedIcon sx={{ fontSize: 40, mb: 1 }} />
          <Typography variant="body2">还没有练习卷。先完成一些批改,再点「生成练习卷」。</Typography>
        </Paper>
      ) : (
        <Stack spacing={1.5}>
          {sheets.map((sheet) => (
            <Paper key={sheet.id} sx={{ p: 2, borderRadius: 3 }}>
              <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems={{ md: 'center' }}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }} noWrap>
                    {sheet.title}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {new Date(sheet.created_at).toLocaleString()}
                    {sheet.class_name ? ` · ${sheet.class_name}` : ''}
                    {` · 共 ${sheet.question_count} 题`}
                    {sheet.model ? ` · 模型 ${sheet.model}` : ''}
                  </Typography>
                  <Stack direction="row" spacing={0.5} sx={{ mt: 0.75, flexWrap: 'wrap', rowGap: 0.5 }}>
                    {(sheet.params.question_types ?? []).map((key) => (
                      <Chip key={key} size="small" variant="outlined" label={labelOf.get(key) ?? key} />
                    ))}
                  </Stack>
                </Box>
                <Stack direction="row" spacing={1}>
                  <Button size="small" variant="outlined" onClick={() => navigate(`/practice/${sheet.id}`)}>
                    打开
                  </Button>
                  <Button
                    size="small"
                    startIcon={<PrintOutlinedIcon />}
                    onClick={() => window.open(`/print/practice/${sheet.id}`, '_blank')}
                  >
                    打印
                  </Button>
                  <Button
                    size="small"
                    color="error"
                    startIcon={<DeleteOutlineOutlinedIcon />}
                    onClick={() => setDeleting(sheet)}
                  >
                    删除
                  </Button>
                </Stack>
              </Stack>
            </Paper>
          ))}
        </Stack>
      )}

      {/* ---------- 生成对话框 ---------- */}
      <Dialog open={dialogOpen} onClose={() => !generating && setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>生成练习卷</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2}>
            {/* 来源 */}
            <Box>
              <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 0.75 }}>
                <Typography variant="subtitle2" sx={{ flex: 1 }}>
                  出题来源(已批改作业)
                </Typography>
                <FormControl size="small" sx={{ minWidth: 170 }}>
                  <InputLabel>按班级筛选</InputLabel>
                  <Select
                    label="按班级筛选"
                    value={filterClassId}
                    onChange={(e) => {
                      const value = e.target.value as number | ''
                      setFilterClassId(value)
                      setSelected(new Set())
                      void loadSources(value)
                    }}
                  >
                    <MenuItem value="">全部班级</MenuItem>
                    {classes.map((cls) => (
                      <MenuItem key={cls.id} value={cls.id}>
                        {cls.name}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Stack>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={scope}
                onChange={(_, v) => v && setScope(v)}
                sx={{ mb: 1 }}
              >
                <ToggleButton value="selected">选择作业</ToggleButton>
                <ToggleButton value="all">全部作业</ToggleButton>
              </ToggleButtonGroup>
              {scope === 'selected' && (
                <Paper variant="outlined" sx={{ borderRadius: 2.5, maxHeight: 220, overflowY: 'auto' }}>
                  {sourcesLoading ? (
                    <Box sx={{ py: 3, textAlign: 'center' }}>
                      <CircularProgress size={22} />
                    </Box>
                  ) : sources.length === 0 ? (
                    <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
                      暂无已完成任务,无法生成练习卷(请先完成批改)。
                    </Typography>
                  ) : (
                    <>
                      <Stack direction="row" sx={{ px: 1.5, pt: 1 }}>
                        <Button
                          size="small"
                          onClick={() =>
                            setSelected(
                              selected.size === sources.length
                                ? new Set()
                                : new Set(sources.map((item) => sourceKey(item))),
                            )
                          }
                        >
                          {selected.size === sources.length ? '取消全选' : '全选'}
                        </Button>
                      </Stack>
                      {sources.map((item) => {
                        const key = sourceKey(item)
                        return (
                          <FormControlLabel
                            key={key}
                            sx={{ display: 'flex', mx: 1.5, my: 0.25 }}
                            control={
                              <Checkbox size="small" checked={selected.has(key)} onChange={() => toggleSource(key)} />
                            }
                            label={
                              <Box sx={{ minWidth: 0 }}>
                                <Typography variant="body2">
                                  {item.name ?? '(未命名作业)'}
                                  {item.class_name ? ` · ${item.class_name}` : ''}
                                </Typography>
                                <Typography variant="caption" color="text.secondary">
                                  {item.task_count} 篇
                                  {item.date_to ? ` · 截至 ${item.date_to}` : ''}
                                  {item.top_categories.length > 0
                                    ? ` · 高频错因:${item.top_categories.join('、')}`
                                    : ''}
                                </Typography>
                              </Box>
                            }
                          />
                        )
                      })}
                    </>
                  )}
                </Paper>
              )}
            </Box>

            <Divider />

            {/* 题型与题数 */}
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                题型(可多选)
              </Typography>
              <Stack direction="row" spacing={0.75} sx={{ flexWrap: 'wrap', rowGap: 0.75 }}>
                {(options?.question_types ?? []).map((t) => (
                  <Chip
                    key={t.key}
                    clickable
                    color={types.includes(t.key) ? 'primary' : 'default'}
                    variant={types.includes(t.key) ? 'filled' : 'outlined'}
                    label={t.label}
                    onClick={() => toggleType(t.key)}
                  />
                ))}
              </Stack>
            </Box>
            <Stack direction="row" spacing={2}>
              <TextField
                size="small"
                type="number"
                label={`题数(${options?.min_count ?? 5}~${options?.max_count ?? 50})`}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                sx={{ width: 180 }}
              />
              <FormControl size="small" sx={{ minWidth: 200 }}>
                <InputLabel>归档到班级(可选)</InputLabel>
                <Select
                  label="归档到班级(可选)"
                  value={classId}
                  onChange={(e) => setClassId(e.target.value as number | '')}
                >
                  <MenuItem value="">不归属班级</MenuItem>
                  {classes.map((cls) => (
                    <MenuItem key={cls.id} value={cls.id}>
                      {cls.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Stack>

            {generateError && (
              <Alert severity="error" sx={{ borderRadius: 2.5 }}>
                {generateError}
              </Alert>
            )}
            <Typography variant="caption" color="text.secondary">
              生成可能需要数十秒(依据所选作业的错因证据命题);失败不会保存任何内容。
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)} disabled={generating}>
            取消
          </Button>
          <Button
            variant="contained"
            onClick={() => void handleGenerate()}
            disabled={generating}
            startIcon={generating ? <CircularProgress size={14} color="inherit" /> : undefined}
          >
            {generating ? '生成中…' : '开始生成'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ---------- 删除确认 ---------- */}
      <Dialog open={Boolean(deleting)} onClose={() => setDeleting(null)} maxWidth="xs" fullWidth>
        <DialogTitle>删除练习卷</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            确定删除「{deleting?.title}」吗?此操作不可恢复。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleting(null)}>取消</Button>
          <Button color="error" variant="contained" onClick={() => void handleDelete()}>
            确认删除
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={3200}
        onClose={() => setToast('')}
        message={toast}
      />
    </Stack>
  )
}
