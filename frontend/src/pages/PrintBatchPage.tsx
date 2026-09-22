/**
 * 批量打印视图(#12 / 方向二)
 *
 * 路由 /print/batch?task_ids=1,2,3&variant=student|teacher&include_images=0|1
 * 把队列页勾选的多份报告合并为一个连续文档(每个任务从新页开始),
 * 一次性打开系统打印对话框;用户可在对话框内选择页码范围实现"指定打印范围"。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, CircularProgress, CssBaseline, ThemeProvider } from '@mui/material'
import ArrowBackOutlinedIcon from '@mui/icons-material/ArrowBackOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { fetchTask, taskImageUrl } from '../api/client'
import MarkdownReport from '../components/MarkdownReport'
import type { TaskDetail } from '../types'
import { getTheme } from '../theme'

type Variant = 'teacher' | 'student'

/** 单次批量打印的任务数上限(防止浏览器内存压力) */
const MAX_BATCH = 60

const VARIANT_LABEL: Record<Variant, string> = {
  teacher: '教师版批改报告',
  student: '学生版订正单',
}

export default function PrintBatchPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const variant = (searchParams.get('variant') ?? 'student') as Variant
  const includeImages = searchParams.get('include_images') === '1'
  const taskIds = useMemo(
    () =>
      (searchParams.get('task_ids') ?? '')
        .split(',')
        .map((s) => Number(s.trim()))
        .filter((n) => Number.isFinite(n) && n > 0),
    [searchParams],
  )

  const [tasks, setTasks] = useState<TaskDetail[] | null>(null)
  const [skippedCount, setSkippedCount] = useState(0)
  const [loadedImages, setLoadedImages] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const printedOnce = useRef(false)

  // 加载任务详情(逐个容错:单个失败不影响整体)
  useEffect(() => {
    if (taskIds.length === 0) {
      setError('未指定要打印的任务')
      return
    }
    if (taskIds.length > MAX_BATCH) {
      setError(`一次最多打印 ${MAX_BATCH} 个任务(当前 ${taskIds.length} 个),请减少勾选后重试。`)
      return
    }
    Promise.all(
      taskIds.map((id) => fetchTask(id).catch(() => null)),
    ).then((results) => {
      const ok = results.filter((t): t is TaskDetail => t !== null)
      const printable = ok.filter((t) => t.status === 'COMPLETED' && t.result != null)
      setSkippedCount(ok.length - printable.length)
      setTasks(printable)
    })
  }, [taskIds])

  /** 全部任务需要加载的图片总数 */
  const imageCount = useMemo(
    () => (tasks && includeImages ? tasks.reduce((sum, t) => sum + t.image_paths.length, 0) : 0),
    [tasks, includeImages],
  )

  // 等待图片加载完成后自动打印
  useEffect(() => {
    if (!tasks || error || tasks.length === 0 || printedOnce.current) return
    if (loadedImages < imageCount) return
    const timer = window.setTimeout(() => {
      printedOnce.current = true
      window.print()
    }, 400)
    return () => window.clearTimeout(timer)
  }, [tasks, error, loadedImages, imageCount])

  const printedAt = useMemo(() => new Date().toLocaleString('zh-CN'), [])

  return (
    <ThemeProvider theme={getTheme('light')}>
      <CssBaseline />
      <div className="print-page" id="print-root">
        {/* ============ 工具栏(打印时隐藏) ============ */}
        <div className="print-toolbar no-print">
          <Button
            size="small"
            color="inherit"
            startIcon={<ArrowBackOutlinedIcon />}
            onClick={() => navigate('/queue')}
          >
            返回队列页
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={<PrintOutlinedIcon />}
            disabled={!tasks || tasks.length === 0}
            onClick={() => window.print()}
          >
            打印
          </Button>
          <span style={{ fontSize: 12.5, color: '#5A6072' }}>
            提示:在打印对话框的“页码范围”里可指定只打印某几页;选择
            “Microsoft Print to PDF”即可保存为 PDF 文件。
          </span>
        </div>

        {error && <Alert severity="error">{error}</Alert>}

        {!tasks && !error && (
          <div style={{ display: 'grid', placeItems: 'center', padding: '120px 0' }}>
            <CircularProgress />
          </div>
        )}

        {tasks && tasks.length === 0 && !error && (
          <Alert severity="warning">所选任务均未完成批改,没有可打印的报告。</Alert>
        )}

        {tasks && skippedCount > 0 && (
          <Alert severity="warning" className="no-print" sx={{ mb: 2 }}>
            有 {skippedCount} 个任务尚未完成批改,已自动跳过,不参与本次打印。
          </Alert>
        )}

        {/* ============ 逐个任务渲染(任务间分页) ============ */}
        {tasks &&
          tasks.map((task) => {
            const markdown =
              variant === 'teacher'
                ? (task.edited_report ?? task.result?.markdown_report ?? '')
                : (task.student_report ?? '')
            return (
              <section className="print-task" key={task.id}>
                <header className="print-header">
                  <h1>
                    {VARIANT_LABEL[variant]} · {task.student_name || '未知'}
                    {task.student_id ? ` / ${task.student_id}` : ''}
                  </h1>
                  <div className="print-meta">
                    {[
                      task.class_name ? `班级:${task.class_name}` : null,
                      task.assignment_name ? `作业:${task.assignment_name}` : null,
                      `任务编号:#${task.id}`,
                      `打印时间:${printedAt}`,
                    ]
                      .filter(Boolean)
                      .join('    |    ')}
                  </div>
                </header>

                {/* 原图(可选,每张一页) */}
                {includeImages &&
                  task.image_paths.map((_, index) => (
                    <div className="print-image-block" key={index}>
                      <img
                        className="pr-image"
                        src={taskImageUrl(task.id, index)}
                        alt={`${task.student_name} 作文第 ${index + 1} 页`}
                        onLoad={() => setLoadedImages((n) => n + 1)}
                        onError={() => setLoadedImages((n) => n + 1)}
                      />
                      <div className="print-image-caption">
                        作文原图 · 第 {index + 1} / {task.image_paths.length} 页
                      </div>
                    </div>
                  ))}

                <MarkdownReport markdown={markdown || '(暂无报告)'} />
              </section>
            )
          })}
      </div>
    </ThemeProvider>
  )
}
