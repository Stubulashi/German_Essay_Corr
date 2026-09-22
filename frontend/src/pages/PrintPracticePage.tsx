/**
 * 练习卷打印页(独立路由,渲染即最终纸面效果)
 *
 * - 第 1 页:试卷(worksheet_markdown);第 2 页:标准答案(answer_markdown);
 * - 顶部工具条仅在屏幕显示(打印时隐藏);进入页面自动触发打印一次。
 */

import { useEffect, useState } from 'react'
import { Box, Button, CircularProgress, Paper, Stack, Typography } from '@mui/material'
import ArrowBackOutlinedIcon from '@mui/icons-material/ArrowBackOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import { useNavigate, useParams } from 'react-router-dom'
import { extractErrorMessage, fetchPracticeSheet } from '../api/client'
import MarkdownReport from '../components/MarkdownReport'
import type { PracticeSheet } from '../types'

/** 打印时隐藏的工具条样式 */
const noPrint = { '@media print': { display: 'none' } } as const

export default function PrintPracticePage() {
  const { sheetId } = useParams()
  const navigate = useNavigate()
  const [sheet, setSheet] = useState<PracticeSheet | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    void fetchPracticeSheet(Number(sheetId))
      .then(setSheet)
      .catch((e) => setError(extractErrorMessage(e)))
  }, [sheetId])

  useEffect(() => {
    if (sheet) {
      const timer = window.setTimeout(() => window.print(), 600)
      return () => window.clearTimeout(timer)
    }
    return undefined
  }, [sheet])

  if (error) {
    return (
      <Box sx={{ p: 4 }}>
        <Typography color="error">{error}</Typography>
      </Box>
    )
  }
  if (!sheet) {
    return (
      <Box sx={{ py: 12, textAlign: 'center' }}>
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Box sx={{ bgcolor: 'grey.100', minHeight: '100vh', py: 3 }}>
      <Stack spacing={3} sx={{ maxWidth: 880, mx: 'auto', px: 2 }}>
        <Stack direction="row" spacing={1.5} sx={noPrint}>
          <Button startIcon={<ArrowBackOutlinedIcon />} onClick={() => navigate(`/practice/${sheet.id}`)}>
            返回详情
          </Button>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" startIcon={<PrintOutlinedIcon />} onClick={() => window.print()}>
            打印
          </Button>
        </Stack>
        <Paper sx={{ p: 4, borderRadius: 2, '@media print': { boxShadow: 'none', borderRadius: 0 } }}>
          <MarkdownReport markdown={sheet.worksheet_markdown} />
        </Paper>
        <Paper
          sx={{
            p: 4,
            borderRadius: 2,
            '@media print': { boxShadow: 'none', borderRadius: 0, pageBreakBefore: 'always' },
          }}
        >
          <MarkdownReport markdown={sheet.answer_markdown} />
        </Paper>
      </Stack>
    </Box>
  )
}
