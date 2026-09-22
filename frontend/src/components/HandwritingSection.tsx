/**
 * 手写样本模型区块(花名册对话框「手写模型」页签)
 *
 * 采集流程:生成抄写素材(题目句+正文句)→ 打印抄写卡 → 学生用标准卷抄写
 * → 批量上传样本 → 系统自动处理(找平/裁姓名区/识别/对账/特征提取)
 * → 形成本地手写模型(供"上传后姓名预识别"按档位调用)。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import AutoAwesomeOutlinedIcon from '@mui/icons-material/AutoAwesomeOutlined'
import DeleteOutlineOutlinedIcon from '@mui/icons-material/DeleteOutlineOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined'
import {
  bindHandwritingSample,
  deleteHandwritingSample,
  extractErrorMessage,
  fetchClassRoster,
  fetchHandwritingModel,
  fetchHandwritingSamples,
  fetchHandwritingSentence,
  fetchHandwritingTrainStatus,
  handwritingCropUrl,
  uploadHandwritingSamples,
} from '../api/client'
import type {
  HandwritingModelOverview,
  HandwritingSampleItem,
  HandwritingTrainStatus,
} from '../api/client'
import type { RosterMember } from '../types'

interface Props {
  classId: number | null
}

/** 秒 → "X 分 Y 秒" / "X 秒"(真实 ETA 展示) */
function formatEta(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds >= 90) {
    const minutes = Math.floor(seconds / 60)
    return `${minutes} 分 ${seconds % 60} 秒`
  }
  return `${Math.max(1, seconds)} 秒`
}

export default function HandwritingSection({ classId }: Props) {
  const [sentence, setSentence] = useState<{ topic: string; sentence: string } | null>(null)
  const [samples, setSamples] = useState<HandwritingSampleItem[]>([])
  const [status, setStatus] = useState<HandwritingTrainStatus | null>(null)
  const [model, setModel] = useState<HandwritingModelOverview | null>(null)
  const [roster, setRoster] = useState<RosterMember[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const reload = useCallback(async () => {
    if (classId == null) return
    try {
      const [sampleList, trainStatus, overview, members] = await Promise.all([
        fetchHandwritingSamples(classId),
        fetchHandwritingTrainStatus(classId),
        fetchHandwritingModel(classId),
        fetchClassRoster(classId),
      ])
      setSamples(sampleList)
      setStatus(trainStatus)
      setModel(overview)
      setRoster(members)
    } catch (e) {
      setError(extractErrorMessage(e))
    }
  }, [classId])

  useEffect(() => {
    void reload()
  }, [reload])

  // 训练进行中:2 秒轮询进度(真实进度 + ETA)
  useEffect(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
    if (status?.running) {
      pollRef.current = window.setInterval(() => {
        void reload()
      }, 2000)
    }
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current)
    }
  }, [status?.running, reload])

  const makeSentence = async () => {
    if (classId == null) return
    setBusy(true)
    try {
      setSentence(await fetchHandwritingSentence(classId))
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const printCard = () => {
    if (!sentence) return
    const params = new URLSearchParams({ topic: sentence.topic, sentence: sentence.sentence })
    window.open(`/print/handwriting-card?${params.toString()}`, '_blank')
  }

  const upload = async (files: FileList | null) => {
    if (classId == null || !files || files.length === 0) return
    setBusy(true)
    setError(null)
    try {
      await uploadHandwritingSamples(classId, Array.from(files))
      await reload()
    } catch (e) {
      setError(extractErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const bindSample = async (sampleId: number, memberKey: string) => {
    const member = roster.find((item) => String(item.id) === memberKey)
    if (!member) return
    try {
      await bindHandwritingSample(sampleId, member.name, member.student_id ?? null)
      await reload()
    } catch (e) {
      setError(extractErrorMessage(e))
    }
  }

  const removeSample = async (sampleId: number) => {
    try {
      await deleteHandwritingSample(sampleId)
      await reload()
    } catch (e) {
      setError(extractErrorMessage(e))
    }
  }

  const modelChip = useMemo(() => {
    if (!model) return null
    const levelLabel: Record<string, string> = {
      auto: '自动',
      light: '轻量',
      medium: '中等',
      precise: '最精确',
    }
    return (
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip
          size="small"
          color={model.ready ? 'success' : 'default'}
          variant="outlined"
          label={model.ready ? `模型就绪:${model.students_count} 人 / ${model.samples_count} 份样本` : '尚未训练'}
        />
        <Chip
          size="small"
          variant="outlined"
          label={`档位:${levelLabel[model.level] ?? model.level}(本机 ${model.cores} 核 / ${
            model.ram_gb ?? '?'
          }GB,推荐 ${levelLabel[model.recommended_level] ?? model.recommended_level})`}
        />
        {model.pending_bind > 0 && (
          <Chip size="small" color="warning" variant="outlined" label={`待指定学生 ${model.pending_bind} 份`} />
        )}
      </Stack>
    )
  }, [model])

  if (classId == null) {
    return (
      <Alert severity="info">请先在工作台选择归属班级,再使用手写模型采集。</Alert>
    )
  }

  return (
    <Stack spacing={2}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* 1. 抄写素材 */}
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          第 1 步 · 生成抄写素材
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          让学生把题目与正文句抄写到「标准答题卷」上,并手写班级/日期/姓名/学号/题目;
          每人也可多抄一份(样本越多越准)。
        </Typography>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Button
            size="small"
            variant="outlined"
            startIcon={<AutoAwesomeOutlinedIcon />}
            disabled={busy}
            onClick={() => void makeSentence()}
          >
            生成素材
          </Button>
          {sentence && (
            <Button size="small" startIcon={<PrintOutlinedIcon />} onClick={printCard}>
              打印抄写卡
            </Button>
          )}
        </Stack>
        {sentence && (
          <Alert severity="success" sx={{ mt: 1 }}>
            题目:{sentence.topic}
            <br />
            正文:{sentence.sentence}
          </Alert>
        )}
      </Box>

      <Divider />

      {/* 2. 上传样本 */}
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          第 2 步 · 上传学生抄写样本(可多份)
        </Typography>
        <Button
          size="small"
          variant="outlined"
          component="label"
          startIcon={<UploadFileOutlinedIcon />}
          disabled={busy}
          sx={{ mt: 0.5 }}
        >
          选择样本图片
          <input
            hidden
            type="file"
            accept=".jpg,.jpeg,.png,.webp,.bmp,image/*"
            multiple
            onChange={(e) => {
              void upload(e.target.files)
              e.target.value = ''
            }}
          />
        </Button>
      </Box>

      {/* 3. 训练进度(真实 percent + ETA) */}
      {status && (status.running || status.total > 0) && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            第 3 步 · 训练进度
          </Typography>
          {status.running ? (
            <Box>
              <Typography variant="body2" sx={{ mb: 0.5 }}>
                正在处理样本({status.done}/{status.total}) · 当前阶段:{status.stage || '准备中'}
                {status.eta_seconds != null && ` · 预计剩余 ${formatEta(status.eta_seconds)}`}
              </Typography>
              <LinearProgress
                variant={status.total ? 'determinate' : 'indeterminate'}
                value={status.percent}
                sx={{ height: 8, borderRadius: 4 }}
              />
            </Box>
          ) : (
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2">
                本轮完成 {status.done}/{status.total}
                {status.errors > 0 && `(失败 ${status.errors} 份,可删除后重传)`}
              </Typography>
              {modelChip}
            </Stack>
          )}
        </Box>
      )}

      {!status?.running && modelChip && (
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
            手写模型将按设置档位在「上传后姓名预识别」中自动参与(设置中心 → 上传 → 手写识别增强档位)。
          </Typography>
        </Box>
      )}

      <Divider />

      {/* 4. 样本列表与绑定 */}
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          样本清单({samples.length} 份)
        </Typography>
        {samples.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            暂无样本
          </Typography>
        ) : (
          <Stack spacing={1} sx={{ maxHeight: 320, overflowY: 'auto' }}>
            {samples.map((sample) => (
              <Stack
                key={sample.id}
                direction="row"
                spacing={1.5}
                alignItems="center"
                sx={{ border: 1, borderColor: 'divider', borderRadius: 2, p: 1 }}
              >
                {sample.has_crop ? (
                  <Box
                    component="img"
                    src={handwritingCropUrl(sample.id)}
                    alt={`样本 ${sample.id}`}
                    loading="lazy"
                    sx={{ width: 120, borderRadius: 1, border: 1, borderColor: 'divider', bgcolor: '#fff' }}
                  />
                ) : (
                  <Box sx={{ width: 120, height: 40, display: 'grid', placeItems: 'center' }}>
                    <CircularProgress size={16} />
                  </Box>
                )}
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap>
                    {sample.student_name ?? '未识别'}
                    {sample.student_id ? ` · ${sample.student_id}` : ''}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {sample.status === 'ok'
                      ? `已绑定${sample.has_features ? ' · 特征已生成' : ''}`
                      : '待指定学生(请在下拉中选择)'}
                  </Typography>
                </Box>
                {sample.status !== 'ok' && (
                  <FormControl size="small" sx={{ minWidth: 150 }}>
                    <InputLabel>指定学生</InputLabel>
                    <Select
                      label="指定学生"
                      value=""
                      onChange={(e) => void bindSample(sample.id, e.target.value)}
                    >
                      {roster.map((member) => (
                        <MenuItem key={member.id} value={String(member.id)}>
                          {member.name}
                          {member.student_id ? `(${member.student_id})` : ''}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                )}
                <Tooltip title="删除样本(可重传)">
                  <IconButton size="small" color="warning" onClick={() => void removeSample(sample.id)}>
                    <DeleteOutlineOutlinedIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            ))}
          </Stack>
        )}
      </Box>
    </Stack>
  )
}
