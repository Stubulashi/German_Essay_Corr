/**
 * 启动数据选择对话框(每次打开系统时展示,可会话内跳过/设置中心关闭)
 *
 * - 选择本地班级数据包(class_package_*.zip)导入后再进入工作台;
 * - 直接进入系统:保持既有解锁/数据流程;
 * - 未处理前 AppShell 不挂载子页面 → 启动不发生数据请求(不再打开即弹密码);
 * - 导入时若数据加密处于锁定态,既有全局解锁窗会自动弹出,解锁后重试即可。
 */

import { useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import FolderOpenOutlinedIcon from '@mui/icons-material/FolderOpenOutlined'
import LoginOutlinedIcon from '@mui/icons-material/LoginOutlined'
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined'
import { extractErrorMessage, importClassPackage } from '../api/client'

interface Props {
  /** 用户完成选择后的回调;skipSession=勾选了「本次会话不再提示」 */
  onDone: (skipSession: boolean) => void
}

export default function StartupDataDialog({ onDone }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [skipSession, setSkipSession] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const handleImport = async () => {
    if (!file) {
      setError('请先选择班级数据包文件(.zip)。')
      return
    }
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      const result = (await importClassPackage(file)) as unknown as Record<string, unknown>
      const name = typeof result.class_name === 'string' ? result.class_name : '班级'
      const count = typeof result.task_count === 'number' ? `,任务 ${result.task_count} 个` : ''
      setSuccess(`导入完成:${name}${count}。正在进入工作台…`)
      window.setTimeout(() => onDone(skipSession), 900)
    } catch (e) {
      const message = extractErrorMessage(e)
      if (
        message.includes('423') ||
        message.includes('锁定') ||
        message.includes('解锁') ||
        message.includes('加密')
      ) {
        setError(
          '数据已加密:已弹出解锁窗口,请输入口令解锁;解锁后页面会自动刷新,请重新点击「导入并进入」。',
        )
      } else {
        setError(message)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Paper sx={{ p: 4, borderRadius: 3, maxWidth: 560, width: '100%' }}>
      <Typography variant="h5" sx={{ mb: 0.5 }}>
        选择班级数据
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
        可先导入本地班级数据包再进入工作台;如本机已有数据,可直接进入。
      </Typography>
      <Stack spacing={2}>
        <Box sx={{ p: 2, border: '1px dashed', borderColor: 'divider', borderRadius: 2.5 }}>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <FolderOpenOutlinedIcon color="primary" />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2">选择本地班级数据包</Typography>
              <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                {file
                  ? `${file.name}(${Math.max(1, Math.round(file.size / 1024))} KB)`
                  : 'class_package_*.zip(由「导出班级数据」生成)'}
              </Typography>
            </Box>
            <Button
              size="small"
              variant="outlined"
              startIcon={<UploadFileOutlinedIcon />}
              onClick={() => inputRef.current?.click()}
              disabled={busy}
            >
              {file ? '重新选择' : '浏览文件'}
            </Button>
          </Stack>
          <input
            ref={inputRef}
            type="file"
            hidden
            accept=".zip"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null)
              e.target.value = ''
            }}
          />
          {file && (
            <Button
              size="small"
              variant="contained"
              sx={{ mt: 1.5 }}
              disabled={busy}
              startIcon={busy ? <CircularProgress size={14} color="inherit" /> : <UploadFileOutlinedIcon />}
              onClick={() => void handleImport()}
            >
              {busy ? '导入中…' : '导入并进入工作台'}
            </Button>
          )}
        </Box>
        {error && (
          <Alert severity="error" sx={{ borderRadius: 2.5 }}>
            {error}
          </Alert>
        )}
        {success && (
          <Alert severity="success" sx={{ borderRadius: 2.5 }}>
            {success}
          </Alert>
        )}
        <FormControlLabel
          control={
            <Checkbox size="small" checked={skipSession} onChange={(e) => setSkipSession(e.target.checked)} />
          }
          label="本次会话不再提示(下次打开仍会显示;可在设置中心永久关闭)"
        />
        <Button
          variant="text"
          startIcon={<LoginOutlinedIcon />}
          onClick={() => onDone(skipSession)}
          disabled={busy}
        >
          直接进入系统(使用现有数据)
        </Button>
      </Stack>
    </Paper>
  )
}
