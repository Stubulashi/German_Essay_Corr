/**
 * 考试详情:考卷上传与识别 + 人工修订 + 分析报告
 *
 * - 上传考卷(文件名匹配花名册;同名再次上传 = 续传多页);
 * - 识别进行中自动轮询刷新;失败可重试;
 * - 人工修订逐题结果(教师修订优先,不被重跑覆盖);
 * - 生成/重新生成班级分析报告(后端确定性 Markdown),可导出。
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
  IconButton,
  LinearProgress,
  Snackbar,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddOutlinedIcon from '@mui/icons-material/AddOutlined'
import ArrowBackOutlinedIcon from '@mui/icons-material/ArrowBackOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import DownloadOutlinedIcon from '@mui/icons-material/DownloadOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import RefreshOutlinedIcon from '@mui/icons-material/RefreshOutlined'
import SummarizeOutlinedIcon from '@mui/icons-material/SummarizeOutlined'
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined'
import { useNavigate, useParams } from 'react-router-dom'
import MarkdownReport from '../components/MarkdownReport'
import PageHeader from '../components/PageHeader'
import {
  downloadBlob,
  exportExamReportBlob,
  extractErrorMessage,
  fetchExamDetail,
  fetchExamReport,
  generateExamReport,
  retryExamPaper,
  updateExamPaper,
  uploadExamPapers,
} from '../api/client'
import type { ExamDetail, ExamPaper, ExamQuestion } from '../types'

const PAPER_STATUS: Record<string, { label: string; color: 'default' | 'info' | 'success' | 'error' }> = {
  PENDING: { label: '待识别', color: 'default' },
  PROCESSING: { label: '识别中', color: 'info' },
  DONE: { label: '已完成', color: 'success' },
  FAILED: { label: '失败', color: 'error' },
}

interface EditorState {
  paper: ExamPaper
  studentName: string
  studentId: string
  totalScore: string
  questions: ExamQuestion[]
}

export default function ExamDetailPage() {
  const { examId } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<ExamDetail | null>(null)
  const [report, setReport] = useState<string>('')
  const [hasReport, setHasReport] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [toast, setToast] = useState<{ msg: string; severity: 'success' | 'error' | 'info' } | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadFiles, setUploadFiles] = useState<File[]>([])
  const [uploadName, setUploadName] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [reportBusy, setReportBusy] = useState(false)

  const id = Number(examId)

  const load = useCallback(async () => {
    try {
      const data = await fetchExamDetail(id)
      setDetail(data)
      setHasReport(data.has_report)
      if (data.has_report) {
        try {
          const reportData = await fetchExamReport(id)
          setReport(reportData.report_markdown)
        } catch {
          setHasReport(false)
        }
      }
    } catch (e) {
      setLoadError(extractErrorMessage(e))
    }
  }, [id])

  useEffect(() => {
    if (!Number.isFinite(id)) {
      navigate('/exams')
      return
    }
    void load()
  }, [id, load, navigate])

  // 识别进行中:轮询刷新(3s)
  const processing = useMemo(
    () => detail?.papers.some((p) => p.ocr_status === 'PENDING' || p.ocr_status === 'PROCESSING') ?? false,
    [detail],
  )
  useEffect(() => {
    if (!processing) return
    const timer = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer)
  }, [processing, load])

  const doUpload = async () => {
    if (uploadFiles.length === 0) return
    setUploading(true)
    setUploadProgress(0)
    try {
      const papers = await uploadExamPapers(id, uploadFiles, {
        studentName: uploadFiles.length === 1 ? uploadName.trim() || undefined : undefined,
        onProgress: setUploadProgress,
      })
      setToast({ msg: `已上传 ${papers.length} 份考卷,开始识别…`, severity: 'success' })
      setUploadOpen(false)
      setUploadFiles([])
      setUploadName('')
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setUploading(false)
    }
  }

  const openEditor = (paper: ExamPaper) => {
    setEditor({
      paper,
      studentName: paper.student_name,
      studentId: paper.student_id ?? '',
      totalScore: paper.total_score != null ? String(paper.total_score) : '',
      questions: paper.question_results.map((q) => ({ ...q })),
    })
  }

  const saveEditor = async () => {
    if (!editor) return
    try {
      await updateExamPaper(id, editor.paper.id, {
        student_name: editor.studentName.trim(),
        student_id: editor.studentId.trim() || null,
        total_score: editor.totalScore === '' ? null : Number(editor.totalScore),
        question_results: editor.questions.map((q) => ({
          ...q,
          max_score: q.max_score == null ? null : Number(q.max_score),
          score: q.score == null ? null : Number(q.score),
        })),
      })
      setEditor(null)
      setToast({ msg: '已保存人工修订(重跑识别不会覆盖人工值)', severity: 'success' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const doRetry = async (paper: ExamPaper) => {
    try {
      await retryExamPaper(id, paper.id)
      setToast({ msg: `已重新入队识别:${paper.student_name}`, severity: 'info' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const doGenerateReport = async () => {
    setReportBusy(true)
    try {
      const data = await generateExamReport(id)
      setReport(data.report_markdown)
      setHasReport(true)
      setToast({ msg: '分析报告已生成(可导出或打印)', severity: 'success' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setReportBusy(false)
    }
  }

  const doExport = async () => {
    try {
      const blob = await exportExamReportBlob(id)
      downloadBlob(blob, `考试报告_${detail?.name ?? id}.md`)
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  if (loadError) {
    return (
      <Box>
        <Alert severity="error" action={<Button onClick={() => navigate('/exams')}>返回列表</Button>}>
          {loadError}
        </Alert>
      </Box>
    )
  }

  if (!detail) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', py: 10 }}>
        <CircularProgress size={28} />
      </Box>
    )
  }

  const doneScore = detail.papers.filter((p) => p.total_score != null)
  const average =
    doneScore.length > 0
      ? Math.round((doneScore.reduce((sum, p) => sum + (p.total_score ?? 0), 0) / doneScore.length) * 10) / 10
      : null

  return (
    <Box>
      <PageHeader
        title={detail.name}
        subtitle={`${detail.class_name ?? '未归属班级'} · ${detail.exam_date} · 满分 ${detail.full_score}${
          detail.exam_type ? ` · ${detail.exam_type}` : ''
        }`}
        chips={
          <>
            <Chip size="small" label={`考卷 ${detail.done_count}/${detail.paper_count} 完成识别`} color={detail.paper_count > 0 && detail.done_count === detail.paper_count ? 'success' : 'default'} />
            {average != null && <Chip size="small" variant="outlined" label={`已完成考卷均分 ${average}`} />}
          </>
        }
        actions={
          <>
            <Button size="small" startIcon={<ArrowBackOutlinedIcon />} onClick={() => navigate('/exams')}>
              返回列表
            </Button>
            <Button size="small" variant="outlined" startIcon={<UploadFileOutlinedIcon />} onClick={() => setUploadOpen(true)}>
              上传考卷
            </Button>
            <Button
              size="small"
              variant="contained"
              startIcon={reportBusy ? <CircularProgress size={14} color="inherit" /> : <SummarizeOutlinedIcon />}
              disabled={reportBusy || detail.done_count === 0}
              onClick={() => void doGenerateReport()}
            >
              {hasReport ? '重新生成报告' : '生成分析报告'}
            </Button>
            {hasReport && (
              <Button size="small" startIcon={<DownloadOutlinedIcon />} onClick={() => void doExport()}>
                导出
              </Button>
            )}
          </>
        }
      />

      {processing && (
        <Alert severity="info" sx={{ mb: 2 }} icon={<RefreshOutlinedIcon />}>
          考卷识别进行中(每 3 秒自动刷新)…已识别 {detail.done_count}/{detail.paper_count} 份
        </Alert>
      )}

      {/* ---------- 考卷列表 ---------- */}
      <Card variant="outlined" sx={{ mb: 3 }}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: 60 }}>页码</TableCell>
                <TableCell>学生</TableCell>
                <TableCell>学号</TableCell>
                <TableCell sx={{ width: 110 }}>状态</TableCell>
                <TableCell sx={{ width: 100 }}>总分</TableCell>
                <TableCell>逐题</TableCell>
                <TableCell align="right" sx={{ width: 170 }}>操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {detail.papers.map((paper) => {
                const meta = PAPER_STATUS[paper.ocr_status] ?? PAPER_STATUS.PENDING
                return (
                  <TableRow key={paper.id} hover>
                    <TableCell>
                      <Typography variant="caption">
                        {(paper.image_paths ?? []).length} 页
                        {paper.teacher_edited ? ' · 已修订' : ''}
                      </Typography>
                    </TableCell>
                    <TableCell>{paper.student_name}</TableCell>
                    <TableCell>{paper.student_id ?? '—'}</TableCell>
                    <TableCell>
                      <Tooltip title={paper.ocr_error ?? ''}>
                        <Chip size="small" color={meta.color} label={meta.label} variant={paper.ocr_status === 'DONE' ? 'filled' : 'outlined'} />
                      </Tooltip>
                    </TableCell>
                    <TableCell>{paper.total_score ?? '—'}</TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {paper.question_results.length} 题
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Button size="small" startIcon={<EditOutlinedIcon />} onClick={() => openEditor(paper)}>
                        修订
                      </Button>
                      <IconButton size="small" onClick={() => void doRetry(paper)}>
                        <RefreshOutlinedIcon fontSize="inherit" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                )
              })}
              {detail.papers.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} align="center">
                    <Typography variant="body2" color="text.secondary" sx={{ py: 4 }}>
                      还没有考卷 —— 点击右上角「上传考卷」开始(可为多个学生一次上传,文件名含姓名/学号将自动匹配)
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Card>

      {/* ---------- 报告 ---------- */}
      {hasReport && report ? (
        <Card variant="outlined">
          <CardContent>
            <MarkdownReport markdown={report} />
          </CardContent>
        </Card>
      ) : (
        <Alert severity="info">识别完成后点击「生成分析报告」:含班级均分、分数段、逐题得分率(弱题在前)、知识点失分与讲评建议。</Alert>
      )}

      {/* ---------- 上传弹窗 ---------- */}
      <Dialog open={uploadOpen} onClose={() => setUploadOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>上传考卷</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Alert severity="info">
              每个文件 = 一名学生的考卷(多页请合并为一个多页文件或按"学生名 第 N 页"命名);
              文件名含姓名或学号将自动匹配花名册;同名再次上传视为续传多页。
            </Alert>
            <Button variant="outlined" component="label" startIcon={<UploadFileOutlinedIcon />}>
              选择图片({uploadFiles.length} 个)
              <input
                hidden
                type="file"
                multiple
                accept="image/*,.zip"
                onChange={(e) => setUploadFiles(Array.from(e.target.files ?? []))}
              />
            </Button>
            {uploadFiles.length === 1 && (
              <TextField
                size="small"
                label="手动指派学生姓名(可选)"
                value={uploadName}
                onChange={(e) => setUploadName(e.target.value)}
                helperText="文件名未包含姓名时填写;留空则使用文件名匹配或取文件名"
              />
            )}
            {uploading && (
              <Box>
                <LinearProgress variant="determinate" value={uploadProgress} />
                <Typography variant="caption" color="text.secondary">
                  上传中 {uploadProgress}%
                </Typography>
              </Box>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUploadOpen(false)} disabled={uploading}>
            取消
          </Button>
          <Button variant="contained" disabled={uploading || uploadFiles.length === 0} onClick={() => void doUpload()}>
            上传并识别
          </Button>
        </DialogActions>
      </Dialog>

      {/* ---------- 人工修订弹窗 ---------- */}
      <Dialog open={Boolean(editor)} onClose={() => setEditor(null)} maxWidth="md" fullWidth>
        <DialogTitle>人工修订 · {editor?.paper.student_name}</DialogTitle>
        <DialogContent dividers>
          {editor && (
            <Stack spacing={2}>
              <Stack direction="row" spacing={1.5}>
                <TextField
                  size="small"
                  label="学生姓名"
                  value={editor.studentName}
                  onChange={(e) => setEditor({ ...editor, studentName: e.target.value })}
                />
                <TextField
                  size="small"
                  label="学号"
                  value={editor.studentId}
                  onChange={(e) => setEditor({ ...editor, studentId: e.target.value })}
                />
                <TextField
                  size="small"
                  type="number"
                  label="总分"
                  value={editor.totalScore}
                  onChange={(e) => setEditor({ ...editor, totalScore: e.target.value })}
                  helperText="留空 = 按逐题合计"
                />
              </Stack>
              <TableContainer sx={{ maxHeight: 340 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ width: 70 }}>题号</TableCell>
                      <TableCell sx={{ width: 140 }}>部分</TableCell>
                      <TableCell sx={{ width: 90 }}>满分</TableCell>
                      <TableCell sx={{ width: 90 }}>得分</TableCell>
                      <TableCell>知识点标签</TableCell>
                      <TableCell sx={{ width: 50 }} />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {editor.questions.map((question, index) => (
                      <TableRow key={index}>
                        <TableCell>
                          <TextField
                            size="small"
                            value={question.no}
                            sx={{ width: 60 }}
                            onChange={(e) =>
                              setEditor({
                                ...editor,
                                questions: editor.questions.map((q, i) => (i === index ? { ...q, no: e.target.value } : q)),
                              })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <TextField
                            size="small"
                            value={question.part ?? ''}
                            onChange={(e) =>
                              setEditor({
                                ...editor,
                                questions: editor.questions.map((q, i) => (i === index ? { ...q, part: e.target.value } : q)),
                              })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <TextField
                            size="small"
                            type="number"
                            value={question.max_score ?? ''}
                            sx={{ width: 76 }}
                            onChange={(e) =>
                              setEditor({
                                ...editor,
                                questions: editor.questions.map((q, i) =>
                                  i === index ? { ...q, max_score: e.target.value === '' ? null : Number(e.target.value) } : q,
                                ),
                              })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <TextField
                            size="small"
                            type="number"
                            value={question.score ?? ''}
                            sx={{ width: 76 }}
                            onChange={(e) =>
                              setEditor({
                                ...editor,
                                questions: editor.questions.map((q, i) =>
                                  i === index ? { ...q, score: e.target.value === '' ? null : Number(e.target.value) } : q,
                                ),
                              })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <TextField
                            size="small"
                            fullWidth
                            value={question.knowledge_tag ?? ''}
                            onChange={(e) =>
                              setEditor({
                                ...editor,
                                questions: editor.questions.map((q, i) => (i === index ? { ...q, knowledge_tag: e.target.value } : q)),
                              })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() =>
                              setEditor({ ...editor, questions: editor.questions.filter((_, i) => i !== index) })
                            }
                          >
                            <DeleteOutlineIcon fontSize="inherit" />
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <Button
                size="small"
                startIcon={<AddOutlinedIcon />}
                onClick={() =>
                  setEditor({
                    ...editor,
                    questions: [...editor.questions, { no: '', part: '', max_score: null, score: null, knowledge_tag: '' }],
                  })
                }
              >
                添加题目
              </Button>
              <Typography variant="caption" color="text.secondary">
                保存后标记为"已修订",重新识别不会覆盖人工结果;知识点标签会自动归一化为统计口径。
              </Typography>
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditor(null)}>取消</Button>
          <Button variant="contained" onClick={() => void saveEditor()}>
            保存修订
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
