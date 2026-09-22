/**
 * 示范学习(风格迁移)
 *
 * 三步闭环:
 * 1) 选择一篇已批改的示例范文(可先在工作台按自己的偏好批改一篇样板);
 * 2) 归纳固化:评分尺度 / 点评语气 / 修改偏好 / 表达习惯(单一生效,可随时停用);
 * 3) 自动迁移:后续所有批改(管线 A / 评分阶段)自动注入该风格。
 *
 * 与设置中心「提示词微调」可叠加;均为"追加",不改变输出 JSON 契约。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  MenuItem,
  Snackbar,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import AddCircleOutlineIcon from '@mui/icons-material/AddCircleOutline'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import PlayArrowOutlinedIcon from '@mui/icons-material/PlayArrowOutlined'
import StopCircleOutlinedIcon from '@mui/icons-material/StopCircleOutlined'
import PageHeader from '../components/PageHeader'
import {
  activateStyleProfile,
  deactivateStyleProfile,
  deleteStyleProfile,
  extractErrorMessage,
  fetchStyleProfiles,
  fetchStyleStatus,
  fetchTasks,
  learnStyle,
  updateStyleProfile,
} from '../api/client'
import type { StyleProfile, StyleStatus, TaskBrief } from '../types'

/** 四维度展示配置(与后端 style_json 键一致) */
const DIMENSIONS: { key: string; label: string }[] = [
  { key: 'scoring_scale', label: '评分尺度' },
  { key: 'tone', label: '点评语气' },
  { key: 'revision_preference', label: '修改偏好' },
  { key: 'expression_habit', label: '表达习惯' },
]

function dimensionSummary(profile: StyleProfile, key: string): string {
  const dim = (profile.style_json as Record<string, { summary?: string }>)[key]
  return dim?.summary ?? '—'
}

export default function StyleLearningPage() {
  const [status, setStatus] = useState<StyleStatus | null>(null)
  const [profiles, setProfiles] = useState<StyleProfile[]>([])
  const [tasks, setTasks] = useState<TaskBrief[]>([])
  const [taskId, setTaskId] = useState<number | ''>('')
  const [name, setName] = useState('')
  const [learning, setLearning] = useState(false)
  const [toast, setToast] = useState<{ msg: string; severity: 'success' | 'error' | 'info' } | null>(null)
  const [detail, setDetail] = useState<StyleProfile | null>(null)
  const [editing, setEditing] = useState<StyleProfile | null>(null)
  const [editName, setEditName] = useState('')
  const [editNarrative, setEditNarrative] = useState('')
  const [deleting, setDeleting] = useState<StyleProfile | null>(null)

  const load = useCallback(async () => {
    try {
      const [statusData, profileData] = await Promise.all([fetchStyleStatus(), fetchStyleProfiles()])
      setStatus(statusData)
      setProfiles(profileData)
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }, [])

  useEffect(() => {
    void load()
    void fetchTasks({ status: 'COMPLETED', limit: 200 })
      .then((data) => setTasks(data.items))
      .catch(() => undefined)
  }, [load])

  const activeId = status?.active?.id
  const canLearn = useMemo(() => taskId !== '' && !learning, [taskId, learning])

  const doLearn = async () => {
    if (taskId === '') return
    setLearning(true)
    try {
      const profile = await learnStyle(Number(taskId), name.trim() || undefined)
      setToast({ msg: `已归纳风格「${profile.name}」并自动启用`, severity: 'success' })
      setName('')
      setTaskId('')
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLearning(false)
    }
  }

  const doActivate = async (profile: StyleProfile) => {
    try {
      await activateStyleProfile(profile.id)
      setToast({ msg: `已启用「${profile.name}」(其余画像自动停用)`, severity: 'success' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const doDeactivate = async (profile: StyleProfile) => {
    try {
      await deactivateStyleProfile(profile.id)
      setToast({ msg: `已停用「${profile.name}」,恢复默认批改风格`, severity: 'info' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const doDelete = async () => {
    if (!deleting) return
    try {
      await deleteStyleProfile(deleting.id)
      setDeleting(null)
      setToast({ msg: '画像已删除', severity: 'info' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const openEdit = (profile: StyleProfile) => {
    setEditing(profile)
    setEditName(profile.name)
    setEditNarrative(profile.narrative)
  }

  const saveEdit = async () => {
    if (!editing) return
    try {
      await updateStyleProfile(editing.id, {
        name: editName.trim() || undefined,
        narrative: editNarrative,
      })
      setEditing(null)
      setToast({ msg: '画像已更新(生效中画像立即应用于后续批改)', severity: 'success' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  return (
    <Box>
      <PageHeader
        title="示范学习"
        subtitle="用一篇「按你想要的标准批改」的示例范文,让系统归纳你的评分尺度、语气与修改偏好,并自动应用到后续所有学生作文的批改中。"
        chips={
          status?.active ? (
            <Chip color="success" variant="outlined" label={`生效中:${status.active.name}`} />
          ) : (
            <Chip variant="outlined" label="未启用(默认风格)" />
          )
        }
      />

      {/* ---------- 第一步:归纳 ---------- */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>
            ① 从示例范文归纳风格
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            选择一篇已完成的批改任务(建议先用目标风格批改一篇样板作文);
            系统会归纳四维度:评分尺度 / 点评语气 / 修改偏好 / 表达习惯。再次归纳会生成新画像并自动启用。
          </Typography>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
            <TextField
              select
              size="small"
              label="示例范文(已完成批改的任务)"
              sx={{ minWidth: 280, flex: 1 }}
              value={taskId}
              onChange={(e) => setTaskId(e.target.value === '' ? '' : Number(e.target.value))}
              disabled={tasks.length === 0}
              helperText={tasks.length === 0 ? '暂无已完成任务:请先在批改工作台完成一篇示例批改' : ' '}
            >
              {tasks.map((task) => (
                <MenuItem key={task.id} value={task.id}>
                  #{task.id} {task.student_name}
                  {task.assignment_name ? ` · ${task.assignment_name}` : ''}
                  {task.overall_score ? ` · ${task.overall_score}` : ''}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="画像名称(可选)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              sx={{ minWidth: 200 }}
            />
            <Button
              variant="contained"
              startIcon={learning ? <CircularProgress size={16} color="inherit" /> : <AddCircleOutlineIcon />}
              disabled={!canLearn}
              onClick={() => void doLearn()}
              sx={{ height: 40, whiteSpace: 'nowrap' }}
            >
              归纳并启用
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {/* ---------- 第二步/第三步说明 ---------- */}
      <Alert severity="info" sx={{ mb: 3 }}>
        ② 归纳完成后自动启用(同一时间仅一个画像生效);③ 后续批改自动注入该风格,
        在设置中心「提示词微调」中可再叠加你的补充要求。停用画像即完全恢复默认风格,历史报告不受影响。
      </Alert>

      {/* ---------- 画像列表 ---------- */}
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
        风格画像({profiles.length})
      </Typography>
      {profiles.length === 0 && (
        <Card variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">暂无画像 —— 完成上面的"归纳并启用"即可创建第一份风格画像。</Typography>
        </Card>
      )}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
        {profiles.map((profile) => {
          const isActive = profile.id === activeId
          return (
            <Card key={profile.id} variant="outlined" sx={{ borderColor: isActive ? 'success.main' : 'divider' }}>
              <CardContent sx={{ pb: 1 }}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1 }} noWrap>
                    {profile.name}
                  </Typography>
                  {isActive ? (
                    <Chip size="small" color="success" label="生效中" />
                  ) : (
                    <Chip size="small" variant="outlined" label="未启用" />
                  )}
                </Stack>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden', mb: 1 }}
                >
                  {profile.narrative}
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                  {DIMENSIONS.map((dim) => (
                    <Chip
                      key={dim.key}
                      size="small"
                      variant="outlined"
                      label={`${dim.label}:${dimensionSummary(profile, dim.key).slice(0, 14)}`}
                    />
                  ))}
                </Stack>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                  来源:{profile.source_student_name || '示例范文'}
                  {profile.source_task_id ? `(任务 #${profile.source_task_id})` : ''} · 创建于{' '}
                  {new Date(profile.created_at).toLocaleString()}
                </Typography>
              </CardContent>
              <Divider />
              <CardActions sx={{ justifyContent: 'flex-end' }}>
                <Button size="small" onClick={() => setDetail(profile)}>
                  详情
                </Button>
                <Button size="small" startIcon={<EditOutlinedIcon />} onClick={() => openEdit(profile)}>
                  编辑
                </Button>
                {isActive ? (
                  <Button size="small" color="warning" startIcon={<StopCircleOutlinedIcon />} onClick={() => void doDeactivate(profile)}>
                    停用
                  </Button>
                ) : (
                  <Button size="small" color="success" startIcon={<PlayArrowOutlinedIcon />} onClick={() => void doActivate(profile)}>
                    启用
                  </Button>
                )}
                <Button size="small" color="error" startIcon={<DeleteOutlineIcon />} onClick={() => setDeleting(profile)}>
                  删除
                </Button>
              </CardActions>
            </Card>
          )
        })}
      </Box>

      {/* ---------- 详情 ---------- */}
      <Dialog open={Boolean(detail)} onClose={() => setDetail(null)} maxWidth="md" fullWidth>
        <DialogTitle>画像详情 · {detail?.name}</DialogTitle>
        <DialogContent dividers>
          {detail && (
            <Stack spacing={2}>
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                  风格描述(注入批改 Prompt 的文本)
                </Typography>
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', bgcolor: 'action.hover', p: 1.5, borderRadius: 2 }}>
                  {detail.narrative}
                </Typography>
              </Box>
              {DIMENSIONS.map((dim) => {
                const value = (detail.style_json as Record<string, unknown>)[dim.key]
                if (!value) return null
                return (
                  <Box key={dim.key}>
                    <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                      {dim.label}
                    </Typography>
                    <Box
                      component="pre"
                      sx={{ m: 0, p: 1.5, fontSize: 12, bgcolor: 'action.hover', borderRadius: 2, overflow: 'auto', whiteSpace: 'pre-wrap' }}
                    >
                      {JSON.stringify(value, null, 2)}
                    </Box>
                  </Box>
                )
              })}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDetail(null)}>关闭</Button>
        </DialogActions>
      </Dialog>

      {/* ---------- 编辑 ---------- */}
      <Dialog open={Boolean(editing)} onClose={() => setEditing(null)} maxWidth="md" fullWidth>
        <DialogTitle>编辑画像 · {editing?.name}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField size="small" label="名称" value={editName} onChange={(e) => setEditName(e.target.value)} />
            <TextField
              multiline
              minRows={6}
              maxRows={14}
              label="风格描述(生效中画像保存后立即生效)"
              value={editNarrative}
              onChange={(e) => setEditNarrative(e.target.value)}
              helperText={`${editNarrative.length} 字;建议 100~600 字,聚焦可执行的批改偏好`}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>取消</Button>
          <Button variant="contained" onClick={() => void saveEdit()}>
            保存
          </Button>
        </DialogActions>
      </Dialog>

      {/* ---------- 删除确认 ---------- */}
      <Dialog open={Boolean(deleting)} onClose={() => setDeleting(null)} maxWidth="xs" fullWidth>
        <DialogTitle>删除画像</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            确认删除「{deleting?.name}」?删除后不可恢复;若该画像生效中,删除即恢复默认风格。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleting(null)}>取消</Button>
          <Button color="error" variant="contained" onClick={() => void doDelete()}>
            删除
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
