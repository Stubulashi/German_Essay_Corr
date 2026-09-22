/**
 * 应用外壳布局(Material Design,全站统一规范)
 *
 * 结构:
 * - 左侧导航抽屉:品牌区 + 导航项(工作台 / 班级管理 / 队列 / 错题本 / 使用指南);
 *   桌面端(md 及以上)常驻展示,小屏自动切换为可开关的临时抽屉;
 * - 顶部应用栏:页面标题 + 演示模式指示 + 使用指南入口 + 主题切换 + 运行配置入口;
 * - 内容区:路由页面。
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  AppBar,
  Box,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  PaletteMode,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material'
import DashboardOutlinedIcon from '@mui/icons-material/DashboardOutlined'
import DarkModeOutlinedIcon from '@mui/icons-material/DarkModeOutlined'
import EventNoteOutlinedIcon from '@mui/icons-material/EventNoteOutlined'
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined'
import BarChartOutlinedIcon from '@mui/icons-material/BarChartOutlined'
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined'
import HelpCenterOutlinedIcon from '@mui/icons-material/HelpCenterOutlined'
import HelpOutlineOutlinedIcon from '@mui/icons-material/HelpOutlineOutlined'
import LightModeOutlinedIcon from '@mui/icons-material/LightModeOutlined'
import MenuBookOutlinedIcon from '@mui/icons-material/MenuBookOutlined'
import MenuOutlinedIcon from '@mui/icons-material/MenuOutlined'
import QuizOutlinedIcon from '@mui/icons-material/QuizOutlined'
import SchoolOutlinedIcon from '@mui/icons-material/SchoolOutlined'
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined'
import ScienceOutlinedIcon from '@mui/icons-material/ScienceOutlined'
import TaskAltOutlinedIcon from '@mui/icons-material/TaskAltOutlined'
import CheckOutlinedIcon from '@mui/icons-material/CheckOutlined'
import TuneOutlinedIcon from '@mui/icons-material/TuneOutlined'
import { useLocation, useNavigate } from 'react-router-dom'
import { fetchEncryptionStatus } from '../api/client'
import SettingsDialog from './SettingsDialog'
import SystemStatusBar from './SystemStatusBar'
import StartupDataDialog from './StartupDataDialog'
import UnlockDialog from './settings/UnlockDialog'
import { useHealth } from '../hooks/useHealth'
import { UI_PROFILE_LABELS, UI_PROFILES, profileEffects, useUiProfile } from '../theme'

/** 抽屉宽度 */
const DRAWER_WIDTH = 248

/** 启动选择对话框的会话跳过标记(sessionStorage 会话级) */
const STARTUP_SKIP_KEY = 'startup-data-picker-skip'

/** 导航项定义(顺序:批改 → 教学 → 统计 → 帮助) */
const NAV_ITEMS = [
  { label: '批改工作台', path: '/', icon: <DashboardOutlinedIcon /> },
  { label: '班级管理', path: '/classes', icon: <GroupsOutlinedIcon /> },
  { label: '批改队列', path: '/queue', icon: <FactCheckOutlinedIcon /> },
  { label: '学生错题本', path: '/students', icon: <MenuBookOutlinedIcon /> },
  { label: '作业台账', path: '/ledger', icon: <TaskAltOutlinedIcon /> },
  { label: '考试统计', path: '/exams', icon: <EventNoteOutlinedIcon /> },
  { label: '统计分析', path: '/statistics', icon: <BarChartOutlinedIcon /> },
  { label: '示范学习', path: '/style', icon: <SchoolOutlinedIcon /> },
  { label: '练习卷', path: '/practice', icon: <QuizOutlinedIcon /> },
  { label: '使用指南', path: '/guide', icon: <HelpCenterOutlinedIcon /> },
]

/** 路由 -> 页面标题映射 */
function pageTitle(pathname: string): string {
  if (pathname.startsWith('/review')) return '批改审阅'
  if (pathname.startsWith('/queue')) return '批改队列'
  if (pathname.startsWith('/classes')) return '班级管理'
  if (pathname.startsWith('/students')) return '学生错题本'
  if (pathname.startsWith('/ledger')) return '作业台账'
  if (pathname.startsWith('/exams')) return '考试统计'
  if (pathname.startsWith('/statistics')) return '统计分析'
  if (pathname.startsWith('/style')) return '示范学习'
  if (pathname.startsWith('/practice')) return '练习卷'
  if (pathname.startsWith('/guide')) return '使用指南'
  return '批改工作台'
}

interface Props {
  mode: PaletteMode
  onToggleMode: () => void
  children: ReactNode
}

export default function AppShell({ mode, onToggleMode, children }: Props) {
  const navigate = useNavigate()
  const location = useLocation()
  const [settingsOpen, setSettingsOpen] = useState(false)
  // 小屏时抽屉改为临时弹出(响应式适配)
  const isMobile = useMediaQuery((theme) => theme.breakpoints.down('md'))
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  // 全局健康状态:演示模式提示条等
  const health = useHealth()
  // UI 档位(均衡/效率优先/高级):即时生效并持久化
  const { profile, changeProfile } = useUiProfile()
  const effects = profileEffects(profile, mode)
  const [profileMenuAnchor, setProfileMenuAnchor] = useState<null | HTMLElement>(null)

  // premium 档:路径切换时重放内容区淡入动画(不 remount,避免页面状态丢失)
  const mainRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (profile !== 'premium') return
    const node = mainRef.current
    if (!node) return
    node.classList.remove('premium-page-enter')
    void node.offsetWidth // 强制重排,保证动画可重放
    node.classList.add('premium-page-enter')
  }, [location.pathname, profile])

  // ---------- 启动数据门禁(分阶段:等待健康 → 选择数据 → 加密检查 → 按需解锁 → 工作台) ----------
  // 未完成前不挂载子页面 → 启动零数据请求;加密状态仅在用户完成选择后才检查,
  // 避免启动阶段弹解锁窗抢占界面。
  const [startupStage, setStartupStage] = useState<'auto' | 'checking' | 'unlock' | 'done'>('auto')
  const [healthWaitExpired, setHealthWaitExpired] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setHealthWaitExpired(true), 3000)
    return () => window.clearTimeout(timer)
  }, [])
  const startupSkipped = sessionStorage.getItem(STARTUP_SKIP_KEY) === '1'

  /** 启动流程收口:此时才检查加密状态;需要则进入解锁步骤,否则直接进入工作台 */
  const finishStartup = async (skipSession: boolean) => {
    if (skipSession) sessionStorage.setItem(STARTUP_SKIP_KEY, '1')
    setStartupStage('checking')
    try {
      const status = await fetchEncryptionStatus()
      if (status.has_password && status.locked) {
        setStartupStage('unlock')
        return
      }
    } catch {
      /* 状态查询异常时放行,由会话中数据请求的 423 事件兜底 */
    }
    setStartupStage('done')
  }

  /** 受控解锁完成:本次会话不再重复启动流程 */
  const handleStartupUnlocked = () => {
    sessionStorage.setItem(STARTUP_SKIP_KEY, '1')
    setStartupStage('done')
  }

  const gateMode: 'none' | 'wait' | 'picker' | 'checking' | 'unlock' =
    startupStage === 'checking'
      ? 'checking'
      : startupStage === 'unlock'
        ? 'unlock'
        : startupStage === 'done' || startupSkipped
          ? 'none'
          : health === null
            ? (healthWaitExpired ? 'none' : 'wait')
            : health.startup_data_picker !== false
              ? 'picker'
              : 'none'

  /** 导航跳转(小屏时顺便收起抽屉) */
  const goTo = (path: string) => {
    navigate(path)
    if (isMobile) setMobileNavOpen(false)
  }

  /** 抽屉内容(桌面常驻 / 小屏临时共用) */
  const drawerContent = (
    <>
      {/* 品牌区 */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.2, px: 1.5, pb: 2 }}>
        <Box
          sx={{
            width: 40,
            height: 40,
            borderRadius: 2.5,
            display: 'grid',
            placeItems: 'center',
            background: effects.logoGradientEnabled
              ? 'linear-gradient(135deg, #3F51B5 0%, #7B8AD3 100%)'
              : mode === 'light'
                ? '#3F51B5'
                : '#7986CB',
            color: '#fff',
          }}
        >
          <MenuBookOutlinedIcon fontSize="small" />
        </Box>
        <Box>
          <Typography variant="subtitle1" lineHeight={1.2}>
            德语作文批改
          </Typography>
          <Typography variant="caption" color="text.secondary">
            German Essay Corrector
          </Typography>
        </Box>
      </Box>

      <Divider sx={{ mb: 1.5 }} />

      {/* 导航列表 */}
      <List disablePadding sx={{ flex: 1 }}>
        {NAV_ITEMS.map((item) => {
          const selected =
            item.path === '/'
              ? location.pathname === '/'
              : location.pathname.startsWith(item.path)
          return (
            <ListItemButton
              key={item.path}
              selected={selected}
              onClick={() => goTo(item.path)}
              sx={{
                borderRadius: 2.5,
                mb: 0.5,
                '&.Mui-selected': {
                  bgcolor: 'primary.main',
                  color: 'primary.contrastText',
                  '& .MuiListItemIcon-root': { color: 'inherit' },
                  '&:hover': { bgcolor: 'primary.dark' },
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 40 }}>{item.icon}</ListItemIcon>
              <ListItemText primary={item.label} primaryTypographyProps={{ fontWeight: 500 }} />
            </ListItemButton>
          )
        })}
      </List>

      {/* 底部说明 */}
      <Typography variant="caption" color="text.secondary" sx={{ px: 1.5 }}>
        双管线架构 · 本地 / 云端
      </Typography>
    </>
  )

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      {(gateMode === 'wait' || gateMode === 'checking' || gateMode === 'unlock') && (
        <Box sx={{ width: '100%', minHeight: '100vh', display: 'grid', placeItems: 'center', p: 2 }}>
          <Box sx={{ textAlign: 'center' }}>
            <CircularProgress />
            {gateMode === 'checking' && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
                正在检查数据状态…
              </Typography>
            )}
          </Box>
        </Box>
      )}
      {gateMode === 'picker' && (
        <Box sx={{ width: '100%', minHeight: '100vh', display: 'grid', placeItems: 'center', p: 2 }}>
          <StartupDataDialog onDone={finishStartup} />
        </Box>
      )}
      {gateMode === 'none' && (
      <>
      {/* ================= 左侧导航(响应式) ================= */}
      {isMobile ? (
        <Drawer
          variant="temporary"
          open={mobileNavOpen}
          onClose={() => setMobileNavOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{ '& .MuiDrawer-paper': { width: DRAWER_WIDTH, px: 1.5, py: 2, display: 'flex', flexDirection: 'column' } }}
        >
          {drawerContent}
        </Drawer>
      ) : (
        <Drawer
          variant="permanent"
          sx={{
            width: DRAWER_WIDTH,
            flexShrink: 0,
            '& .MuiDrawer-paper': {
              width: DRAWER_WIDTH,
              boxSizing: 'border-box',
              borderRight: 1,
              borderColor: 'divider',
              px: 1.5,
              py: 2,
              display: 'flex',
              flexDirection: 'column',
            },
          }}
        >
          {drawerContent}
        </Drawer>
      )}

      {/* ================= 主区域 ================= */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <AppBar
          position="sticky"
          color="inherit"
          elevation={0}
          sx={{
            bgcolor: 'background.paper',
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Toolbar sx={{ gap: 1 }}>
            {isMobile && (
              <IconButton
                edge="start"
                aria-label="打开导航"
                onClick={() => setMobileNavOpen(true)}
              >
                <MenuOutlinedIcon />
              </IconButton>
            )}
            <Typography variant="h6" sx={{ flexGrow: 1, minWidth: 0 }} noWrap>
              {pageTitle(location.pathname)}
            </Typography>

            {/* 演示模式提示 */}
            {health?.mock_mode && (
              <Chip
                size="small"
                color="warning"
                variant="outlined"
                label="演示模式(Mock)"
                icon={<ScienceOutlinedIcon />}
              />
            )}

            {/* 使用指南入口 */}
            <Tooltip title="使用指南">
              <IconButton aria-label="使用指南" onClick={() => navigate('/guide')}>
                <HelpOutlineOutlinedIcon />
              </IconButton>
            </Tooltip>

            {/* 外观档位(均衡/效率优先/高级;即时生效并持久化) */}
            <Tooltip title={`外观档位:${UI_PROFILE_LABELS[profile]}`}>
              <IconButton
                aria-label="外观档位"
                onClick={(event) => setProfileMenuAnchor(event.currentTarget)}
              >
                <TuneOutlinedIcon />
              </IconButton>
            </Tooltip>

            {/* 主题切换 */}
            <Tooltip title={mode === 'light' ? '切换深色模式' : '切换浅色模式'}>
              <IconButton onClick={onToggleMode}>
                {mode === 'light' ? <DarkModeOutlinedIcon /> : <LightModeOutlinedIcon />}
              </IconButton>
            </Tooltip>

            {/* 设置(展示运行配置) */}
            <Tooltip title="运行配置">
              <IconButton onClick={() => setSettingsOpen(true)}>
                <SettingsOutlinedIcon />
              </IconButton>
            </Tooltip>
          </Toolbar>
        </AppBar>

        {/* 外观档位菜单 */}
        <Menu
          anchorEl={profileMenuAnchor}
          open={Boolean(profileMenuAnchor)}
          onClose={() => setProfileMenuAnchor(null)}
        >
          {UI_PROFILES.map((item) => (
            <MenuItem
              key={item}
              selected={item === profile}
              onClick={() => {
                changeProfile(item)
                setProfileMenuAnchor(null)
              }}
            >
              <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 132 }}>
                <Typography variant="body2" sx={{ flex: 1 }}>
                  {UI_PROFILE_LABELS[item]}
                </Typography>
                {item === profile && <CheckOutlinedIcon fontSize="small" />}
              </Stack>
            </MenuItem>
          ))}
        </Menu>

        {/* 全局等待态(真实进度 + 真实自检;无活跃任务时自动隐藏) */}
        <SystemStatusBar />

        {/* 内容区(premium 档在路径切换时叠加淡入过渡) */}
        <Box
          ref={mainRef}
          component="main"
          sx={{
            flex: 1,
            p: { xs: 2, md: 3 },
            background: effects.workspaceBackground,
          }}
        >
          {children}
        </Box>
      </Box>
      </>
      )}

      {/* 设置中心(窗口式) */}
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        health={health}
        onUiProfileChange={(next) => changeProfile(next, { syncBackend: false })}
      />

      {/* 全局解锁弹窗:启动阶段受控(选择数据后按需解锁);会话中由 423 事件驱动 */}
      <UnlockDialog
        open={gateMode === 'unlock' ? true : undefined}
        onUnlocked={handleStartupUnlocked}
      />
    </Box>
  )
}
