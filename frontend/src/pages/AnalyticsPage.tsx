/**
 * 班级共性错因分析(「班级管理 → 共性错因分析」标签内的内容区块)
 *
 * 功能:
 * - 按班级 / 日期范围筛选已完成批改数据;
 * - 统计卡片:样本量、学生数、错因总数、平均分;
 * - 图表:错因分布(横向条形)、得分分布;
 * - 讲评摘要:后端确定性渲染的 Markdown,支持下载;
 * - 高频错因明细:频次 / 涉及人数 / 占比 / 典型错例。
 *
 * 注:数据包导出/导入与班级合并已迁至「班级管理」对应标签,本区块不再承载管理操作。
 */

import { useCallback, useEffect, useState } from 'react'
import { useUiProfile } from '../theme'
import type { ReactNode } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
// MUI v6 新版 Grid(支持 size 响应式属性)
import Grid from '@mui/material/Grid2'
import DownloadOutlinedIcon from '@mui/icons-material/DownloadOutlined'
import InsightsOutlinedIcon from '@mui/icons-material/InsightsOutlined'
import RefreshOutlinedIcon from '@mui/icons-material/RefreshOutlined'
import SchoolOutlinedIcon from '@mui/icons-material/SchoolOutlined'
import ErrorOutlineOutlinedIcon from '@mui/icons-material/ErrorOutlineOutlined'
import WorkspacePremiumOutlinedIcon from '@mui/icons-material/WorkspacePremiumOutlined'
import ContentCopyOutlinedIcon from '@mui/icons-material/ContentCopyOutlined'
import { BarChart } from '@mui/x-charts/BarChart'
import {
  extractErrorMessage,
  fetchClassDiagnosis,
  fetchClasses,
} from '../api/client'
import MarkdownReport from '../components/MarkdownReport'
import type { ClassDiagnosis, SchoolClass } from '../types'

/** 统计指标卡片 */
function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: ReactNode
  label: string
  value: string
  hint?: string
}) {
  return (
    <Card>
      <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
        <Stack direction="row" spacing={1.2} alignItems="center">
          <Box sx={{ color: 'primary.main', display: 'flex' }}>{icon}</Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              {label}
            </Typography>
            <Typography variant="h6" lineHeight={1.2}>
              {value}
            </Typography>
            {hint && (
              <Typography variant="caption" color="text.disabled">
                {hint}
              </Typography>
            )}
          </Box>
        </Stack>
      </CardContent>
    </Card>
  )
}

export default function AnalyticsPage() {
  // UI 档位:效率优先档关闭图表动画(真实削减;均衡/高级保持动画)
  const { profile: uiProfile } = useUiProfile()
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [classFilter, setClassFilter] = useState<number | ''>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [diagnosis, setDiagnosis] = useState<ClassDiagnosis | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // 加载班级列表
  useEffect(() => {
    fetchClasses()
      .then(setClasses)
      .catch(() => setClasses([]))
  }, [])

  /** 拉取诊断数据 */
  const loadDiagnosis = useCallback(
    async (showLoading = true) => {
      if (showLoading) setLoading(true)
      setError(null)
      try {
        const data = await fetchClassDiagnosis({
          class_id: classFilter === '' ? undefined : classFilter,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
        })
        setDiagnosis(data)
      } catch (e) {
        setError(extractErrorMessage(e))
      } finally {
        if (showLoading) setLoading(false)
      }
    },
    [classFilter, dateFrom, dateTo],
  )

  useEffect(() => {
    loadDiagnosis()
  }, [loadDiagnosis])

  /** 下载讲评摘要 .md */
  const handleDownloadSummary = () => {
    if (!diagnosis?.teaching_summary_markdown) return
    const blob = new Blob([diagnosis.teaching_summary_markdown], {
      type: 'text/markdown;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `讲评摘要_${diagnosis.filters.class_name ?? '全部数据'}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  /** 复制讲评摘要 */
  const handleCopySummary = async () => {
    if (!diagnosis?.teaching_summary_markdown) return
    await navigator.clipboard.writeText(diagnosis.teaching_summary_markdown)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  // ---------- 图表数据 ----------
  const topStats = diagnosis?.category_stats.slice(0, 8) ?? []
  const errorChartLabels = topStats.map((s) => s.label)
  const errorChartValues = topStats.map((s) => s.count)
  const scoreLabels = diagnosis?.score_distribution.map((b) => b.label) ?? []
  const scoreValues = diagnosis?.score_distribution.map((b) => b.count) ?? []

  return (
    <Box sx={{ maxWidth: 1440, mx: 'auto' }}>
      {/* ================= 筛选与刷新 ================= */}
      <Card sx={{ mb: 2 }}>
        <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
          <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
            <Chip size="small" variant="outlined" label="基于已完成批改的错因数据" />
            <FormControl size="small" sx={{ minWidth: 200 }}>
              <InputLabel>班级</InputLabel>
              <Select
                label="班级"
                value={classFilter}
                onChange={(e) => setClassFilter(e.target.value as number | '')}
              >
                <MenuItem value="">全部班级(汇总)</MenuItem>
                {classes.map((c) => (
                  <MenuItem key={c.id} value={c.id} disabled={Boolean(c.merged_into_id)}>
                    {c.name}
                    {c.task_count > 0 ? ` (${c.task_count})` : ''}
                    {c.merged_into_id ? '（已并入）' : ''}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              size="small"
              label="起始日期"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              sx={{ width: 170 }}
            />
            <TextField
              size="small"
              label="截止日期"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              sx={{ width: 170 }}
            />
            <Box sx={{ flex: 1 }} />
            <Button
              size="small"
              color="inherit"
              startIcon={loading ? <CircularProgress size={14} /> : <RefreshOutlinedIcon />}
              onClick={() => loadDiagnosis(true)}
              disabled={loading}
            >
              刷新
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2, borderRadius: 3 }}>
          {error}
        </Alert>
      )}

      {loading && !diagnosis ? (
        <Box sx={{ display: 'grid', placeItems: 'center', py: 10 }}>
          <CircularProgress />
        </Box>
      ) : diagnosis ? (
        <>
          {/* ================= 统计卡片 ================= */}
          <Grid container spacing={2} sx={{ mb: 3 }}>
            <Grid size={{ xs: 6, md: 3 }}>
              <StatCard
                icon={<InsightsOutlinedIcon />}
                label="样本作文"
                value={`${diagnosis.task_count}`}
                hint="已完成批改数"
              />
            </Grid>
            <Grid size={{ xs: 6, md: 3 }}>
              <StatCard
                icon={<SchoolOutlinedIcon />}
                label="涉及学生"
                value={`${diagnosis.student_count} 人`}
              />
            </Grid>
            <Grid size={{ xs: 6, md: 3 }}>
              <StatCard
                icon={<ErrorOutlineOutlinedIcon />}
                label="错因总数"
                value={`${diagnosis.error_total} 条`}
              />
            </Grid>
            <Grid size={{ xs: 6, md: 3 }}>
              <StatCard
                icon={<WorkspacePremiumOutlinedIcon />}
                label="平均分"
                value={
                  diagnosis.average_score != null
                    ? `${diagnosis.average_score}${diagnosis.score_basis ? ` / ${diagnosis.score_basis}` : ''}`
                    : '—'
                }
                hint={diagnosis.average_score == null ? '仅支持数值得分统计' : undefined}
              />
            </Grid>
          </Grid>

          <Grid container spacing={3}>
            {/* ---------- 左:图表与明细 ---------- */}
            <Grid size={{ xs: 12, lg: 7 }}>
              <Card sx={{ mb: 3 }}>
                <CardContent sx={{ p: 2.5 }}>
                  <Typography variant="subtitle1" gutterBottom>
                    错因分布(高频 Top 8)
                  </Typography>
                  {topStats.length > 0 ? (
                    <BarChart
                      skipAnimation={uiProfile === 'efficiency'}
                      height={300}
                      layout="horizontal"
                      yAxis={[{ scaleType: 'band', data: errorChartLabels }]}
                      series={[{ data: errorChartValues, label: '出现次数', color: '#3F51B5' }]}
                      margin={{ left: 90, right: 20, top: 10, bottom: 30 }}
                      grid={{ vertical: true }}
                    />
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ py: 6, textAlign: 'center' }}>
                      当前筛选条件下暂无错因数据
                    </Typography>
                  )}
                </CardContent>
              </Card>

              <Card sx={{ mb: 3 }}>
                <CardContent sx={{ p: 2.5 }}>
                  <Typography variant="subtitle1" gutterBottom>
                    得分分布
                  </Typography>
                  {scoreValues.some((v) => v > 0) ? (
                    <BarChart
                      skipAnimation={uiProfile === 'efficiency'}
                      height={220}
                      xAxis={[{ scaleType: 'band', data: scoreLabels }]}
                      series={[{ data: scoreValues, label: '人数', color: '#F59E0B' }]}
                      margin={{ left: 40, right: 20, top: 10, bottom: 30 }}
                      grid={{ horizontal: true }}
                    />
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: 'center' }}>
                      暂无数值得分数据(DSD 等级结果不计入分布)
                    </Typography>
                  )}
                </CardContent>
              </Card>

              {/* 高频错因明细 */}
              <Card>
                <CardContent sx={{ p: 2.5 }}>
                  <Typography variant="subtitle1" gutterBottom>
                    错因明细与典型错例
                  </Typography>
                  <TableContainer>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>考点分类</TableCell>
                          <TableCell align="right">次数</TableCell>
                          <TableCell align="right">涉及人数</TableCell>
                          <TableCell align="right">占比</TableCell>
                          <TableCell>典型错例(学生)</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {diagnosis.category_stats.length === 0 && (
                          <TableRow>
                            <TableCell colSpan={5} align="center" sx={{ py: 4 }}>
                              <Typography variant="body2" color="text.secondary">
                                暂无错因数据
                              </Typography>
                            </TableCell>
                          </TableRow>
                        )}
                        {diagnosis.category_stats.slice(0, 12).map((stat) => (
                          <TableRow key={stat.category} hover>
                            <TableCell>
                              <Typography variant="body2" fontWeight={600}>
                                {stat.label}
                              </Typography>
                            </TableCell>
                            <TableCell align="right">{stat.count}</TableCell>
                            <TableCell align="right">{stat.student_count}</TableCell>
                            <TableCell align="right">{stat.percentage}%</TableCell>
                            <TableCell sx={{ maxWidth: 320 }}>
                              {stat.examples.length > 0 ? (
                                <Tooltip
                                  title={`修正:${stat.examples[0].corrected_text || '(未给出)'}`}
                                  placement="top"
                                >
                                  <Typography variant="body2" noWrap color="text.secondary">
                                    ❌ {stat.examples[0].original_text}({stat.examples[0].student_name})
                                  </Typography>
                                </Tooltip>
                              ) : (
                                <Typography variant="caption" color="text.disabled">
                                  —
                                </Typography>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </CardContent>
              </Card>
            </Grid>

            {/* ---------- 右:讲评摘要 ---------- */}
            <Grid size={{ xs: 12, lg: 5 }}>
              <Card sx={{ position: 'sticky', top: 88 }}>
                <CardContent sx={{ p: 2.5 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
                    <Typography variant="subtitle1" sx={{ flex: 1 }}>
                      讲评摘要
                    </Typography>
                    <Tooltip title="复制 Markdown 全文">
                      <Button
                        size="small"
                        color="inherit"
                        startIcon={<ContentCopyOutlinedIcon />}
                        onClick={handleCopySummary}
                      >
                        {copied ? '已复制' : '复制'}
                      </Button>
                    </Tooltip>
                    <Tooltip title="下载 .md 文件">
                      <Button
                        size="small"
                        color="inherit"
                        startIcon={<DownloadOutlinedIcon />}
                        onClick={handleDownloadSummary}
                      >
                        下载
                      </Button>
                    </Tooltip>
                  </Box>
                  <Paper
                    variant="outlined"
                    sx={{ p: 2.5, borderRadius: 3, maxHeight: 720, overflowY: 'auto' }}
                  >
                    <MarkdownReport markdown={diagnosis.teaching_summary_markdown} />
                  </Paper>
                </CardContent>
              </Card>
            </Grid>
          </Grid>
        </>
      ) : null}
    </Box>
  )
}
