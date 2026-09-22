/**
 * 重新批改对话框(已完成任务原地重跑,需求一)
 *
 * - 预填任务当前配置;仅"修改过"的字段随请求提交,未修改项由后端沿用原值;
 * - 覆盖工作台全部可选参数:管线 / 评分标准 / 细致度 / OCR 复核 / 学生信息 /
 *   班级与作业元数据;风格画像与提示词附录由服务端实时读取(此处提示生效画像);
 * - 复用原图、不重新上传;系统报告将被新结果覆盖,教师编辑版与寄语保留。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import AutoAwesomeOutlinedIcon from '@mui/icons-material/AutoAwesomeOutlined'
import RestartAltOutlinedIcon from '@mui/icons-material/RestartAltOutlined'
import {
  extractErrorMessage,
  fetchClasses,
  fetchStyleStatus,
  recorrectTask,
} from '../api/client'
import type {
  DetailLevel,
  GradingStandard,
  PipelineChoice,
  RecorrectPayload,
  SchoolClass,
  TaskDetail,
} from '../types'

interface Props {
  open: boolean
  onClose: () => void
  task: TaskDetail
  /** 已成功入队(返回更新后的任务,调用方负责刷新与轮询) */
  onStarted: (task: TaskDetail) => void
}

export default function RecorrectDialog({ open, onClose, task, onStarted }: Props) {
  const [pipeline, setPipeline] = useState<PipelineChoice>(task.pipeline_choice)
  const [standard, setStandard] = useState<GradingStandard>(task.grading_standard)
  const [detail, setDetail] = useState<DetailLevel>(task.detail_level)
  const [review, setReview] = useState(Boolean(task.require_ocr_review))
  const [name, setName] = useState(task.student_name === '未知' ? '' : task.student_name)
  const [studentId, setStudentId] = useState(task.student_id ?? '')
  const [classId, setClassId] = useState<number | ''>(task.class_id ?? '')
  const [assignment, setAssignment] = useState(task.assignment_name ?? '')
  const [topic, setTopic] = useState(task.topic ?? '')
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [styleName, setStyleName] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /** 打开时重置为任务当前配置(以最新 prop 为准) */
  useEffect(() => {
    if (!open) return
    setPipeline(task.pipeline_choice)
    setStandard(task.grading_standard)
    setDetail(task.detail_level)
    setReview(Boolean(task.require_ocr_review))
    setName(task.student_name === '未知' ? '' : task.student_name)
    setStudentId(task.student_id ?? '')
    setClassId(task.class_id ?? '')
    setAssignment(task.assignment_name ?? '')
    setTopic(task.topic ?? '')
    setError(null)
  }, [open, task])

  useEffect(() => {
    if (!open) return
    fetchClasses()
      .then((data) => setClasses(data.filter((c) => !c.merged_into_id || c.id === task.class_id)))
      .catch(() => undefined)
    fetchStyleStatus()
      .then((status) => setStyleName(status.active?.name ?? null))
      .catch(() => undefined)
  }, [open, task.class_id])

  /** 仅提交与任务原值不同的字段(未修改项后端沿用原值) */
  const buildPayload = useCallback((): RecorrectPayload => {
    const payload: RecorrectPayload = {}
    if (pipeline !== task.pipeline_choice) payload.pipeline_choice = pipeline
    if (standard !== task.grading_standard) payload.grading_standard = standard
    if (detail !== task.detail_level) payload.detail_level = detail
    if (review !== Boolean(task.require_ocr_review)) payload.require_ocr_review = review
    const originalName = task.student_name === '未知' ? '' : task.student_name
    if (name.trim() && name.trim() !== originalName) payload.student_name = name.trim()
    if (studentId.trim() !== (task.student_id ?? '')) payload.student_id = studentId.trim() || null
    const originalClass = task.class_id ?? ''
    if (classId !== originalClass) payload.class_id = classId === '' ? null : classId
    if (assignment.trim() !== (task.assignment_name ?? ''))
      payload.assignment_name = assignment.trim() || null
    if (topic.trim() !== (task.topic ?? '')) payload.topic = topic.trim() || null
    return payload
  }, [pipeline, standard, detail, review, name, studentId, classId, assignment, topic, task])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const updated = await recorrectTask(task.id, buildPayload())
      onStarted(updated)
      onClose()
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <RestartAltOutlinedIcon color="primary" />
        重新批改(原地重跑)
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Alert severity="info">
            复用本任务已保存的原图重跑,不重新上传、不产生重复任务;新系统报告将覆盖旧报告,
            教师编辑版报告与教师寄语保留;旧错因记录先清理、完成后按新结果重建(下游统计自动一致)。
          </Alert>

          {styleName ? (
            <Alert severity="success" icon={<AutoAwesomeOutlinedIcon />}>
              将应用当前生效的示范学习风格:「{styleName}」(由服务端实时读取,无需在此选择)
            </Alert>
          ) : (
            <Typography variant="caption" color="text.secondary">
              当前未启用示范学习风格;提示词微调附录与其他运行配置同样由服务端实时读取。
            </Typography>
          )}

          {error && <Alert severity="error">{error}</Alert>}

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              批改管线
            </Typography>
            <ToggleButtonGroup
              exclusive
              fullWidth
              size="small"
              value={pipeline}
              onChange={(_, v) => v && setPipeline(v)}
            >
              <ToggleButton value="PIPELINE_A_LOCAL">管线 A · 本地 VLM</ToggleButton>
              <ToggleButton value="PIPELINE_B_CLOUD">管线 B · 云端 OCR + DeepSeek</ToggleButton>
            </ToggleButtonGroup>
          </Box>

          <Stack direction="row" spacing={1.5}>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                评分标准
              </Typography>
              <ToggleButtonGroup
                exclusive
                fullWidth
                size="small"
                value={standard}
                onChange={(_, v) => v && setStandard(v)}
              >
                <ToggleButton value="GAOKAO">高考 25 分制</ToggleButton>
                <ToggleButton value="DSD">DSD / CEFR</ToggleButton>
              </ToggleButtonGroup>
            </Box>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                细致度
              </Typography>
              <ToggleButtonGroup
                exclusive
                fullWidth
                size="small"
                value={detail}
                onChange={(_, v) => v && setDetail(v)}
              >
                <ToggleButton value="LOW">低</ToggleButton>
                <ToggleButton value="MEDIUM">中</ToggleButton>
                <ToggleButton value="HIGH">高</ToggleButton>
              </ToggleButtonGroup>
            </Box>
          </Stack>

          {pipeline === 'PIPELINE_B_CLOUD' && (
            <FormControlLabel
              control={<Switch checked={review} onChange={(e) => setReview(e.target.checked)} />}
              label={
                <Box>
                  <Typography variant="body2">OCR 人工复核</Typography>
                  <Typography variant="caption" color="text.secondary">
                    识别完成后暂停,教师校对转录文本再继续评分
                  </Typography>
                </Box>
              }
            />
          )}

          <Stack direction="row" spacing={1.5}>
            <TextField
              size="small"
              label="学生姓名"
              value={name}
              onChange={(e) => setName(e.target.value)}
              sx={{ flex: 1 }}
            />
            <TextField
              size="small"
              label="学号(可选)"
              value={studentId}
              onChange={(e) => setStudentId(e.target.value)}
              sx={{ flex: 1 }}
            />
          </Stack>

          <Stack direction="row" spacing={1.5}>
            <TextField
              select
              size="small"
              label="归属班级"
              value={classId}
              onChange={(e) => setClassId(e.target.value === '' ? '' : Number(e.target.value))}
              sx={{ flex: 1 }}
            >
              <MenuItem value="">未指定班级</MenuItem>
              {classes.map((cls) => (
                <MenuItem key={cls.id} value={cls.id}>
                  {cls.name}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="作业名称(可选)"
              value={assignment}
              onChange={(e) => setAssignment(e.target.value)}
              sx={{ flex: 1 }}
            />
          </Stack>

          <TextField
            size="small"
            label="作文题目/要求(可选)"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            multiline
            minRows={1}
            maxRows={3}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose} disabled={busy}>
          取消
        </Button>
        <Button
          variant="contained"
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <RestartAltOutlinedIcon />}
          disabled={busy}
          onClick={() => void submit()}
        >
          {busy ? '正在入队…' : '开始重新批改'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
