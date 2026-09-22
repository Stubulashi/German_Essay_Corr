/**
 * 班级花名册区块(班级管理页内;自原花名册对话框提炼为页内区块)
 *
 * - 支持"粘贴文本"或"上传 CSV/TXT 文件"(每行 `姓名,学号`,学号可选);
 * - 显示当前花名册(顺序 = 导入顺序 = "按名单顺序指派"所用顺序);
 * - 重复姓名自动去重(已存在时补全学号),导入为增量更新。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Snackbar,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined'
import { extractErrorMessage, fetchClassRoster, importClassRoster } from '../../api/client'
import type { RosterMember } from '../../types'

interface Props {
  /** 当前班级;为空时展示引导提示且不请求 */
  classId: number | null
}

export default function RosterSection({ classId }: Props) {
  const [members, setMembers] = useState<RosterMember[]>([])
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ msg: string; severity: 'success' | 'error' | 'info' } | null>(null)

  const load = useCallback(async () => {
    if (classId == null) {
      setMembers([])
      return
    }
    try {
      setMembers(await fetchClassRoster(classId))
    } catch {
      setMembers([])
    }
  }, [classId])

  useEffect(() => {
    void load()
  }, [load])

  const doImport = async () => {
    if (classId == null || (!text.trim() && !file)) return
    setBusy(true)
    try {
      const result = await importClassRoster(classId, { text: text.trim() || undefined, file })
      setToast({ msg: `导入完成:新增 ${result.imported} · 更新 ${result.updated} · 共 ${result.total} 人`, severity: 'success' })
      setText('')
      setFile(null)
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setBusy(false)
    }
  }

  if (classId == null) {
    return <Alert severity="info">请先在上方选择班级;还没有班级?切换到「班级总览」新建。</Alert>
  }

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        每行一名学生,格式 `姓名,学号`(学号可省略)。花名册用于:上传时"文件名匹配 / 按名单顺序"指派、
        考试考卷自动匹配与统计对齐。
      </Alert>

      <Stack spacing={2}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Button variant="outlined" component="label" startIcon={<UploadFileOutlinedIcon />} size="small">
            选择 CSV/TXT 文件
            <input
              hidden
              type="file"
              accept=".csv,.txt,.md"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </Button>
          {file && <Chip size="small" label={file.name} onDelete={() => setFile(null)} />}
        </Stack>

        <TextField
          multiline
          minRows={4}
          maxRows={10}
          fullWidth
          size="small"
          placeholder={'姓名,学号\n(每行一条;学号可省略)'}
          label="或粘贴名单文本"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />

        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            当前花名册({members.length} 人;顺序即"按名单顺序指派"顺序)
          </Typography>
          {members.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              尚未导入
            </Typography>
          ) : (
            <Stack spacing={0.4} sx={{ maxHeight: 360, overflowY: 'auto' }}>
              {members.map((member, index) => (
                <Stack key={member.id} direction="row" spacing={1} alignItems="center">
                  <Typography variant="caption" color="text.disabled" sx={{ width: 26 }}>
                    {index + 1}.
                  </Typography>
                  <Typography variant="body2" sx={{ flex: 1 }}>
                    {member.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {member.student_id ?? '—'}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          )}
        </Box>

        <Box>
          <Button
            variant="contained"
            disabled={busy || (!text.trim() && !file)}
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
            onClick={() => void doImport()}
          >
            导入花名册
          </Button>
        </Box>
      </Stack>

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
