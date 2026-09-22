/**
 * 错因解析展示拆分(与后端 report_renderer._split_explanation 同规则)
 *
 * 仅用于呈现层排版:把"规则名 / 单步推理 / 另注说明"拆为独立条目;
 * 无任何标记时整体作为规则段,保证零信息损失。不改变任何数据与契约。
 */

export interface ExplanationParts {
  rule: string
  steps: string[]
  notes: string[]
}

const SPLIT_RE = /(单步推理|另注|补充说明|附注)[:：]?/

/** 把错因解析文本拆为 规则 / 推理 / 说明 三段(启发式) */
export function splitExplanation(text: string): ExplanationParts {
  const raw = (text || '').trim()
  if (!raw) return { rule: '', steps: [], notes: [] }
  const segments = raw.split(SPLIT_RE)
  let rule = (segments[0] ?? '').trim().replace(/[。;；\n]+$/, '')
  const steps: string[] = []
  const notes: string[] = []
  for (let i = 1; i < segments.length - 1; i += 2) {
    const label = segments[i]
    const body = (segments[i + 1] ?? '').trim().replace(/[。;；\n]+$/, '')
    if (!body) continue
    if (label === '单步推理') steps.push(body)
    else notes.push(body)
  }
  if (!rule && steps.length === 0 && notes.length === 0) rule = raw
  return { rule, steps, notes }
}
