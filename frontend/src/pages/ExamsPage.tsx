/**
 * 考试统计(考试列表)
 *
 * - 新建考试(归属班级、满分、类型);
 * - 考试卡片显示考卷识别进度(完成/总数);
 * - 进入详情:上传考卷、人工修订、生成分析报告。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Snackbar,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import AddOutlinedIcon from '@mui/icons-material/AddOutlined'
import ChevronRightOutlinedIcon from '@mui/icons-material/ChevronRightOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import PageHeader from '../components/PageHeader'
import { createExam, deleteExam, extractErrorMessage, fetchClasses, fetchExams } from '../api/client'
import type { ExamBrief, SchoolClass } from '../types'
import { useNavigate } from 'react-router-dom'

const STATUS_META: Record<string, { label: string; color: 'default' | 'info' | 'success' | 'warning' }> = {
  DRAFT: { label: '建档', color: 'default' },
  PROCESSING: { label: '识别中', color: 'info' },
  READY: { label: '识别完成', color: 'success' },
  REPORTED: { label: '已出报告', color: 'warning' },
}

const EXAM_TYPES = ['月考', '期中', '期末', '其他']

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export default function ExamsPage() {
  const navigate = useNavigate()
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [classFilter, setClassFilter] = useState<number | ''>('')
  const [exams, setExams] = useState<ExamBrief[]>([])
  const [loading, setLoading] = useState(false)
  const [toast, setToast] = useState<{ msg: string; severity: 'success' | 'error' | 'info' } | null>(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [classId, setClassId] = useState<number | ''>('')
  const [examDate, setExamDate] = useState(today())
  const [fullScore, setFullScore] = useState(100)
  const [examType, setExamType] = useState('月考')
  const [creating, setCreating] = useState(false)

  const activeClasses = useMemo(() => classes.filter((c) => !c.merged_into_id), [classes])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setExams(await fetchExams(classFilter === '' ? null : classFilter))
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [classFilter])

  useEffect(() => {
    void fetchClasses()
      .then((data) => {
        setClasses(data)
        const first = data.find((c) => !c.merged_into_id)
        if (first) setClassId((prev) => (prev === '' ? first.id : prev))
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const doCreate = async () => {
    if (!name.trim() || classId === '') return
    setCreating(true)
    try {
      const exam = await createExam({
        name: name.trim(),
        exam_date: examDate,
        class_id: classId,
        full_score: fullScore || 100,
        exam_type: examType || null,
      })
      setCreateOpen(false)
      setName('')
      setToast({ msg: `考试「${exam.name}」已创建,进入详情上传考卷`, severity: 'success' })
      navigate(`/exams/${exam.id}`)
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setCreating(false)
    }
  }

  const doDelete = async (exam: ExamBrief) => {
    if (!window.confirm(`删除考试「${exam.name}」将同时删除全部考卷与报告(不可恢复),确认?`)) return
    try {
      await deleteExam(exam.id)
      setToast({ msg: '考试已删除', severity: 'info' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  return (
    <Box>
      <PageHeader
        title="考试统计"
        subtitle="上传考卷图片自动识别逐题得分(听力不纳入),人工修订后可生成班级分析报告;考试数据将自动进入学生画像与统计分析。"
        actions={
          <>
            <TextField
              select
              size="small"
              label="班级"
              value={classFilter}
              onChange={(e) => setClassFilter(e.target.value === '' ? '' : Number(e.target.value))}
              sx={{ minWidth: 170 }}
            >
              <MenuItem value="">全部班级</MenuItem>
              {activeClasses.map((cls) => (
                <MenuItem key={cls.id} value={cls.id}>
                  {cls.name}
                </MenuItem>
              ))}
            </TextField>
            <Button variant="contained" size="small" startIcon={<AddOutlinedIcon />} onClick={() => setCreateOpen(true)}>
              新建考试
            </Button>
          </>
        }
      />

      {loading && exams.length === 0 && (
        <Box sx={{ display: 'grid', placeItems: 'center', py: 8 }}>
          <CircularProgress size={26} />
        </Box>
      )}

      {!loading && exams.length === 0 && (
        <Alert severity="info">暂无考试 —— 点「新建考试」建立一场考试,再上传学生考卷开始识别。</Alert>
      )}

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr', lg: '1fr 1fr 1fr' }, gap: 2 }}>
        {exams.map((exam) => {
          const meta = STATUS_META[exam.status] ?? STATUS_META.DRAFT
          return (
            <Card key={exam.id} variant="outlined">
              <CardActionArea onClick={() => navigate(`/exams/${exam.id}`)}>
                <CardContent>
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                    <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1 }} noWrap>
                      {exam.name}
                    </Typography>
                    <Chip size="small" color={meta.color} label={meta.label} />
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {exam.class_name ?? '未归属班级'} · {exam.exam_date} · 满分 {exam.full_score}
                    {exam.exam_type ? ` · ${exam.exam_type}` : ''}
                  </Typography>
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
                    <Chip
                      size="small"
                      variant="outlined"
                      label={`考卷 ${exam.done_count}/${exam.paper_count} 完成识别`}
                      color={exam.paper_count > 0 && exam.done_count === exam.paper_count ? 'success' : 'default'}
                    />
                    <Box sx={{ flex: 1 }} />
                    <ChevronRightOutlinedIcon fontSize="small" color="disabled" />
                  </Stack>
                </CardContent>
              </CardActionArea>
              <Stack direction="row" justifyContent="flex-end" sx={{ px: 1, pb: 0.5 }}>
                <Button size="small" color="error" startIcon={<DeleteOutlineIcon />} onClick={() => void doDelete(exam)}>
                  删除
                </Button>
              </Stack>
            </Card>
          )
        })}
      </Box>

      {/* ---------- 新建考试 ---------- */}
      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>新建考试</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField size="small" label="考试名称" placeholder="如:第一次月考" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            <TextField
              select
              size="small"
              label="归属班级"
              value={classId}
              onChange={(e) => setClassId(e.target.value === '' ? '' : Number(e.target.value))}
              helperText="用于花名册匹配与班级维度统计"
            >
              {activeClasses.map((cls) => (
                <MenuItem key={cls.id} value={cls.id}>
                  {cls.name}
                </MenuItem>
              ))}
            </TextField>
            <Stack direction="row" spacing={1.5}>
              <TextField
                size="small"
                type="date"
                label="考试日期"
                value={examDate}
                onChange={(e) => setExamDate(e.target.value)}
                InputLabelProps={{ shrink: true }}
                sx={{ flex: 1 }}
              />
              <TextField
                size="small"
                type="number"
                label="满分"
                value={fullScore}
                onChange={(e) => setFullScore(Number(e.target.value) || 100)}
                sx={{ width: 110 }}
              />
            </Stack>
            <TextField select size="small" label="类型" value={examType} onChange={(e) => setExamType(e.target.value)}>
              {EXAM_TYPES.map((type) => (
                <MenuItem key={type} value={type}>
                  {type}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>取消</Button>
          <Button variant="contained" disabled={creating || !name.trim() || classId === ''} onClick={() => void doCreate()}>
            创建并进入
          </Button>
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
