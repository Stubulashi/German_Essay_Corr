/**
 * 练习卷详情:试卷 / 标准答案 双页签 + 导出 .md + 打印 + 删除
 *
 * - 试卷页签:worksheet_markdown(后端确定性渲染);
 * - 答案页签:answer_markdown(逐题答案 + 中文解析);
 * - 打印跳转独立路由 /print/practice/:sheetId(试卷 + 答案两页)。
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
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from '@mui/material'
import ArrowBackOutlinedIcon from '@mui/icons-material/ArrowBackOutlined'
import DeleteOutlineOutlinedIcon from '@mui/icons-material/DeleteOutlineOutlined'
import DownloadOutlinedIcon from '@mui/icons-material/DownloadOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import { useNavigate, useParams } from 'react-router-dom'
import { deletePracticeSheet, extractErrorMessage, fetchPracticeSheet } from '../api/client'
import MarkdownReport from '../components/MarkdownReport'
import type { PracticeSheet } from '../types'

/** 触发浏览器下载(与批注导出一致的实现方式) */
function downloadText(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function PracticeDetailPage() {
  const { sheetId } = useParams()
  const navigate = useNavigate()
  const [sheet, setSheet] = useState<PracticeSheet | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState(0)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setSheet(await fetchPracticeSheet(Number(sheetId)))
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [sheetId])

  useEffect(() => {
    void load()
  }, [load])

  const handleDelete = async () => {
    if (!sheet) return
    try {
      await deletePracticeSheet(sheet.id)
      navigate('/practice')
    } catch (e) {
      setError(extractErrorMessage(e))
      setConfirmDelete(false)
    }
  }

  if (loading) {
    return (
      <Box sx={{ py: 10, textAlign: 'center' }}>
        <CircularProgress />
      </Box>
    )
  }
  if (error || !sheet) {
    return (
      <Stack spacing={2}>
        <Alert severity="error" sx={{ borderRadius: 2.5 }}>
          {error || '练习卷不存在'}
        </Alert>
        <Button startIcon={<ArrowBackOutlinedIcon />} onClick={() => navigate('/practice')} sx={{ alignSelf: 'flex-start' }}>
          返回练习卷列表
        </Button>
      </Stack>
    )
  }

  return (
    <Stack spacing={2.5}>
      {/* ---------- 页头 ---------- */}
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems={{ md: 'center' }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Button size="small" startIcon={<ArrowBackOutlinedIcon />} onClick={() => navigate('/practice')}>
              返回
            </Button>
            <Typography variant="h5" noWrap>
              {sheet.title}
            </Typography>
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {new Date(sheet.created_at).toLocaleString()}
            {sheet.class_name ? ` · ${sheet.class_name}` : ''}
            {` · 共 ${sheet.question_count} 题`}
            {` · 来源任务 ${String((sheet.source as { task_count?: number }).task_count ?? '-')} 篇`}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            startIcon={<DownloadOutlinedIcon />}
            onClick={() => downloadText(`${sheet.title}-试卷.md`, sheet.worksheet_markdown)}
          >
            导出试卷
          </Button>
          <Button
            size="small"
            startIcon={<DownloadOutlinedIcon />}
            onClick={() => downloadText(`${sheet.title}-标准答案.md`, sheet.answer_markdown)}
          >
            导出答案
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={<PrintOutlinedIcon />}
            onClick={() => window.open(`/print/practice/${sheet.id}`, '_blank')}
          >
            打印
          </Button>
          <Button
            size="small"
            color="error"
            startIcon={<DeleteOutlineOutlinedIcon />}
            onClick={() => setConfirmDelete(true)}
          >
            删除
          </Button>
        </Stack>
      </Stack>

      {/* ---------- 双页签内容 ---------- */}
      <Paper sx={{ borderRadius: 3 }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ px: 1.5, borderBottom: 1, borderColor: 'divider' }}>
          <Tab label="试卷" />
          <Tab label="标准答案" />
        </Tabs>
        <Box sx={{ p: 3 }}>
          <MarkdownReport markdown={tab === 0 ? sheet.worksheet_markdown : sheet.answer_markdown} />
        </Box>
      </Paper>

      {/* ---------- 删除确认 ---------- */}
      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)} maxWidth="xs" fullWidth>
        <DialogTitle>删除练习卷</DialogTitle>
        <DialogContent>
          <Typography variant="body2">确定删除「{sheet.title}」吗?此操作不可恢复。</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(false)}>取消</Button>
          <Button color="error" variant="contained" onClick={() => void handleDelete()}>
            确认删除
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
