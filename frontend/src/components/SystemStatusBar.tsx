/**
 * 全局等待态状态条(真实进度 + 真实自检;无占位式假提示)
 *
 * 数据源:GET /api/status/overview(3 秒轮询)——
 * - active:批改队列 / 姓名预识别 / 图片压缩 / 手写训练 的真实 percent 与 ETA;
 * - checkup:后台**真实执行**的轻量自检摘要(数据库/存储/磁盘/识别引擎)。
 * 无活跃任务时自动隐藏;文案模板:「正在进行任务 XX,预计等待 XX,目前状态:自检短语」。
 */

import { useEffect, useState } from 'react'
import { Box, Chip, LinearProgress, Stack, Typography } from '@mui/material'
import { fetchStatusOverview } from '../api/client'
import type { StatusOverview } from '../api/client'

/** 秒 → "X 分 Y 秒" / "X 秒"(真实 ETA 展示) */
function formatEta(seconds: number | null): string {
  if (seconds == null) return ''
  if (seconds >= 90) {
    return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
  }
  return `${Math.max(1, seconds)} 秒`
}

export default function SystemStatusBar() {
  const [data, setData] = useState<StatusOverview | null>(null)

  useEffect(() => {
    let disposed = false
    const tick = async () => {
      try {
        const overview = await fetchStatusOverview()
        if (!disposed) setData(overview)
      } catch {
        /* 状态条失败静默;不影响主流程 */
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), 3000)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [])

  const active = data?.active ?? []
  if (active.length === 0) return null
  const checkup = data?.checkup

  return (
    <Box
      sx={{
        px: 2,
        py: 0.75,
        bgcolor: 'background.paper',
        borderBottom: 1,
        borderColor: 'divider',
      }}
    >
      <Stack spacing={0.75}>
        {active.map((task, index) => (
          <Box key={`${task.kind}-${index}`}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.25 }}>
              <Typography variant="caption" sx={{ fontWeight: 600 }}>
                正在进行任务 {task.label}
              </Typography>
              {task.eta_seconds != null && (
                <Typography variant="caption" color="text.secondary">
                  预计等待约 {formatEta(task.eta_seconds)}
                </Typography>
              )}
              {task.percent != null && (
                <Typography variant="caption" color="text.secondary">
                  {task.percent}%
                </Typography>
              )}
              <Typography variant="caption" color="text.secondary" noWrap sx={{ ml: 'auto' }}>
                {task.status_text}
              </Typography>
            </Stack>
            <LinearProgress
              variant={task.percent != null ? 'determinate' : 'indeterminate'}
              value={task.percent ?? undefined}
              sx={{ height: 4, borderRadius: 2 }}
            />
          </Box>
        ))}
        {checkup && (
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              size="small"
              color={checkup.ok ? 'success' : 'warning'}
              variant="outlined"
              label={checkup.ok ? '自检正常' : '自检发现异常'}
            />
            <Typography variant="caption" color="text.secondary" noWrap>
              目前状态:{checkup.summary}
            </Typography>
          </Stack>
        )}
      </Stack>
    </Box>
  )
}
