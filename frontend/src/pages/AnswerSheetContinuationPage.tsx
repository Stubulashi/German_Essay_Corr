/**
 * 续写纸打印页(/print/answer-sheet-continuation)
 *
 * 与首页共用主定位块几何;通过「页底类型带」与首页区分:
 * - 页底 y=277mm 处 L(x90mm)/R(x120mm) 两个 8mm 实心块(位串 101)=续写页;
 * - 首页无此标记(兼容全部旧卷) → 系统按"无标记=首页"处理。
 *
 * 上传后系统检测到续写纸会自动把该页并入"上一张图片"的同一任务
 * (相邻归页;首张即续写/未命中标记则按独立任务安全回退)。
 *
 * 打印提示:务必按 100% 比例(不缩放)打印,并关闭"适应页面";建议 A4 普通纸。
 */

import { Box, Button } from '@mui/material'
import PrintIcon from '@mui/icons-material/Print'

const MARGIN = 12 // 页边距(mm)
const BLOCK = 16 // 主定位块边长(mm)
const TYPE_MARK = 8 // 类型带槽位块边长(mm)
const INK = '#000'
const GRAY = '#666'
const LINE = '#b8b8b8'

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

export default function AnswerSheetContinuationPage() {
  const lineStart = 30 // 书写线起点(mm,续写纸无信息区)
  const lineGap = 9
  const lineCount = 26 // 30..255mm

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

      {/* A4 页面(窄屏分级缩放,打印强制 100%) */}
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
        {/* 主定位块 ×4 */}
        <Fiducial top={MARGIN} left={MARGIN} />
        <Fiducial top={MARGIN} left={210 - MARGIN - BLOCK} />
        <Fiducial top={297 - MARGIN - BLOCK} left={210 - MARGIN - BLOCK} />
        <Fiducial top={297 - MARGIN - BLOCK} left={MARGIN} />

        {/* 页眉说明 */}
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
          德语作文 · 续写纸
          <Box component="span" sx={{ fontSize: '8pt' }}>
            (与首页一同上传,系统会自动接到上一页同一份作文;顶部书写即可)
          </Box>
        </Box>

        {/* 书写线(无信息区,首页同款 9mm 行距) */}
        <Box sx={{ position: 'absolute', top: `${lineStart - 5}mm`, left: `${MARGIN}mm`, right: `${MARGIN}mm` }}>
          <Box sx={{ fontSize: '7.5pt', color: GRAY, textAlign: 'center', mb: '1.5mm' }}>
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

        {/* 类型带:底带 L/R 两块(位串 101 = 续写页;请勿涂改/遮挡) */}
        <Fiducial top={273} left={86} size={TYPE_MARK} />
        <Fiducial top={273} left={116} size={TYPE_MARK} />

        {/* 页脚提示 */}
        <Box
          sx={{
            position: 'absolute',
            bottom: '6mm',
            left: `${MARGIN}mm`,
            right: `${MARGIN}mm`,
            textAlign: 'center',
            fontSize: '7.5pt',
            color: GRAY,
          }}
        >
          本页为续写纸(页底两枚小方块为自动归页标记,请保持清晰完整)
        </Box>
      </Box>
    </Box>
  )
}
