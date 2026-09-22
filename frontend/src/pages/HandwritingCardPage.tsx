/**
 * 手写抄写卡打印页(/print/handwriting-card?topic=…&sentence=…)
 *
 * 花名册「手写模型」采集流程的素材卡:教师打印后交给学生,
 * 学生把题目与正文句抄写到「标准答题卷」上,并手写班级/日期/姓名/学号/题目。
 */

import { Box, Button } from '@mui/material'
import PrintIcon from '@mui/icons-material/Print'
import { useSearchParams } from 'react-router-dom'

export default function HandwritingCardPage() {
  const [params] = useSearchParams()
  const topic = params.get('topic') ?? ''
  const sentence = params.get('sentence') ?? ''

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: '#eceff1', py: 3, overflowX: 'auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'center', gap: 2, mb: 2, '@media print': { display: 'none' } }}>
        <Button variant="contained" startIcon={<PrintIcon />} onClick={() => window.print()}>
          打印抄写卡
        </Button>
        <Button variant="outlined" onClick={() => window.close()}>
          关闭
        </Button>
      </Box>

      <Box
        sx={{
          width: '210mm',
          minHeight: '160mm',
          mx: 'auto',
          bgcolor: '#fff',
          color: '#000',
          p: '20mm',
          boxSizing: 'border-box',
          borderRadius: 1,
          zoom: { xs: 0.62, sm: 0.75, md: 0.9, lg: 1 },
          '@media print': { zoom: 1, borderRadius: 0, mx: 0 },
        }}
      >
        <Box sx={{ fontSize: '13pt', fontWeight: 700, mb: 6 }}>德语作文 · 抄写练习素材</Box>
        <Box sx={{ fontSize: '15pt', mb: 1 }}>题目 Thema:</Box>
        <Box sx={{ fontSize: '19pt', fontWeight: 700, mb: 8, lineHeight: 1.6 }}>{topic}</Box>
        <Box sx={{ fontSize: '15pt', mb: 1 }}>正文 Text:</Box>
        <Box sx={{ fontSize: '19pt', fontWeight: 700, lineHeight: 1.8 }}>{sentence}</Box>
        <Box sx={{ fontSize: '10.5pt', color: '#444', mt: 10, lineHeight: 1.9 }}>
          请将以上题目与正文抄写到「标准答题卷」上,并在卷面上手写填写:
          班级、日期、姓名(拼音)、学号、以及该题目。
          <br />
          书写请使用黑色笔,保持工整;四角黑色方块与页底标记请保持清晰完整。
        </Box>
      </Box>
    </Box>
  )
}
