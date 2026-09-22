/**
 * 后端 API 封装(axios)
 *
 * 统一处理:
 * - 基础路径(开发环境由 Vite 代理到 8765)
 * - 错误消息提取(FastAPI 的 detail 字段)
 * - 各业务接口的类型化调用
 */

import axios, { AxiosError, type AxiosProgressEvent } from 'axios'
import type {
  AnnotationResponse,
  BatchCreateResponse,
  BatchDeleteResponse,
  BatchRetryResponse,
  ClassCreatePayload,
  ClassDiagnosis,
  ClassImportResult,
  ClassMergePreview,
  ClassMergeResult,
  ConnectivityResult,
  CorrectionConfigPayload,
  DistributionResult,
  EncryptionActionResult,
  EncryptionStatus,
  ExamBrief,
  ExamDetail,
  ExamPaper,
  ExamQuestion,
  ExamReport,
  Gradebook,
  HealthResponse,
  LedgerBatchResult,
  LedgerClassSummary,
  LedgerEntry,
  LedgerItem,
  LedgerRecord,
  LedgerRecordListResponse,
  LedgerStudentOption,
  MaintenanceClearResult,
  MaintenanceSeedResult,
  OcrConfirmPayload,
  PromptAppendices,
  ImageCompressProgress,
  PracticeGeneratePayload,
  PracticeOptions,
  PracticeSheet,
  PracticeSheetBrief,
  PracticeSourceItem,
  RankingsResult,
  RecorrectPayload,
  RosterImportResult,
  RosterMember,
  SchoolClass,
  SettingsRollbackResult,
  SettingsUpdatePayload,
  SettingsUpdateResult,
  SettingsView,
  SingleCreateResponse,
  StatisticsSource,
  StudentOut,
  StudentProfile,
  StudentUpdatePayload,
  StyleProfile,
  StyleStatus,
  TaskDetail,
  TaskListResponse,
  TaskQueryParams,
  TrendsResult,
} from '../types'

const http = axios.create({
  baseURL: '/api',
  timeout: 300000, // 上传大文件 / 本地推理场景放宽超时
})

// ================= 全局拦截器 =================

/** 设置中心访问令牌的 localStorage 键名 */
export const ADMIN_TOKEN_STORAGE_KEY = 'correction-app:admin-token'

/** 数据加密锁定事件名(423):由 AppShell 监听并弹出解锁对话框 */
export const ENCRYPTION_LOCKED_EVENT = 'correction-app:encryption-locked'

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)
  if (token) config.headers.set('X-Admin-Token', token)
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    // 数据接口因"未解锁"被拒绝:广播全局事件,由解锁弹窗接管
    if (error instanceof AxiosError && error.response?.status === 423) {
      window.dispatchEvent(new CustomEvent(ENCRYPTION_LOCKED_EVENT))
    }
    return Promise.reject(error)
  },
)

/** 读取本机保存的设置访问令牌(仅存浏览器本地,不入服务端) */
export function getAdminToken(): string {
  return localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) ?? ''
}

/** 保存/清除设置访问令牌 */
export function setAdminToken(token: string): void {
  if (token) localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token)
  else localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
}

/** 从 axios 错误中提取可读消息(FastAPI 错误体为 {detail: string} 或校验错误数组) */
export function extractErrorMessage(error: unknown): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // Pydantic 校验错误数组
      return detail.map((d: { msg?: string }) => d?.msg ?? '参数错误').join(';')
    }
    if (error.code === 'ECONNABORTED') return '请求超时,请稍后重试'
    if (!error.response) return '无法连接后端服务,请确认后端已启动(端口 8765)'
    return `请求失败(HTTP ${error.response.status})`
  }
  return error instanceof Error ? error.message : '未知错误'
}

// ================= 系统 =================

/** 启动门禁/状态类轻量接口的独立超时(秒):避免后端未就绪时请求长挂,阻塞启动流程 */
const LIGHT_TIMEOUT_MS = 15000

/** 获取健康检查与配置状态 */
export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await http.get<HealthResponse>('/health', { timeout: LIGHT_TIMEOUT_MS })
  return data
}

// ================= 批改创建 =================

/** 构造 multipart 表单数据 */
function buildCorrectionForm(
  files: File[],
  config: CorrectionConfigPayload,
): FormData {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  form.append('pipeline_choice', config.pipeline_choice)
  form.append('grading_standard', config.grading_standard)
  form.append('detail_level', config.detail_level)
  form.append('require_ocr_review', String(config.require_ocr_review))
  // 班级与作业元数据(可选)
  if (config.class_id != null) form.append('class_id', String(config.class_id))
  if (config.assignment_name) form.append('assignment_name', config.assignment_name)
  if (config.topic) form.append('topic', config.topic)
  // 单篇:手动指派学生(可选)
  if (config.student_name) form.append('student_name', config.student_name)
  if (config.student_id) form.append('student_id', config.student_id)
  // 批量:指派方式 / 顺序起点 / 分片批次复用
  if (config.assign_mode) form.append('assign_mode', config.assign_mode)
  if (config.assign_order_start != null)
    form.append('assign_order_start', String(config.assign_order_start))
  if (config.batch_id) form.append('batch_id', config.batch_id)
  return form
}

/** 个人信息探测结果项(工作台「强制性姓名识别」;仅展示,不落库不入任务) */
export interface IdentityProbeItem {
  index: number
  source_name: string
  name: string | null
  age: string | null
  student_id: string | null
  status: 'ok' | 'empty' | 'error'
  /** 识别输入来源:qr(二维码解码) | canonical/landmarks(依定位点裁剪) | full-image(无定位点整图回退) */
  basis?: string | null
}

/** 个人信息探测(选择文件瞬间;强制本地引擎,支持图片/ZIP/PDF;不落盘不建任务) */
export async function probeIdentity(file: File): Promise<{ items: IdentityProbeItem[] }> {
  const form = new FormData()
  form.append('files', file)
  const { data } = await http.post<{ items: IdentityProbeItem[] }>(
    '/corrections/probe-identity',
    form,
    { timeout: 300000 },
  )
  return data
}

/** 创建单篇批改任务(同一学生的多页图片) */
export async function createSingleCorrection(
  files: File[],
  config: CorrectionConfigPayload,
): Promise<SingleCreateResponse> {
  const { data } = await http.post<SingleCreateResponse>(
    '/corrections',
    buildCorrectionForm(files, config),
  )
  return data
}

/** 创建批量批改任务(每张图=一篇;支持 ZIP;onProgress 上报上传进度 0-100) */
export async function createBatchCorrection(
  files: File[],
  config: CorrectionConfigPayload,
  onProgress?: (percent: number) => void,
): Promise<BatchCreateResponse> {
  const { data } = await http.post<BatchCreateResponse>(
    '/corrections/batch',
    buildCorrectionForm(files, config),
    {
      onUploadProgress: onProgress
        ? (event: AxiosProgressEvent) => {
            if (event.total) onProgress(Math.round((event.loaded * 100) / event.total))
          }
        : undefined,
    },
  )
  return data
}

// ================= 班级(#4) =================

/** 班级列表(含任务数统计) */
export async function fetchClasses(): Promise<SchoolClass[]> {
  const { data } = await http.get<SchoolClass[]>('/classes')
  return data
}

/** 创建班级 */
export async function createClass(payload: ClassCreatePayload): Promise<SchoolClass> {
  const { data } = await http.post<SchoolClass>('/classes', payload)
  return data
}

/** 删除班级(班级下存在批改任务时后端 409 拒绝并返回原因) */
export async function deleteClass(classId: number): Promise<void> {
  await http.delete(`/classes/${classId}`)
}

/** 导出班级数据包(ZIP,方向三) */
export async function exportClassPackageBlob(classId: number): Promise<Blob> {
  const { data } = await http.post<Blob>(
    `/classes/${classId}/export`,
    {},
    { responseType: 'blob', timeout: 120000 },
  )
  return data
}

/** 导入班级数据包(方向三) */
export async function importClassPackage(
  file: File,
  newName?: string,
): Promise<ClassImportResult> {
  const form = new FormData()
  form.append('file', file)
  if (newName) form.append('new_name', newName)
  const { data } = await http.post<ClassImportResult>('/classes/import', form, {
    timeout: 120000,
  })
  return data
}

// ================= 班级合并 =================

/** 班级合并预览(只读:数据规模 + 花名册匹配方案 + 台账项处置方案) */
export async function fetchMergePreview(
  sourceId: number,
  targetId: number,
): Promise<ClassMergePreview> {
  const { data } = await http.get<ClassMergePreview>('/classes/merge/preview', {
    params: { source_id: sourceId, target_id: targetId },
  })
  return data
}

/** 执行班级合并(后端 confirm=true 二次确认;失败在服务端整体回滚) */
export async function mergeClasses(payload: {
  source_class_id: number
  target_class_id: number
}): Promise<ClassMergeResult> {
  const { data } = await http.post<ClassMergeResult>(
    '/classes/merge',
    { ...payload, confirm: true },
    { timeout: 120000 },
  )
  return data
}

// ================= 教学分析(#2 / #3) =================

/** 班级共性错因诊断(支持班级/批次/日期范围筛选) */
export async function fetchClassDiagnosis(params?: {
  class_id?: number
  batch_id?: string
  date_from?: string
  date_to?: string
}): Promise<ClassDiagnosis> {
  const { data } = await http.get<ClassDiagnosis>('/analytics/class-diagnosis', { params })
  return data
}

/** 学生列表 */
export async function fetchStudents(): Promise<StudentOut[]> {
  const { data } = await http.get<StudentOut[]>('/students')
  return data
}

/** 学生画像与错题本(学号优先,姓名兜底) */
export async function fetchStudentProfile(params: {
  student_id?: string
  name?: string
}): Promise<StudentProfile> {
  const { data } = await http.get<StudentProfile>('/students/profile', { params })
  return data
}

// ================= 任务 =================

/** 任务列表查询 */
export async function fetchTasks(params?: TaskQueryParams): Promise<TaskListResponse> {
  const { data } = await http.get<TaskListResponse>('/tasks', { params })
  return data
}

/** 任务详情查询 */
export async function fetchTask(taskId: number): Promise<TaskDetail> {
  const { data } = await http.get<TaskDetail>(`/tasks/${taskId}`)
  return data
}

/** 任务原图 URL(供 <img> 直接引用) */
export function taskImageUrl(taskId: number, imageIndex: number): string {
  return `/api/tasks/${taskId}/images/${imageIndex}`
}

/** 任务姓名/学号信息区小图(审阅页展示;裁剪失败返回 404,前端静默隐藏) */
export function taskNameCropUrl(taskId: number): string {
  return `/api/tasks/${taskId}/name-crop`
}

/** 获取任务转录批注定位(批注核对视图数据源,#13) */
export async function fetchAnnotation(taskId: number): Promise<AnnotationResponse> {
  const { data } = await http.get<AnnotationResponse>(`/tasks/${taskId}/annotation`)
  return data
}

/** 提交 OCR 人工复核结果(管线 B) */
export async function confirmOcr(
  taskId: number,
  payload: OcrConfirmPayload,
): Promise<TaskDetail> {
  const { data } = await http.post<TaskDetail>(`/tasks/${taskId}/ocr-confirm`, payload)
  return data
}

/** 重试失败任务 */
export async function retryTask(taskId: number): Promise<void> {
  await http.post(`/tasks/${taskId}/retry`)
}

/** 批量重试(失败/待复核/中断状态的任务) */
export async function batchRetryTasks(taskIds: number[]): Promise<BatchRetryResponse> {
  const { data } = await http.post<BatchRetryResponse>('/tasks/batch-retry', {
    task_ids: taskIds,
  })
  return data
}

/** 批量删除任务(含错题记录与图片文件清理) */
export async function batchDeleteTasks(taskIds: number[]): Promise<BatchDeleteResponse> {
  const { data } = await http.post<BatchDeleteResponse>('/tasks/batch-delete', {
    task_ids: taskIds,
  })
  return data
}

/** 从 Blob 错误响应中提取可读消息(blob 下载接口的 4xx 错误体也为 Blob) */
export async function extractBlobError(error: unknown): Promise<string> {
  if (error instanceof AxiosError && error.response?.data instanceof Blob) {
    try {
      const text = await error.response.data.text()
      const parsed = JSON.parse(text)
      if (typeof parsed?.detail === 'string') return parsed.detail
    } catch {
      /* 忽略解析失败,回退通用错误 */
    }
  }
  return extractErrorMessage(error)
}

/** 批量导出报告 ZIP */
export async function exportReportsBlob(taskIds: number[]): Promise<Blob> {
  const { data } = await http.post<Blob>(
    '/tasks/export-reports',
    { task_ids: taskIds },
    { responseType: 'blob' },
  )
  return data
}

/** 批量导出成绩表 CSV */
export async function exportGradesBlob(taskIds: number[]): Promise<Blob> {
  const { data } = await http.post<Blob>(
    '/tasks/export-grades',
    { task_ids: taskIds },
    { responseType: 'blob' },
  )
  return data
}

/** 触发浏览器下载 Blob */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** 保存教师编辑后的 Markdown 报告(markdown 传 null 表示恢复系统原始报告) */
export async function updateReport(taskId: number, markdown: string | null): Promise<TaskDetail> {
  const { data } = await http.put<TaskDetail>(`/tasks/${taskId}/report`, {
    markdown_report: markdown,
  })
  return data
}

// ================= 花名册(上传指派基础) =================

/** 获取班级花名册(顺序 = 导入顺序 = 按名单顺序指派所用顺序) */
export async function fetchClassRoster(classId: number): Promise<RosterMember[]> {
  const { data } = await http.get<RosterMember[]>(`/classes/${classId}/roster`)
  return data
}

/** 导入班级花名册(支持粘贴文本或上传 CSV/TXT 文件,每行"姓名,学号") */
export async function importClassRoster(
  classId: number,
  payload: { text?: string; file?: File | null },
): Promise<RosterImportResult> {
  const form = new FormData()
  if (payload.text) form.append('text', payload.text)
  if (payload.file) form.append('file', payload.file)
  const { data } = await http.post<RosterImportResult>(`/classes/${classId}/roster/import`, form)
  return data
}

// ================= 手写样本模型(花名册“手写模型”) =================
export interface HandwritingSentence {
  topic: string
  sentence: string
}

export interface HandwritingSampleItem {
  id: number
  status: string
  student_name: string | null
  student_id: string | null
  has_crop: boolean
  has_features: boolean
  created_at: string
}

export interface HandwritingTrainStatus {
  running: boolean
  done: number
  total: number
  percent: number
  eta_seconds: number | null
  stage: string
  errors: number
  recent_avg_seconds: number | null
  model_ready: boolean
  samples_count: number
  pending_bind: number
}

export interface HandwritingModelOverview {
  class_id: number
  ready: boolean
  samples_count: number
  students_count: number
  pending_bind: number
  level: string
  configured_level?: string | null
  updated_at: string | null
  cores: number
  ram_gb: number | null
  recommended_level: string
}

/** 生成抄写素材(题目句 + 正文句;本地句库) */
export async function fetchHandwritingSentence(classId: number): Promise<HandwritingSentence> {
  const { data } = await http.get<HandwritingSentence>(`/classes/${classId}/handwriting/sentence`)
  return data
}

/** 样本列表 */
export async function fetchHandwritingSamples(classId: number): Promise<HandwritingSampleItem[]> {
  const { data } = await http.get<HandwritingSampleItem[]>(`/classes/${classId}/handwriting/samples`)
  return data
}

/** 批量上传手写样本(异步处理训练) */
export async function uploadHandwritingSamples(
  classId: number,
  files: File[],
): Promise<{ accepted: number; sample_ids: number[] }> {
  const form = new FormData()
  files.forEach((file) => form.append('files', file))
  const { data } = await http.post<{ accepted: number; sample_ids: number[] }>(
    `/classes/${classId}/handwriting/samples`,
    form,
  )
  return data
}

/** 训练/处理进度(含真实 ETA) */
export async function fetchHandwritingTrainStatus(classId: number): Promise<HandwritingTrainStatus> {
  const { data } = await http.get<HandwritingTrainStatus>(`/classes/${classId}/handwriting/train/status`)
  return data
}

/** 模型概览(含本机画像与档位) */
export async function fetchHandwritingModel(classId: number): Promise<HandwritingModelOverview> {
  const { data } = await http.get<HandwritingModelOverview>(`/classes/${classId}/handwriting/model`)
  return data
}

/** 样本姓名区小图 URL */
export function handwritingCropUrl(sampleId: number): string {
  return `/api/handwriting/samples/${sampleId}/crop`
}

/** 指定学生(待绑定样本) */
export async function bindHandwritingSample(
  sampleId: number,
  studentName: string,
  studentId?: string | null,
): Promise<void> {
  await http.put(`/handwriting/samples/${sampleId}/bind`, {
    student_name: studentName,
    student_id: studentId || null,
  })
}

/** 删除样本 */
export async function deleteHandwritingSample(sampleId: number): Promise<void> {
  await http.delete(`/handwriting/samples/${sampleId}`)
}

// ================= 全局状态(真实进度 + 真实自检) =================
export interface StatusActiveTask {
  kind: string
  label: string
  percent: number | null
  eta_seconds: number | null
  status_text: string
}

export interface StatusCheckup {
  ok: boolean
  summary: string
  ok_items: string[]
  problems: string[]
  checked_at: string
}

export interface StatusOverview {
  active: StatusActiveTask[]
  checkup: StatusCheckup
}

/** 全局状态总览 */
export async function fetchStatusOverview(): Promise<StatusOverview> {
  const { data } = await http.get<StatusOverview>('/status/overview')
  return data
}

export interface SystemProfile {
  cores: number
  ram_gb: number | null
  recommended_level: string
  configured_level: string
  effective_level: string
}

/** 本机配置与档位推荐 */
export async function fetchSystemProfile(): Promise<SystemProfile> {
  const { data } = await http.get<SystemProfile>('/settings/system-profile')
  return data
}

/** 修改任务的学生信息(事后纠错;同步错题本/档案/台账对齐) */
export async function updateTaskStudent(
  taskId: number,
  payload: StudentUpdatePayload,
): Promise<TaskDetail> {
  const { data } = await http.put<TaskDetail>(`/tasks/${taskId}/student`, payload)
  return data
}

/** 重新批改(已完成任务原地重跑:复用原图,仅覆盖传入参数) */
export async function recorrectTask(taskId: number, payload: RecorrectPayload): Promise<TaskDetail> {
  const { data } = await http.post<TaskDetail>(`/tasks/${taskId}/recorrect`, payload)
  return data
}

/** 保存/清除学生版教师寄语(空/null = 恢复系统默认寄语) */
export async function updateTeacherMessage(
  taskId: number,
  teacherMessage: string | null,
): Promise<TaskDetail> {
  const { data } = await http.put<TaskDetail>(`/tasks/${taskId}/teacher-message`, {
    teacher_message: teacherMessage,
  })
  return data
}

/** 保存教师修订后的转录原文(重渲染系统报告;教师编辑版不受影响) */
export async function updateTranscript(
  taskId: number,
  transcribedText: string,
): Promise<TaskDetail> {
  const { data } = await http.put<TaskDetail>(`/tasks/${taskId}/transcript`, {
    transcribed_text: transcribedText,
  })
  return data
}

// ================= 设置中心 =================

/** 设置视图(分组 + 元数据 + 脱敏值 + 模式/快照状态) */
export async function fetchSettings(): Promise<SettingsView> {
  const { data } = await http.get<SettingsView>('/settings', { timeout: LIGHT_TIMEOUT_MS })
  return data
}

/** 批量更新设置(仅传入的字段生效;敏感字段空串=清除) */
export async function updateSettings(payload: SettingsUpdatePayload): Promise<SettingsUpdateResult> {
  const { data } = await http.put<SettingsUpdateResult>('/settings', payload)
  return data
}

/** 探索性设置:恢复系统默认值 */
export async function resetSetting(key: string): Promise<SettingsUpdateResult> {
  const { data } = await http.post<SettingsUpdateResult>('/settings/reset', { key })
  return data
}

/** 探索性设置:一键回退到本批修改前的状态 */
export async function rollbackSettings(): Promise<SettingsRollbackResult> {
  const { data } = await http.post<SettingsRollbackResult>('/settings/rollback')
  return data
}

/** 大模型超参:清空"端点不支持参数"的自动适配记录(此后按配置/画像重新发送) */
export async function clearLlmParamLearnings(): Promise<SettingsUpdateResult> {
  const { data } = await http.post<SettingsUpdateResult>('/settings/llm-params/clear')
  return data
}

/** 管线连通性测试(可携带未保存的候选值,先测后存) */
export async function testConnection(
  target: 'pipeline_a' | 'ocr' | 'deepseek',
  overrides?: Record<string, string | number | boolean | null>,
): Promise<ConnectivityResult> {
  const { data } = await http.post<ConnectivityResult>('/settings/test', { target, overrides })
  return data
}

/** 提示词微调附录与最终提示词预览 */
export async function fetchPromptAppendices(): Promise<PromptAppendices> {
  const { data } = await http.get<PromptAppendices>('/settings/prompts')
  return data
}

/** 更新提示词附录(空串 = 清除) */
export async function updatePromptAppendices(
  payload: { pipeline_a?: string; ocr?: string; grading?: string },
): Promise<PromptAppendices> {
  const { data } = await http.put<PromptAppendices>('/settings/prompts', payload)
  return data
}

// ================= 数据加密 =================

/** 加密状态(含迁移进度) */
export async function fetchEncryptionStatus(): Promise<EncryptionStatus> {
  const { data } = await http.get<EncryptionStatus>('/encryption/status', { timeout: LIGHT_TIMEOUT_MS })
  return data
}

/** 启用加密(设置口令;随后可执行存量迁移) */
export async function setupEncryption(password: string): Promise<EncryptionStatus> {
  const { data } = await http.post<EncryptionStatus>('/encryption/setup', { password })
  return data
}

/** 解锁(口令载入内存;进程重启后需重新解锁) */
export async function unlockEncryption(password: string): Promise<EncryptionStatus> {
  const { data } = await http.post<EncryptionStatus>('/encryption/unlock', { password })
  return data
}

/** 立即锁定(数据接口将返回 423 直至再次解锁) */
export async function lockEncryption(): Promise<EncryptionStatus> {
  const { data } = await http.post<EncryptionStatus>('/encryption/lock')
  return data
}

/** 修改口令(仅重新包裹密钥,不重加密数据) */
export async function changeEncryptionPassword(
  oldPassword: string,
  newPassword: string,
): Promise<EncryptionStatus> {
  const { data } = await http.post<EncryptionStatus>('/encryption/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
  return data
}

/** 启动存量迁移(encrypt=加密全部存量;decrypt=还原明文;后台执行) */
export async function startEncryptionMigration(
  mode: 'encrypt' | 'decrypt',
): Promise<EncryptionActionResult> {
  const { data } = await http.post<EncryptionActionResult>('/encryption/migrate', { mode })
  return data
}

/** 关闭加密(decrypt_all=先还原明文;keep_ciphertext=保留密文) */
export async function disableEncryption(
  password: string,
  mode: 'keep_ciphertext' | 'decrypt_all',
): Promise<EncryptionStatus> {
  const { data } = await http.post<EncryptionStatus>(
    '/encryption/disable',
    { password, mode, confirm: true },
    { timeout: 600000 },
  )
  return data
}

/** 生成一次性恢复密钥(仅此一次展示,请离线保存) */
export async function generateRecoveryKey(): Promise<string> {
  const { data } = await http.post<{ recovery_key: string }>('/encryption/recovery/generate')
  return data.recovery_key
}

/** 用恢复密钥重置口令(强制轮换密钥并全量重加密) */
export async function resetWithRecoveryKey(
  recoveryKey: string,
  newPassword: string,
): Promise<EncryptionStatus> {
  const { data } = await http.post<EncryptionStatus>(
    '/encryption/recovery/reset',
    { recovery_key: recoveryKey, new_password: newPassword },
    { timeout: 600000 },
  )
  return data
}

/** Plan B 终极兜底:归档密文副本后清空重建(确认短语必须为"清空重建") */
export async function planbArchiveReinit(confirmPhrase: string): Promise<EncryptionActionResult> {
  const { data } = await http.post<EncryptionActionResult>(
    '/encryption/planb/archive-reinit',
    { confirm_phrase: confirmPhrase },
    { timeout: 600000 },
  )
  return data
}

// ================= 作业台账 =================

/** 登记项列表(含全局模板项与班级私有项) */
export async function fetchLedgerItems(classId?: number | null): Promise<LedgerItem[]> {
  const { data } = await http.get<LedgerItem[]>('/ledger/items', {
    params: classId != null ? { class_id: classId } : undefined,
  })
  return data
}

/** 新建登记项(class_id 为空 = 全局模板项) */
export async function createLedgerItem(payload: {
  name: string
  class_id?: number | null
  category?: string | null
  scoring_mode: string
  config?: Record<string, unknown>
  sort_order?: number
}): Promise<LedgerItem> {
  const { data } = await http.post<LedgerItem>('/ledger/items', payload)
  return data
}

/** 一键创建预设登记项 */
export async function createLedgerPresets(classId?: number | null): Promise<LedgerItem[]> {
  const { data } = await http.post<LedgerItem[]>('/ledger/items/presets', {
    class_id: classId ?? null,
  })
  return data
}

/** 更新登记项 */
export async function updateLedgerItem(
  itemId: number,
  payload: Partial<{ name: string; category: string | null; config: Record<string, unknown>; sort_order: number }>,
): Promise<LedgerItem> {
  const { data } = await http.put<LedgerItem>(`/ledger/items/${itemId}`, payload)
  return data
}

/** 删除/归档登记项(有记录时仅归档) */
export async function deleteLedgerItem(itemId: number): Promise<void> {
  await http.delete(`/ledger/items/${itemId}`)
}

/** 批量登记(同一登记项 + 同一日期 + 多名学生;value 为空 = 撤销该条) */
export async function batchUpsertLedger(payload: {
  item_id: number
  record_date: string
  class_id?: number | null
  entries: LedgerEntry[]
}): Promise<LedgerBatchResult> {
  const { data } = await http.post<LedgerBatchResult>('/ledger/records/batch', payload)
  return data
}

/** 登记明细查询(后端契约字段为 items,映射为 records 供调用方使用) */
export async function fetchLedgerRecords(params: {
  class_id?: number | null
  item_id?: number | null
  student?: string | null
  date_from?: string | null
  date_to?: string | null
  limit?: number
  offset?: number
}): Promise<{ total: number; records: LedgerRecord[] }> {
  const { data } = await http.get<LedgerRecordListResponse>('/ledger/records', {
    params,
  })
  return { total: data.total ?? 0, records: data.items ?? [] }
}

/** 单条登记纠错 */
export async function updateLedgerRecord(
  recordId: number,
  payload: { value?: string; note?: string | null },
): Promise<LedgerRecord> {
  const { data } = await http.put<LedgerRecord>(`/ledger/records/${recordId}`, payload)
  return data
}

/** 删除登记记录 */
export async function deleteLedgerRecord(recordId: number): Promise<void> {
  await http.delete(`/ledger/records/${recordId}`)
}

/** 班级台账汇总(学生 × 登记项矩阵) */
export async function fetchLedgerSummary(params: {
  class_id: number
  date_from?: string | null
  date_to?: string | null
}): Promise<LedgerClassSummary> {
  const { data } = await http.get<LedgerClassSummary>('/ledger/summary', { params })
  return data
}

/** 登记用学生列表(花名册优先,缺省回退历史学生) */
export async function fetchLedgerStudents(classId?: number | null): Promise<LedgerStudentOption[]> {
  const { data } = await http.get<LedgerStudentOption[]>('/ledger/students', {
    params: classId != null ? { class_id: classId } : undefined,
  })
  return data
}

// ================= 考试统计 =================

/** 考试列表(含考卷数量统计) */
export async function fetchExams(classId?: number | null): Promise<ExamBrief[]> {
  const { data } = await http.get<ExamBrief[]>('/exams', {
    params: classId != null ? { class_id: classId } : undefined,
  })
  return data
}

/** 新建考试 */
export async function createExam(payload: {
  name: string
  exam_date: string
  class_id?: number | null
  subject?: string
  full_score?: number
  exam_type?: string | null
  note?: string | null
}): Promise<ExamBrief> {
  const { data } = await http.post<ExamBrief>('/exams', payload)
  return data
}

/** 考试详情(含全部考卷与报告状态) */
export async function fetchExamDetail(examId: number): Promise<ExamDetail> {
  const { data } = await http.get<ExamDetail>(`/exams/${examId}`)
  return data
}

/** 更新考试基本信息 */
export async function updateExam(
  examId: number,
  payload: Partial<{ name: string; exam_date: string; full_score: number; exam_type: string | null; note: string | null }>,
): Promise<ExamBrief> {
  const { data } = await http.put<ExamBrief>(`/exams/${examId}`, payload)
  return data
}

/** 删除考试及全部考卷/报告(不可恢复) */
export async function deleteExam(examId: number): Promise<void> {
  await http.delete(`/exams/${examId}`, { params: { confirm: true } })
}

/** 批量上传考卷(文件名匹配花名册;同名续传多页) */
export async function uploadExamPapers(
  examId: number,
  files: File[],
  options?: { studentName?: string; studentId?: string; onProgress?: (percent: number) => void },
): Promise<ExamPaper[]> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  if (options?.studentName) form.append('student_name', options.studentName)
  if (options?.studentId) form.append('student_id', options.studentId)
  const { data } = await http.post<ExamPaper[]>(`/exams/${examId}/papers`, form, {
    onUploadProgress: options?.onProgress
      ? (event: AxiosProgressEvent) => {
          if (event.total) options.onProgress?.(Math.round((event.loaded * 100) / event.total))
        }
      : undefined,
  })
  return data
}

/** 人工修订考卷(teacher_edited=1,人工值优先) */
export async function updateExamPaper(
  examId: number,
  paperId: number,
  payload: {
    student_name?: string
    student_id?: string | null
    total_score?: number | null
    question_results?: ExamQuestion[]
  },
): Promise<ExamPaper> {
  const { data } = await http.put<ExamPaper>(`/exams/${examId}/papers/${paperId}`, payload)
  return data
}

/** 重新识别考卷 */
export async function retryExamPaper(examId: number, paperId: number): Promise<ExamPaper> {
  const { data } = await http.post<ExamPaper>(`/exams/${examId}/papers/${paperId}/retry`)
  return data
}

/** 生成/重新生成考试分析报告 */
export async function generateExamReport(examId: number): Promise<ExamReport> {
  const { data } = await http.post<ExamReport>(`/exams/${examId}/report`)
  return data
}

/** 查看已生成的报告 */
export async function fetchExamReport(examId: number): Promise<ExamReport> {
  const { data } = await http.get<ExamReport>(`/exams/${examId}/report`)
  return data
}

/** 下载报告 Markdown */
export async function exportExamReportBlob(examId: number): Promise<Blob> {
  const { data } = await http.get<Blob>(`/exams/${examId}/report/export`, { responseType: 'blob' })
  return data
}

// ================= 统一统计层 =================

/** 学生成绩总表(三源聚合) */
export async function fetchGradebook(params: {
  class_id?: number | null
  date_from?: string | null
  date_to?: string | null
  sources?: StatisticsSource[] | null
}): Promise<Gradebook> {
  const { data } = await http.get<Gradebook>('/statistics/gradebook', {
    params: {
      class_id: params.class_id ?? undefined,
      date_from: params.date_from ?? undefined,
      date_to: params.date_to ?? undefined,
      sources: params.sources?.length ? params.sources.join(',') : undefined,
    },
  })
  return data
}

/** 导出成绩总表 CSV */
export async function exportGradebookBlob(params: {
  class_id?: number | null
  date_from?: string | null
  date_to?: string | null
  sources?: StatisticsSource[] | null
}): Promise<Blob> {
  const { data } = await http.get<Blob>('/statistics/gradebook/export', {
    params: {
      class_id: params.class_id ?? undefined,
      date_from: params.date_from ?? undefined,
      date_to: params.date_to ?? undefined,
      sources: params.sources?.length ? params.sources.join(',') : undefined,
    },
    responseType: 'blob',
  })
  return data
}

/** 单来源分布统计 */
export async function fetchDistribution(params: {
  source: StatisticsSource
  class_id?: number | null
  target_id?: number | null
}): Promise<DistributionResult> {
  const { data } = await http.get<DistributionResult>('/statistics/distribution', { params })
  return data
}

/** 排行榜(综合/考试/进步/覆盖率) */
export async function fetchRankings(classId?: number | null): Promise<RankingsResult> {
  const { data } = await http.get<RankingsResult>('/statistics/rankings', {
    params: classId != null ? { class_id: classId } : undefined,
  })
  return data
}

/** 趋势分析(月度平均 + 可选学生序列) */
export async function fetchTrends(params: {
  class_id?: number | null
  student?: string | null
}): Promise<TrendsResult> {
  const { data } = await http.get<TrendsResult>('/statistics/trends', {
    params: {
      class_id: params.class_id ?? undefined,
      student: params.student ?? undefined,
    },
  })
  return data
}

// ================= 示范学习(风格迁移) =================

/** 示范学习当前状态(供工作台 chip 与设置页展示) */
export async function fetchStyleStatus(): Promise<StyleStatus> {
  const { data } = await http.get<StyleStatus>('/style/status')
  return data
}

/** 风格画像列表 */
export async function fetchStyleProfiles(): Promise<StyleProfile[]> {
  const { data } = await http.get<StyleProfile[]>('/style/profiles')
  return data
}

/** 从已完成的示例范文归纳风格画像 */
export async function learnStyle(taskId: number, name?: string): Promise<StyleProfile> {
  const { data } = await http.post<StyleProfile>(
    '/style/profiles/learn',
    { task_id: taskId, name: name || undefined },
    { timeout: 600000 },
  )
  return data
}

/** 编辑画像(名称/风格描述) */
export async function updateStyleProfile(
  profileId: number,
  payload: { name?: string; narrative?: string },
): Promise<StyleProfile> {
  const { data } = await http.put<StyleProfile>(`/style/profiles/${profileId}`, payload)
  return data
}

/** 启用画像(单一生效:自动停用其他画像) */
export async function activateStyleProfile(profileId: number): Promise<StyleProfile> {
  const { data } = await http.post<StyleProfile>(`/style/profiles/${profileId}/activate`)
  return data
}

/** 停用画像(恢复默认批改风格) */
export async function deactivateStyleProfile(profileId: number): Promise<StyleProfile> {
  const { data } = await http.post<StyleProfile>(`/style/profiles/${profileId}/deactivate`)
  return data
}

/** 删除画像 */
export async function deleteStyleProfile(profileId: number): Promise<void> {
  await http.delete(`/style/profiles/${profileId}`)
}

/* ================= 练习卷(依据历史作业错因生成) ================= */

/** 题型与数量选项 */
export async function fetchPracticeOptions(): Promise<PracticeOptions> {
  const { data } = await http.get<PracticeOptions>('/practice/options')
  return data
}

/** 可选作业来源(class_id 可选过滤) */
export async function fetchPracticeSources(
  classId?: number | null,
): Promise<{ assignments: PracticeSourceItem[] }> {
  const { data } = await http.get<{ assignments: PracticeSourceItem[] }>('/practice/sources', {
    params: classId ? { class_id: classId } : {},
  })
  return data
}

/** 练习卷列表 */
export async function fetchPracticeSheets(): Promise<PracticeSheetBrief[]> {
  const { data } = await http.get<PracticeSheetBrief[]>('/practice/sheets')
  return data
}

/** 生成练习卷(同步生成,超时放宽到 10 分钟) */
export async function generatePracticeSheet(payload: PracticeGeneratePayload): Promise<PracticeSheet> {
  const { data } = await http.post<PracticeSheet>('/practice/sheets', payload, { timeout: 600000 })
  return data
}

/** 练习卷详情 */
export async function fetchPracticeSheet(sheetId: number): Promise<PracticeSheet> {
  const { data } = await http.get<PracticeSheet>(`/practice/sheets/${sheetId}`)
  return data
}

/** 删除练习卷(需 confirm 二次确认) */
export async function deletePracticeSheet(sheetId: number): Promise<void> {
  await http.delete(`/practice/sheets/${sheetId}`, { params: { confirm: true } })
}

/* ================= 维护:存档图片压缩 ================= */

/** 启动存量图片批量压缩(后台执行) */
export async function startImageCompress(): Promise<{ started: boolean; progress: ImageCompressProgress }> {
  const { data } = await http.post<{ started: boolean; progress: ImageCompressProgress }>(
    '/maintenance/compress-images',
  )
  return data
}

/** 存量压缩进度 */
export async function fetchImageCompressStatus(): Promise<ImageCompressProgress> {
  const { data } = await http.get<ImageCompressProgress>('/maintenance/compress-images/status')
  return data
}

/** 清除演示数据(仅删除「演示模式(Mock)」产生的任务/学生/图片) */
export async function clearDemoData(): Promise<{ tasks: number; students: number; files: number }> {
  const { data } = await http.post<{ tasks: number; students: number; files: number }>(
    '/maintenance/clear-demo-data',
    {},
    { params: { confirm: true } },
  )
  return data
}

/** 一键清除所有数据(全部业务数据 + 上传文件;系统配置与密钥保留;不可恢复) */
export async function clearAllData(): Promise<MaintenanceClearResult> {
  const { data } = await http.post<MaintenanceClearResult>(
    '/maintenance/clear-all-data',
    {},
    { params: { confirm: true } },
  )
  return data
}

/** 一键恢复所有示例数据(先清空现有全部数据,再写入完整示例数据;不可恢复) */
export async function seedDemoData(): Promise<MaintenanceSeedResult> {
  const { data } = await http.post<MaintenanceSeedResult>(
    '/maintenance/seed-demo-data',
    {},
    { params: { confirm: true } },
  )
  return data
}
