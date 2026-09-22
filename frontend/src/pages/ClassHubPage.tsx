/**
 * 班级管理(与「批改工作台」并列的独立功能区)
 *
 * 六个子标签(URL 驱动,可直达与刷新;路由 /classes/:section):
 * - overview    班级总览:班级列表 / 新建 / 删除
 * - roster      花名册:导入与查看(上传指派的名单基础)
 * - handwriting 手写模型:抄写素材、样本上传与本地训练
 * - package     数据包:导出 / 导入(跨电脑迁移班级数据)
 * - merge       班级合并:预览与执行
 * - analytics   共性错因分析:统计卡片 / 图表 / 讲评摘要
 *
 * 班级维度功能统一入口;旧路由 /analytics 重定向至本页 analytics 标签。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Card,
  CardContent,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Tab,
  Tabs,
} from '@mui/material'
import { useNavigate, useParams } from 'react-router-dom'
import PageHeader from '../components/PageHeader'
import ClassOverviewSection from '../components/classes/ClassOverviewSection'
import ClassPackageSection from '../components/classes/ClassPackageSection'
import ClassMergeSection from '../components/classes/ClassMergeSection'
import RosterSection from '../components/classes/RosterSection'
import HandwritingSection from '../components/HandwritingSection'
import AnalyticsPage from './AnalyticsPage'
import { fetchClasses } from '../api/client'
import type { SchoolClass } from '../types'

/** 子标签定义(URL 段 -> 显示名;与路由 /classes/:section 一一对应) */
const SECTIONS = [
  { key: 'overview', label: '班级总览' },
  { key: 'roster', label: '花名册' },
  { key: 'handwriting', label: '手写模型' },
  { key: 'package', label: '数据包' },
  { key: 'merge', label: '班级合并' },
  { key: 'analytics', label: '共性错因分析' },
] as const
type SectionKey = (typeof SECTIONS)[number]['key']

/** 需要"当前班级"上下文的标签(页头显示共享班级选择器) */
const CLASS_SCOPED: SectionKey[] = ['roster', 'handwriting', 'package']

export default function ClassHubPage() {
  const params = useParams()
  const navigate = useNavigate()
  const [classes, setClasses] = useState<SchoolClass[]>([])
  const [classId, setClassId] = useState<number | ''>('')

  /** URL 段 -> 标签(非法段回退 overview) */
  const section: SectionKey = useMemo(() => {
    const raw = params.section
    return SECTIONS.some((s) => s.key === raw) ? (raw as SectionKey) : 'overview'
  }, [params.section])

  /** 加载班级列表(新建/删除/导入/合并后回调复用) */
  const loadClasses = useCallback(() => {
    fetchClasses()
      .then((data) => {
        setClasses(data)
        setClassId((prev) => {
          if (prev !== '' && data.some((c) => c.id === prev)) return prev
          const first = data.find((c) => !c.merged_into_id)
          return first ? first.id : ''
        })
      })
      .catch(() => setClasses([]))
  }, [])

  useEffect(() => {
    loadClasses()
  }, [loadClasses])

  const needsClass = CLASS_SCOPED.includes(section)
  const activeClasses = classes.filter((c) => !c.merged_into_id)

  return (
    <Box sx={{ maxWidth: 1440, mx: 'auto' }}>
      <PageHeader
        title="班级管理"
        subtitle="班级的创建与维护、花名册、手写模型、数据包与合并,以及班级共性错因分析——班级维度功能统一入口。"
        actions={
          needsClass ? (
            <FormControl size="small" sx={{ minWidth: 220 }}>
              <InputLabel>当前班级</InputLabel>
              <Select
                label="当前班级"
                value={classId}
                onChange={(e) => setClassId(e.target.value as number | '')}
              >
                {activeClasses.map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {c.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          ) : undefined
        }
      />

      <Tabs
        value={section}
        onChange={(_event, value: SectionKey) => navigate(`/classes/${value}`)}
        sx={{ mb: 2 }}
      >
        {SECTIONS.map((s) => (
          <Tab key={s.key} value={s.key} label={s.label} />
        ))}
      </Tabs>

      {section === 'analytics' ? (
        <AnalyticsPage />
      ) : (
        <Card>
          <CardContent>
            {section === 'overview' && <ClassOverviewSection classes={classes} onReload={loadClasses} />}
            {section === 'roster' && <RosterSection classId={classId === '' ? null : classId} />}
            {section === 'handwriting' &&
              (classId === '' ? (
                <Alert severity="info">请先在上方选择班级;还没有班级?到「班级总览」新建。</Alert>
              ) : (
                <HandwritingSection classId={classId} />
              ))}
            {section === 'package' && (
              <ClassPackageSection
                classes={classes}
                classId={classId}
                onImported={loadClasses}
              />
            )}
            {section === 'merge' && <ClassMergeSection classes={classes} onReload={loadClasses} />}
          </CardContent>
        </Card>
      )}
    </Box>
  )
}
