/**
 * 班级合并区块(班级管理页内;自班级分析页对话框迁移为页内区块)
 *
 * - 选择来源/目标班级后自动加载预览(数据规模 + 花名册匹配方案 + 台账处置);
 * - 勾选确认后执行合并(后端原子操作,失败整体回滚);
 * - 来源班级不删除,仅标记「已并入」以保证可追溯。
 */

import { useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import CallMergeOutlinedIcon from '@mui/icons-material/CallMergeOutlined'
import { extractErrorMessage, fetchMergePreview, mergeClasses } from '../../api/client'
import type { ClassMergePreview, SchoolClass } from '../../types'

interface Props {
  classes: SchoolClass[]
  /** 合并完成后的回调(刷新班级列表) */
  onReload: () => void
}

export default function ClassMergeSection({ classes, onReload }: Props) {
  const [source, setSource] = useState<number | ''>('')
  const [target, setTarget] = useState<number | ''>('')
  const [preview, setPreview] = useState<ClassMergePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [feedback, setFeedback] = useState<{ severity: 'success' | 'error'; text: string } | null>(null)

  /** 可参与合并的班级(已并入的班级不再出现在合并入口) */
  const mergeableClasses = classes.filter((c) => !c.merged_into_id)

  /** 选择来源/目标后自动加载预览 */
  const handleSelect = async (nextSource: number | '', nextTarget: number | '') => {
    setPreview(null)
    setError(null)
    if (nextSource === '' || nextTarget === '') return
    setLoading(true)
    try {
      setPreview(await fetchMergePreview(nextSource, nextTarget))
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  /** 执行合并(需勾选确认;失败在服务端整体回滚) */
  const handleExecute = async () => {
    if (source === '' || target === '') return
    setExecuting(true)
    setError(null)
    try {
      const result = await mergeClasses({ source_class_id: source, target_class_id: target })
      setFeedback({
        severity: 'success',
        text:
          `合并完成:「${result.source.name}」已并入「${result.target.name}」——` +
          `任务 ${result.moved.tasks} 个 / 考试 ${result.moved.exams} 场 / ` +
          `花名册迁入 ${result.roster.moved} 人(去重 ${result.roster.deduplicated}、补全学号 ${result.roster.filled_id}、学号冲突 ${result.roster.conflicts}）`,
      })
      setSource('')
      setTarget('')
      setPreview(null)
      setConfirm(false)
      onReload()
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setExecuting(false)
    }
  }

  return (
    <Box sx={{ maxWidth: 860 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        把「来源班级」的全部数据(任务 / 错因 / 花名册 / 台账 / 考试)并入「目标班级」。
        合并为原子操作:失败自动完整回滚;来源班级不删除,仅标记为「已并入」以保证可追溯。
      </Typography>

      {feedback && (
        <Alert severity={feedback.severity} onClose={() => setFeedback(null)} sx={{ mb: 2 }}>
          {feedback.text}
        </Alert>
      )}

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 2 }}>
        <FormControl size="small" fullWidth>
          <InputLabel>来源班级(将被并入)</InputLabel>
          <Select
            label="来源班级(将被并入)"
            value={source}
            onChange={(e) => {
              const value = e.target.value as number | ''
              setSource(value)
              void handleSelect(value, target)
            }}
          >
            {mergeableClasses.map((c) => (
              <MenuItem key={c.id} value={c.id} disabled={c.id === target}>
                {c.name}
                {c.task_count > 0 ? ` (${c.task_count})` : ''}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" fullWidth>
          <InputLabel>目标班级(接收数据)</InputLabel>
          <Select
            label="目标班级(接收数据)"
            value={target}
            onChange={(e) => {
              const value = e.target.value as number | ''
              setTarget(value)
              void handleSelect(source, value)
            }}
          >
            {mergeableClasses.map((c) => (
              <MenuItem key={c.id} value={c.id} disabled={c.id === source}>
                {c.name}
                {c.task_count > 0 ? ` (${c.task_count})` : ''}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      {loading && (
        <Box sx={{ display: 'grid', placeItems: 'center', py: 3 }}>
          <CircularProgress size={26} />
        </Box>
      )}

      {preview && (
        <>
          <Divider sx={{ mb: 2 }} />
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            数据规模预览
          </Typography>
          <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1, mb: 2 }}>
            <Chip size="small" variant="outlined" label={`任务 ${preview.counts.tasks}`} />
            <Chip size="small" variant="outlined" label={`错因 ${preview.counts.error_records}`} />
            <Chip size="small" variant="outlined" label={`花名册 ${preview.counts.roster}`} />
            <Chip size="small" variant="outlined" label={`台账记录 ${preview.counts.homework_records}`} />
            <Chip size="small" variant="outlined" label={`考试 ${preview.counts.exams}(考卷 ${preview.counts.exam_papers})`} />
          </Stack>

          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            花名册匹配方案
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            迁入 {preview.roster.move} · 补全学号 {preview.roster.fill_id} · 去重 {preview.roster.duplicate} · 学号冲突 {preview.roster.conflict}(以目标为准)
          </Typography>
          <TableContainer component={Paper} variant="outlined" sx={{ mb: 2, maxHeight: 280 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>学生</TableCell>
                  <TableCell>处置</TableCell>
                  <TableCell>学号(来源 → 目标)</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {preview.roster.plan.map((m) => (
                  <TableRow key={m.name}>
                    <TableCell>{m.name}</TableCell>
                    <TableCell>
                      {m.status === 'move' && <Chip size="small" color="info" variant="outlined" label="迁入" />}
                      {m.status === 'fill_id' && <Chip size="small" color="success" variant="outlined" label="补全学号" />}
                      {m.status === 'duplicate' && <Chip size="small" variant="outlined" label="去重" />}
                      {m.status === 'conflict' && <Chip size="small" color="warning" variant="outlined" label="学号冲突(以目标为准）" />}
                    </TableCell>
                    <TableCell>
                      {m.source_student_id ?? '—'} → {m.target_student_id ?? '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          {preview.homework_items.total > 0 && (
            <Alert severity="info" variant="outlined" sx={{ mb: 2 }}>
              台账登记项:随班改挂 {preview.homework_items.move} 项;与目标同名 {preview.homework_items.merge_records} 项(其登记记录将并入目标同名项)
            </Alert>
          )}

          <FormControlLabel
            control={<Checkbox checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />}
            label={<Typography variant="body2">我已知晓以上影响,确认执行合并</Typography>}
          />
        </>
      )}

      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          {error}
        </Alert>
      )}

      <Box sx={{ mt: 2 }}>
        <Button
          variant="contained"
          disabled={!preview || !confirm || executing || loading}
          startIcon={executing ? <CircularProgress size={16} color="inherit" /> : <CallMergeOutlinedIcon />}
          onClick={() => void handleExecute()}
        >
          {executing ? '正在合并...' : '执行合并'}
        </Button>
      </Box>
    </Box>
  )
}
