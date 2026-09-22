/**
 * 作业台账(日常作业登记与汇总)
 *
 * 三个工作区:
 * - 快速登记:选登记项 + 日期,全班一次登记(等级 / 分值 / 完成度 / 星级四种计分模式);
 * - 班级汇总:学生 × 登记项矩阵(最近一次) + 各登记项均分与分布;
 * - 登记明细:条件查询、单条纠错与删除。
 *
 * 登记项支持"全局模板项(所有班级可用)"与"班级私有项";台账成绩可被
 * 学生错题本、班级分析、统计分析(三源聚合)读时联动,无需手工同步。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  MenuItem,
  Snackbar,
  Stack,
  Switch,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddOutlinedIcon from '@mui/icons-material/AddOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import PlaylistAddCheckOutlinedIcon from '@mui/icons-material/PlaylistAddCheckOutlined'
import PageHeader from '../components/PageHeader'
import {
  batchUpsertLedger,
  createLedgerItem,
  createLedgerPresets,
  deleteLedgerItem,
  deleteLedgerRecord,
  extractErrorMessage,
  fetchClasses,
  fetchLedgerItems,
  fetchLedgerRecords,
  fetchLedgerStudents,
  fetchLedgerSummary,
} from '../api/client'
import type {
  LedgerClassSummary,
  LedgerItem,
  LedgerRecord,
  LedgerStudentOption,
  SchoolClass,
} from '../types'

const MODE_LABELS: Record<string, string> = {
  LEVEL: '等级制',
  SCORE: '分值制',
  FLAG: '完成度',
  STARS: '星级',
}

const PRESET_CATEGORIES = ['书面作业', '背诵', '听写', '课堂表现', '订正', '其他']

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

/** 等级选项(登记项配置优先) */
function levelsOf(item: LedgerItem): string[] {
  const levels = (item.config as Record<string, unknown>).levels
  return Array.isArray(levels) && levels.length > 0 ? (levels as string[]) : ['A', 'B', 'C', 'D']
}

function scoreMaxOf(item: LedgerItem): number {
  const value = (item.config as Record<string, unknown>).full_score
  return typeof value === 'number' ? value : 100
}

function starMaxOf(item: LedgerItem): number {
  const value = (item.config as Record<string, unknown>).max
  return typeof value === 'number' ? value : 5
}

export default function LedgerPage() {
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [classId, setClassId] = useState<number | ''>('')
  const [items, setItems] = useState<LedgerItem[]>([])
  const [students, setStudents] = useState<LedgerStudentOption[]>([])
  const [tab, setTab] = useState(0)
  const [toast, setToast] = useState<{ msg: string; severity: 'success' | 'error' | 'info' } | null>(null)
  const [loading, setLoading] = useState(false)

  // 快速登记状态
  const [itemId, setItemId] = useState<number | ''>('')
  const [recordDate, setRecordDate] = useState(today())
  const [values, setValues] = useState<Record<string, string>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)

  // 汇总状态
  const [summary, setSummary] = useState<LedgerClassSummary | null>(null)
  const [summaryFrom, setSummaryFrom] = useState('')
  const [summaryTo, setSummaryTo] = useState('')

  // 明细状态
  const [records, setRecords] = useState<LedgerRecord[]>([])
  const [recordTotal, setRecordTotal] = useState(0)
  const [recordItem, setRecordItem] = useState<number | ''>('')
  const [recordStudent, setRecordStudent] = useState('')

  // 登记项管理
  const [manageOpen, setManageOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newCategory, setNewCategory] = useState('书面作业')
  const [newMode, setNewMode] = useState('LEVEL')
  const [newLevels, setNewLevels] = useState('A,B,C,D')
  const [newScope, setNewScope] = useState<'global' | 'class'>('class')

  const activeClasses = useMemo(() => classes.filter((c) => !c.merged_into_id), [classes])
  const currentItem = useMemo(() => items.find((i) => i.id === itemId) ?? null, [items, itemId])

  const loadClasses = useCallback(async () => {
    try {
      const data = await fetchClasses()
      setClasses(data)
      const first = data.find((c) => !c.merged_into_id)
      if (first) setClassId((prev) => (prev === '' ? first.id : prev))
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }, [])

  const loadItems = useCallback(async () => {
    try {
      const data = await fetchLedgerItems(classId === '' ? null : classId)
      setItems(data.filter((i) => !i.archived))
      setItemId((prev) =>
        prev !== '' && data.some((i) => i.id === prev) ? prev : (data[0]?.id ?? ''),
      )
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }, [classId])

  const loadStudents = useCallback(async () => {
    try {
      setStudents(await fetchLedgerStudents(classId === '' ? null : classId))
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }, [classId])

  const loadSummary = useCallback(async () => {
    if (classId === '') return
    setLoading(true)
    try {
      const data = await fetchLedgerSummary({
        class_id: classId,
        date_from: summaryFrom || undefined,
        date_to: summaryTo || undefined,
      })
      setSummary(data)
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [classId, summaryFrom, summaryTo])

  const loadRecords = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchLedgerRecords({
        class_id: classId === '' ? undefined : classId,
        item_id: recordItem === '' ? undefined : recordItem,
        student: recordStudent || undefined,
        limit: 200,
      })
      setRecords(data.records)
      setRecordTotal(data.total)
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [classId, recordItem, recordStudent])

  useEffect(() => {
    void loadClasses()
  }, [loadClasses])

  useEffect(() => {
    void loadItems()
    void loadStudents()
    setValues({})
    setNotes({})
  }, [loadItems, loadStudents])

  useEffect(() => {
    if (tab === 1) void loadSummary()
    if (tab === 2) void loadRecords()
  }, [tab, loadSummary, loadRecords])

  // ---------- 操作 ----------

  const submitBatch = async () => {
    if (!currentItem || classId === '') return
    const entries = students
      .map((student) => ({
        student_name: student.name,
        student_id: student.student_id ?? null,
        value: (values[student.name] ?? '').trim(),
        note: (notes[student.name] ?? '').trim() || null,
      }))
      // 仅提交有登记值的学生(空值代表不提交;撤销请到「登记明细」删除)
      .filter((entry) => entry.value !== '')
    if (entries.length === 0) {
      setToast({ msg: '请至少登记一名学生的成绩(留空的学生不提交)', severity: 'info' })
      return
    }
    setSubmitting(true)
    try {
      const result = await batchUpsertLedger({
        item_id: currentItem.id,
        record_date: recordDate,
        class_id: classId,
        entries,
      })
      setToast({
        msg: `登记完成:新增 ${result.created} · 更新 ${result.updated}${result.deleted ? ` · 撤销 ${result.deleted}` : ''}`,
        severity: 'success',
      })
      setValues({})
      setNotes({})
      if (tab === 1) void loadSummary()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setSubmitting(false)
    }
  }

  const renderValueControl = (student: LedgerStudentOption) => {
    if (!currentItem) return null
    const key = student.name
    const value = values[key] ?? ''
    if (currentItem.scoring_mode === 'LEVEL') {
      return (
        <TextField
          select
          size="small"
          value={value}
          sx={{ width: 120 }}
          onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
        >
          <MenuItem value="">—</MenuItem>
          {levelsOf(currentItem).map((level) => (
            <MenuItem key={level} value={level}>
              {level}
            </MenuItem>
          ))}
        </TextField>
      )
    }
    if (currentItem.scoring_mode === 'SCORE') {
      return (
        <TextField
          size="small"
          type="number"
          sx={{ width: 120 }}
          placeholder={`0~${scoreMaxOf(currentItem)}`}
          value={value}
          onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
        />
      )
    }
    if (currentItem.scoring_mode === 'STARS') {
      const max = starMaxOf(currentItem)
      const current = Number(value) || 0
      return (
        <Stack direction="row" spacing={0.25} alignItems="center">
          {Array.from({ length: max }, (_, index) => index + 1).map((star) => (
            <Button
              key={star}
              size="small"
              variant={star <= current ? 'contained' : 'outlined'}
              sx={{ minWidth: 30, px: 0.5 }}
              onClick={() => setValues((v) => ({ ...v, [key]: String(star) }))}
            >
              {star}
            </Button>
          ))}
          {current > 0 && (
            <Button size="small" onClick={() => setValues((v) => ({ ...v, [key]: '' }))}>
              清
            </Button>
          )}
        </Stack>
      )
    }
    // FLAG
    return (
      <Switch
        size="small"
        checked={value === 'done'}
        onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.checked ? 'done' : '' }))}
      />
    )
  }

  const createItem = async () => {
    if (!newName.trim()) return
    try {
      const config: Record<string, unknown> =
        newMode === 'LEVEL'
          ? { levels: newLevels.split(',').map((s) => s.trim()).filter(Boolean) }
          : newMode === 'SCORE'
            ? { full_score: 100 }
            : newMode === 'STARS'
              ? { max: 5 }
              : {}
      await createLedgerItem({
        name: newName.trim(),
        category: newCategory,
        scoring_mode: newMode,
        config,
        class_id: newScope === 'class' && classId !== '' ? classId : null,
      })
      setNewName('')
      setToast({ msg: '登记项已创建', severity: 'success' })
      await loadItems()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const addPresets = async () => {
    try {
      const created = await createLedgerPresets(classScope())
      setToast({ msg: `已创建 ${created.length} 个预设登记项`, severity: 'success' })
      await loadItems()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const classScope = () => (classId === '' ? null : classId)

  const removeItem = async (item: LedgerItem) => {
    try {
      await deleteLedgerItem(item.id)
      setToast({ msg: `已删除/归档「${item.name}」`, severity: 'info' })
      await loadItems()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const removeRecord = async (record: LedgerRecord) => {
    try {
      await deleteLedgerRecord(record.id)
      setToast({ msg: '记录已删除', severity: 'info' })
      await loadRecords()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  return (
    <Box>
      <PageHeader
        title="作业台账"
        subtitle="日常作业的登记、汇总与纠错。台账成绩将自动进入学生错题本、班级分析与统计分析(三源聚合),无需重复录入。"
        chips={summary ? <Chip variant="outlined" label={`记录 ${summary.record_count} 条`} /> : undefined}
        actions={
          <>
            <TextField
              select
              size="small"
              label="班级"
              value={classId}
              onChange={(e) => setClassId(e.target.value === '' ? '' : Number(e.target.value))}
              sx={{ minWidth: 180 }}
            >
              {activeClasses.map((cls) => (
                <MenuItem key={cls.id} value={cls.id}>
                  {cls.name}
                </MenuItem>
              ))}
            </TextField>
            <Button size="small" variant="outlined" startIcon={<PlaylistAddCheckOutlinedIcon />} onClick={() => setManageOpen(true)}>
              登记项管理
            </Button>
          </>
        }
      />

      {classId === '' ? (
        <Alert severity="info">请先在右上角选择班级;还没有班级?到「班级管理」页新建。</Alert>
      ) : (
        <>
          <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ mb: 2 }}>
            <Tab label="快速登记" />
            <Tab label="班级汇总" />
            <Tab label="登记明细" />
          </Tabs>

          {/* ================= 快速登记 ================= */}
          {tab === 0 && (
            <Card>
              <CardContent>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} sx={{ mb: 2 }}>
                  <TextField
                    select
                    size="small"
                    label="登记项"
                    value={itemId}
                    onChange={(e) => setItemId(e.target.value === '' ? '' : Number(e.target.value))}
                    sx={{ minWidth: 240 }}
                    helperText={items.length === 0 ? '暂无登记项,请点击右上角「登记项管理」创建或一键使用预设' : ' '}
                  >
                    {items.map((item) => (
                      <MenuItem key={item.id} value={item.id}>
                        {item.name}({MODE_LABELS[item.scoring_mode] ?? item.scoring_mode}
                        {item.class_id == null ? ' · 全局' : ''})
                      </MenuItem>
                    ))}
                  </TextField>
                  <TextField
                    size="small"
                    type="date"
                    label="登记日期"
                    value={recordDate}
                    onChange={(e) => setRecordDate(e.target.value)}
                    InputLabelProps={{ shrink: true }}
                  />
                  <Box sx={{ flex: 1 }} />
                  <Button
                    variant="contained"
                    disabled={submitting || !currentItem}
                    onClick={() => void submitBatch()}
                    startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : undefined}
                    sx={{ height: 40 }}
                  >
                    提交登记({Object.values(values).filter((v) => v !== '').length} 人)
                  </Button>
                </Stack>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  {students.length} 名学生(花名册);留空的学生不会产生记录;再次提交同一天同项会覆盖更新。
                </Typography>
                <TableContainer sx={{ maxHeight: 520 }}>
                  <Table size="small" stickyHeader>
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ width: 140 }}>学生</TableCell>
                        <TableCell sx={{ width: 90 }}>学号</TableCell>
                        <TableCell>登记值{currentItem ? `(${MODE_LABELS[currentItem.scoring_mode]})` : ''}</TableCell>
                        <TableCell>备注(可选)</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {students.map((student) => (
                        <TableRow key={`${student.name}-${student.student_id ?? ''}`} hover>
                          <TableCell>{student.name}</TableCell>
                          <TableCell>{student.student_id ?? '—'}</TableCell>
                          <TableCell>{renderValueControl(student)}</TableCell>
                          <TableCell>
                            <TextField
                              size="small"
                              fullWidth
                              placeholder="如:补交 / 书写需改进"
                              value={notes[student.name] ?? ''}
                              onChange={(e) => setNotes((n) => ({ ...n, [student.name]: e.target.value }))}
                            />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </CardContent>
            </Card>
          )}

          {/* ================= 班级汇总 ================= */}
          {tab === 1 && (
            <Box>
              <Stack direction="row" spacing={1.5} sx={{ mb: 2 }} alignItems="center">
                <TextField
                  size="small"
                  type="date"
                  label="起始"
                  value={summaryFrom}
                  onChange={(e) => setSummaryFrom(e.target.value)}
                  InputLabelProps={{ shrink: true }}
                />
                <TextField
                  size="small"
                  type="date"
                  label="截止"
                  value={summaryTo}
                  onChange={(e) => setSummaryTo(e.target.value)}
                  InputLabelProps={{ shrink: true }}
                />
                <Button size="small" variant="outlined" onClick={() => void loadSummary()}>
                  刷新
                </Button>
                {loading && <CircularProgress size={18} />}
              </Stack>

              {summary && summary.item_stats.length > 0 && (
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(3, 1fr)' }, gap: 1.5, mb: 2 }}>
                  {summary.item_stats.map((stat) => (
                    <Card key={stat.item_id} variant="outlined">
                      <CardContent sx={{ py: 1.5 }}>
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Typography variant="subtitle2" sx={{ flex: 1 }} noWrap>
                            {stat.name}
                          </Typography>
                          <Chip size="small" variant="outlined" label={MODE_LABELS[stat.scoring_mode] ?? stat.scoring_mode} />
                        </Stack>
                        <Typography variant="h6" sx={{ mt: 0.5 }}>
                          {stat.avg_score_value != null ? `${stat.avg_score_value} 分` : '—'}
                          <Typography component="span" variant="caption" color="text.secondary">
                            {' '}· {stat.count} 条 / {stat.student_count} 人
                          </Typography>
                        </Typography>
                        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.5 }}>
                          {Object.entries(stat.distribution).map(([key, count]) => (
                            <Chip key={key} size="small" label={`${key} × ${count}`} />
                          ))}
                        </Stack>
                      </CardContent>
                    </Card>
                  ))}
                </Box>
              )}

              {summary && (
                <TableContainer component={Card} variant="outlined" sx={{ maxHeight: 560 }}>
                  <Table size="small" stickyHeader>
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ width: 130 }}>学生</TableCell>
                        <TableCell sx={{ width: 90 }}>均分</TableCell>
                        <TableCell sx={{ width: 80 }}>记录数</TableCell>
                        {summary.items.map((item) => (
                          <TableCell key={item.id}>{item.name}</TableCell>
                        ))}
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {summary.students.map((row) => (
                        <TableRow key={`${row.student_name}-${row.student_id ?? ''}`} hover>
                          <TableCell>{row.student_name}</TableCell>
                          <TableCell>{row.overall_avg ?? '—'}</TableCell>
                          <TableCell>{row.record_count}</TableCell>
                          {summary.items.map((item) => {
                            const cell = row.by_item[String(item.id)]
                            return (
                              <TableCell key={item.id}>
                                {cell ? (
                                  <Tooltip title={`${cell.record_date}${cell.count > 1 ? ` · ${cell.count} 次` : ''}`}>
                                    <Chip
                                      size="small"
                                      label={cell.value}
                                      color={cell.score_value != null ? (cell.score_value >= 80 ? 'success' : cell.score_value >= 60 ? 'default' : 'warning') : 'default'}
                                      variant={cell.score_value != null ? 'filled' : 'outlined'}
                                    />
                                  </Tooltip>
                                ) : (
                                  <Typography variant="caption" color="text.disabled">
                                    —
                                  </Typography>
                                )}
                              </TableCell>
                            )
                          })}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}
            </Box>
          )}

          {/* ================= 登记明细 ================= */}
          {tab === 2 && (
            <Box>
              <Stack direction="row" spacing={1.5} sx={{ mb: 2 }} alignItems="center" flexWrap="wrap" useFlexGap>
                <TextField
                  select
                  size="small"
                  label="登记项"
                  value={recordItem}
                  onChange={(e) => setRecordItem(e.target.value === '' ? '' : Number(e.target.value))}
                  sx={{ minWidth: 200 }}
                >
                  <MenuItem value="">全部</MenuItem>
                  {items.map((item) => (
                    <MenuItem key={item.id} value={item.id}>
                      {item.name}
                    </MenuItem>
                  ))}
                </TextField>
                <TextField
                  size="small"
                  label="学生(姓名/学号)"
                  value={recordStudent}
                  onChange={(e) => setRecordStudent(e.target.value)}
                />
                <Button size="small" variant="outlined" onClick={() => void loadRecords()}>
                  查询
                </Button>
                <Typography variant="caption" color="text.secondary">
                  共 {recordTotal} 条
                </Typography>
                {loading && <CircularProgress size={18} />}
              </Stack>
              <TableContainer component={Card} variant="outlined" sx={{ maxHeight: 560 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell>日期</TableCell>
                      <TableCell>学生</TableCell>
                      <TableCell>登记项</TableCell>
                      <TableCell>值</TableCell>
                      <TableCell>折算分</TableCell>
                      <TableCell>备注</TableCell>
                      <TableCell align="right">操作</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {records.map((record) => (
                      <TableRow key={record.id} hover>
                        <TableCell>{record.record_date}</TableCell>
                        <TableCell>
                          {record.student_name}
                          {record.student_id ? `(${record.student_id})` : ''}
                        </TableCell>
                        <TableCell>{record.item_name ?? `#${record.item_id}`}</TableCell>
                        <TableCell>{record.value}</TableCell>
                        <TableCell>{record.score_value ?? '—'}</TableCell>
                        <TableCell>{record.note || '—'}</TableCell>
                        <TableCell align="right">
                          <IconButton size="small" color="error" onClick={() => void removeRecord(record)}>
                            <DeleteOutlineIcon fontSize="inherit" />
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                    {records.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={7} align="center">
                          <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
                            暂无符合条件的记录
                          </Typography>
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          )}
        </>
      )}

      {/* ================= 登记项管理 ================= */}
      <Dialog open={manageOpen} onClose={() => setManageOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>登记项管理</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2}>
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                新建登记项
              </Typography>
              <Stack spacing={1.5}>
                <Stack direction="row" spacing={1.5}>
                  <TextField size="small" label="名称" value={newName} onChange={(e) => setNewName(e.target.value)} sx={{ flex: 1 }} />
                  <TextField select size="small" label="分类" value={newCategory} onChange={(e) => setNewCategory(e.target.value)} sx={{ width: 140 }}>
                    {PRESET_CATEGORIES.map((cat) => (
                      <MenuItem key={cat} value={cat}>
                        {cat}
                      </MenuItem>
                    ))}
                  </TextField>
                </Stack>
                <Stack direction="row" spacing={1.5}>
                  <TextField select size="small" label="计分模式" value={newMode} onChange={(e) => setNewMode(e.target.value)} sx={{ width: 160 }}>
                    <MenuItem value="LEVEL">等级制(A/B/C)</MenuItem>
                    <MenuItem value="SCORE">分值制(0~100)</MenuItem>
                    <MenuItem value="FLAG">完成度(做/未做)</MenuItem>
                    <MenuItem value="STARS">星级(1~5)</MenuItem>
                  </TextField>
                  <TextField select size="small" label="归属" value={newScope} onChange={(e) => setNewScope(e.target.value as 'global' | 'class')} sx={{ width: 160 }}>
                    <MenuItem value="class">当前班级私有</MenuItem>
                    <MenuItem value="global">全局模板(所有班级)</MenuItem>
                  </TextField>
                  {newMode === 'LEVEL' && (
                    <TextField size="small" label="等级档位(逗号分隔)" value={newLevels} onChange={(e) => setNewLevels(e.target.value)} sx={{ flex: 1 }} />
                  )}
                </Stack>
                <Stack direction="row" spacing={1}>
                  <Button variant="contained" size="small" startIcon={<AddOutlinedIcon />} onClick={() => void createItem()} disabled={!newName.trim()}>
                    创建
                  </Button>
                  <Button size="small" variant="outlined" onClick={() => void addPresets()}>
                    一键创建预设(书面作业/背诵/听写/课堂表现/订正)
                  </Button>
                </Stack>
              </Stack>
            </Box>
            <Divider />
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                现有登记项({items.length})
              </Typography>
              <Stack spacing={0.5}>
                {items.map((item) => (
                  <Stack key={item.id} direction="row" alignItems="center" spacing={1}>
                    <Typography variant="body2" sx={{ flex: 1 }}>
                      {item.name}
                    </Typography>
                    <Chip size="small" label={MODE_LABELS[item.scoring_mode] ?? item.scoring_mode} variant="outlined" />
                    <Chip size="small" label={item.class_id == null ? '全局' : '班级'} variant="outlined" />
                    <IconButton size="small" color="error" onClick={() => void removeItem(item)}>
                      <DeleteOutlineIcon fontSize="inherit" />
                    </IconButton>
                  </Stack>
                ))}
              </Stack>
            </Box>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setManageOpen(false)}>关闭</Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={toast?.severity ?? 'info'} onClose={() => setToast(null)}>
          {toast?.msg}
        </Alert>
      </Snackbar>
    </Box>
  )
}
