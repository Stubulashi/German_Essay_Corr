/**
 * 学生错题本页(#3)
 *
 * 左侧:学生列表(搜索);
 * 右侧:学生画像 —— 批改次数 / 平均分 / 复现错因高亮 / 错因分布图 /
 *       批改时间线(可跳转对应报告)。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useUiProfile } from '../theme'
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  InputAdornment,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import Grid from '@mui/material/Grid2'
import HistoryToggleOffOutlinedIcon from '@mui/icons-material/HistoryToggleOffOutlined'
import OpenInNewOutlinedIcon from '@mui/icons-material/OpenInNewOutlined'
import PersonSearchOutlinedIcon from '@mui/icons-material/PersonSearchOutlined'
import RepeatOutlinedIcon from '@mui/icons-material/RepeatOutlined'
import SearchOutlinedIcon from '@mui/icons-material/SearchOutlined'
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined'
import { BarChart } from '@mui/x-charts/BarChart'
import { useNavigate } from 'react-router-dom'
import { extractErrorMessage, fetchStudentProfile, fetchStudents } from '../api/client'
import PageHeader from '../components/PageHeader'
import ReportProblemOutlinedIcon from '@mui/icons-material/ReportProblemOutlined'
import type { StudentOut, StudentProfile } from '../types'

/** 格式化日期 */
function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function StudentsPage() {
  // UI 档位:效率优先档关闭图表动画(真实削减;均衡/高级保持动画)
  const { profile: uiProfile } = useUiProfile()
  const navigate = useNavigate()
  const [students, setStudents] = useState<StudentOut[]>([])
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<StudentOut | null>(null)
  const [profile, setProfile] = useState<StudentProfile | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 加载学生列表
  useEffect(() => {
    fetchStudents()
      .then((list) => {
        setStudents(list)
        if (list.length > 0) setSelected(list[0])
      })
      .catch((e) => setError(extractErrorMessage(e)))
  }, [])

  // 选中学生 -> 加载画像
  const loadProfile = useCallback(async (student: StudentOut) => {
    setProfileLoading(true)
    setError(null)
    try {
      const data = await fetchStudentProfile(
        student.student_id ? { student_id: student.student_id } : { name: student.name },
      )
      setProfile(data)
    } catch (e) {
      setProfile(null)
      setError(extractErrorMessage(e))
    } finally {
      setProfileLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selected) loadProfile(selected)
  }, [selected, loadProfile])

  /** 过滤后的学生列表 */
  const filteredStudents = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return students
    return students.filter(
      (s) => s.name.toLowerCase().includes(q) || (s.student_id ?? '').toLowerCase().includes(q),
    )
  }, [students, search])

  // 图表数据(前 8 个考点)
  const topStats = profile?.category_stats.slice(0, 8) ?? []
  const chartLabels = topStats.map((s) => s.label)
  const chartValues = topStats.map((s) => s.count)

  return (
    <Box sx={{ maxWidth: 1440, mx: 'auto' }}>
      {/* ================= 标题(统一规范) ================= */}
      <PageHeader
        title="学生错题本"
        subtitle="按学生查看历史批改画像、错因分布与复现问题;点击时间线条目可跳转到对应报告。"
        chips={<Chip size="small" variant="outlined" label="按学生追踪历史错因与复现问题" />}
      />

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2, borderRadius: 3 }}>
          {error}
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* ================= 左:学生列表 ================= */}
        <Grid size={{ xs: 12, md: 4, lg: 3 }}>
          <Card sx={{ position: 'sticky', top: 88 }}>
            <CardContent sx={{ p: 2 }}>
              <TextField
                size="small"
                fullWidth
                placeholder="搜索姓名 / 学号"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                slotProps={{
                  input: {
                    startAdornment: (
                      <InputAdornment position="start">
                        <SearchOutlinedIcon fontSize="small" />
                      </InputAdornment>
                    ),
                  },
                }}
                sx={{ mb: 1.5 }}
              />
              {students.length === 0 ? (
                <Box sx={{ textAlign: 'center', py: 5 }}>
                  <PersonSearchOutlinedIcon sx={{ fontSize: 40, color: 'text.disabled', mb: 1 }} />
                  <Typography variant="body2" color="text.secondary">
                    暂无学生数据
                  </Typography>
                  <Typography variant="caption" color="text.disabled">
                    完成批改后自动建档
                  </Typography>
                </Box>
              ) : (
                <List dense sx={{ maxHeight: 620, overflowY: 'auto' }}>
                  {filteredStudents.map((s) => (
                    <ListItemButton
                      key={s.id}
                      selected={selected?.id === s.id}
                      onClick={() => setSelected(s)}
                      sx={{
                        borderRadius: 2.5,
                        mb: 0.4,
                        '&.Mui-selected': { bgcolor: 'primary.main', color: '#fff' },
                        '&.Mui-selected:hover': { bgcolor: 'primary.dark' },
                      }}
                    >
                      <ListItemText
                        primary={s.name}
                        secondary={s.student_id || '无所号'}
                        primaryTypographyProps={{ fontSize: 14, fontWeight: 500 }}
                        secondaryTypographyProps={{ fontSize: 11.5 }}
                      />
                    </ListItemButton>
                  ))}
                  {filteredStudents.length === 0 && (
                    <Typography variant="body2" color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
                      没有匹配的学生
                    </Typography>
                  )}
                </List>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* ================= 右:学生画像 ================= */}
        <Grid size={{ xs: 12, md: 8, lg: 9 }}>
          {profileLoading ? (
            <Box sx={{ display: 'grid', placeItems: 'center', py: 12 }}>
              <CircularProgress />
            </Box>
          ) : profile ? (
            <Stack spacing={3}>
              {/* 概览卡片 */}
              <Card>
                <CardContent sx={{ p: 2.5 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', mb: 2 }}>
                    <Typography variant="h6">
                      {profile.student_name}
                      {profile.student_id && (
                        <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                          {profile.student_id}
                        </Typography>
                      )}
                    </Typography>
                    {profile.average_score != null && (
                      <Chip
                        size="small"
                        color="primary"
                        label={`平均 ${profile.average_score}${profile.score_basis ? ` / ${profile.score_basis}` : ''}`}
                      />
                    )}
                    <Chip size="small" variant="outlined" label={`批改 ${profile.task_count} 次`} />
                    <Chip size="small" variant="outlined" label={`累计错因 ${profile.error_total} 条`} />
                  </Box>

                  {/* 复现错因高亮 */}
                  {profile.recurring.length > 0 ? (
                    <Alert
                      severity="warning"
                      variant="outlined"
                      icon={<RepeatOutlinedIcon />}
                      sx={{ borderRadius: 2.5 }}
                    >
                      <Typography variant="body2" fontWeight={600} gutterBottom>
                        复现错因(多次出现,建议重点突破)
                      </Typography>
                      <Box sx={{ display: 'flex', gap: 0.6, flexWrap: 'wrap' }}>
                        {profile.recurring.map((r) => (
                          <Chip key={r} size="small" color="warning" label={r} />
                        ))}
                      </Box>
                    </Alert>
                  ) : (
                    <Alert severity="success" variant="outlined" sx={{ borderRadius: 2.5 }}>
                      暂未发现复现错因,继续保持!
                    </Alert>
                  )}

                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
                    首次批改:{profile.first_seen ? formatDate(profile.first_seen) : '—'}
                    {profile.last_seen && ` · 最近批改:${formatDate(profile.last_seen)}`}
                  </Typography>
                </CardContent>
              </Card>

              {/* 错因分布图 */}
              <Card>
                <CardContent sx={{ p: 2.5 }}>
                  <Typography variant="subtitle1" gutterBottom>
                    个体错因分布(Top 8)
                  </Typography>
                  {topStats.length > 0 ? (
                    <BarChart
                      skipAnimation={uiProfile === 'efficiency'}
                      height={280}
                      layout="horizontal"
                      yAxis={[{ scaleType: 'band', data: chartLabels }]}
                      series={[{ data: chartValues, label: '出现次数', color: '#3F51B5' }]}
                      margin={{ left: 90, right: 20, top: 10, bottom: 30 }}
                      grid={{ vertical: true }}
                    />
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ py: 5, textAlign: 'center' }}>
                      暂无错因数据
                    </Typography>
                  )}
                </CardContent>
              </Card>

              {/* 时间线 */}
              <Card>
                <CardContent sx={{ p: 2.5 }}>
                  <Typography variant="subtitle1" gutterBottom>
                    批改时间线
                  </Typography>
                  <List dense>
                    {[...profile.timeline].reverse().map((item) => (
                      <Paper
                        key={item.task_id}
                        variant="outlined"
                        sx={{ borderRadius: 2.5, mb: 1, overflow: 'hidden' }}
                      >
                        <ListItemButton
                          onClick={() => navigate(`/review/${item.task_id}`)}
                          sx={{ py: 1.2 }}
                        >
                          <HistoryToggleOffOutlinedIcon
                            sx={{ mr: 1.5, color: 'text.secondary', fontSize: 20 }}
                          />
                          <ListItemText
                            primary={
                              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                                <Typography variant="body2" fontWeight={500}>
                                  {formatDate(item.created_at)}
                                </Typography>
                                {item.assignment_name && (
                                  <Typography variant="caption" color="text.secondary">
                                    {item.assignment_name}
                                  </Typography>
                                )}
                              </Box>
                            }
                            secondary={
                              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.4, flexWrap: 'wrap' }}>
                                <Chip size="small" variant="outlined" label={`得分 ${item.overall_score || '—'}`} />
                                <Chip
                                  size="small"
                                  variant="outlined"
                                  color={item.error_count > 0 ? 'default' : 'success'}
                                  icon={item.error_count > 0 ? <ReportProblemOutlinedIcon /> : undefined}
                                  label={`错因 ${item.error_count} 条`}
                                />
                                {item.top_category_label && (
                                  <Chip
                                    size="small"
                                    variant="outlined"
                                    color="warning"
                                    icon={<WarningAmberOutlinedIcon />}
                                    label={`主要:${item.top_category_label}`}
                                  />
                                )}
                              </Box>
                            }
                          />
                          <Tooltip title="查看报告">
                            <OpenInNewOutlinedIcon fontSize="small" sx={{ color: 'text.disabled' }} />
                          </Tooltip>
                        </ListItemButton>
                      </Paper>
                    ))}
                  </List>
                </CardContent>
              </Card>
            </Stack>
          ) : (
            <Card sx={{ py: 10 }}>
              <Box sx={{ textAlign: 'center' }}>
                <PersonSearchOutlinedIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1.5 }} />
                <Typography variant="body1" color="text.secondary">
                  从左侧选择一名学生查看错题本
                </Typography>
              </Box>
            </Card>
          )}
        </Grid>
      </Grid>
    </Box>
  )
}
