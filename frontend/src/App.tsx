/**
 * 应用根组件:主题(明/暗) + 布局外壳 + 路由
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { PaletteMode } from '@mui/material'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { Navigate, Route, Routes } from 'react-router-dom'
import AnswerSheetPage from './pages/AnswerSheetPage'
import AnswerSheetContinuationPage from './pages/AnswerSheetContinuationPage'
import HandwritingCardPage from './pages/HandwritingCardPage'
import AppShell from './components/AppShell'
import ClassHubPage from './pages/ClassHubPage'
import CorrectionPage from './pages/CorrectionPage'
import ExamDetailPage from './pages/ExamDetailPage'
import ExamsPage from './pages/ExamsPage'
import GuidePage from './pages/GuidePage'
import LedgerPage from './pages/LedgerPage'
import PracticeDetailPage from './pages/PracticeDetailPage'
import PracticePage from './pages/PracticePage'
import PrintBatchPage from './pages/PrintBatchPage'
import PrintPracticePage from './pages/PrintPracticePage'
import PrintViewPage from './pages/PrintViewPage'
import QueuePage from './pages/QueuePage'
import ReviewPage from './pages/ReviewPage'
import StatsPage from './pages/StatsPage'
import StudentsPage from './pages/StudentsPage'
import StyleLearningPage from './pages/StyleLearningPage'
import { fetchSettings, updateSettings } from './api/client'
import {
  getTheme,
  normalizeUiProfile,
  UI_PROFILE_KEY,
  UiProfileContext,
  type UiProfile,
} from './theme'

/** 主题模式持久化 key */
const MODE_KEY = 'corrector-theme-mode'

function App() {
  // 从 localStorage 恢复主题模式(默认浅色)
  const [mode, setMode] = useState<PaletteMode>(() => {
    const saved = localStorage.getItem(MODE_KEY)
    return saved === 'dark' ? 'dark' : 'light'
  })

  const toggleMode = () => {
    setMode((prev) => {
      const next = prev === 'light' ? 'dark' : 'light'
      localStorage.setItem(MODE_KEY, next)
      return next
    })
  }

  // ---------- UI 档位(均衡/效率优先/高级):localStorage 即时缓存 + 后端设置权威 ----------
  const [uiProfile, setUiProfile] = useState<UiProfile>(() =>
    normalizeUiProfile(localStorage.getItem(UI_PROFILE_KEY)),
  )

  const changeProfile = useCallback(
    (next: UiProfile, options?: { syncBackend?: boolean }) => {
      setUiProfile(next)
      localStorage.setItem(UI_PROFILE_KEY, next)
      if (options?.syncBackend !== false) {
        // 静默回写后端设置(离线/无令牌时保持本地值,不阻塞界面)
        void updateSettings({ ui_profile: next }).catch(() => undefined)
      }
    },
    [],
  )

  // 启动校准:后端设置为权威(读取失败则保持本地缓存)
  useEffect(() => {
    fetchSettings()
      .then((view) => {
        const field = view.groups
          .flatMap((group) => group.fields)
          .find((item) => item.key === 'UI_PROFILE')
        const backend = normalizeUiProfile(field?.value)
        setUiProfile((prev) => {
          if (backend !== prev) localStorage.setItem(UI_PROFILE_KEY, backend)
          return backend
        })
      })
      .catch(() => undefined)
  }, [])

  const theme = useMemo(() => getTheme(mode, uiProfile), [mode, uiProfile])
  const profileContext = useMemo(
    () => ({ profile: uiProfile, changeProfile }),
    [uiProfile, changeProfile],
  )

  return (
    <ThemeProvider theme={theme}>
      <UiProfileContext.Provider value={profileContext}>
      <CssBaseline />
      <Routes>
        {/* 打印视图:独立于 AppShell 外壳(渲染即最终纸面效果,#12 方向二) */}
        <Route path="/print/batch" element={<PrintBatchPage />} />
        <Route path="/print/answer-sheet" element={<AnswerSheetPage />} />
        <Route path="/print/answer-sheet-continuation" element={<AnswerSheetContinuationPage />} />
        <Route path="/print/handwriting-card" element={<HandwritingCardPage />} />
        <Route path="/print/practice/:sheetId" element={<PrintPracticePage />} />
        <Route path="/print/:taskId" element={<PrintViewPage />} />
        {/* 常规页面:带侧边栏外壳 */}
        <Route
          path="*"
          element={
            <AppShell mode={mode} onToggleMode={toggleMode}>
              <Routes>
                {/* 工作台:上传 + 参数配置 */}
                <Route path="/" element={<CorrectionPage />} />
                {/* 审阅页:左右分屏(原图 | 报告 / OCR 复核) */}
                <Route path="/review/:taskId" element={<ReviewPage />} />
                {/* 队列页:批量任务与历史 */}
                <Route path="/queue" element={<QueuePage />} />
                {/* 班级管理:班级维度功能统一入口(含共性错因分析) */}
                <Route path="/classes" element={<ClassHubPage />} />
                <Route path="/classes/:section" element={<ClassHubPage />} />
                {/* 旧路由兼容:班级分析已并入班级管理 */}
                <Route path="/analytics" element={<Navigate to="/classes/analytics" replace />} />
                {/* 学生错题本:个体画像与复现错因 */}
                <Route path="/students" element={<StudentsPage />} />
                {/* 作业台账:登记项与批量登记/汇总 */}
                <Route path="/ledger" element={<LedgerPage />} />
                {/* 考试统计:考试列表与详情(考卷识别 + 报告) */}
                <Route path="/exams" element={<ExamsPage />} />
                <Route path="/exams/:examId" element={<ExamDetailPage />} />
                {/* 统计分析:成绩总表 / 分布 / 排行 / 趋势(三源聚合) */}
                <Route path="/statistics" element={<StatsPage />} />
                {/* 示范学习:示例范文风格归纳与迁移 */}
                <Route path="/style" element={<StyleLearningPage />} />
                {/* 练习卷:依据历史作业错因生成练习 + 标准答案 */}
                <Route path="/practice" element={<PracticePage />} />
                <Route path="/practice/:sheetId" element={<PracticeDetailPage />} />
                {/* 使用指南:全功能说明(搜索 + 目录跳转) */}
                <Route path="/guide" element={<GuidePage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppShell>
          }
        />
      </Routes>
      </UiProfileContext.Provider>
    </ThemeProvider>
  )
}

export default App
