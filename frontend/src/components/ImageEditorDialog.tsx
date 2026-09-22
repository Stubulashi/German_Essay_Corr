/**
 * 图片编辑器(批改工作台,需求二)
 *
 * 能力:
 * - 灰度化 / 黑白(高对比阈值)处理;
 * - 裁剪(鼠标拖拽框选后应用)、旋转(左右 90°);
 * - 「姓名栏选区」引导:一键在顶部生成条带选区,便于裁剪保留(见下方说明);
 * - 上一张 / 下一张翻页(与文件列表顺序一致,显示"第 x / y 张");
 * - 撤销上一步 / 重置原图(参数化操作栈,内存轻量,可反复回退)。
 *
 * 生效方式与安全边界:
 * - 编辑在浏览器本地完成,"应用修改"后替换待提交列表中的该文件(文件名保持不变,
 *   保证"文件名匹配指派"仍可用);上传时只有编辑后的版本会落盘,原图不会被额外
 *   复制或残留;随时可"重置原图"恢复为最初选择的文件。
 *
 * 关于姓名栏框选:当前批改管线的姓名识别来自整图 OCR 的抬头文本(识别后由
 * 身份决策链对齐花名册),没有独立的"姓名栏图像"输入通道;因此本选区用于
 * 快速裁剪保留(例如裁掉无关背景、只保留含姓名的干净区域),不作为单独识别通道,
 * 以免污染作文转录。若后续需要"按框识别姓名",需在管线契约上新增输入,属独立需求。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
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
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import CropOutlinedIcon from '@mui/icons-material/CropOutlined'
import CropFreeOutlinedIcon from '@mui/icons-material/CropFreeOutlined'
import HighlightAltOutlinedIcon from '@mui/icons-material/HighlightAltOutlined'
import NavigateBeforeOutlinedIcon from '@mui/icons-material/NavigateBeforeOutlined'
import NavigateNextOutlinedIcon from '@mui/icons-material/NavigateNextOutlined'
import RestartAltOutlinedIcon from '@mui/icons-material/RestartAltOutlined'
import RotateLeftOutlinedIcon from '@mui/icons-material/RotateLeftOutlined'
import RotateRightOutlinedIcon from '@mui/icons-material/RotateRightOutlined'
import UndoOutlinedIcon from '@mui/icons-material/UndoOutlined'
import { compressImageFile } from '../utils/imageCompress'

/** 单步操作(参数化操作栈:重放语义,撤销/重置零成本) */
type EditOp =
  | { type: 'grayscale' }
  | { type: 'bw' }
  | { type: 'rotate'; clockwise: boolean }
  | { type: 'crop'; x: number; y: number; w: number; h: number }

interface Selection {
  x: number
  y: number
  w: number
  h: number
}

interface Props {
  open: boolean
  /** 待处理文件列表(列表顺序 = 页面顺序) */
  files: File[]
  /** 打开时定位的序号 */
  startIndex: number
  /** 取某页的"原图"(已编辑过时返回最初文件;返回 null 表示未编辑) */
  getOriginal: (index: number) => File | null
  /** 应用编辑结果(替换列表中的该文件;文件名保持不变) */
  onApplyIndex: (index: number, file: File) => void
  /** 重置某页为原图 */
  onResetIndex: (index: number) => void
  onClose: () => void
}

/** 把操作栈重放到 canvas(灰度/黑白/旋转/裁剪;裁剪坐标为当步坐标系) */
function renderOps(
  source: HTMLCanvasElement,
  ops: EditOp[],
  target: HTMLCanvasElement,
): void {
  target.width = source.width
  target.height = source.height
  const ctx = target.getContext('2d')
  if (!ctx) return
  ctx.drawImage(source, 0, 0)

  for (const op of ops) {
    if (op.type === 'rotate') {
      const temp = document.createElement('canvas')
      temp.width = target.height
      temp.height = target.width
      const tctx = temp.getContext('2d')
      if (!tctx) continue
      tctx.translate(temp.width / 2, temp.height / 2)
      tctx.rotate(op.clockwise ? Math.PI / 2 : -Math.PI / 2)
      tctx.drawImage(target, -target.width / 2, -target.height / 2)
      target.width = temp.width
      target.height = temp.height
      const nctx = target.getContext('2d')
      if (nctx) nctx.drawImage(temp, 0, 0)
    } else if (op.type === 'crop') {
      const w = Math.max(1, Math.round(op.w))
      const h = Math.max(1, Math.round(op.h))
      const temp = document.createElement('canvas')
      temp.width = w
      temp.height = h
      const tctx = temp.getContext('2d')
      if (!tctx) continue
      tctx.drawImage(target, Math.round(op.x), Math.round(op.y), w, h, 0, 0, w, h)
      target.width = w
      target.height = h
      const nctx = target.getContext('2d')
      if (nctx) nctx.drawImage(temp, 0, 0)
    } else if (op.type === 'grayscale' || op.type === 'bw') {
      const image = ctx.getImageData(0, 0, target.width, target.height)
      const data = image.data
      for (let i = 0; i < data.length; i += 4) {
        const gray = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]
        if (op.type === 'bw') {
          const value = gray < 140 ? 0 : 255
          data[i] = value
          data[i + 1] = value
          data[i + 2] = value
        } else {
          data[i] = gray
          data[i + 1] = gray
          data[i + 2] = gray
        }
      }
      ctx.putImageData(image, 0, 0)
    }
  }
}

export default function ImageEditorDialog({
  open,
  files,
  startIndex,
  getOriginal,
  onApplyIndex,
  onResetIndex,
  onClose,
}: Props) {
  const [index, setIndex] = useState(startIndex)
  const [opsByIndex, setOpsByIndex] = useState<Record<number, EditOp[]>>({})
  const [selection, setSelection] = useState<Selection | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const sourceRef = useRef<HTMLCanvasElement | null>(null) // 原图全分辨率
  const workRef = useRef<HTMLCanvasElement | null>(null) // 重放结果全分辨率
  const viewRef = useRef<HTMLCanvasElement | null>(null) // 可视画布(缩放显示)
  const dragStart = useRef<{ x: number; y: number } | null>(null)
  // 稳定持有最新 redraw:loadImage 完成时 canvas 可能刚挂载(loading 复位后),需据此补绘
  const redrawRef = useRef<() => void>(() => {})

  const currentFile = files[index] ?? null
  const currentOps = opsByIndex[index] ?? []

  /** 载入某页原图(编辑过的取最初文件;HEIC 等浏览器不能解码则报错提示) */
  const loadImage = useCallback(
    async (targetIndex: number) => {
      const file = files[targetIndex]
      if (!file) return
      setLoading(true)
      setError(null)
      setSelection(null)
      try {
        const base = getOriginal(targetIndex) ?? file
        const lower = base.name.toLowerCase()
        if (lower.endsWith('.heic') || lower.endsWith('.heif')) {
          setError('HEIC 照片无法在浏览器内编辑(上传时由后端自动解码);请跳过该页或改用 JPG 导出。')
          setLoading(false)
          return
        }
        const bitmap = await createImageBitmap(base)
        const source = document.createElement('canvas')
        source.width = bitmap.width
        source.height = bitmap.height
        source.getContext('2d')?.drawImage(bitmap, 0, 0)
        bitmap.close()
        sourceRef.current = source
      } catch {
        setError('图片解码失败,无法编辑该文件(可能为损坏文件或浏览器不支持的格式)。')
      } finally {
        setLoading(false)
        // 首帧空白修复:loading 期间 canvas 不在 DOM 中,redraw 需等挂载后的下一帧补绘
        requestAnimationFrame(() => redrawRef.current())
      }
    },
    // redraw 为稳定引用,依赖在下方声明
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [files, getOriginal],
  )

  /** 重放操作栈 → 工作画布 → 缩放绘制到可视画布(含选区框) */
  const redraw = useCallback(() => {
    const source = sourceRef.current
    const view = viewRef.current
    if (!source || !view) return
    if (!workRef.current) workRef.current = document.createElement('canvas')
    const work = workRef.current
    renderOps(source, opsByIndex[index] ?? [], work)

    // 可视画布:按容器宽度等比缩放
    const maxW = view.parentElement?.clientWidth ?? 560
    const scale = Math.min(1, maxW / work.width)
    view.width = Math.max(1, Math.round(work.width * scale))
    view.height = Math.max(1, Math.round(work.height * scale))
    const ctx = view.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, view.width, view.height)
    ctx.drawImage(work, 0, 0, view.width, view.height)

    // 选区(显示坐标)
    if (selection) {
      const sx = selection.x * scale
      const sy = selection.y * scale
      const sw = selection.w * scale
      const sh = selection.h * scale
      ctx.fillStyle = 'rgba(63, 81, 181, 0.18)'
      ctx.fillRect(sx, sy, sw, sh)
      ctx.strokeStyle = '#3F51B5'
      ctx.lineWidth = 1.5
      ctx.setLineDash([6, 4])
      ctx.strokeRect(sx, sy, sw, sh)
      ctx.setLineDash([])
    }
  }, [opsByIndex, index, selection])

  useEffect(() => {
    if (open) {
      setIndex(startIndex)
      setOpsByIndex({})
      setSelection(null)
      setNotice(null)
      setError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, startIndex])

  useEffect(() => {
    if (open && currentFile) void loadImage(index)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, index, currentFile])

  useEffect(() => {
    redraw()
    redrawRef.current = redraw
  }, [redraw])

  const pushOp = (op: EditOp) => {
    setOpsByIndex((prev) => ({ ...prev, [index]: [...(prev[index] ?? []), op] }))
    setNotice(null)
  }

  const undo = () => {
    setOpsByIndex((prev) => {
      const ops = prev[index] ?? []
      if (ops.length === 0) return prev
      return { ...prev, [index]: ops.slice(0, -1) }
    })
    setSelection(null)
  }

  const resetCurrent = () => {
    const original = getOriginal(index)
    if (original) onResetIndex(index)
    setOpsByIndex((prev) => ({ ...prev, [index]: [] }))
    setSelection(null)
    setNotice('已重置为原图(编辑操作已全部撤销)')
    void loadImage(index)
  }

  /** 指针 → 画布像素坐标 */
  const toCanvasPoint = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const view = viewRef.current
    const work = workRef.current
    if (!view || !work) return null
    const rect = view.getBoundingClientRect()
    const scaleX = work.width / rect.width
    const scaleY = work.height / rect.height
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    }
  }

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const point = toCanvasPoint(e)
    if (!point) return
    dragStart.current = point
    setSelection({ x: point.x, y: point.y, w: 0, h: 0 })
  }
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragStart.current) return
    const point = toCanvasPoint(e)
    if (!point) return
    const start = dragStart.current
    setSelection({
      x: Math.min(start.x, point.x),
      y: Math.min(start.y, point.y),
      w: Math.abs(point.x - start.x),
      h: Math.abs(point.y - start.y),
    })
  }
  const onPointerUp = () => {
    dragStart.current = null
  }

  /** 姓名栏引导选区:顶部条带(可再手动调整) */
  const nameBandSelection = () => {
    const work = workRef.current
    if (!work) return
    const y = Math.round(work.height * 0.06)
    const h = Math.round(work.height * 0.16)
    setSelection({ x: 0, y, w: work.width, h })
    setNotice('已在顶部生成"姓名栏"条带选区:可直接裁剪保留,或重新拖拽微调')
  }

  const applyCrop = () => {
    if (!selection || selection.w < 8 || selection.h < 8) {
      setError('请先在图片上拖拽框选裁剪区域(或使用"姓名栏选区")')
      return
    }
    pushOp({ type: 'crop', x: selection.x, y: selection.y, w: selection.w, h: selection.h })
    setSelection(null)
    setError(null)
  }

  /** 应用修改:画布 → 压缩 → 新 File(文件名保持不变) */
  const applyAndCommit = async (): Promise<boolean> => {
    const work = workRef.current
    if (!work || !currentFile) return true
    if (currentOps.length === 0) {
      // 无操作但已编辑过的页面(理论上不出现)直接视为已应用
      return true
    }
    setSaving(true)
    setError(null)
    try {
      const blob: Blob | null = await new Promise((resolve) =>
        work.toBlob((b) => resolve(b), 'image/jpeg', 0.92),
      )
      if (!blob) throw new Error('导出编辑图失败')
      const raw = new File([blob], currentFile.name, { type: 'image/jpeg' })
      // 复用既有客户端压缩策略(编辑图通常已足够小,compressImageFile 会自动跳过不划算的压缩)
      const { file: finalFile } = await compressImageFile(raw)
      onApplyIndex(index, finalFile)
      setOpsByIndex((prev) => ({ ...prev, [index]: [] }))
      setNotice(`第 ${index + 1} 张已应用(仅编辑版会随批改上传,原图不会重复落盘)`)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : '应用修改失败')
      return false
    } finally {
      setSaving(false)
    }
  }

  const goTo = async (next: number) => {
    if (next < 0 || next >= files.length) return
    // 切换前自动提交当前页(若有未应用的操作)
    if (currentOps.length > 0) {
      const ok = await applyAndCommit()
      if (!ok) return
    }
    setIndex(next)
  }

  const finish = async () => {
    if (currentOps.length > 0) {
      const ok = await applyAndCommit()
      if (!ok) return
    }
    onClose()
  }

  const editedThisPage = currentOps.length > 0
  const alreadyEdited = getOriginal(index) != null

  return (
    <Dialog open={open} onClose={() => void finish()} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <CropFreeOutlinedIcon color="primary" />
        图片编辑
        <Chip size="small" variant="outlined" label={`第 ${index + 1} / ${Math.max(files.length, 1)} 张`} />
        {currentFile && (
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1, minWidth: 0 }} noWrap>
            {currentFile.name}
          </Typography>
        )}
        {(editedThisPage || alreadyEdited) && <Chip size="small" color="warning" label="已编辑" />}
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={1.5}>
          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}
          {notice && (
            <Alert severity="success" onClose={() => setNotice(null)}>
              {notice}
            </Alert>
          )}

          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
            <Button size="small" variant="outlined" onClick={() => pushOp({ type: 'grayscale' })}>
              灰度
            </Button>
            <Button size="small" variant="outlined" onClick={() => pushOp({ type: 'bw' })}>
              黑白(高对比)
            </Button>
            <Tooltip title="逆时针旋转 90°">
              <Button size="small" variant="outlined" startIcon={<RotateLeftOutlinedIcon />} onClick={() => pushOp({ type: 'rotate', clockwise: false })}>
                左转
              </Button>
            </Tooltip>
            <Tooltip title="顺时针旋转 90°">
              <Button size="small" variant="outlined" startIcon={<RotateRightOutlinedIcon />} onClick={() => pushOp({ type: 'rotate', clockwise: true })}>
                右转
              </Button>
            </Tooltip>
            <Tooltip title="在图片上拖拽框选区域">
              <Button size="small" variant="outlined" startIcon={<CropOutlinedIcon />} onClick={applyCrop}>
                应用裁剪
              </Button>
            </Tooltip>
            <Tooltip title="在顶部自动生成姓名栏条带选区(用于裁剪保留;当前管线姓名识别来自整图 OCR,该选区不作为独立识别通道)">
              <Button size="small" variant="outlined" startIcon={<HighlightAltOutlinedIcon />} onClick={nameBandSelection}>
                姓名栏选区
              </Button>
            </Tooltip>
            {selection && (
              <Button size="small" color="inherit" onClick={() => setSelection(null)}>
                清除选区
              </Button>
            )}
            <Box sx={{ flex: 1 }} />
            <Tooltip title="撤销上一步">
              <span>
                <Button size="small" startIcon={<UndoOutlinedIcon />} disabled={currentOps.length === 0} onClick={undo}>
                  撤销
                </Button>
              </span>
            </Tooltip>
            <Tooltip title="丢弃全部编辑,恢复为最初选择的原图">
              <Button size="small" color="warning" startIcon={<RestartAltOutlinedIcon />} disabled={!editedThisPage && !alreadyEdited} onClick={resetCurrent}>
                重置原图
              </Button>
            </Tooltip>
          </Stack>

          <Box sx={{ position: 'relative', bgcolor: 'action.hover', borderRadius: 2, p: 1, minHeight: 320 }}>
            {loading ? (
              <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 300 }}>
                <CircularProgress />
              </Box>
            ) : (
              <canvas
                ref={viewRef}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={onPointerUp}
                style={{ width: '100%', display: 'block', cursor: 'crosshair', borderRadius: 8 }}
              />
            )}
          </Box>

          <Typography variant="caption" color="text.secondary">
            操作说明:在图片上按住鼠标拖拽即可框选(用于裁剪);编辑仅在"应用修改"后替换待提交文件,
            上传时只有编辑版会落盘、文件名保持不变(文件名匹配指派依然可用);随时可"重置原图"。
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button
          startIcon={<NavigateBeforeOutlinedIcon />}
          disabled={index <= 0 || saving}
          onClick={() => void goTo(index - 1)}
        >
          上一张
        </Button>
        <Button
          endIcon={<NavigateNextOutlinedIcon />}
          disabled={index >= files.length - 1 || saving}
          onClick={() => void goTo(index + 1)}
        >
          下一张
        </Button>
        <Box sx={{ flex: 1 }} />
        <Button color="inherit" disabled={saving} onClick={onClose}>
          取消(放弃未应用的编辑)
        </Button>
        <Button
          variant="contained"
          disabled={saving || loading}
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : undefined}
          onClick={() => void applyAndCommit()}
        >
          应用修改
        </Button>
        <Button variant="contained" color="success" disabled={saving || loading} onClick={() => void finish()}>
          完成
        </Button>
      </DialogActions>
    </Dialog>
  )
}
