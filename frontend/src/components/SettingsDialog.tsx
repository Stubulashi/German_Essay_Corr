/**
 * 设置中心(窗口式,替代原侧边抽屉)
 *
 * 结构:
 * - 居中大窗(Dialog maxWidth="xl",约 92vh 高,可滚动/可关闭);
 * - 左侧「大范围」一级导航:基础运行 / 管线 A / 管线 B·识别 / 管线 B·评分 /
 *   上传与图片 / 教学与报告 / 存储与高级(开发者) / 安全与访问 /
 *   提示词微调 / 数据加密 / 自动适配记录 / 探索性实验项;
 * - 右侧「小项」区:该范围下的全部字段控件(说明 / 默认值 / 当前值 / 恢复默认)。
 *
 * 延续既有能力:敏感字段脱敏与显式编辑/清除、探索项徽标与单项恢复默认、
 * 需重启徽标、管线组"测试连接"(可带未保存候选值)、保存写回 .env 并热生效、
 * 探索项一键回退、运行模式(正常/演示/开发)、数据加密区块、访问令牌。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ImageCompressProgress } from '../types'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  LinearProgress,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Snackbar,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import AutoAwesomeOutlinedIcon from '@mui/icons-material/AutoAwesomeOutlined'
import CloseOutlinedIcon from '@mui/icons-material/CloseOutlined'
import DeleteForeverOutlinedIcon from '@mui/icons-material/DeleteForeverOutlined'
import DocumentScannerOutlinedIcon from '@mui/icons-material/DocumentScannerOutlined'
import EditNoteOutlinedIcon from '@mui/icons-material/EditNoteOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import MemoryOutlinedIcon from '@mui/icons-material/MemoryOutlined'
import PhotoLibraryOutlinedIcon from '@mui/icons-material/PhotoLibraryOutlined'
import RestartAltOutlinedIcon from '@mui/icons-material/RestartAltOutlined'
import RestoreOutlinedIcon from '@mui/icons-material/RestoreOutlined'
import SaveOutlinedIcon from '@mui/icons-material/SaveOutlined'
import SchoolOutlinedIcon from '@mui/icons-material/SchoolOutlined'
import ScienceOutlinedIcon from '@mui/icons-material/ScienceOutlined'
import SecurityOutlinedIcon from '@mui/icons-material/SecurityOutlined'
import StorageOutlinedIcon from '@mui/icons-material/StorageOutlined'
import TuneOutlinedIcon from '@mui/icons-material/TuneOutlined'
import {
  getAdminToken,
  setAdminToken,
  extractErrorMessage,
  fetchPromptAppendices,
  startImageCompress,
  fetchImageCompressStatus,
  clearDemoData,
  clearAllData,
  seedDemoData,
  fetchSettings,
  resetSetting,
  rollbackSettings,
  clearLlmParamLearnings,
  testConnection,
  updatePromptAppendices,
  fetchSystemProfile,
  updateSettings,
} from '../api/client'
import type {
  HealthResponse,
  MaintenanceClearResult,
  MaintenanceSeedResult,
  PromptAppendices,
  SettingsField,
  SettingsUpdatePayload,
  SettingsView,
} from '../types'
import { useRefreshHealth } from '../hooks/useHealth'
import EncryptionSection from './settings/EncryptionSection'

interface Props {
  open: boolean
  onClose: () => void
  health?: HealthResponse | null
  /** UI 档位在设置中心被保存后回调(由 AppShell 同步到全局并即时生效) */
  onUiProfileChange?: (profile: 'balanced' | 'efficiency' | 'premium') => void
}

/** 分组 -> 连通性测试目标 */
const GROUP_TEST_TARGET: Record<string, 'pipeline_a' | 'ocr' | 'deepseek'> = {
  pipeline_a: 'pipeline_a',
  pipeline_b_ocr: 'ocr',
  pipeline_b_grading: 'deepseek',
}

/** 分组/特殊区块 -> 左栏图标与标题覆盖 */
const SECTION_META: Record<string, { icon: JSX.Element; title?: string }> = {
  basic: { icon: <TuneOutlinedIcon /> },
  pipeline_a: { icon: <MemoryOutlinedIcon /> },
  pipeline_b_ocr: { icon: <DocumentScannerOutlinedIcon /> },
  pipeline_b_grading: { icon: <AutoAwesomeOutlinedIcon /> },
  upload: { icon: <PhotoLibraryOutlinedIcon /> },
  teaching: { icon: <SchoolOutlinedIcon /> },
  storage: { icon: <StorageOutlinedIcon /> },
  security: { icon: <SecurityOutlinedIcon /> },
  prompts: { icon: <EditNoteOutlinedIcon />, title: '提示词微调' },
  encryption: { icon: <LockOutlinedIcon />, title: '数据加密' },
  'llm-learned': { icon: <RestartAltOutlinedIcon />, title: '自动适配记录' },
  experimental: { icon: <ScienceOutlinedIcon />, title: '探索性实验项' },
}

const PROMPT_KIND_LABELS: Record<string, string> = {
  pipeline_a: '管线 A · 综合批改',
  ocr: '管线 B · 手写识别',
  grading: '管线 B · 结构化评分',
}

export default function SettingsDialog({ open, onClose, health, onUiProfileChange }: Props) {
  const { refresh: refreshHealth } = useRefreshHealth()
  const [view, setView] = useState<SettingsView | null>(null)
  const [loadError, setLoadError] = useState('')
  const [active, setActive] = useState('basic')
  const [draft, setDraft] = useState<Record<string, boolean | number | string | null>>({})
  const [secretEdits, setSecretEdits] = useState<Record<string, string>>({})
  const [clearedSecrets, setClearedSecrets] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [clearingLearnings, setClearingLearnings] = useState(false)
  const [toast, setToast] = useState<{ msg: string; severity: 'success' | 'error' | 'info' } | null>(null)
  const [confirmRollback, setConfirmRollback] = useState(false)
  const [testing, setTesting] = useState('')
  const [adminToken, setAdminTokenLocal] = useState(getAdminToken())
  // 提示词微调(内嵌区块)
  const [prompts, setPrompts] = useState<PromptAppendices | null>(null)
  const [promptKind, setPromptKind] = useState('pipeline_a')
  const [promptText, setPromptText] = useState('')
  // 本机配置画像(手写识别档位的自动推荐依据;真实检测)
  const [profile, setProfile] = useState<{ cores: number; ram_gb: number | null; recommended_level: string; configured_level: string; effective_level: string } | null>(null)

  const allFields = useMemo(() => {
    const map: Record<string, SettingsField> = {}
    view?.groups.forEach((g) => g.fields.forEach((f) => (map[f.key] = f)))
    return map
  }, [view])

  const load = useCallback(async () => {
    setLoadError('')
    try {
      const data = await fetchSettings()
      setView(data)
      const fresh: Record<string, boolean | number | string | null> = {}
      data.groups.forEach((g) =>
        g.fields.forEach((f) => {
          if (!f.sensitive) fresh[f.key] = f.value ?? null
        }),
      )
      setDraft(fresh)
      setSecretEdits({})
      setClearedSecrets([])
    } catch (e) {
      setLoadError(extractErrorMessage(e))
    }
  }, [])

  useEffect(() => {
    if (open) void load()
    if (open) {
      fetchSystemProfile()
        .then(setProfile)
        .catch(() => setProfile(null))
    }
  }, [open, load])

  /** 打开提示词区块时加载附录与预览 */
  useEffect(() => {
    if (!open || active !== 'prompts') return
    fetchPromptAppendices()
      .then((data) => {
        setPrompts(data)
        setPromptText(data.appendices[promptKind] ?? '')
      })
      .catch((e) => setToast({ msg: extractErrorMessage(e), severity: 'error' }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, active])

  const dirty = useMemo(() => {
    if (!view) return false
    if (Object.keys(secretEdits).some((k) => secretEdits[k] !== '')) return true
    if (clearedSecrets.length > 0) return true
    return view.groups.some((g) =>
      g.fields.some((f) => !f.sensitive && draft[f.key] !== (f.value ?? null)),
    )
  }, [view, draft, secretEdits, clearedSecrets])

  // ---------- 操作 ----------

  /** 清除大模型"自动适配记录"(端点不支持参数的剔除记忆) */
  const doClearLearnings = async () => {
    setClearingLearnings(true)
    try {
      const result = await clearLlmParamLearnings()
      setToast({ msg: result.message || '已清除自动适配记录', severity: 'success' })
      await load()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setClearingLearnings(false)
    }
  }

  const quickUpdate = async (payload: SettingsUpdatePayload, successMsg: string) => {
    setSaving(true)
    try {
      const result = await updateSettings(payload)
      setToast({ msg: successMsg || result.message, severity: 'success' })
      await load()
      await refreshHealth()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const cycleMode = (next: 'normal' | 'mock' | 'dev') => {
    if (!view || saving) return
    if (next === 'normal') void quickUpdate({ mock_mode: false, dev_mode: false }, '已切换为正常运行模式')
    if (next === 'mock') void quickUpdate({ mock_mode: !view.mock_mode }, view.mock_mode ? '已关闭演示模式' : '已开启演示模式')
    if (next === 'dev') void quickUpdate({ dev_mode: !view.dev_mode }, view.dev_mode ? '已关闭开发模式' : '已开启开发模式')
  }

  const currentMode = view?.dev_mode ? 'dev' : view?.mock_mode ? 'mock' : 'normal'

  const save = async () => {
    if (!view) return
    const payload: SettingsUpdatePayload = {}
    view.groups.forEach((g) =>
      g.fields.forEach((f) => {
        const name = f.key.toLowerCase()
        if (f.sensitive) {
          if (clearedSecrets.includes(f.key)) payload[name] = ''
          else if (secretEdits[f.key]) payload[name] = secretEdits[f.key]
        } else if (draft[f.key] !== (f.value ?? null)) {
          payload[name] = draft[f.key]
        }
      }),
    )
    if (Object.keys(payload).length === 0) {
      setToast({ msg: '配置无变化', severity: 'info' })
      return
    }
    setSaving(true)
    try {
      const result = await updateSettings(payload)
      const restart = result.restart_required.length
        ? `(需重启生效:${result.restart_required.join('、')})`
        : ''
      setToast({ msg: `${result.message}${restart}`, severity: 'success' })
      await load()
      await refreshHealth()
      const savedProfile = payload.ui_profile
      if (savedProfile === 'balanced' || savedProfile === 'efficiency' || savedProfile === 'premium') {
        onUiProfileChange?.(savedProfile)
      }
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const doReset = async (field: SettingsField) => {
    try {
      const result = await resetSetting(field.key)
      setToast({ msg: result.message, severity: 'success' })
      await load()
      await refreshHealth()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const doRollback = async () => {
    setConfirmRollback(false)
    try {
      const result = await rollbackSettings()
      setToast({ msg: result.message, severity: 'success' })
      await load()
      await refreshHealth()
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  const doTest = async (target: 'pipeline_a' | 'ocr' | 'deepseek') => {
    setTesting(target)
    try {
      // 先测后存:优先使用草稿中未保存的候选值
      const overrides: Record<string, string | number | boolean | null> = {}
      const map: Record<string, string[]> = {
        pipeline_a: ['LOCAL_VLM_BASE_URL', 'LOCAL_VLM_API_KEY', 'LOCAL_VLM_MODEL'],
        ocr: ['OCR_PROVIDER', 'OCR_BASE_URL', 'OCR_API_KEY', 'AZURE_OCR_ENDPOINT', 'AZURE_OCR_KEY'],
        deepseek: ['DEEPSEEK_BASE_URL', 'DEEPSEEK_API_KEY'],
      }
      map[target].forEach((key) => {
        const field = allFields[key]
        if (field?.sensitive) {
          if (secretEdits[key]) overrides[field.key.toLowerCase()] = secretEdits[key]
        } else if (field && draft[key] !== undefined && draft[key] !== (field.value ?? null)) {
          overrides[field.key.toLowerCase()] = draft[key]
        }
      })
      const result = await testConnection(target, Object.keys(overrides).length ? overrides : undefined)
      setToast({ msg: `${result.ok ? '✓' : '✗'} ${result.detail}`, severity: result.ok ? 'success' : 'error' })
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setTesting('')
    }
  }

  // ---------- 字段控件 ----------
  const renderField = (field: SettingsField, showSourceGroup = false, sourceGroup = '') => {
    const value = draft[field.key]
    const original = field.sensitive ? '' : field.value ?? null
    const changed = !field.sensitive && value !== original
    return (
      <Box key={field.key} sx={{ py: 1.2, borderBottom: '1px dashed', borderColor: 'divider' }}>
        <Stack direction="row" alignItems="center" spacing={0.5} flexWrap="wrap" useFlexGap>
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {field.label}
          </Typography>
          {field.exploratory && <Chip size="small" color="info" variant="outlined" label="探索" />}
          {field.audience === 'developer' && <Chip size="small" color="secondary" variant="outlined" label="开发者" />}
          {field.restart_required && <Chip size="small" color="warning" variant="outlined" label="需重启" />}
          {showSourceGroup && <Chip size="small" variant="outlined" label={sourceGroup} />}
          <Box sx={{ flex: 1 }} />
          {field.exploratory && (
            <Tooltip title="恢复系统默认值(仍可整体回退)">
              <IconButton size="small" onClick={() => void doReset(field)}>
                <RestartAltOutlinedIcon fontSize="inherit" />
              </IconButton>
            </Tooltip>
          )}
        </Stack>
        {field.description && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
            {field.description}
          </Typography>
        )}
        <Box sx={{ mt: 0.75, maxWidth: 520 }}>
          {field.control === 'switch' && (
            <Stack direction="row" alignItems="center" spacing={1}>
              <Switch
                size="small"
                checked={Boolean(value)}
                onChange={(e) => setDraft((d) => ({ ...d, [field.key]: e.target.checked }))}
              />
              <Typography variant="caption" color="text.secondary">
                {value ? '开启' : '关闭'}
              </Typography>
            </Stack>
          )}
          {field.control === 'select' && (
            <>
            <TextField
              select
              fullWidth
              size="small"
              value={String(value ?? '')}
              onChange={(e) => setDraft((d) => ({ ...d, [field.key]: e.target.value }))}
            >
              {field.choices.map((choice) => (
                <MenuItem key={choice} value={choice}>
                  {choice}
                </MenuItem>
              ))}
            </TextField>
            {field.key === 'HANDWRITING_OCR_LEVEL' && profile && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                本机检测:{profile.cores} 核 /
                {profile.ram_gb != null ? ` ${profile.ram_gb}GB` : ' 内存未知'} → 推荐{
                  { auto: '自动', light: '轻量', medium: '中等', precise: '最精确' }[profile.recommended_level] ?? profile.recommended_level
                };
                当前生效:{
                  { auto: '自动', light: '轻量', medium: '中等', precise: '最精确' }[profile.effective_level] ?? profile.effective_level
                }(auto 按推荐执行,手动选择优先)
              </Typography>
            )}
            </>
          )}
          {field.control === 'number' && (
            <TextField
              fullWidth
              size="small"
              type="number"
              value={value ?? ''}
              inputProps={{ min: field.min_value ?? undefined, max: field.max_value ?? undefined }}
              helperText={
                field.min_value != null || field.max_value != null
                  ? `范围:${field.min_value ?? '—'} ~ ${field.max_value ?? '—'}`
                  : undefined
              }
              onChange={(e) =>
                setDraft((d) => ({ ...d, [field.key]: e.target.value === '' ? null : Number(e.target.value) }))
              }
            />
          )}
          {(field.control === 'text' || field.control === 'url') && (
            <TextField
              fullWidth
              size="small"
              placeholder={field.control === 'url' ? 'https://…' : undefined}
              value={value ?? ''}
              onChange={(e) => setDraft((d) => ({ ...d, [field.key]: e.target.value }))}
            />
          )}
          {field.control === 'secret' && (
            <Stack direction="row" spacing={1} alignItems="center">
              {secretEdits[field.key] !== undefined && !clearedSecrets.includes(field.key) ? (
                <>
                  <TextField
                    fullWidth
                    size="small"
                    type="password"
                    autoFocus
                    value={secretEdits[field.key]}
                    onChange={(e) => setSecretEdits((s) => ({ ...s, [field.key]: e.target.value }))}
                  />
                  <IconButton
                    size="small"
                    onClick={() =>
                      setSecretEdits((s) => {
                        const next = { ...s }
                        delete next[field.key]
                        return next
                      })
                    }
                  >
                    <CloseOutlinedIcon fontSize="inherit" />
                  </IconButton>
                </>
              ) : (
                <>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'text.secondary', flex: 1 }}>
                    {clearedSecrets.includes(field.key) ? '(保存后清除)' : field.has_value ? field.masked : '(未配置)'}
                  </Typography>
                  <Button
                    size="small"
                    startIcon={<EditOutlinedIcon />}
                    onClick={() => {
                      setClearedSecrets((keys) => keys.filter((k) => k !== field.key))
                      setSecretEdits((s) => ({ ...s, [field.key]: '' }))
                    }}
                  >
                    编辑
                  </Button>
                  {field.has_value && !clearedSecrets.includes(field.key) && (
                    <Button
                      size="small"
                      color="warning"
                      onClick={() => {
                        setSecretEdits((s) => {
                          const next = { ...s }
                          delete next[field.key]
                          return next
                        })
                        setClearedSecrets((keys) => [...keys, field.key])
                      }}
                    >
                      清除
                    </Button>
                  )}
                </>
              )}
            </Stack>
          )}
          {changed && (
            <Typography variant="caption" color="primary.main">
              已修改(保存后生效)
            </Typography>
          )}
        </Box>
      </Box>
    )
  }

  /** 左栏区块列表(过滤空分组) */
  const sections = useMemo(() => {
    if (!view) return [] as { id: string; title: string; description: string; count: number }[]
    const items = view.groups
      .map((g) => ({
        id: g.id,
        title: SECTION_META[g.id]?.title ?? g.title,
        description: g.description,
        count: g.fields.filter((f) => f.audience !== 'developer' || view.dev_mode).length,
      }))
      .filter((s) => s.count > 0)
    const experimentalCount = view.groups
      .flatMap((g) => g.fields)
      .filter((f) => f.exploratory).length
    items.push({ id: 'prompts', title: '提示词微调', description: '三位置追加指令与最终提示词预览', count: 0 })
    items.push({ id: 'encryption', title: '数据加密', description: '透明加解密 / 恢复密钥 / Plan B', count: 0 })
    items.push({
      id: 'llm-learned',
      title: '自动适配记录',
      description: '端点不支持参数的剔除记忆(可清除)',
      count: Object.keys(view.llm_learned_params ?? {}).length,
    })
    items.push({
      id: 'experimental',
      title: '探索性实验项',
      description: '跨分组的探索开关聚合(支持恢复默认与一键回退)',
      count: experimentalCount,
    })
    return items
  }, [view])

  const groupById = (id: string) => view?.groups.find((g) => g.id === id)

  /** 右侧内容:按当前左栏区块渲染 */
  const renderSection = () => {
    if (!view) return null
    // 特殊区块
    if (active === 'encryption') {
      return (
        <Stack spacing={1.5}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            数据加密
          </Typography>
          <EncryptionSection onChanged={() => void refreshHealth()} />
        </Stack>
      )
    }
    if (active === 'prompts') {
      return (
        <Stack spacing={1.5}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            提示词微调
          </Typography>
          <Typography variant="caption" color="text.secondary">
            在基础模板之后追加「教师自定义补充要求」(不改 JSON 契约);与示范学习风格可叠加,保存后对下一次批改生效。
          </Typography>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={promptKind}
            onChange={(_, v) => {
              if (!v) return
              setPromptKind(v)
              setPromptText(prompts?.appendices[v] ?? '')
            }}
          >
            {Object.entries(PROMPT_KIND_LABELS).map(([kind, label]) => (
              <ToggleButton key={kind} value={kind} sx={{ px: 2 }}>
                {label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <TextField
            multiline
            minRows={6}
            maxRows={14}
            fullWidth
            size="small"
            value={promptText}
            onChange={(e) => setPromptText(e.target.value)}
            helperText={`${promptText.length}/${prompts?.max_chars ?? 2000}(保存后对下一次批改生效;与基础契约冲突时以 JSON 结构为准)`}
          />
          <Stack direction="row" spacing={1.5}>
            <Button
              variant="contained"
              size="small"
              onClick={async () => {
                try {
                  const data = await updatePromptAppendices({ [promptKind]: promptText } as never)
                  setPrompts(data)
                  setToast({ msg: '提示词附录已保存(下次批改生效)', severity: 'success' })
                } catch (e) {
                  setToast({ msg: extractErrorMessage(e), severity: 'error' })
                }
              }}
            >
              保存附录
            </Button>
            <Button size="small" color="inherit" onClick={() => setPromptText('')}>
              清空输入
            </Button>
          </Stack>
          {prompts?.previews[promptKind] && (
            <Box>
              <Typography variant="caption" color="text.secondary">
                最终系统提示词预览(含示范学习风格与追加指令)· 截取尾部:
              </Typography>
              <Box
                component="pre"
                sx={{
                  m: 0.5,
                  p: 1.5,
                  maxHeight: 240,
                  overflow: 'auto',
                  fontSize: 11.5,
                  bgcolor: 'action.hover',
                  borderRadius: 2,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {prompts.previews[promptKind].slice(-1200)}
              </Box>
            </Box>
          )}
        </Stack>
      )
    }
    if (active === 'llm-learned') {
      const entries = Object.entries(view.llm_learned_params ?? {})
      return (
        <Stack spacing={1.5}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            自动适配记录
          </Typography>
          <Typography variant="caption" color="text.secondary">
            端点返回「参数不支持」类错误时,系统会自动剔除该参数、重试并记住这份记录;
            同一端点+模型的后续请求将直接不再发送这些参数。修正端点(如模型升级已支持)后可在此清除。
          </Typography>
          {entries.length === 0 ? (
            <Alert severity="success" sx={{ py: 0 }}>
              暂无记录:目前所有端点均未出现「参数不支持」类错误。
            </Alert>
          ) : (
            <Box
              sx={{
                border: '1px solid',
                borderColor: 'divider',
                borderRadius: 1,
                maxHeight: '46vh',
                overflow: 'auto',
              }}
            >
              {entries.map(([key, params]) => (
                <Box key={key} sx={{ px: 1.5, py: 1, borderBottom: '1px dashed', borderColor: 'divider' }}>
                  <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
                    {key.replace('|', ' · ')}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    已剔除:{params.join('、')}
                  </Typography>
                </Box>
              ))}
            </Box>
          )}
          <Box>
            <Button
              size="small"
              variant="outlined"
              color="warning"
              startIcon={<RestartAltOutlinedIcon />}
              disabled={entries.length === 0 || clearingLearnings}
              onClick={() => void doClearLearnings()}
            >
              {clearingLearnings ? '清除中…' : '清除记录'}
            </Button>
          </Box>
        </Stack>
      )
    }
    if (active === 'experimental') {
      const rows = view.groups.flatMap((g) =>
        g.fields.filter((f) => f.exploratory).map((f) => ({ field: f, groupTitle: g.title })),
      )
      if (rows.length === 0) {
        return <Alert severity="info">当前没有可用的探索性实验项。</Alert>
      }
      return (
        <Stack spacing={1}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            探索性实验项({rows.length})
          </Typography>
          <Typography variant="caption" color="text.secondary">
            这些开关为"可回退的实验能力":默认值与历史行为一致,可按需开启;支持单项恢复默认与底部「一键回退」。
          </Typography>
          {rows.map(({ field, groupTitle }) => renderField(field, true, groupTitle))}
        </Stack>
      )
    }

    // 常规分组
    const group = groupById(active)
    if (!group) return <Alert severity="info">请选择左侧的设置范围。</Alert>
    const visible = group.fields.filter((f) => f.audience !== 'developer' || view.dev_mode)
    const target = GROUP_TEST_TARGET[group.id]
    return (
      <Stack spacing={1}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              {SECTION_META[group.id]?.title ?? group.title}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {group.description}
            </Typography>
          </Box>
          {target && (
            <Button
              size="small"
              variant="outlined"
              startIcon={testing === target ? <CircularProgress size={13} /> : <ScienceOutlinedIcon />}
              onClick={() => void doTest(target)}
              disabled={Boolean(testing)}
            >
              测试连接
            </Button>
          )}
        </Stack>
        {group.id === 'basic' && (
          <Alert severity="info" variant="outlined" sx={{ borderRadius: 2.5 }}>
            运行模式:{currentMode === 'normal' ? '正常' : currentMode === 'mock' ? '演示' : '开发'}
            —— 可在窗口右上角随时切换;演示与开发互不冲突。
          </Alert>
        )}
        {group.id === 'basic' && <ClearDemoDataBlock />}
        {group.id === 'basic' && <DataMaintenanceBlock />}
        {visible.map((f) => renderField(f))}
        {group.id === 'security' && (
          <Box sx={{ pt: 1.5 }}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              设置访问令牌(本机保存)
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              未配置令牌时设置接口仅本机可访问;远程管理时请在服务端配置 SETTINGS_ADMIN_TOKEN 并在此填写相同令牌(自动随请求携带)。
            </Typography>
            <Stack direction="row" spacing={1} sx={{ maxWidth: 520 }}>
              <TextField
                fullWidth
                size="small"
                type="password"
                placeholder="与服务端一致的令牌"
                value={adminToken}
                onChange={(e) => setAdminTokenLocal(e.target.value)}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  setAdminToken(adminToken.trim())
                  void load()
                  setToast({ msg: '访问令牌已保存到本机浏览器', severity: 'success' })
                }}
              >
                保存
              </Button>
            </Stack>
          </Box>
        )}
        {group.id === 'upload' && <CompressImagesBlock />}
      </Stack>
    )
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xl"
      fullWidth
      slotProps={{
        paper: { sx: { height: 'min(92vh, 900px)', display: 'flex', flexDirection: 'column', borderRadius: 3 } },
      }}
    >
      {/* ================= 头部 ================= */}
      <Box sx={{ px: 2.5, py: 1.5, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <TuneOutlinedIcon color="primary" />
        <Typography variant="h6" sx={{ flex: 1, minWidth: 120 }}>
          设置中心
        </Typography>
        {health && (
          <Typography variant="caption" color="text.secondary">
            后端:{health.status}
            {health.mock_mode ? ' · 演示模式' : ''}
          </Typography>
        )}
        <ToggleButtonGroup
          exclusive
          size="small"
          value={currentMode}
          onChange={(_, v) => v && cycleMode(v)}
        >
          <ToggleButton value="normal">正常</ToggleButton>
          <ToggleButton value="mock">演示</ToggleButton>
          <ToggleButton value="dev">开发</ToggleButton>
        </ToggleButtonGroup>
        <Button
          size="small"
          variant="contained"
          startIcon={<SaveOutlinedIcon />}
          disabled={!dirty || saving}
          onClick={() => void save()}
        >
          保存修改
        </Button>
        <IconButton size="small" onClick={onClose} aria-label="关闭设置">
          <CloseOutlinedIcon />
        </IconButton>
      </Box>
      <Divider />

      {/* ================= 主体:左导航 + 右小项 ================= */}
      <DialogContent sx={{ p: 0, display: 'flex', minHeight: 0, flex: 1 }}>
        {loadError && (
          <Alert
            severity="error"
            sx={{ position: 'absolute', m: 2, zIndex: 5 }}
            action={
              <Button size="small" onClick={() => void load()}>
                重试
              </Button>
            }
          >
            {loadError}(设置接口默认仅本机可访问;如需远程管理请在「安全与访问」填写访问令牌)
          </Alert>
        )}

        {/* 左:大范围导航 */}
        <Box sx={{ width: 250, flexShrink: 0, borderRight: 1, borderColor: 'divider', overflowY: 'auto', py: 1 }}>
          <List dense disablePadding sx={{ px: 1 }}>
            {sections.map((section) => (
              <ListItemButton
                key={section.id}
                selected={active === section.id}
                onClick={() => setActive(section.id)}
                sx={{ borderRadius: 2.5, mb: 0.5 }}
              >
                <ListItemIcon sx={{ minWidth: 36 }}>{SECTION_META[section.id]?.icon}</ListItemIcon>
                <ListItemText
                  primary={section.title}
                  primaryTypographyProps={{ fontSize: 14, fontWeight: 500 }}
                  secondary={section.description}
                  secondaryTypographyProps={{ fontSize: 11, noWrap: true }}
                />
                {section.count > 0 && <Chip size="small" label={section.count} sx={{ height: 20 }} />}
              </ListItemButton>
            ))}
          </List>
        </Box>

        {/* 右:小项区 */}
        <Box sx={{ flex: 1, overflowY: 'auto', px: 3, py: 2 }}>
          {!view && !loadError ? (
            <Box sx={{ display: 'grid', placeItems: 'center', py: 10 }}>
              <CircularProgress size={26} />
            </Box>
          ) : (
            renderSection()
          )}
          <Box sx={{ height: 24 }} />
        </Box>
      </DialogContent>

      {/* ================= 底部操作条 ================= */}
      <Divider />
      <Box sx={{ px: 2.5, py: 1.25, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        {view?.snapshot.available ? (
          <Alert
            severity="info"
            sx={{ flex: 1, py: 0 }}
            action={
              <Button size="small" color="inherit" onClick={() => setConfirmRollback(true)}>
                一键回退
              </Button>
            }
          >
            检测到探索性设置改动({view.snapshot.keys.length} 项),可批量还原到修改前的状态。
          </Alert>
        ) : (
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
            探索性实验项的改动会记录快照,支持单项恢复默认与整体一键回退。
          </Typography>
        )}
        <Button
          size="small"
          variant="contained"
          startIcon={<SaveOutlinedIcon />}
          disabled={!dirty || saving}
          onClick={() => void save()}
        >
          {saving ? '保存中…' : '保存修改'}
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={<RestartAltOutlinedIcon />}
          disabled={!view?.snapshot.available}
          onClick={() => setConfirmRollback(true)}
        >
          回退
        </Button>
      </Box>

      {/* 回退确认 */}
      <Dialog open={confirmRollback} onClose={() => setConfirmRollback(false)} maxWidth="xs" fullWidth>
        <DialogTitle>一键回退探索性设置</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            将把 {view?.snapshot.keys.join('、') || '全部探索项'} 还原到本批修改前的状态,并清空回退快照。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmRollback(false)}>取消</Button>
          <Button variant="contained" color="warning" onClick={() => void doRollback()}>
            回退
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={toast?.severity === 'error' ? 6000 : 3000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={toast?.severity ?? 'info'} onClose={() => setToast(null)} sx={{ maxWidth: 520 }}>
          {toast?.msg}
        </Alert>
      </Snackbar>
    </Dialog>
  )
}

/** 字节数格式化(设置面板展示用) */
function formatBytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(0)} KB`
  return `${value} B`
}

/** 存档图片压缩(「上传与图片处理」面板内嵌:按钮 + 进度 + 结果) */
function CompressImagesBlock() {
  const [progress, setProgress] = useState<ImageCompressProgress | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => {
      void fetchImageCompressStatus()
        .then((data) => {
          setProgress(data)
          if (!data.running) setBusy(false)
        })
        .catch(() => undefined)
    }, 1500)
    return () => window.clearInterval(timer)
  }, [busy])

  const start = () => {
    setError('')
    void startImageCompress()
      .then((data) => {
        setProgress(data.progress)
        setBusy(true)
      })
      .catch((e) => setError(extractErrorMessage(e)))
  }

  const done = Boolean(progress && !progress.running && progress.finished_at)
  const saved = progress ? Math.max(progress.before_bytes - progress.after_bytes, 0) : 0
  return (
    <Box sx={{ pt: 1.5 }}>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        压缩存量图片(已存档的历史照片)
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
        遍历上传目录就地压缩:仅当更小才替换、带压缩标记可安全重跑;原图路径与审阅/打印/导出流程不变。
      </Typography>
      <Stack direction="row" spacing={1.25} alignItems="center">
        <Button
          size="small"
          variant="outlined"
          startIcon={busy ? <CircularProgress size={13} /> : <StorageOutlinedIcon />}
          onClick={start}
          disabled={busy}
        >
          {busy ? '压缩中…' : '压缩存量图片'}
        </Button>
        {busy && progress && (
          <Typography variant="caption" color="text.secondary">
            进度 {progress.done}/{progress.total}(已压缩 {progress.changed},跳过 {progress.skipped})
          </Typography>
        )}
      </Stack>
      {busy && progress && progress.total > 0 && (
        <LinearProgress
          variant="determinate"
          value={Math.round((progress.done / progress.total) * 100)}
          sx={{ mt: 0.75, borderRadius: 1, maxWidth: 520 }}
        />
      )}
      {error && (
        <Alert severity="error" sx={{ mt: 1, borderRadius: 2.5 }}>
          {error}
        </Alert>
      )}
      {done && (
        <Alert severity={progress?.error ? 'warning' : 'success'} sx={{ mt: 1, borderRadius: 2.5 }}>
          处理 {progress?.total} 个,压缩 {progress?.changed} 个,跳过 {progress?.skipped} 个;
          {formatBytes(progress?.before_bytes ?? 0)} → {formatBytes(progress?.after_bytes ?? 0)}(节省 {formatBytes(saved)})
          {progress?.error ? `;错误:{progress.error}` : ''}
        </Alert>
      )}
    </Box>
  )
}

/** 清除演示数据(基础运行面板内嵌;仅删演示模式产生的任务/学生/图片) */
function ClearDemoDataBlock() {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState('')
  const [error, setError] = useState('')

  const run = () => {
    setBusy(true)
    setError('')
    setResult('')
    void clearDemoData()
      .then((data) => {
        setResult(`已删除演示任务 ${data.tasks} 个、学生 ${data.students} 条、图片 ${data.files} 张。`)
      })
      .catch((e) => setError(extractErrorMessage(e)))
      .finally(() => {
        setBusy(false)
        setConfirmOpen(false)
      })
  }

  return (
    <Box sx={{ pt: 1.5 }}>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        清除演示数据
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
        仅删除「演示模式(Mock)」产生的任务、错因、图片与孤儿学生;真实数据不受影响。可在正式使用前执行一次。
      </Typography>
      <Button
        size="small"
        variant="outlined"
        color="error"
        startIcon={busy ? <CircularProgress size={13} /> : <RestartAltOutlinedIcon />}
        onClick={() => setConfirmOpen(true)}
        disabled={busy}
      >
        {busy ? '清理中…' : '清除演示数据'}
      </Button>
      {result && (
        <Alert severity="success" sx={{ mt: 1, borderRadius: 2.5 }}>
          {result}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mt: 1, borderRadius: 2.5 }}>
          {error}
        </Alert>
      )}
      <Dialog open={confirmOpen} onClose={() => setConfirmOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>清除演示数据</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            将删除本机所有「演示模式(Mock)」产生的数据,操作不可恢复;真实批改数据不受影响。确定继续吗?
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmOpen(false)}>取消</Button>
          <Button color="error" variant="contained" onClick={run}>
            确认清除
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

/** 清除所有数据的结果 -> 人类可读文案 */
function formatClearAllResult(data: MaintenanceClearResult): string {
  const parts = [
    `批改任务 ${data.tasks} 个`,
    `错因 ${data.errors} 条`,
    `学生 ${data.students} 名`,
    `班级 ${data.classes} 个`,
    `考试 ${data.exams} 场`,
    `台账记录 ${data.ledger_records} 条`,
    `练习卷 ${data.practice_sheets} 份`,
    `风格画像 ${data.style_profiles} 份`,
    `图片文件 ${data.files} 张`,
  ]
  const freed = data.bytes ? `,释放 ${formatBytes(data.bytes)}` : ''
  return `已清除全部数据:${parts.join('、')}${freed}。系统配置与密钥(设置中心配置/提示词附录/加密密钥)已保留。`
}

/** 恢复示例数据的结果 -> 人类可读文案 */
function formatSeedResult(data: MaintenanceSeedResult): string {
  return (
    `已写入示例数据:班级 ${data.classes} 个、学生 ${data.students} 名(含花名册 ${data.roster} 人)、` +
    `批改任务 ${data.tasks} 个(错因 ${data.errors} 条)、考试 ${data.exams} 场(考卷 ${data.exam_papers} 份)、` +
    `台账记录 ${data.ledger_records} 条、练习卷 ${data.practice_sheets} 份、风格画像 ${data.style_profiles} 份、图片 ${data.files} 张。`
  )
}

/** 数据维护(基础运行面板内嵌):一键清除所有数据 / 一键恢复所有示例数据 */
function DataMaintenanceBlock() {
  const { refresh: refreshHealth } = useRefreshHealth()
  const [dialog, setDialog] = useState<'' | 'clear' | 'seed'>('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState('')
  const [error, setError] = useState('')

  const closeDialog = () => {
    if (!busy) setDialog('')
  }

  const run = (kind: 'clear' | 'seed') => {
    setBusy(true)
    setError('')
    setResult('')
    const job = kind === 'clear' ? clearAllData() : seedDemoData()
    void job
      .then((data) => {
        setResult(
          kind === 'clear'
            ? formatClearAllResult(data as MaintenanceClearResult)
            : formatSeedResult(data as MaintenanceSeedResult),
        )
        void refreshHealth()
      })
      .catch((e) => setError(extractErrorMessage(e)))
      .finally(() => {
        setBusy(false)
        setDialog('')
      })
  }

  return (
    <Box sx={{ pt: 1.5, borderTop: '1px dashed', borderColor: 'divider' }}>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        数据维护(全部数据 / 示例数据)
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
        与上方「清除演示数据」的区别:本区两项操作作用于「全部数据」——「一键清除所有数据」清空全部业务数据与上传文件;
        「一键恢复所有示例数据」先清空再写入一套完整示例数据(回到可直接演示的「出厂演示」状态)。两项均不可恢复,
        系统配置与密钥不受影响。
      </Typography>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Button
          size="small"
          variant="outlined"
          color="error"
          startIcon={busy && dialog === 'clear' ? <CircularProgress size={13} /> : <DeleteForeverOutlinedIcon />}
          onClick={() => setDialog('clear')}
          disabled={busy}
        >
          一键清除所有数据
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={busy && dialog === 'seed' ? <CircularProgress size={13} /> : <RestoreOutlinedIcon />}
          onClick={() => setDialog('seed')}
          disabled={busy}
        >
          一键恢复所有示例数据
        </Button>
      </Stack>
      {result && (
        <Alert
          severity="success"
          sx={{ mt: 1, borderRadius: 2.5 }}
          action={
            <Button size="small" color="inherit" onClick={() => window.location.reload()}>
              刷新页面
            </Button>
          }
        >
          {result}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mt: 1, borderRadius: 2.5 }}>
          {error}
        </Alert>
      )}

      {/* 一键清除所有数据:二次确认(明确提示不可恢复) */}
      <Dialog open={dialog === 'clear'} onClose={closeDialog} maxWidth="sm" fullWidth>
        <DialogTitle>一键清除所有数据</DialogTitle>
        <DialogContent>
          <Alert severity="error" sx={{ mb: 1.5, borderRadius: 2.5 }}>
            该操作将删除全部数据且无法恢复。
          </Alert>
          <Typography variant="body2">
            将清空:批改任务与报告、错因记录、学生与班级、考试与报告、作业台账、练习卷、示范学习画像、
            统计与分析结果,以及上传的图片与全部关联文件。
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            系统配置与密钥(设置中心配置、提示词附录、数据加密密钥)不受影响;如仍需保留任何数据,请先取消并导出。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog} disabled={busy}>
            取消
          </Button>
          <Button color="error" variant="contained" onClick={() => run('clear')} disabled={busy}>
            {busy && dialog === 'clear' ? '清除中…' : '确认删除全部数据'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* 一键恢复所有示例数据:确认(提示会覆盖现有数据) */}
      <Dialog open={dialog === 'seed'} onClose={closeDialog} maxWidth="sm" fullWidth>
        <DialogTitle>一键恢复所有示例数据</DialogTitle>
        <DialogContent>
          <Alert severity="warning" sx={{ mb: 1.5, borderRadius: 2.5 }}>
            将先清空现有全部数据(含真实数据),再写入示例数据;操作不可恢复。
          </Alert>
          <Typography variant="body2">
            示例数据覆盖:示例班级与花名册、学生档案、批改任务与报告/错因(含 1 个待人工复核任务)、
            考试与报告、台账登记记录、练习卷与示范学习画像;写入后可在队列/审阅/分析/台账/考试等页面直接演示。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog} disabled={busy}>
            取消
          </Button>
          <Button variant="contained" onClick={() => run('seed')} disabled={busy}>
            {busy && dialog === 'seed' ? '写入中…' : '确认恢复示例数据'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
