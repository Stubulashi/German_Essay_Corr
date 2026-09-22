/**
 * 标准答题卷打印页(/print/answer-sheet)
 *
 * 与后端 `services/sheet_align.py` 共享同一几何(px = 1500/210 ≈ 7.1429/mm):
 * - 主定位块 ×4:16×16mm 实心黑方,外侧距页边 12mm(中心 20mm)→ 整页找平基准;
 * - 次定位块 ×2:8×8mm,y=68mm、x=12/190mm → 姓名/学号区与正文区的分界标记;
 * - 信息区:行1 班级/日期(y34–44)、行2 姓名/学号(y44–56)、行3 题目(y56–66),网格两列;
 * - 右侧身份二维码(新):16×16mm,与信息区顶部对齐(约 y36–52,x≈174–190mm),
 *   位于姓名区裁剪窗(x12–152mm)之外 → 解码走"全图直扫"链,不影响既有裁剪/OCR 路径;
 * - 正文书写线:y84 起,21 行,9mm 步距(末线 273);
 * - 姓名+学号裁剪区(后端)≈ (86,310)–(1086,416)px。
 *
 * 绑定模式:通过 URL 参数 `?classId=X&rosterId=Y` 指定学生(由工作台"打印标准答题卷"
 * 选择班级/学生后打开);二维码由后端 `/api/classes/rosters/{id}/qr.png` 本地生成
 * (内容 V1|学号|姓名),教师上传该卷照片时本地解码即得身份。无参数时为旧版式
 * (无二维码,识别回退手写 OCR 链)。
 *
 * 打印提示:务必按 100% 比例(不缩放)打印,并关闭"适应页面";建议 A4 普通纸。
 * 平面固定配色(不随明暗主题):白底黑字、纯黑定位块,打印时强制精确着色。
 */

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Box, Button } from '@mui/material'
import PrintIcon from '@mui/icons-material/Print'
import { fetchClassRoster } from '../api/client'
import type { RosterMember } from '../types'

const MARGIN = 12 // 页边距(mm)
const BLOCK = 16 // 主定位块边长(mm)
const SUB_BLOCK = 8 // 次定位块边长(mm)
const QR_SIZE = 16 // 身份二维码边长(mm)
const INK = '#000'
const GRAY = '#666'
const LINE = '#b8b8b8'

/** 定位块(实心黑方;打印强制纯黑) */
function Fiducial({ top, left, size = BLOCK }: { top: number; left: number; size?: number }) {
  return (
    <Box
      sx={{
        position: 'absolute',
        top: `${top}mm`,
        left: `${left}mm`,
        width: `${size}mm`,
        height: `${size}mm`,
        bgcolor: INK,
        '@media print': {
          bgcolor: INK,
          WebkitPrintColorAdjust: 'exact',
          printColorAdjust: 'exact',
        },
      }}
    />
  )
}

/** 下划线填写框(标签 + 弹性下划线;宽度由所在网格列决定,结构性防溢出) */
function Field({ label, flex = 1 }: { label: string; flex?: number }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-end', flex, minWidth: 0, height: '10mm' }}>
      <Box component="span" sx={{ fontSize: '10.5pt', color: INK, whiteSpace: 'nowrap', mr: '2mm', pb: '1mm' }}>
        {label}
      </Box>
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          borderBottom: `0.45mm solid ${INK}`,
          height: '7mm',
        }}
      />
    </Box>
  )
}

export default function AnswerSheetPage() {
  const lineStart = 84 // 正文书写线起点(mm)
  const lineGap = 9 // 行距(mm)
  const lineCount = 21 // (273-84)/9 = 21

  // ---------- 学生绑定(可选;?classId=X&rosterId=Y) ----------
  const [searchParams] = useSearchParams()
  const classId = Number(searchParams.get('classId') || 0)
  const rosterId = Number(searchParams.get('rosterId') || 0)
  const [student, setStudent] = useState<RosterMember | null>(null)

  useEffect(() => {
    let cancelled = false
    if (!classId || !rosterId) return
    void fetchClassRoster(classId)
      .then((members) => {
        if (cancelled) return
        const matched = members.find((m) => m.id === rosterId) ?? null
        setStudent(matched)
      })
      .catch(() => {
        if (!cancelled) setStudent(null) // 加载失败:静默降级为未绑定版式
      })
    return () => {
      cancelled = true
    }
  }, [classId, rosterId])

  const bound = student !== null

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: '#eceff1', py: 3, overflowX: 'auto' }}>
      {/* 工具栏(仅屏幕显示) */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          gap: 2,
          mb: 2,
          '@media print': { display: 'none' },
        }}
      >
        <Button variant="contained" startIcon={<PrintIcon />} onClick={() => window.print()}>
          打印(按 100% 比例,不缩放)
        </Button>
        <Button variant="outlined" onClick={() => window.close()}>
          关闭
        </Button>
      </Box>

      {/* A4 页面(固定白底黑字,不随明暗主题;窄屏分级缩放,打印强制 100%) */}
      <Box
        sx={{
          width: '210mm',
          height: '297mm',
          mx: 'auto',
          bgcolor: '#fff',
          color: INK,
          position: 'relative',
          zoom: { xs: 0.62, sm: 0.75, md: 0.9, lg: 1 },
          boxShadow: { xs: 0, print: 'none' },
          '@media print': { zoom: 1, boxShadow: 'none', mx: 0 },
        }}
      >
        {/* 主定位块 ×4(外侧距页边 12mm) */}
        <Fiducial top={MARGIN} left={MARGIN} />
        <Fiducial top={MARGIN} left={210 - MARGIN - BLOCK} />
        <Fiducial top={297 - MARGIN - BLOCK} left={210 - MARGIN - BLOCK} />
        <Fiducial top={297 - MARGIN - BLOCK} left={MARGIN} />

        {/* 页眉说明(居中 y≈20mm,避开两侧角块) */}
        <Box
          sx={{
            position: 'absolute',
            top: '17mm',
            left: `${MARGIN + BLOCK + 4}mm`,
            right: `${MARGIN + BLOCK + 4}mm`,
            textAlign: 'center',
            fontSize: '9pt',
            color: GRAY,
          }}
        >
          德语作文 · 标准答题卷
          <Box component="span" sx={{ fontSize: '8pt' }}>
            (黑色笔工整书写;姓名/学号用拼音;四角黑块与中部小方块请保持清晰完整)
          </Box>
          <Box sx={{ fontSize: '8pt', mt: '0.5mm' }}>
            {bound ? '本卷已绑定:' : '未绑定学生(可手写姓名/学号,系统将自动识别)'}
            {bound && student ? (
              <Box component="span" sx={{ color: INK }}>
                {student.name}
                {student.student_id ? `(${student.student_id})` : ''}
              </Box>
            ) : null}
          </Box>
        </Box>

        {/* 信息区(y34–66;左列三行 + 右侧身份二维码列,几何与定位块/裁剪窗完全兼容) */}
        <Box
          sx={{
            position: 'absolute',
            top: '34mm',
            left: `${MARGIN}mm`,
            right: `${MARGIN}mm`,
            display: 'flex',
            alignItems: 'flex-start',
          }}
        >
          {/* 左列:原三行信息(未绑定时为全宽,保持旧版式) */}
          <Box sx={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '2mm' }}>
            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: '6mm' }}>
              <Field label="班级 Klasse:" />
              <Field label="日期 Datum:" />
            </Box>
            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: '6mm' }}>
              <Field label="姓名 Name(Pinyin):" />
              <Field label="学号 ID(Nummer):" />
            </Box>
            <Field label="题目 Thema:" />
          </Box>

          {/* 右列:身份二维码(仅绑定模式;16mm,右上对齐;位于姓名区裁剪窗之外) */}
          {bound && student ? (
            <Box
              sx={{
                width: `${QR_SIZE}mm`,
                flexShrink: 0,
                ml: '6mm',
                mr: '8mm',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
              }}
            >
              <Box
                component="img"
                src={`/api/classes/rosters/${student.id}/qr.png`}
                alt={`${student.name} 身份二维码`}
                sx={{
                  width: `${QR_SIZE}mm`,
                  height: `${QR_SIZE}mm`,
                  display: 'block',
                  imageRendering: 'pixelated',
                  '@media print': { WebkitPrintColorAdjust: 'exact', printColorAdjust: 'exact' },
                }}
              />
              <Box sx={{ fontSize: '6.5pt', color: GRAY, mt: '0.5mm' }}>身份二维码</Box>
            </Box>
          ) : null}
        </Box>

        {/* 次定位块 ×2(信息区与正文区的分界标记;y=68mm,左右各一) */}
        <Fiducial top={68} left={MARGIN} size={SUB_BLOCK} />
        <Fiducial top={68} left={210 - MARGIN - SUB_BLOCK} size={SUB_BLOCK} />

        {/* 正文书写线(9mm 行距,21 行;首线上方浅灰小字提示) */}
        <Box sx={{ position: 'absolute', top: `${lineStart - 6}mm`, left: `${MARGIN}mm`, right: `${MARGIN}mm` }}>
          <Box sx={{ fontSize: '7.5pt', color: GRAY, textAlign: 'center', mb: '2mm' }}>
            从下方第一条横线开始书写
          </Box>
          {Array.from({ length: lineCount }, (_, index) => (
            <Box
              key={index}
              sx={{
                height: `${lineGap}mm`,
                borderBottom: `0.2mm solid ${LINE}`,
                '@media print': { borderBottom: `0.2mm solid ${LINE}` },
              }}
            />
          ))}
        </Box>

        {/* 页脚提示 */}
        <Box
          sx={{
            position: 'absolute',
            bottom: '10mm',
            left: `${MARGIN}mm`,
            right: `${MARGIN}mm`,
            textAlign: 'center',
            fontSize: '7.5pt',
            color: GRAY,
          }}
        >
          拍摄/扫描时整页入框、四角黑块完整可见,系统将自动找平校正
        </Box>
      </Box>
    </Box>
  )
}
