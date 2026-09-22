/**
 * 数据加密区块(设置中心内嵌卡片 + 全部操作弹窗)
 *
 * 覆盖能力:
 * - 状态展示:未启用 / 已启用(已解锁|锁定) / 恢复密钥状态 / 迁移进度轮询
 * - 启用(设置口令)→ 生成恢复密钥(仅一次展示)→ 存量加密迁移
 * - 解锁 / 锁定 / 修改口令(仅重新包裹)
 * - 关闭加密:decrypt_all(先还原明文) / keep_ciphertext(保留密文)
 * - 忘记口令:恢复密钥重置(强制轮换密钥)/ 归档重建(Plan B 终极兜底)
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  LinearProgress,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import ContentCopyOutlinedIcon from '@mui/icons-material/ContentCopyOutlined'
import KeyOutlinedIcon from '@mui/icons-material/KeyOutlined'
import LockOpenOutlinedIcon from '@mui/icons-material/LockOpenOutlined'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import PlayArrowOutlinedIcon from '@mui/icons-material/PlayArrowOutlined'
import SecurityOutlinedIcon from '@mui/icons-material/SecurityOutlined'
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined'
import {
  changeEncryptionPassword,
  disableEncryption,
  extractErrorMessage,
  fetchEncryptionStatus,
  generateRecoveryKey,
  lockEncryption,
  planbArchiveReinit,
  resetWithRecoveryKey,
  setupEncryption,
  startEncryptionMigration,
  unlockEncryption,
} from '../../api/client'
import type { EncryptionStatus } from '../../types'

type DialogKind =
  | null
  | 'setup'
  | 'unlock'
  | 'change'
  | 'generate-key'
  | 'show-key'
  | 'reset-with-key'
  | 'disable'
  | 'planb'

function passwordHelper(text: string): { ok: boolean; hint: string } {
  if (text.length < 8) return { ok: false, hint: '至少 8 位' }
  if (!/[a-zA-Z]/.test(text) || !/\d/.test(text)) return { ok: false, hint: '需同时包含字母与数字' }
  return { ok: true, hint: '强度符合要求' }
}

export default function EncryptionSection({ onChanged }: { onChanged?: () => void }) {
  const [status, setStatus] = useState<EncryptionStatus | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [dialog, setDialog] = useState<DialogKind>(null)
  const [busy, setBusy] = useState(false)
  const [pw1, setPw1] = useState('')
  const [pw2, setPw2] = useState('')
  const [recoveryKey, setRecoveryKey] = useState('')
  const [recoveryInput, setRecoveryInput] = useState('')
  const [keySaved, setKeySaved] = useState(false)
  const [disableMode, setDisableMode] = useState<'decrypt_all' | 'keep_ciphertext'>('decrypt_all')
  const [planbPhrase, setPlanbPhrase] = useState('')
  const pollTimer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await fetchEncryptionStatus()
      setStatus(data)
      return data
    } catch (e) {
      setError(extractErrorMessage(e))
      return null
    }
  }, [])

  useEffect(() => {
    void refresh()
    return () => {
      if (pollTimer.current) window.clearInterval(pollTimer.current)
    }
  }, [refresh])

  // 迁移进行中:轮询进度(每 1.5s),完成后刷新并提示
  useEffect(() => {
    if (status?.migration?.running && !pollTimer.current) {
      pollTimer.current = window.setInterval(async () => {
        const data = await refresh()
        if (data && !data.migration.running) {
          if (pollTimer.current) window.clearInterval(pollTimer.current)
          pollTimer.current = null
          if (data.migration.error) setError(`迁移失败:${data.migration.error}`)
          else setNotice(`迁移完成,共处理 ${data.migration.done} 行`)
          onChanged?.()
        }
      }, 1500)
    }
  }, [status?.migration?.running, refresh, onChanged])

  const resetDialogs = () => {
    setDialog(null)
    setPw1('')
    setPw2('')
    setRecoveryInput('')
    setKeySaved(false)
    setPlanbPhrase('')
  }

  const run = async (task: () => Promise<void>) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await task()
      await refresh()
      onChanged?.()
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (status === null) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={16} />
        <Typography variant="body2" color="text.secondary">
          正在读取加密状态…
        </Typography>
      </Box>
    )
  }

  const migrating = status.migration.running
  const progress =
    status.migration.total > 0 ? Math.round((status.migration.done / status.migration.total) * 100) : 0

  return (
    <Box
      sx={{
        border: 1,
        borderColor: status.enabled || status.has_password ? 'primary.main' : 'divider',
        borderRadius: 3,
        p: 1.5,
        bgcolor: 'action.hover',
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        <SecurityOutlinedIcon fontSize="small" color="primary" />
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          数据加密
        </Typography>
        {status.enabled ? (
          <Chip size="small" color="success" label="已启用" variant="outlined" />
        ) : status.has_password ? (
          <Chip size="small" color="warning" label="保留密文" variant="outlined" />
        ) : (
          <Chip size="small" label="未启用" variant="outlined" />
        )}
        {status.locked && <Chip size="small" color="error" icon={<LockOutlinedIcon />} label="锁定中" variant="outlined" />}
      </Stack>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        对学生姓名/学号、批改结果、错因、考卷等敏感列做 AES-256 透明加解密(存储层自动生效,
        统计与批改链路不受影响);密钥仅驻留内存,重启后需口令解锁。
      </Typography>

      {status.dev_master_enabled && (
        <Typography variant="caption" color="warning.main" sx={{ display: 'block', mb: 1 }}>
          开发模式:已启用万能密码兜底(锁定状态下可直接提交预置长 Hash 解锁;请勿在生产环境开启)
        </Typography>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError('')}>
          {error}
        </Alert>
      )}
      {notice && (
        <Alert severity="success" sx={{ mb: 1 }} onClose={() => setNotice('')}>
          {notice}
        </Alert>
      )}
      {status.migration.error && (
        <Alert severity="warning" sx={{ mb: 1 }} onClose={() => void refresh()}>
          上次迁移出错:{status.migration.error}(已处理 {status.migration.done} 行;可重跑)
        </Alert>
      )}

      {migrating && (
        <Box sx={{ mb: 1 }}>
          <LinearProgress variant="determinate" value={progress} />
          <Typography variant="caption" color="text.secondary">
            存量{status.migration.mode === 'decrypt' ? '还原' : '加密'}中… {status.migration.done}/
            {status.migration.total}
          </Typography>
        </Box>
      )}

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {!status.has_password && (
          <Button size="small" variant="contained" startIcon={<KeyOutlinedIcon />} onClick={() => setDialog('setup')}>
            启用加密
          </Button>
        )}
        {status.has_password && status.locked && (
          <Button size="small" variant="contained" startIcon={<LockOpenOutlinedIcon />} onClick={() => setDialog('unlock')}>
            解锁
          </Button>
        )}
        {status.has_password && !status.locked && (
          <>
            <Button size="small" variant="outlined" startIcon={<LockOutlinedIcon />} onClick={() => void run(() => lockEncryption().then(() => undefined))}>
              立即锁定
            </Button>
            <Button size="small" variant="outlined" startIcon={<KeyOutlinedIcon />} onClick={() => setDialog('change')}>
              修改口令
            </Button>
            <Button
              size="small"
              variant="outlined"
              startIcon={<ContentCopyOutlinedIcon />}
              onClick={() => setDialog('generate-key')}
            >
              {status.recovery_configured ? '重新生成恢复密钥' : '生成恢复密钥'}
            </Button>
            <Button
              size="small"
              variant="outlined"
              color="warning"
              startIcon={<PlayArrowOutlinedIcon />}
              disabled={migrating}
              onClick={() => void run(async () => {
                await startEncryptionMigration('encrypt')
                setNotice('存量加密已启动(后台执行)')
              })}
            >
              加密存量数据
            </Button>
            <Button size="small" variant="outlined" color="error" startIcon={<WarningAmberOutlinedIcon />} onClick={() => setDialog('disable')}>
              关闭加密
            </Button>
          </>
        )}
        {status.has_password && status.locked && (
          <Button size="small" variant="text" color="warning" onClick={() => setDialog('reset-with-key')}>
            忘记口令?用恢复密钥重置
          </Button>
        )}
      </Stack>

      {/* ---------- 弹窗 ---------- */}
      <Dialog open={dialog === 'setup'} onClose={resetDialogs} maxWidth="xs" fullWidth>
        <DialogTitle>启用数据加密</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              设置加密口令(至少 8 位,含字母与数字)。口令用于包裹密钥,系统不保存口令本身;
              请务必牢记或生成恢复密钥。
            </Typography>
            <TextField label="口令" type="password" size="small" value={pw1} onChange={(e) => setPw1(e.target.value)} helperText={pw1 ? passwordHelper(pw1).hint : ' '} error={Boolean(pw1) && !passwordHelper(pw1).ok} />
            <TextField label="确认口令" type="password" size="small" value={pw2} onChange={(e) => setPw2(e.target.value)} error={Boolean(pw2) && pw2 !== pw1} helperText={pw2 && pw2 !== pw1 ? '两次输入不一致' : ' '} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={resetDialogs}>取消</Button>
          <Button
            variant="contained"
            disabled={busy || !passwordHelper(pw1).ok || pw1 !== pw2}
            onClick={() =>
              void run(async () => {
                await setupEncryption(pw1)
                setNotice('加密已启用;建议继续:生成恢复密钥 → 加密存量数据')
                resetDialogs()
              })
            }
          >
            启用
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={dialog === 'unlock'} onClose={resetDialogs} maxWidth="xs" fullWidth>
        <DialogTitle>解锁学生数据</DialogTitle>
        <DialogContent>
          <TextField autoFocus fullWidth label="口令" type="password" size="small" sx={{ mt: 1 }} value={pw1} onChange={(e) => setPw1(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && pw1 && void run(async () => { await unlockEncryption(pw1); resetDialogs(); setNotice('已解锁') })} />
          {status.dev_master_enabled && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              开发模式:口令遗失时可直接输入预置万能密码(长 Hash)解锁
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={resetDialogs}>取消</Button>
          <Button variant="contained" disabled={busy || !pw1} onClick={() => void run(async () => { await unlockEncryption(pw1); resetDialogs(); setNotice('已解锁') })}>
            解锁
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={dialog === 'change'} onClose={resetDialogs} maxWidth="xs" fullWidth>
        <DialogTitle>修改口令</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField label="当前口令" type="password" size="small" value={pw1} onChange={(e) => setPw1(e.target.value)} />
            <TextField label="新口令" type="password" size="small" value={pw2} onChange={(e) => setPw2(e.target.value)} helperText={pw2 ? passwordHelper(pw2).hint : ' '} error={Boolean(pw2) && !passwordHelper(pw2).ok} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={resetDialogs}>取消</Button>
          <Button variant="contained" disabled={busy || !pw1 || !passwordHelper(pw2).ok} onClick={() => void run(async () => { await changeEncryptionPassword(pw1, pw2); resetDialogs(); setNotice('口令已更新(密钥未轮换,无需重加密)') })}>
            保存
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={dialog === 'generate-key'} onClose={resetDialogs} maxWidth="sm" fullWidth>
        <DialogTitle>生成恢复密钥</DialogTitle>
        <DialogContent>
          <Alert severity="info" sx={{ mb: 2 }}>
            恢复密钥用于忘记口令时重置(会强制轮换密钥并全量重加密)。密钥仅展示一次,
            请手抄或保存到离线位置;生成后旧恢复密钥立即失效。
          </Alert>
          {recoveryKey && (
            <Box sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 2, bgcolor: 'action.hover', mb: 1 }}>
              <Typography variant="body2" sx={{ fontFamily: 'monospace', letterSpacing: 1, wordBreak: 'break-all' }}>
                {recoveryKey}
              </Typography>
              <Button size="small" startIcon={<ContentCopyOutlinedIcon />} sx={{ mt: 1 }} onClick={() => void navigator.clipboard.writeText(recoveryKey)}>
                复制
              </Button>
            </Box>
          )}
          <FormControlLabel
            control={<Checkbox checked={keySaved} onChange={(e) => setKeySaved(e.target.checked)} />}
            label="我已离线保存恢复密钥,并知晓其丢失后无法找回"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={resetDialogs}>关闭</Button>
          {!recoveryKey && (
            <Button variant="contained" disabled={busy} onClick={() => void run(async () => setRecoveryKey(await generateRecoveryKey()))}>
              生成
            </Button>
          )}
          {recoveryKey && (
            <Button variant="contained" disabled={!keySaved} onClick={resetDialogs}>
              完成
            </Button>
          )}
        </DialogActions>
      </Dialog>

      <Dialog open={dialog === 'reset-with-key'} onClose={resetDialogs} maxWidth="xs" fullWidth>
        <DialogTitle>用恢复密钥重置口令</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Alert severity="warning">重置将强制轮换加密密钥并全量重加密,期间请勿中断服务。</Alert>
            <TextField label="恢复密钥" size="small" placeholder="XXXXX-XXXXX-…" value={recoveryInput} onChange={(e) => setRecoveryInput(e.target.value)} />
            <TextField label="新口令" type="password" size="small" value={pw2} onChange={(e) => setPw2(e.target.value)} helperText={pw2 ? passwordHelper(pw2).hint : ' '} error={Boolean(pw2) && !passwordHelper(pw2).ok} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={resetDialogs}>取消</Button>
          <Button
            variant="contained"
            disabled={busy || !recoveryInput || !passwordHelper(pw2).ok}
            onClick={() =>
              void run(async () => {
                await resetWithRecoveryKey(recoveryInput, pw2)
                resetDialogs()
                setNotice('已重置口令并完成密钥轮换(原恢复密钥继续有效)')
              })
            }
          >
            重置
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={dialog === 'disable'} onClose={resetDialogs} maxWidth="xs" fullWidth>
        <DialogTitle>关闭加密</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ pt: 1 }}>
            <TextField label="当前口令" type="password" size="small" value={pw1} onChange={(e) => setPw1(e.target.value)} />
            <FormControl>
              <RadioGroup value={disableMode} onChange={(e) => setDisableMode(e.target.value as typeof disableMode)}>
                <FormControlLabel value="decrypt_all" control={<Radio size="small" />} label="还原全部明文并彻底关闭(推荐,需已解锁)" />
                <FormControlLabel value="keep_ciphertext" control={<Radio size="small" />} label="停止新写入加密、保留已有密文(重启后仍需解锁)" />
              </RadioGroup>
            </FormControl>
            <Alert severity="warning" icon={<WarningAmberOutlinedIcon />}>
              还原操作会逐行解密全部学生数据,请确认后执行。
            </Alert>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={resetDialogs}>取消</Button>
          <Button
            color="error"
            variant="contained"
            disabled={busy || !pw1}
            onClick={() => void run(async () => { await disableEncryption(pw1, disableMode); resetDialogs(); setNotice('加密已关闭') })}
          >
            确认关闭
          </Button>
        </DialogActions>
      </Dialog>

      {status.has_password && status.locked && (
        <Box sx={{ mt: 1.5 }}>
          <Button size="small" variant="text" color="error" startIcon={<WarningAmberOutlinedIcon />} onClick={() => setDialog('planb')}>
            无法找回密钥?归档密文并重建(Plan B,数据不可直接读取)
          </Button>
        </Box>
      )}

      <Dialog open={dialog === 'planb'} onClose={resetDialogs} maxWidth="sm" fullWidth>
        <DialogTitle>Plan B · 归档密文并清空重建</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Alert severity="error">
              这是最后的兜底方案:系统会把数据库密文完整归档到 backend/data/archive_时间戳/,
              然后清空全部业务数据重建空库(密钥/恢复密钥一并清除)。归档密文在找回密钥前无法读取。
            </Alert>
            <TextField
              label='输入"清空重建"以确认'
              size="small"
              value={planbPhrase}
              onChange={(e) => setPlanbPhrase(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={resetDialogs}>取消</Button>
          <Button
            color="error"
            variant="contained"
            disabled={busy || planbPhrase.trim() !== '清空重建'}
            onClick={() =>
              void run(async () => {
                const result = await planbArchiveReinit(planbPhrase)
                resetDialogs()
                setNotice(`${result.detail}(归档目录:${String(result.data.archive_dir ?? '')})`)
              })
            }
          >
            执行归档重建
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
