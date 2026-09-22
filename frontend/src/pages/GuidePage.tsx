/**
 * 使用指南页(全功能说明,支持搜索与折叠)
 *
 * 功能:
 * - 左侧目录:按模块跳转(点击展开并滚动定位);
 * - 顶部搜索:按关键字过滤功能点(标题 / 作用 / 场景 / 步骤 / 参数 / 注意事项全文检索);
 * - 主体内容:按模块分组的手风琴面板,内含每个功能点的
 *   作用 / 适用场景 / 操作步骤 / 关键参数 / 注意事项。
 *
 * 说明:内容为纯前端结构化数据(src/guide/guideContent.ts),无后端依赖。
 */

import { useMemo, useState } from 'react'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  InputAdornment,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import ExpandMoreOutlinedIcon from '@mui/icons-material/ExpandMoreOutlined'
import MenuBookOutlinedIcon from '@mui/icons-material/MenuBookOutlined'
import SearchOutlinedIcon from '@mui/icons-material/SearchOutlined'
import UnfoldLessOutlinedIcon from '@mui/icons-material/UnfoldLessOutlined'
import UnfoldMoreOutlinedIcon from '@mui/icons-material/UnfoldMoreOutlined'
import PageHeader from '../components/PageHeader'
import { GUIDE_SECTIONS } from '../guide/guideContent'
import type { GuideItem } from '../guide/guideContent'

/** 单个功能点匹配(全文检索) */
function itemMatches(item: GuideItem, q: string): boolean {
  const haystack = [
    item.title,
    item.purpose,
    item.scenes,
    ...item.steps,
    ...(item.notes ?? []),
    ...(item.params ?? []).flatMap((p) => [p.name, p.meaning]),
  ]
    .join('\n')
    .toLowerCase()
  return haystack.includes(q)
}

/** 功能点详情卡片(作用 / 场景 / 步骤 / 参数 / 注意事项) */
function GuideItemBlock({ item }: { item: GuideItem }) {
  return (
    <Box>
      <Typography variant="subtitle1" gutterBottom>
        {item.title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {item.purpose}
      </Typography>

      <Box sx={{ mb: 1 }}>
        <Typography variant="caption" color="text.disabled" sx={{ fontWeight: 600 }}>
          适用场景
        </Typography>
        <Typography variant="body2">{item.scenes}</Typography>
      </Box>

      <Box sx={{ mb: item.params || item.notes ? 1 : 0 }}>
        <Typography variant="caption" color="text.disabled" sx={{ fontWeight: 600 }}>
          操作步骤
        </Typography>
        <Box component="ol" sx={{ pl: 2.5, my: 0.5 }}>
          {item.steps.map((step, i) => (
            <Typography key={i} component="li" variant="body2" sx={{ mb: 0.4 }}>
              {step}
            </Typography>
          ))}
        </Box>
      </Box>

      {item.params && item.params.length > 0 && (
        <Box sx={{ mb: item.notes ? 1 : 0 }}>
          <Typography variant="caption" color="text.disabled" sx={{ fontWeight: 600 }}>
            关键参数
          </Typography>
          <Stack spacing={0.6} sx={{ mt: 0.6 }}>
            {item.params.map((p) => (
              <Box key={p.name} sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
                <Chip size="small" variant="outlined" label={p.name} sx={{ height: 22, flexShrink: 0 }} />
                <Typography variant="body2" color="text.secondary">
                  {p.meaning}
                </Typography>
              </Box>
            ))}
          </Stack>
        </Box>
      )}

      {item.notes && item.notes.length > 0 && (
        <Box>
          <Typography variant="caption" color="text.disabled" sx={{ fontWeight: 600 }}>
            注意事项
          </Typography>
          <Box component="ul" sx={{ pl: 2.5, my: 0.5 }}>
            {item.notes.map((note, i) => (
              <Typography key={i} component="li" variant="body2" color="text.secondary" sx={{ mb: 0.4 }}>
                {note}
              </Typography>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  )
}

export default function GuidePage() {
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<string[]>([GUIDE_SECTIONS[0].id])

  const q = query.trim().toLowerCase()

  /** 命中搜索的分组(无关键字时展示全部) */
  const filteredSections = useMemo(() => {
    if (!q) return GUIDE_SECTIONS
    return GUIDE_SECTIONS.map((section) => {
      if (section.title.toLowerCase().includes(q) || section.summary.toLowerCase().includes(q)) {
        return section
      }
      return { ...section, items: section.items.filter((item) => itemMatches(item, q)) }
    }).filter((section) => section.items.length > 0)
  }, [q])

  const isExpanded = (id: string) => (q ? true : expanded.includes(id))

  const toggleSection = (id: string) => {
    setExpanded((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  /** 目录跳转:展开分组并平滑滚动到锚点 */
  const jumpTo = (id: string) => {
    setExpanded((prev) => (prev.includes(id) ? prev : [...prev, id]))
    window.setTimeout(() => {
      document.getElementById(`guide-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 80)
  }

  return (
    <Box sx={{ maxWidth: 1440, mx: 'auto' }}>
      <PageHeader
        title="使用指南"
        subtitle="按模块汇总系统的全部可选功能:每一项均说明作用、适用场景、操作步骤、关键参数与注意事项。支持关键字搜索与目录跳转。"
        chips={<Chip size="small" variant="outlined" label={`共 ${GUIDE_SECTIONS.length} 个模块`} />}
        actions={
          <>
            <Button
              size="small"
              color="inherit"
              startIcon={<UnfoldMoreOutlinedIcon />}
              onClick={() => setExpanded(GUIDE_SECTIONS.map((s) => s.id))}
            >
              全部展开
            </Button>
            <Button
              size="small"
              color="inherit"
              startIcon={<UnfoldLessOutlinedIcon />}
              onClick={() => setExpanded([])}
            >
              全部折叠
            </Button>
          </>
        }
      />

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '240px minmax(0, 1fr)' }, gap: 3, alignItems: 'start' }}>
        {/* ---------- 左:目录(小屏隐藏) ---------- */}
        <Card sx={{ display: { xs: 'none', md: 'block' }, position: 'sticky', top: 88 }}>
          <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1, py: 0.8 }}>
              <MenuBookOutlinedIcon fontSize="small" sx={{ color: 'primary.main' }} />
              <Typography variant="subtitle2">目录</Typography>
            </Box>
            <List dense disablePadding>
              {GUIDE_SECTIONS.map((section, index) => (
                <ListItemButton
                  key={section.id}
                  onClick={() => jumpTo(section.id)}
                  sx={{ borderRadius: 2 }}
                >
                  <ListItemText
                    primary={`${index + 1}. ${section.title}`}
                    primaryTypographyProps={{ fontSize: 13.5, noWrap: true }}
                  />
                </ListItemButton>
              ))}
            </List>
          </CardContent>
        </Card>

        {/* ---------- 右:搜索 + 内容 ---------- */}
        <Box sx={{ minWidth: 0 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="搜索功能,例如:ZIP / 打印 / 复核 / 导出…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            sx={{ mb: 2.5 }}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchOutlinedIcon fontSize="small" />
                  </InputAdornment>
                ),
              },
            }}
          />

          {filteredSections.length === 0 ? (
            <Alert severity="info" variant="outlined">
              没有找到与「{query.trim()}」匹配的功能说明,请尝试更换关键字(如:批量、复核、打印、导出、班级)。
            </Alert>
          ) : (
            <Stack spacing={1.5}>
              {filteredSections.map((section) => (
                <Accordion
                  key={section.id}
                  id={`guide-${section.id}`}
                  expanded={isExpanded(section.id)}
                  onChange={() => toggleSection(section.id)}
                  sx={{ scrollMarginTop: 88 }}
                >
                  <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
                    <Box>
                      <Typography variant="subtitle1">{section.title}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {section.summary}
                      </Typography>
                    </Box>
                  </AccordionSummary>
                  <AccordionDetails sx={{ pt: 0.5 }}>
                    <Stack spacing={2.5} divider={<Divider flexItem />}>
                      {section.items.map((item) => (
                        <GuideItemBlock key={item.title} item={item} />
                      ))}
                    </Stack>
                  </AccordionDetails>
                </Accordion>
              ))}
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  )
}
