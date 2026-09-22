/**
 * 班级总览区块(班级管理页内)
 *
 * - 班级卡片列表(名称 / 备注 / 任务数 / 已并入徽章);
 * - 新建班级(名称必填,备注可选);
 * - 删除班级(二次确认;班级下存在批改任务时后端拒绝并给出原因)。
 */

import { useState } from 'react'
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
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddOutlinedIcon from '@mui/icons-material/AddOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import { createClass, deleteClass, extractErrorMessage } from '../../api/client'
import type { SchoolClass } from '../../types'

interface Props {
  classes: SchoolClass[]
  /** 新建/删除后回调刷新班级列表 */
  onReload: () => void
}

export default function ClassOverviewSection({ classes, onReload }: Props) {
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [creating, setCreating] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<SchoolClass | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [feedback, setFeedback] = useState<{ severity: 'success' | 'error'; text: string } | null>(null)

  const handleCreate = async () => {
    const trimmed = name.trim()
    if (!trimmed) return
    setCreating(true)
    setFeedback(null)
    try {
      const created = await createClass({ name: trimmed, note: note.trim() || null })
      setFeedback({ severity: 'success', text: `班级「${created.name}」已创建` })
      setCreateOpen(false)
      setName('')
      setNote('')
      onReload()
    } catch (e) {
      setFeedback({ severity: 'error', text: extractErrorMessage(e) })
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    setFeedback(null)
    try {
      await deleteClass(deleteTarget.id)
      setFeedback({ severity: 'success', text: `班级「${deleteTarget.name}」已删除` })
      setDeleteTarget(null)
      onReload()
    } catch (e) {
      setFeedback({ severity: 'error', text: extractErrorMessage(e) })
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          共 {classes.length} 个班级(含已并入)
        </Typography>
        <Button size="small" variant="contained" startIcon={<AddOutlinedIcon />} onClick={() => setCreateOpen(true)}>
          新建班级
        </Button>
      </Stack>

      {feedback && (
        <Alert severity={feedback.severity} onClose={() => setFeedback(null)} sx={{ mb: 2 }}>
          {feedback.text}
        </Alert>
      )}

      {classes.length === 0 ? (
        <Alert severity="info">还没有班级,点击右上角「新建班级」创建第一个班级。</Alert>
      ) : (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', lg: 'repeat(3, 1fr)' },
            gap: 1.5,
          }}
        >
          {classes.map((cls) => (
            <Card key={cls.id} variant="outlined">
              <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 0 }} noWrap>
                    {cls.name}
                  </Typography>
                  {cls.merged_into_id != null && (
                    <Chip size="small" variant="outlined" label="已并入" />
                  )}
                  <Tooltip title="删除班级">
                    <span>
                      <Button
                        size="small"
                        color="error"
                        sx={{ minWidth: 34, px: 0.5 }}
                        disabled={cls.merged_into_id != null}
                        onClick={() => setDeleteTarget(cls)}
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </Button>
                    </span>
                  </Tooltip>
                </Stack>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }} noWrap>
                  {cls.note || '—'}
                </Typography>
                <Typography variant="caption" color="text.disabled">
                  批改任务 {cls.task_count} 个
                </Typography>
              </CardContent>
            </Card>
          ))}
        </Box>
      )}

      {/* ================= 新建班级 ================= */}
      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>新建班级</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            fullWidth
            label="班级名称"
            placeholder="如:高二(3)班德语"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <TextField
            margin="dense"
            fullWidth
            label="备注(可选)"
            placeholder="如:2026 秋季学期"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setCreateOpen(false)}>
            取消
          </Button>
          <Button variant="contained" disabled={!name.trim() || creating} onClick={() => void handleCreate()}>
            创建
          </Button>
        </DialogActions>
      </Dialog>

      {/* ================= 删除确认 ================= */}
      <Dialog open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)} maxWidth="xs" fullWidth>
        <DialogTitle>删除班级</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            确定删除班级「{deleteTarget?.name}」吗?此操作不可撤销;
            若班级下仍存在批改任务,系统会拒绝删除并提示处理方式。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setDeleteTarget(null)}>
            取消
          </Button>
          <Button
            color="error"
            variant="contained"
            disabled={deleting}
            startIcon={deleting ? <CircularProgress size={14} color="inherit" /> : undefined}
            onClick={() => void handleDelete()}
          >
            确认删除
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
