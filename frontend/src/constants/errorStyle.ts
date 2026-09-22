/**
 * 错因分类 -> 视觉样式映射(批注核对视图,#13 增强版)
 *
 * 设计要点:
 * - 「类别 -> 颜色 + 线型」为唯一视觉来源(键与后端 canonical_type 一一对应);
 * - 每个类别独立颜色,同一类别在任何位置表现一致;语法主干保持黄橙色系、
 *   拼写为红色、更地道的表达为绿色(基本锚点),其余类别颜色可区分;
 * - 线型(实线/双线/波浪/虚线/点线/加粗/底色高亮/渐变/锯齿)作为冗余特征,
 *   兼顾色盲用户;高亮采用浅色底 + 深色文字,明暗主题均可读;
 * - 新增类别只需在 STYLE_MAP 补充一行,未知类别自动落入 OTHER 样式。
 */

export type ErrorDecoration =
  | 'solid'
  | 'double'
  | 'dashed'
  | 'dotted'
  | 'wavy'
  | 'bold'
  | 'highlight'
  | 'gradient'
  | 'zigzag'

export interface ErrorVisualStyle {
  /** 主色(下划线 / 色块 / 图例) */
  color: string
  /** 线型/呈现方式(冗余特征,兼顾色盲用户) */
  decoration: ErrorDecoration
  /** 视觉分组(图例分组与归类展示用) */
  group: string
}

/** 分类中文标签(与后端 CATEGORY_LABELS 保持一致,前端集中维护) */
export const CATEGORY_LABELS: Record<string, string> = {
  VERB_POSITION: '动词位序',
  SENTENCE_FRAME: '框型结构',
  CASE_DECLENSION: '名词变格',
  PREPOSITION: '介词搭配',
  ADJECTIVE_ENDING: '形容词词尾',
  ARTICLE: '冠词',
  PRONOUN: '代词',
  VERB_FORM: '动词形式',
  TENSE: '时态',
  SUBJECT_VERB_AGREEMENT: '主谓一致',
  NEGATION: '否定',
  PLURAL: '名词复数',
  SENTENCE_STRUCTURE: '句子成分',
  WORD_CHOICE: '词汇选择',
  SPELLING: '拼写',
  CAPITALIZATION: '大小写',
  PUNCTUATION: '标点',
  STYLE_COHERENCE: '篇章与表达',
  OTHER: '其他',
}

/** 分类键 -> 样式(19 类全覆盖;键名与后端 ErrorCategory 一致) */
const STYLE_MAP: Record<string, ErrorVisualStyle> = {
  // 语序与结构:黄橙色系 + 实线
  VERB_POSITION: { color: '#E65100', decoration: 'solid', group: '语序与结构' },
  SENTENCE_FRAME: { color: '#F57F17', decoration: 'solid', group: '语序与结构' },
  SENTENCE_STRUCTURE: { color: '#F9A825', decoration: 'solid', group: '语序与结构' },
  // 变格与搭配:蓝青色系 + 双线
  CASE_DECLENSION: { color: '#1565C0', decoration: 'double', group: '变格与搭配' },
  PREPOSITION: { color: '#0277BD', decoration: 'double', group: '变格与搭配' },
  ADJECTIVE_ENDING: { color: '#3949AB', decoration: 'double', group: '变格与搭配' },
  ARTICLE: { color: '#6A1B9A', decoration: 'double', group: '变格与搭配' },
  PRONOUN: { color: '#8E24AA', decoration: 'double', group: '变格与搭配' },
  PLURAL: { color: '#00838F', decoration: 'double', group: '变格与搭配' },
  // 动词与句式:紫粉色系 + 波浪/加粗
  VERB_FORM: { color: '#7B1FA2', decoration: 'wavy', group: '动词与句式' },
  TENSE: { color: '#AD1457', decoration: 'wavy', group: '动词与句式' },
  SUBJECT_VERB_AGREEMENT: { color: '#D81B60', decoration: 'bold', group: '动词与句式' },
  NEGATION: { color: '#5E35B1', decoration: 'wavy', group: '动词与句式' },
  // 词汇与表达:绿红对比 + 虚线/波浪
  WORD_CHOICE: { color: '#2E7D32', decoration: 'dashed', group: '词汇与表达' },
  SPELLING: { color: '#D32F2F', decoration: 'wavy', group: '词汇与表达' },
  // 格式与篇章:灰蓝青 + 高亮/点线/渐变/锯齿
  CAPITALIZATION: { color: '#546E7A', decoration: 'highlight', group: '格式与篇章' },
  PUNCTUATION: { color: '#616161', decoration: 'dotted', group: '格式与篇章' },
  STYLE_COHERENCE: { color: '#00897B', decoration: 'gradient', group: '格式与篇章' },
  OTHER: { color: '#757575', decoration: 'zigzag', group: '格式与篇章' },
}

/** 兜底样式(未知分类,保证向前兼容) */
export const DEFAULT_ERROR_STYLE: ErrorVisualStyle = {
  color: '#757575',
  decoration: 'zigzag',
  group: '格式与篇章',
}

/** 获取某分类的视觉样式 */
export function getErrorStyle(canonicalType?: string | null): ErrorVisualStyle {
  if (canonicalType && STYLE_MAP[canonicalType]) {
    return STYLE_MAP[canonicalType]
  }
  return DEFAULT_ERROR_STYLE
}

/** 分类标签(未知分类返回“其他”) */
export function getCategoryLabel(canonicalType?: string | null): string {
  if (canonicalType && CATEGORY_LABELS[canonicalType]) return CATEGORY_LABELS[canonicalType]
  return '其他'
}

/** hex -> rgba(浅色高亮用;输入非法时原样返回) */
export function withAlpha(hex: string, alphaValue: number): string {
  const match = /^#([0-9a-fA-F]{6})$/.exec(hex)
  if (!match) return hex
  const value = parseInt(match[1], 16)
  const r = (value >> 16) & 255
  const g = (value >> 8) & 255
  const b = value & 255
  return `rgba(${r}, ${g}, ${b}, ${alphaValue})`
}

/**
 * 生成"装饰"样式(直接展开到 MUI sx 中)
 *
 * - 文本装饰类(solid/double/dashed/dotted/wavy/bold)使用 text-decoration;
 * - 高亮类(highlight)使用浅色底 + 底部实线;
 * - 渐变/锯齿类使用背景层(仅占底部 3px),不遮挡文字;
 * - 均设置 printColorAdjust: exact,保证打印视图颜色一致。
 */
export function errorDecorationSx(style: ErrorVisualStyle): Record<string, unknown> {
  const base: Record<string, unknown> = {
    borderRadius: '2px',
    printColorAdjust: 'exact',
    WebkitPrintColorAdjust: 'exact',
  }
  switch (style.decoration) {
    case 'highlight':
      return {
        ...base,
        backgroundColor: withAlpha(style.color, 0.22),
        boxShadow: `inset 0 -2px 0 ${style.color}`,
      }
    case 'gradient':
      return {
        ...base,
        backgroundImage: `linear-gradient(90deg, ${style.color}, ${withAlpha(style.color, 0.15)})`,
        backgroundRepeat: 'no-repeat',
        backgroundSize: '100% 3px',
        backgroundPosition: '0 100%',
        paddingBottom: '3px',
      }
    case 'zigzag':
      return {
        ...base,
        backgroundImage: `repeating-linear-gradient(135deg, ${style.color} 0 1.5px, transparent 1.5px 4px)`,
        backgroundRepeat: 'no-repeat',
        backgroundSize: '100% 3px',
        backgroundPosition: '0 100%',
        paddingBottom: '3px',
      }
    default: {
      const cssStyle = style.decoration === 'bold' ? 'solid' : style.decoration
      const thickness = style.decoration === 'bold' ? '4px' : '2px'
      return {
        ...base,
        textDecorationLine: 'underline',
        textDecorationStyle: cssStyle,
        textDecorationColor: style.color,
        textDecorationThickness: thickness,
        textUnderlineOffset: '4px',
      }
    }
  }
}

/** 图例条目:按类别聚合(含数量),用于图例展示与筛选 */
export interface ErrorLegendEntry {
  canonicalType: string
  label: string
  color: string
  decoration: ErrorDecoration
  count: number
}

/** 生成图例(仅包含文档中实际出现的类别;按出现次数降序) */
export function buildLegend(
  items: Array<{ canonical_type?: string | null }>,
): ErrorLegendEntry[] {
  const counter = new Map<string, number>()
  items.forEach((item) => {
    const key = item.canonical_type || 'OTHER'
    counter.set(key, (counter.get(key) ?? 0) + 1)
  })
  return [...counter.entries()]
    .map(([canonicalType, count]) => {
      const style = getErrorStyle(canonicalType)
      return {
        canonicalType,
        label: getCategoryLabel(canonicalType),
        color: style.color,
        decoration: style.decoration,
        count,
      }
    })
    .sort((a, b) => b.count - a.count)
}
