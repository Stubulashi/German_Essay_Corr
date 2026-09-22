/**
 * 单任务打印视图(#12 / 方向二)
 *
 * 独立于 AppShell 的干净页面(路由 /print/:taskId),渲染即为最终纸面效果:
 * 页眉 → 原图页(可选,每张一页)→ 报告(教师版 / 学生版 / 两份)→ 转录原文(可选)。
 * 进入页面后等待图片加载完成,自动调起系统打印对话框(可选打印机、页码范围)。
 *
 * URL 参数:
 * - variant=teacher|student|both   (默认 teacher)
 * - include_images=0|1             (默认 0)
 * - include_transcript=0|1         (默认 0)
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, CircularProgress } from '@mui/material'
import ArrowBackOutlinedIcon from '@mui/icons-material/ArrowBackOutlined'
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { extractErrorMessage, fetchTask, taskImageUrl } from '../api/client'
import MarkdownReport from '../components/MarkdownReport'
import type { TaskDetail } from '../types'
import { getTheme } from '../theme'

type Variant = 'teacher' | 'student' | 'both'

export default function PrintViewPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const id = Number(taskId)

  const variant = (searchParams.get('variant') ?? 'teacher') as Variant
  const includeImages = searchParams.get('include_images') === '1'
  const includeTranscript = searchParams.get('include_transcript') === '1'

  const [task, setTask] = useState<TaskDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadedImages, setLoadedImages] = useState(0)
  const printedOnce = useRef(false)

  // 加载任务详情
  useEffect(() => {
    if (!Number.isFinite(id)) {
      setError('无效的任务 ID')
      return
    }
    fetchTask(id)
      .then(setTask)
      .catch((e) => setError(extractErrorMessage(e)))
  }, [id])

  const imageCount = task && includeImages ? task.image_paths.length : 0

  // 等待图片全部加载(或失败)后,自动调起打印
  useEffect(() => {
    if (!task || error || printedOnce.current) return
    if (loadedImages < imageCount) return
    const timer = window.setTimeout(() => {
      printedOnce.current = true
      window.print()
    }, 400)
    return () => window.clearTimeout(timer)
  }, [task, error, loadedImages, imageCount])

  // 报告内容(按版本选择)
  const teacherMarkdown = useMemo(
    () => task?.edited_report ?? task?.result?.markdown_report ?? '',
    [task],
  )
  const studentMarkdown = useMemo(() => task?.student_report ?? '', [task])

  /** 打印时间(进入页面时定格,避免重渲染漂移) */
  const printedAt = useMemo(() => new Date().toLocaleString('zh-CN'), [])

  if (error) {
    return (
      <ThemeProvider theme={getTheme('light')}>
        <CssBaseline />
        <div className="print-page">
          <Alert severity="error">{error}</Alert>
          <Button sx={{ mt: 2 }} onClick={() => navigate(-1)}>
            返回
          </Button>
        </div>
      </ThemeProvider>
    )
  }
  if (!task) {
    return (
      <ThemeProvider theme={getTheme('light')}>
        <CssBaseline />
        <div className="print-page" id="print-root">
          <div style={{ display: 'grid', placeItems: 'center', padding: '120px 0' }}>
            <CircularProgress />
          </div>
        </div>
      </ThemeProvider>
    )
  }

  const printable = task.status === 'COMPLETED' && task.result != null

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
            onClick={() => navigate(`/review/${task.id}`)}
          >
            返回审阅页
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={<PrintOutlinedIcon />}
            disabled={!printable}
            onClick={() => window.print()}
          >
            打印
          </Button>
          <span style={{ fontSize: 12.5, color: '#5A6072' }}>
            提示:在打印对话框中可选择打印机与页码范围;选择 “Microsoft Print to PDF” 或“另存为
            PDF”即可保存为 PDF 文件。
          </span>
        </div>

        {!printable && (
          <Alert severity="warning" className="no-print" sx={{ mb: 2 }}>
            该任务尚未完成批改(当前状态:{task.status}),暂无报告可打印。
          </Alert>
        )}

        {/* ============ 页眉 ============ */}
        <header className="print-header">
          <h1>
            德语作文批改 · {task.student_name || '未知'}
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

        {/* ============ 原图页(可选,每张一页) ============ */}
        {imageCount > 0 &&
          task.image_paths.map((_, index) => (
            <div className="print-image-block" key={index}>
              <img
                className="pr-image"
                src={taskImageUrl(task.id, index)}
                alt={`作文第 ${index + 1} 页`}
                onLoad={() => setLoadedImages((n) => n + 1)}
                onError={() => setLoadedImages((n) => n + 1)}
              />
              <div className="print-image-caption">
                学生作文原图 · 第 {index + 1} / {task.image_paths.length} 页
              </div>
            </div>
          ))}

        {/* ============ 教师版报告 ============ */}
        {(variant === 'teacher' || variant === 'both') && teacherMarkdown && (
          <section className="print-variant">
            {variant === 'both' && <h2>教师版批改报告</h2>}
            <MarkdownReport markdown={teacherMarkdown} />
          </section>
        )}

        {/* ============ 学生版订正单 ============ */}
        {(variant === 'student' || variant === 'both') && studentMarkdown && (
          <section className="print-variant">
            {(variant === 'both' || variant === 'student') && <h2>学生版订正单</h2>}
            <MarkdownReport markdown={studentMarkdown} />
          </section>
        )}

        {/* ============ 转录原文(可选) ============ */}
        {includeTranscript && task.result?.transcribed_text && (
          <section className="print-variant">
            <h2>附:作文转录原文</h2>
            <div className="print-transcript">{task.result.transcribed_text}</div>
          </section>
        )}
      </div>
    </ThemeProvider>
  )
}
