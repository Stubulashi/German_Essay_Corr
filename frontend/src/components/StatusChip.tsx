/**
 * 任务状态标签组件
 *
 * 统一展示任务状态(排队中 / 批改中 / 待复核 / 已完成 / 失败),
 * 处理中状态附带小型加载动画。
 */

import { Chip, CircularProgress } from '@mui/material'
import type { TaskStatus } from '../types'
import { STATUS_META } from '../types'

interface Props {
  status: TaskStatus
  size?: 'small' | 'medium'
}

export default function StatusChip({ status, size = 'small' }: Props) {
  const meta = STATUS_META[status] ?? { label: status, color: 'default' as const }
  const processing = status === 'PROCESSING' || status === 'PENDING'

  return (
    <Chip
      size={size}
      color={meta.color}
      variant={status === 'COMPLETED' ? 'filled' : 'outlined'}
      label={meta.label}
      icon={processing ? <CircularProgress size={14} color="inherit" /> : undefined}
    />
  )
}
