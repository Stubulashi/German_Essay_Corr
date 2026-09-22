/**
 * 全局解锁弹窗(数据加密)——双模式组件
 *
 * - 非受控(open 不传):会话中任意数据接口返回 423(axios 拦截器广播事件)时自动弹出;
 *   解锁成功后刷新页面以重新加载全部数据。**挂载时不再主动查询状态**,避免启动抢占界面。
 * - 受控(传 open):由启动流程编排使用(选择班级数据 → 加密状态检查 → 需要时解锁 → 进入工作台);
 *   解锁成功回调 onUnlocked,不刷新页面(启动期没有已加载数据需要重载)。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from '@mui/material'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import {
  ENCRYPTION_LOCKED_EVENT,
  extractErrorMessage,
  fetchEncryptionStatus,
  unlockEncryption,
} from '../../api/client'

interface Props {
  /** 受控开关;不传时为非受控(423 事件驱动) */
  open?: boolean
  /** 受控模式解锁成功回调(外层据此进入下一步) */
  onUnlocked?: () => void
}

export default function UnlockDialog({ open: controlledOpen, onUnlocked }: Props = {}) {
  const controlled = controlledOpen !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // 开发模式万能密码提示开关(生产恒 false:状态接口该字段恒为 false,不渲染任何内容)
  const [devMasterEnabled, setDevMasterEnabled] = useState(false)
  const open = controlled ? Boolean(controlledOpen) : internalOpen

  /** 重新检查状态(受控:未锁定则直接放行;非受控:按状态开关弹窗) */
  const checkStatus = useCallback(async () => {
    try {
      const status = await fetchEncryptionStatus()
      if (!(status.has_password && status.locked)) {
        if (controlled) onUnlocked?.()
        else setInternalOpen(false)
      } else if (!controlled) {
        setInternalOpen(true)
      } else {
        setError('数据仍未解锁,请输入口令。')
      }
    } catch (e) {
      if (controlled) setError(extractErrorMessage(e))
    }
  }, [controlled, onUnlocked])

  useEffect(() => {
    if (controlled) return // 受控模式由启动流程编排:不自察、不监听事件
    const onLocked = () => setInternalOpen(true)
    window.addEventListener(ENCRYPTION_LOCKED_EVENT, onLocked)
    return () => window.removeEventListener(ENCRYPTION_LOCKED_EVENT, onLocked)
  }, [controlled])

  // 弹窗打开时查询一次加密状态:仅用于决定是否展示"开发模式万能密码"提示
  // (挂载时不查询,不抢占启动界面;查询失败静默,不阻塞解锁流程)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    void fetchEncryptionStatus()
      .then((status) => {
        if (!cancelled) setDevMasterEnabled(Boolean(status.dev_master_enabled))
      })
      .catch(() => {
        /* 状态查询异常:不展示开发提示 */
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      await unlockEncryption(password)
      setPassword('')
      if (controlled) {
        onUnlocked?.()
      } else {
        setInternalOpen(false)
        window.location.reload()
      }
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} maxWidth="xs" fullWidth disableEscapeKeyDown>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <LockOutlinedIcon color="primary" />
        学生数据已加密
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          请输入加密口令解锁后继续使用;忘记口令可在「运行配置 → 数据加密」中用恢复密钥重置。
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {error}
          </Alert>
        )}
        <TextField
          autoFocus
          fullWidth
          size="small"
          type="password"
          label="加密口令"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && password && !busy) void submit()
          }}
        />
        {devMasterEnabled && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            开发模式:口令遗失时可直接输入预置万能密码(长 Hash)解锁
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={() => void checkStatus()} disabled={busy}>
          重新检查状态
        </Button>
        <Button variant="contained" disabled={busy || !password} onClick={() => void submit()}>
          {busy ? <CircularProgress size={18} /> : '解锁'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
