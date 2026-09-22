/**
 * 统计分析(统一统计层 · 三源聚合)
 *
 * 数据来源:作文批改 / 考试统计 / 作业台账(读时聚合,各源独立权威,无需手工同步)。
 * 四个工作区:
 * - 成绩总表:学生 × 三源评估序列 + 综合平均 / 最近成绩 / 环比(可导出 CSV);
 * - 成绩分布:单来源分数段分布(柱状图 + 及格率等指标);
 * - 排行榜:综合平均 / 最近考试 / 进步与退步 / 台账覆盖率;
 * - 趋势:班级月度平均曲线 + 选定学生的评估序列。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useUiProfile } from '../theme'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  MenuItem,
  Snackbar,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'
import DownloadOutlinedIcon from '@mui/icons-material/DownloadOutlined'
import EmojiEventsOutlinedIcon from '@mui/icons-material/EmojiEventsOutlined'
import TrendingDownOutlinedIcon from '@mui/icons-material/TrendingDownOutlined'
import TrendingUpOutlinedIcon from '@mui/icons-material/TrendingUpOutlined'
import BarChartOutlinedIcon from '@mui/icons-material/BarChartOutlined'
import { BarChart, LineChart } from '@mui/x-charts'
import PageHeader from '../components/PageHeader'
import {
  downloadBlob,
  exportGradebookBlob,
  extractErrorMessage,
  fetchClasses,
  fetchDistribution,
  fetchGradebook,
  fetchRankings,
  fetchTrends,
} from '../api/client'
import type {
  DistributionResult,
  Gradebook,
  GradebookRow,
  RankingsResult,
  SchoolClass,
  StatisticsSource,
  TrendsResult,
} from '../types'

const SOURCE_LABELS: Record<StatisticsSource, string> = {
  correction: '作文批改',
  exam: '考试',
  ledger: '作业台账',
}

const SOURCE_COLORS: Record<StatisticsSource, 'primary' | 'secondary' | 'success'> = {
  correction: 'primary',
  exam: 'secondary',
  ledger: 'success',
}

function percentChip(value: number | null | undefined, delta = false) {
  if (value == null) return <Typography variant="caption" color="text.disabled">—</Typography>
  const color = delta ? (value >= 0 ? 'success.main' : 'error.main') : value >= 60 ? 'success.main' : 'warning.main'
  return (
    <Typography variant="body2" sx={{ fontWeight: 600, color }}>
      {delta && value > 0 ? '+' : ''}
      {value}
    </Typography>
  )
}

export default function StatsPage() {
  // UI 档位:效率优先档关闭图表动画(真实削减;均衡/高级保持动画)
  const { profile: uiProfile } = useUiProfile()
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [classId, setClassId] = useState<number | ''>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [sources, setSources] = useState<StatisticsSource[]>(['correction', 'exam', 'ledger'])
  const [tab, setTab] = useState(0)
  const [loading, setLoading] = useState(false)
  const [toast, setToast] = useState<{ msg: string; severity: 'success' | 'error' | 'info' } | null>(null)

  const [gradebook, setGradebook] = useState<Gradebook | null>(null)
  const [entryDialog, setEntryDialog] = useState<GradebookRow | null>(null)

  const [distSource, setDistSource] = useState<StatisticsSource>('exam')
  const [distribution, setDistribution] = useState<DistributionResult | null>(null)

  const [rankings, setRankings] = useState<RankingsResult | null>(null)

  const [trends, setTrends] = useState<TrendsResult | null>(null)
  const [trendStudent, setTrendStudent] = useState('')

  const activeClasses = useMemo(() => classes.filter((c) => !c.merged_into_id), [classes])

  useEffect(() => {
    void fetchClasses()
      .then(setClasses)
      .catch(() => undefined)
  }, [])

  const loadGradebook = useCallback(async () => {
    setLoading(true)
    try {
      setGradebook(
        await fetchGradebook({
          class_id: classId === '' ? null : classId,
          date_from: dateFrom || null,
          date_to: dateTo || null,
          sources,
        }),
      )
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [classId, dateFrom, dateTo, sources])

  const loadDistribution = useCallback(async () => {
    setLoading(true)
    try {
      setDistribution(await fetchDistribution({ source: distSource, class_id: classId === '' ? null : classId }))
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [distSource, classId])

  const loadRankings = useCallback(async () => {
    setLoading(true)
    try {
      setRankings(await fetchRankings(classId === '' ? null : classId))
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [classId])

  const loadTrends = useCallback(async () => {
    setLoading(true)
    try {
      setTrends(
        await fetchTrends({ class_id: classId === '' ? null : classId, student: trendStudent || null }),
      )
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    } finally {
      setLoading(false)
    }
  }, [classId, trendStudent])

  useEffect(() => {
    if (tab === 0) void loadGradebook()
    if (tab === 1) void loadDistribution()
    if (tab === 2) void loadRankings()
    if (tab === 3) void loadTrends()
  }, [tab, loadGradebook, loadDistribution, loadRankings, loadTrends])

  const toggleSource = (source: StatisticsSource) => {
    setSources((prev) =>
      prev.includes(source) ? (prev.length > 1 ? prev.filter((s) => s !== source) : prev) : [...prev, source],
    )
  }

  const doExport = async () => {
    try {
      const blob = await exportGradebookBlob({
        class_id: classId === '' ? null : classId,
        date_from: dateFrom || null,
        date_to: dateTo || null,
        sources,
      })
      downloadBlob(blob, `成绩总表_${new Date().toISOString().slice(0, 10)}.csv`)
    } catch (e) {
      setToast({ msg: extractErrorMessage(e), severity: 'error' })
    }
  }

  return (
    <Box>
      <PageHeader
        title="统计分析"
        subtitle="作文批改、考试统计与作业台账三源聚合:成绩总表、分布、排行与趋势。各来源独立权威、读时动态联动,数据实时一致。"
        actions={
          <>
            <TextField
              select
              size="small"
              label="班级"
              value={classId}
              onChange={(e) => setClassId(e.target.value === '' ? '' : Number(e.target.value))}
              sx={{ minWidth: 160 }}
            >
              <MenuItem value="">全部学生</MenuItem>
              {activeClasses.map((cls) => (
                <MenuItem key={cls.id} value={cls.id}>
                  {cls.name}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              type="date"
              label="起始"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              InputLabelProps={{ shrink: true }}
              sx={{ width: 150 }}
            />
            <TextField
              size="small"
              type="date"
              label="截止"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              InputLabelProps={{ shrink: true }}
              sx={{ width: 150 }}
            />
            {loading && <CircularProgress size={18} />}
          </>
        }
      />

      {/* 来源过滤 */}
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
        <Typography variant="body2" color="text.secondary">
          数据来源:
        </Typography>
        {(Object.keys(SOURCE_LABELS) as StatisticsSource[]).map((source) => (
          <Chip
            key={source}
            label={SOURCE_LABELS[source]}
            color={SOURCE_COLORS[source]}
            variant={sources.includes(source) ? 'filled' : 'outlined'}
            onClick={() => toggleSource(source)}
            size="small"
          />
        ))}
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<DownloadOutlinedIcon />} onClick={() => void doExport()}>
          导出总表 CSV
        </Button>
      </Stack>

      <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ mb: 2 }}>
        <Tab label="成绩总表" />
        <Tab label="成绩分布" />
        <Tab label="排行榜" />
        <Tab label="趋势" />
      </Tabs>

      {/* ================= 成绩总表 ================= */}
      {tab === 0 && (
        <TableContainer component={Card} variant="outlined" sx={{ maxHeight: 600 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>学生</TableCell>
                <TableCell>学号</TableCell>
                <TableCell>综合平均</TableCell>
                <TableCell>最近</TableCell>
                <TableCell>环比</TableCell>
                <TableCell>批改</TableCell>
                <TableCell>考试</TableCell>
                <TableCell>台账</TableCell>
                <TableCell>记录数</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(gradebook?.students ?? []).map((row) => (
                <TableRow
                  key={`${row.student_name}-${row.student_id ?? ''}`}
                  hover
                  sx={{ cursor: 'pointer' }}
                  onClick={() => setEntryDialog(row)}
                >
                  <TableCell>{row.student_name}</TableCell>
                  <TableCell>{row.student_id ?? '—'}</TableCell>
                  <TableCell>{percentChip(row.overall_percent)}</TableCell>
                  <TableCell>{percentChip(row.latest_percent)}</TableCell>
                  <TableCell>{percentChip(row.delta, true)}</TableCell>
                  <TableCell>{percentChip(row.source_average.correction)}</TableCell>
                  <TableCell>{percentChip(row.source_average.exam)}</TableCell>
                  <TableCell>{percentChip(row.source_average.ledger)}</TableCell>
                  <TableCell>{row.record_count}</TableCell>
                </TableRow>
              ))}
              {gradebook && gradebook.students.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} align="center">
                    <Typography variant="body2" color="text.secondary" sx={{ py: 4 }}>
                      暂无数据 —— 完成批改 / 登记台账 / 识别考卷后自动出现在这里
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* ================= 成绩分布 ================= */}
      {tab === 1 && (
        <Box>
          <Stack direction="row" spacing={1} sx={{ mb: 2 }} alignItems="center">
            <TextField
              select
              size="small"
              label="来源"
              value={distSource}
              onChange={(e) => setDistSource(e.target.value as StatisticsSource)}
              sx={{ minWidth: 150 }}
            >
              {(Object.keys(SOURCE_LABELS) as StatisticsSource[]).map((source) => (
                <MenuItem key={source} value={source}>
                  {SOURCE_LABELS[source]}
                </MenuItem>
              ))}
            </TextField>
            <Button size="small" variant="outlined" onClick={() => void loadDistribution()}>
              刷新
            </Button>
          </Stack>
          {distribution && distribution.count > 0 ? (
            <>
              <Stack direction="row" spacing={2} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
                <Chip label={`样本 ${distribution.count}`} />
                <Chip color="primary" label={`平均 ${distribution.average}`} />
                <Chip label={`最高 ${distribution.highest}`} />
                <Chip label={`最低 ${distribution.lowest}`} />
                <Chip color={distribution.pass_rate != null && distribution.pass_rate >= 80 ? 'success' : 'warning'} label={`及格率 ${distribution.pass_rate}%`} />
              </Stack>
              <Card variant="outlined">
                <BarChart
                  skipAnimation={uiProfile === 'efficiency'}
                  height={300}
                  xAxis={[{ scaleType: 'band', data: distribution.buckets.map((b) => b.label) }]}
                  series={[{ data: distribution.buckets.map((b) => b.count), label: '人数', color: '#3F51B5' }]}
                  margin={{ left: 40, right: 20, top: 30, bottom: 30 }}
                />
              </Card>
            </>
          ) : (
            <Alert severity="info">该来源在当前筛选下暂无百分制数据(CEFR 等级等无数值成绩仅参与计数展示)。</Alert>
          )}
        </Box>
      )}

      {/* ================= 排行榜 ================= */}
      {tab === 2 && rankings && (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
          <Card variant="outlined">
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <EmojiEventsOutlinedIcon color="warning" />
                <Typography variant="subtitle2">综合平均榜</Typography>
              </Stack>
              {rankings.combined.map((row, index) => (
                <Stack key={row.student_name} direction="row" spacing={1} sx={{ py: 0.5 }}>
                  <Typography variant="body2" sx={{ width: 24, color: index < 3 ? 'warning.main' : 'text.disabled', fontWeight: 700 }}>
                    {index + 1}
                  </Typography>
                  <Typography variant="body2" sx={{ flex: 1 }} noWrap>
                    {row.student_name}
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {row.average}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    ({row.count})
                  </Typography>
                </Stack>
              ))}
              {rankings.combined.length === 0 && <Typography variant="body2" color="text.secondary">暂无数据</Typography>}
            </CardContent>
          </Card>

          <Card variant="outlined">
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <BarChartOutlinedIcon color="secondary" />
                <Typography variant="subtitle2">最近考试榜{rankings.exam_ranking.exam_name ? ` · ${rankings.exam_ranking.exam_name}` : ''}</Typography>
              </Stack>
              {rankings.exam_ranking.rows.map((row, index) => (
                <Stack key={row.student_name} direction="row" spacing={1} sx={{ py: 0.5 }}>
                  <Typography variant="body2" sx={{ width: 24, color: index < 3 ? 'warning.main' : 'text.disabled', fontWeight: 700 }}>
                    {index + 1}
                  </Typography>
                  <Typography variant="body2" sx={{ flex: 1 }} noWrap>
                    {row.student_name}
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {row.total_score}
                    <Typography component="span" variant="caption" color="text.secondary">
                      /{row.full_score}
                    </Typography>
                  </Typography>
                </Stack>
              ))}
              {rankings.exam_ranking.rows.length === 0 && <Typography variant="body2" color="text.secondary">暂无考试数据</Typography>}
            </CardContent>
          </Card>

          <Card variant="outlined">
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <TrendingUpOutlinedIcon color="success" />
                <Typography variant="subtitle2">进步榜(最近两次对比)</Typography>
              </Stack>
              {rankings.progress.map((row) => (
                <Stack key={row.student_name} direction="row" spacing={1} sx={{ py: 0.5 }}>
                  <Typography variant="body2" sx={{ flex: 1 }} noWrap>
                    {row.student_name}
                  </Typography>
                  <Typography variant="body2" color="success.main" sx={{ fontWeight: 600 }}>
                    +{row.delta}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {row.previous} → {row.latest}
                  </Typography>
                </Stack>
              ))}
              {rankings.progress.length === 0 && <Typography variant="body2" color="text.secondary">暂无对比数据</Typography>}
            </CardContent>
          </Card>

          <Card variant="outlined">
            <CardContent>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <TrendingDownOutlinedIcon color="error" />
                <Typography variant="subtitle2">需关注(退步)与台账覆盖率</Typography>
              </Stack>
              {rankings.decline.filter((r) => r.delta < 0).map((row) => (
                <Stack key={row.student_name} direction="row" spacing={1} sx={{ py: 0.5 }}>
                  <Typography variant="body2" sx={{ flex: 1 }} noWrap>
                    {row.student_name}
                  </Typography>
                  <Typography variant="body2" color="error.main" sx={{ fontWeight: 600 }}>
                    {row.delta}
                  </Typography>
                </Stack>
              ))}
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1, mb: 0.5 }}>
                台账覆盖率(登记项覆盖比例):
              </Typography>
              {rankings.coverage.map((row) => (
                <Stack key={row.student_name} direction="row" spacing={1} sx={{ py: 0.25 }}>
                  <Typography variant="body2" sx={{ flex: 1 }} noWrap>
                    {row.student_name}
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {row.coverage}%
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {row.covered_items}/{row.item_total}
                  </Typography>
                </Stack>
              ))}
              {rankings.coverage.length === 0 && rankings.decline.filter((r) => r.delta < 0).length === 0 && (
                <Typography variant="body2" color="text.secondary">暂无数据</Typography>
              )}
            </CardContent>
          </Card>
        </Box>
      )}

      {/* ================= 趋势 ================= */}
      {tab === 3 && trends && (
        <Box>
          <Stack direction="row" spacing={1.5} sx={{ mb: 2 }} alignItems="center">
            <TextField
              size="small"
              label="对比学生(姓名/学号,可选)"
              value={trendStudent}
              onChange={(e) => setTrendStudent(e.target.value)}
              sx={{ width: 240 }}
            />
            <Button size="small" variant="outlined" onClick={() => void loadTrends()}>
              查询
            </Button>
          </Stack>
          {trends.monthly.length > 0 ? (
            <Card variant="outlined" sx={{ mb: 2 }}>
              <LineChart
                skipAnimation={uiProfile === 'efficiency'}
                height={300}
                xAxis={[{ scaleType: 'point', data: trends.monthly.map((m) => m.month) }]}
                series={[
                  {
                    data: trends.monthly.map((m) => m.average ?? null),
                    label: '月度平均(三源)',
                    color: '#3F51B5',
                  },
                ]}
                margin={{ left: 40, right: 20, top: 30, bottom: 30 }}
              />
            </Card>
          ) : (
            <Alert severity="info" sx={{ mb: 2 }}>
              暂无趋势数据
            </Alert>
          )}
          {trends.student_selected && (
            <TableContainer component={Card} variant="outlined" sx={{ maxHeight: 420 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell>日期</TableCell>
                    <TableCell>来源</TableCell>
                    <TableCell>项目</TableCell>
                    <TableCell>归一百分比</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {trends.student_entries.map((entry, index) => (
                    <TableRow key={index} hover>
                      <TableCell>{entry.date}</TableCell>
                      <TableCell>
                        <Chip size="small" variant="outlined" label={SOURCE_LABELS[entry.source]} />
                      </TableCell>
                      <TableCell>{entry.label}</TableCell>
                      <TableCell>{percentChip(entry.percent)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Box>
      )}

      {/* 学生评估序列弹窗 */}
      <Dialog open={Boolean(entryDialog)} onClose={() => setEntryDialog(null)} maxWidth="sm" fullWidth>
        <DialogTitle>
          {entryDialog?.student_name} · 评估记录({entryDialog?.entries.length ?? 0})
        </DialogTitle>
        <DialogContent dividers>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>日期</TableCell>
                <TableCell>来源</TableCell>
                <TableCell>项目</TableCell>
                <TableCell>原始分</TableCell>
                <TableCell>归一</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(entryDialog?.entries ?? []).slice().reverse().map((entry, index) => (
                <TableRow key={index}>
                  <TableCell>{entry.date}</TableCell>
                  <TableCell>
                    <Chip size="small" variant="outlined" label={SOURCE_LABELS[entry.source]} color={SOURCE_COLORS[entry.source]} />
                  </TableCell>
                  <TableCell>{entry.label}</TableCell>
                  <TableCell>{entry.raw}</TableCell>
                  <TableCell>{entry.percent ?? '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DialogContent>
      </Dialog>

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={toast?.severity ?? 'info'} onClose={() => setToast(null)}>
          {toast?.msg}
        </Alert>
      </Snackbar>
    </Box>
  )
}
