/**
 * Material Design 主题配置(MUI v6)—— 全站统一设计 Token(唯一来源)
 *
 * 设计语言:
 * - 主色:靛蓝(Indigo),沉稳专业,适合教师办公场景;
 * - 辅色:琥珀(Amber),用于高亮与强调;
 * - 圆角体系:卡片 16px / 对话框 20px / 控件 10px / 标签 8px;
 * - 支持明 / 暗两种模式(mode: light | dark);
 * - 三档 UI 档位(均衡 balanced / 极简 efficiency / 高级 premium):
 *   全部由本文件同一套 Token 派生(见 PROFILE 变体),页面不感知档位差异;
 *   极简档:灰阶调色板(仅保留低饱和语义色),零动效/无波纹/无阴影/纯色背景,
 *   用留白、描边、字重与间距区分层级;
 *
 * 统一约定(所有页面与组件必须遵循,详见 docs/UI规范.md):
 * - 页面结构:PageHeader(标题 / 说明 / 徽章 / 操作)+ 内容卡片(栅格间距 3 = 24px);
 * - 控件尺寸:密集表单统一 size="small";页面主操作为 large contained,头部操作为 small + color="inherit";
 * - 控件变体:卡片与纸张统一"描边 + 无抬升";按钮统一无阴影、圆角 10、字重 600;
 * - 文案规范:加载态"正在…";空状态"暂无…";错误统一经 extractErrorMessage 提取后端 detail。
 */

import { createContext, useContext } from 'react'
import { createTheme } from '@mui/material/styles'
import type { PaletteMode } from '@mui/material'
import type { ThemeOptions } from '@mui/material/styles'

// =============================================================
// 三档 UI 档位(同一套 Token 派生;详见 docs/UI规范.md 第九章)
// - balanced  均衡:既有表现(默认,零变化);
// - efficiency 极简:灰阶调色板(仅保留低饱和语义色)、动效/过渡归零、关闭波纹、
//   阴影全无、去除渐变、图表禁动画、Chip 去填充——最简界面,低配设备首选;
// - premium   高级:尊享质感——顺滑曲线、多层光影、悬停微交互、页面淡入。
// =============================================================
export type UiProfile = 'balanced' | 'efficiency' | 'premium'

export const UI_PROFILES: UiProfile[] = ['balanced', 'efficiency', 'premium']
export const UI_PROFILE_LABELS: Record<UiProfile, string> = {
  balanced: '均衡',
  efficiency: '极简',
  premium: '高级',
}
/** localStorage 缓存键(启动即时生效;后端设置为权威,双轨同步) */
export const UI_PROFILE_KEY = 'corrector-ui-profile'

export function normalizeUiProfile(value: unknown): UiProfile {
  return value === 'efficiency' || value === 'premium' ? value : 'balanced'
}

const PREMIUM_EASE = 'cubic-bezier(0.22, 1, 0.36, 1)'

/** 高级档多层光影(环境影 + 主色微光;避免大面积高模糊,保持低开销) */
function premiumShadows(isLight: boolean): string[] {
  const base = isLight ? '26, 29, 43' : '0, 0, 0'
  const tint = isLight ? '63, 81, 181' : '121, 134, 203'
  const level = (index: number): string => {
    if (index === 0) return 'none'
    if (index <= 1) return `0 1px 2px rgba(${base},0.05), 0 2px 8px -2px rgba(${tint},0.10)`
    if (index <= 3) return `0 2px 4px rgba(${base},0.06), 0 6px 20px -6px rgba(${tint},0.14)`
    if (index <= 7) return `0 6px 24px -8px rgba(${base},0.14), 0 2px 6px rgba(${base},0.06)`
    if (index <= 15) return `0 14px 40px -12px rgba(${base},0.20), 0 4px 12px rgba(${base},0.08)`
    return `0 24px 64px -16px rgba(${base},0.26), 0 8px 20px rgba(${base},0.10)`
  }
  return Array.from({ length: 25 }, (_, index) => level(index))
}

interface ProfileVariant {
  transitions?: ThemeOptions['transitions']
  shadows?: string[]
  /** 档位专属调色板(极简档灰阶化;缺省 = 基座品牌色板) */
  palette?: Record<string, unknown>
  /** 注入 CssBaseline 的补充样式(如极简档的全局动效归零/关波纹) */
  globalCss: Record<string, Record<string, unknown>>
  buttonRoot: Record<string, unknown>
  iconButtonRoot: Record<string, unknown>
  menuItemRoot: Record<string, unknown>
  chipRoot: Record<string, unknown>
  cardRoot: Record<string, unknown>
  cardShadow: (isLight: boolean) => string
}

/** 极简档灰阶调色板:装饰色全部去色;仅保留低饱和语义色(成/败/警示可辨) */
function efficiencyPalette(isLight: boolean): Record<string, unknown> {
  return {
    primary: {
      main: isLight ? '#37474F' : '#90A4AE',
      light: '#546E7A',
      dark: '#263238',
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: isLight ? '#546E7A' : '#B0BEC5',
      light: '#78909C',
      dark: '#37474F',
    },
    success: { main: isLight ? '#4E6E52' : '#81A885' },
    warning: { main: isLight ? '#8A6D3B' : '#C6A667' },
    error: { main: isLight ? '#A34343' : '#C97A7A' },
    info: { main: isLight ? '#546E7A' : '#7FA1B5' },
    background: {
      default: isLight ? '#FAFAFA' : '#101214',
      paper: isLight ? '#FFFFFF' : '#16181B',
    },
    divider: isLight ? 'rgba(0, 0, 0, 0.15)' : 'rgba(255, 255, 255, 0.14)',
    text: {
      primary: isLight ? '#1A1A1A' : '#E8E8E8',
      secondary: isLight ? '#5C5C5C' : '#9E9E9E',
    },
  }
}

function buildProfileVariant(profile: UiProfile, isLight: boolean): ProfileVariant {
  if (profile === 'efficiency') {
    return {
      transitions: {
        duration: {
          shortest: 0,
          shorter: 0,
          short: 0,
          standard: 0,
          complex: 0,
          enteringScreen: 0,
          leavingScreen: 0,
        },
      },
      shadows: Array.from({ length: 25 }, () => 'none'),
      // 灰阶调色板:装饰色全部去色,仅保留低饱和语义色(成/败/警示可辨)
      palette: efficiencyPalette(isLight),
      globalCss: {
        // 兜底:任何第三方动画/过渡一并归零(替代 prefers-reduced-motion)
        '*, *::before, *::after': {
          transitionDuration: '0.001ms !important',
          animationDuration: '0.001ms !important',
          animationIterationCount: '1 !important',
        },
        '[class*="MuiTouchRipple"]': { display: 'none' },
      },
      buttonRoot: {},
      iconButtonRoot: {},
      menuItemRoot: {},
      // 默认色 Chip 去填充(转为描边);语义色 Chip(成功/错误等)保持不变
      chipRoot: {
        '&.MuiChip-filled.MuiChip-colorDefault': {
          backgroundColor: 'transparent',
          border: '1px solid',
          borderColor: 'divider',
          color: 'text.primary',
        },
      },
      cardRoot: {},
      cardShadow: () => 'none',
    }
  }
  if (profile === 'premium') {
    const shadows = premiumShadows(isLight)
    return {
      transitions: {
        duration: {
          shortest: 200,
          shorter: 240,
          short: 280,
          standard: 320,
          complex: 420,
          enteringScreen: 300,
          leavingScreen: 240,
        },
        easing: {
          easeInOut: PREMIUM_EASE,
          easeOut: PREMIUM_EASE,
          easeIn: 'cubic-bezier(0.55, 0.06, 0.68, 0.19)',
          sharp: 'cubic-bezier(0.4, 0, 0.2, 1)',
        },
      },
      shadows,
      globalCss: {
        // 键盘可达性:柔和焦点光晕 + 品牌色选中文本(细节质感)
        ':focus-visible': {
          outline: `2px solid ${isLight ? 'rgba(63, 81, 181, 0.45)' : 'rgba(121, 134, 203, 0.55)'}`,
          outlineOffset: 2,
        },
        '::selection': {
          backgroundColor: isLight ? 'rgba(63, 81, 181, 0.16)' : 'rgba(121, 134, 203, 0.28)',
        },
        // 页面内容过渡:AppShell 在路径切换时重放该类(premium 专属,动画结束不保留样式)
        '@keyframes premiumPageEnter': {
          from: { opacity: 0, transform: 'translateY(10px)' },
          to: { opacity: 1, transform: 'translateY(0)' },
        },
        '.premium-page-enter': {
          animation: `premiumPageEnter 420ms ${PREMIUM_EASE}`,
        },
      },
      buttonRoot: {
        // 微交互:悬停轻抬 1px + 按下回弹;contained 附主色微光
        transition: `transform 280ms ${PREMIUM_EASE}, box-shadow 280ms ${PREMIUM_EASE}, background-color 240ms ${PREMIUM_EASE}`,
        '&:hover:not(:disabled)': { transform: 'translateY(-1px)' },
        '&:active:not(:disabled)': { transform: 'translateY(0) scale(0.985)' },
        '&.MuiButton-contained:not(:disabled)': {
          boxShadow: `0 2px 14px -6px ${isLight ? 'rgba(63, 81, 181, 0.45)' : 'rgba(121, 134, 203, 0.50)'}, 0 1px 2px rgba(26, 29, 43, 0.10)`,
        },
      },
      iconButtonRoot: {
        transition: `background-color 240ms ${PREMIUM_EASE}, transform 240ms ${PREMIUM_EASE}`,
        '&:hover': { transform: 'scale(1.05)' },
        '&:active': { transform: 'scale(0.97)' },
      },
      menuItemRoot: {
        transition: `background-color 220ms ${PREMIUM_EASE}`,
      },
      chipRoot: {},
      cardRoot: {
        // 卡片:影阶随动 + 边框主色微亮(克制,不做位移)
        transition: `box-shadow 320ms ${PREMIUM_EASE}, border-color 320ms ${PREMIUM_EASE}`,
        '&:hover': {
          boxShadow: shadows[8],
          borderColor: isLight ? 'rgba(63, 81, 181, 0.22)' : 'rgba(121, 134, 203, 0.32)',
        },
      },
      cardShadow: (light) =>
        light
          ? `${shadows[4]}, inset 0 1px 0 rgba(255, 255, 255, 0.85)`
          : shadows[4],
    }
  }
  // balanced:完全保持既有表现
  return {
    globalCss: {},
    buttonRoot: {},
    iconButtonRoot: {},
    menuItemRoot: {},
    chipRoot: {},
    cardRoot: {},
    cardShadow: (light) =>
      light
        ? '0 1px 2px rgba(26, 29, 43, 0.04), 0 8px 24px -12px rgba(63, 81, 181, 0.12)'
        : 'none',
  }
}

/** 页面级视觉效果(由几个少量消费点读取,避免样式分叉) */
export interface UiProfileEffects {
  workspaceBackground: string | undefined
  logoGradientEnabled: boolean
  chartAnimationEnabled: boolean
}

export function profileEffects(profile: UiProfile, mode: PaletteMode): UiProfileEffects {
  const isLight = mode === 'light'
  if (profile === 'efficiency') {
    return { workspaceBackground: undefined, logoGradientEnabled: false, chartAnimationEnabled: false }
  }
  if (profile === 'premium') {
    return {
      workspaceBackground: isLight
        ? 'radial-gradient(1400px 480px at 18% -12%, rgba(63,81,181,0.07), transparent), radial-gradient(1000px 420px at 85% -8%, rgba(245,158,11,0.05), transparent), #F4F6FB'
        : 'radial-gradient(1400px 480px at 18% -12%, rgba(121,134,203,0.09), transparent), radial-gradient(1000px 420px at 85% -8%, rgba(255,183,77,0.05), transparent), #0F1115',
      logoGradientEnabled: true,
      chartAnimationEnabled: true,
    }
  }
  return {
    workspaceBackground: isLight
      ? 'radial-gradient(1200px 400px at 20% -10%, rgba(63,81,181,0.06), transparent), #F4F6FB'
      : undefined,
    logoGradientEnabled: true,
    chartAnimationEnabled: true,
  }
}

/** 档位上下文(由 App 提供;AppShell/图表页等少量消费点读取) */
export interface UiProfileContextValue {
  profile: UiProfile
  /** 切换档位:默认同步本地缓存并静默回写后端设置(syncBackend=false 用于设置对话框已保存后的本地同步) */
  changeProfile: (profile: UiProfile, options?: { syncBackend?: boolean }) => void
}

export const UiProfileContext = createContext<UiProfileContextValue>({
  profile: 'balanced',
  changeProfile: () => {},
})

export const useUiProfile = () => useContext(UiProfileContext)

/** 生成指定模式的 Material 主题(含三档 UI 档位;默认 balanced = 既有表现) */
export function getTheme(mode: PaletteMode, profile: UiProfile = 'balanced') {
  const isLight = mode === 'light'
  const variant = buildProfileVariant(profile, isLight)

  // 统一描边色(卡片 / 纸张 / 手风琴 / 对话框共用)
  const borderColor = isLight ? 'rgba(63, 81, 181, 0.10)' : 'rgba(255, 255, 255, 0.08)'
  const hairline = `1px solid ${borderColor}`

  // 基座调色板(品牌色);极简档由变体的灰阶色板整体替换
  const basePalette = {
    mode,
    primary: {
      main: isLight ? '#3F51B5' : '#7986CB',
      light: '#7B8AD3',
      dark: '#2C3A8C',
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: isLight ? '#F59E0B' : '#FFB74D',
      light: '#FBBF24',
      dark: '#B45309',
    },
    success: { main: isLight ? '#2E7D32' : '#66BB6A' },
    warning: { main: isLight ? '#ED6C02' : '#FFA726' },
    error: { main: isLight ? '#D32F2F' : '#EF5350' },
    info: { main: isLight ? '#0288D1' : '#4FC3F7' },
    background: {
      default: isLight ? '#F4F6FB' : '#0F1115',
      paper: isLight ? '#FFFFFF' : '#171A21',
    },
    divider: borderColor,
    text: {
      primary: isLight ? '#1A1D2B' : '#ECEDF2',
      secondary: isLight ? '#5A6072' : '#9AA0B0',
    },
  }

  return createTheme({
    // 注意:不得向 createTheme 传入 undefined——MUI 会把默认 transitions/shadows
    // 整体覆盖为 undefined,运行期读取 theme.shadows[n] 将抛 TypeError 导致首屏白屏;
    // balanced 档“不传”这两键,由 MUI 默认值兜底(与既有表现逐字一致)。
    ...(variant.transitions ? { transitions: variant.transitions } : {}),
    ...(variant.shadows ? { shadows: variant.shadows as ThemeOptions['shadows'] } : {}),
    palette: (variant.palette ? { mode, ...variant.palette } : basePalette) as ThemeOptions['palette'],
    shape: {
      borderRadius: 12,
    },
    typography: {
      fontFamily: [
        'Roboto',
        '"Noto Sans SC"',
        '-apple-system',
        'BlinkMacSystemFont',
        '"Segoe UI"',
        'sans-serif',
      ].join(','),
      h4: { fontWeight: 700, letterSpacing: '-0.02em' },
      h5: { fontWeight: 700, letterSpacing: '-0.01em' },
      h6: { fontWeight: 600 },
      subtitle1: { fontWeight: 600 },
      subtitle2: { fontWeight: 600 },
      button: { fontWeight: 600, textTransform: 'none' },
      body2: { lineHeight: 1.65 },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            // 平滑滚动与更精致的滚动条 + 字体平滑
            scrollbarWidth: 'thin',
            WebkitFontSmoothing: 'antialiased',
            MozOsxFontSmoothing: 'grayscale',
          },
          ...variant.globalCss,
        },
      },
      // ---------- 按钮与图标按钮 ----------
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: {
            borderRadius: 10,
            paddingInline: 18,
            paddingBlock: 8,
            ...variant.buttonRoot,
          },
          sizeSmall: { paddingInline: 12, paddingBlock: 6 },
        },
      },
      MuiIconButton: {
        styleOverrides: {
          root: { borderRadius: 10, ...variant.iconButtonRoot },
        },
      },
      MuiToggleButton: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 600,
          },
        },
      },
      MuiToggleButtonGroup: {
        styleOverrides: {
          root: { borderRadius: 10 },
        },
      },
      // ---------- 输入类控件(统一圆角) ----------
      MuiOutlinedInput: {
        styleOverrides: {
          root: { borderRadius: 10 },
        },
      },
      // ---------- 容器:纸张 / 卡片 / 手风琴 ----------
      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            border: hairline,
          },
        },
      },
      MuiCard: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          root: {
            borderRadius: 16,
            border: hairline,
            boxShadow: variant.cardShadow(isLight),
            ...variant.cardRoot,
          },
        },
      },
      MuiAccordion: {
        defaultProps: { disableGutters: true, elevation: 0 },
        styleOverrides: {
          root: {
            border: hairline,
            borderRadius: 12,
            '&:before': { display: 'none' },
            '&.Mui-expanded': { margin: 0 },
          },
        },
      },
      MuiAccordionSummary: {
        styleOverrides: {
          root: { borderRadius: 12, minHeight: 48 },
        },
      },
      // ---------- 对话框 / 菜单 ----------
      MuiDialog: {
        styleOverrides: {
          paper: { borderRadius: 20 },
        },
      },
      MuiDialogTitle: {
        styleOverrides: {
          root: { fontSize: 18, fontWeight: 600 },
        },
      },
      MuiMenu: {
        styleOverrides: {
          paper: { borderRadius: 12, marginTop: 4 },
        },
      },
      MuiMenuItem: {
        styleOverrides: {
          root: { fontSize: 14, borderRadius: 8, ...variant.menuItemRoot },
        },
      },
      // ---------- 反馈类:提示 / 进度 / 提示气泡 ----------
      MuiAlert: {
        styleOverrides: {
          root: { borderRadius: 12 },
        },
      },
      MuiLinearProgress: {
        styleOverrides: {
          root: { borderRadius: 999 },
        },
      },
      MuiTooltip: {
        styleOverrides: {
          tooltip: { fontSize: 12, borderRadius: 8 },
        },
      },
      // ---------- 数据展示:标签 / 表格 / 选项卡 ----------
      MuiChip: {
        styleOverrides: {
          root: { borderRadius: 8, fontWeight: 500, ...variant.chipRoot },
        },
      },
      MuiTableCell: {
        styleOverrides: {
          head: { fontWeight: 600, whiteSpace: 'nowrap' },
        },
      },
      MuiTab: {
        styleOverrides: {
          root: { textTransform: 'none', fontWeight: 600 },
        },
      },
    },
  })
}
