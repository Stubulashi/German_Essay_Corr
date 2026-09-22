/**
 * 全局类型定义
 *
 * 与后端 Pydantic Schema(app/models/schemas.py)逐字段对齐,
 * 保证双管线切换与 API 变更时的类型安全。
 */

// ================= 枚举 =================

/** 管线选择:双管线热切换的核心开关 */
export type PipelineChoice = 'PIPELINE_A_LOCAL' | 'PIPELINE_B_CLOUD'

/** 评分标准:高考(25 分制) / DSD(CEFR) */
export type GradingStandard = 'GAOKAO' | 'DSD'

/** 批改细致度:低(标错) / 中(标错+修改) / 高(保姆级解析) */
export type DetailLevel = 'LOW' | 'MEDIUM' | 'HIGH'

/** 任务状态机 */
export type TaskStatus = 'PENDING' | 'PROCESSING' | 'WAITING_REVIEW' | 'COMPLETED' | 'FAILED'

/** 任务处理阶段 */
export type TaskStage = 'UPLOADED' | 'OCR' | 'GRADING' | 'RENDERING' | 'DONE'

// ================= 核心业务模型 =================

/** 单条错误条目(与后端 ErrorItem 对齐) */
export interface ErrorItem {
  original_text: string
  corrected_text: string
  error_type: string
  explanation?: string | null
}

/** 标准化批改结果(双管线统一输出契约) */
export interface EssayCorrectionResult {
  student_name: string
  student_id?: string | null
  transcribed_text: string
  overall_score: string
  overall_comment: string
  errors: ErrorItem[]
  highlights: string[]
  markdown_report: string
}

/** OCR 中间结果(管线 B 人工复核用) */
export interface OcrExtractionResult {
  student_name: string
  student_id?: string | null
  transcribed_text: string
  /** 整体识别质量(OCR 契约 v2;旧数据可能缺省/null) */
  recognition_quality?: 'high' | 'medium' | 'low' | null
  /** 低识别质量时的中文说明 */
  quality_note?: string | null
}

// ================= 任务模型 =================

/** 任务简要信息(列表展示) */
export interface TaskBrief {
  id: number
  batch_id?: string | null
  student_name: string
  student_id?: string | null
  pipeline_choice: PipelineChoice
  pipeline_used?: PipelineChoice | null
  grading_standard: GradingStandard
  detail_level: DetailLevel
  status: TaskStatus
  stage: TaskStage
  overall_score?: string | null
  fallback_triggered: boolean
  error_message?: string | null
  /** 归属班级 ID(可空) */
  class_id?: number | null
  /** 归属班级名称(后端联表返回) */
  class_name?: string | null
  /** 作业名称(如 "第一次月考作文") */
  assignment_name?: string | null
  /** 作文题目/要求 */
  topic?: string | null
  created_at: string
  updated_at: string
}

/** 任务完整信息(详情/审阅页) */
export interface TaskDetail extends TaskBrief {
  image_paths: string[]
  ocr_result?: OcrExtractionResult | null
  result?: EssayCorrectionResult | null
  edited_report?: string | null
  /** 学生版报告(订正单,弱化分数;#11) */
  student_report?: string | null
  require_ocr_review: boolean
  /** 重新批改次数(追溯) */
  recorrect_count: number
  /** 最近一次重新批改时间 */
  last_recorrect_at?: string | null
  /** 上一次批改总分(完成后差异提示) */
  prev_overall_score?: string | null
  /** 上一次批改错因数(完成后差异提示) */
  prev_error_count?: number | null
  /** 学生版教师寄语(空 = 系统默认寄语) */
  teacher_message?: string | null
}

/** 重新批改请求(仅传入的字段覆盖任务原配置;未传入项沿用原值) */
export interface RecorrectPayload {
  pipeline_choice?: PipelineChoice
  grading_standard?: GradingStandard
  detail_level?: DetailLevel
  require_ocr_review?: boolean
  student_name?: string
  student_id?: string | null
  class_id?: number | null
  assignment_name?: string | null
  topic?: string | null
}

/** 任务列表响应 */
export interface TaskListResponse {
  total: number
  items: TaskBrief[]
}

/** 任务列表查询参数 */
export interface TaskQueryParams {
  status?: string
  batch_id?: string
  class_id?: number
  created_from?: string
  created_to?: string
  limit?: number
  offset?: number
}

/** 批量重试响应 */
export interface BatchRetryResponse {
  retried: number[]
  skipped: number[]
}

/** 批量删除响应 */
export interface BatchDeleteResponse {
  deleted: number[]
  skipped: number[]
}

// ================= 请求模型 =================

/** 创建批改任务时的配置参数(含班级、作业元数据与学生指派) */
export interface CorrectionConfigPayload {
  pipeline_choice: PipelineChoice
  grading_standard: GradingStandard
  detail_level: DetailLevel
  require_ocr_review: boolean
  /** 归属班级 ID(可选) */
  class_id?: number | null
  /** 作业名称(可选) */
  assignment_name?: string | null
  /** 作文题目/要求(可选) */
  topic?: string | null
  /** 单篇:手动指派学生姓名(可选,留空则自动识别) */
  student_name?: string | null
  /** 单篇:手动指派学号(可选) */
  student_id?: string | null
  /** 批量:学生指派方式(可选,缺省 recognize=自动识别) */
  assign_mode?: AssignMode
  /** 批量:按名单顺序指派时的起始序号(1-based) */
  assign_order_start?: number | null
  /** 批量:分片提交时复用同一批次 ID(可选) */
  batch_id?: string | null
}

/** 单篇创建响应 */
export interface SingleCreateResponse {
  task_id: number
}

/** 批量创建响应 */
export interface BatchCreateResponse {
  batch_id: string
  task_ids: number[]
}

/** OCR 复核提交 */
export interface OcrConfirmPayload {
  student_name: string
  student_id?: string | null
  transcribed_text: string
}

// ================= 班级(#4) =================

/** 班级信息(含任务数统计与合并标记) */
export interface SchoolClass {
  id: number
  name: string
  note?: string | null
  task_count: number
  /** 已并入的目标班级 ID(来源班级被合并后非空;用于筛选器徽章与入口过滤) */
  merged_into_id?: number | null
  merged_at?: string | null
  created_at: string
}

/** 创建班级请求 */
export interface ClassCreatePayload {
  name: string
  note?: string | null
}

/** 班级数据包导入结果(方向三) */
export interface ClassImportResult {
  class_id: number
  class_name: string
  task_count: number
  error_count: number
  image_count: number
  renamed: boolean
}

// ================= 班级合并 =================

/** 花名册成员匹配处置 */
export interface MergeRosterPlanItem {
  name: string
  source_student_id?: string | null
  target_student_id?: string | null
  /** move=随班迁入 / fill_id=补全学号 / duplicate=去重 / conflict=学号冲突(以目标为准) */
  status: 'move' | 'fill_id' | 'duplicate' | 'conflict'
}

/** 台账登记项处置 */
export interface MergeHomeworkPlanItem {
  id: number
  name: string
  scoring_mode: string
  /** move=随班改挂 / merge_records=记录并入目标同名项 */
  status: 'move' | 'merge_records'
}

/** 班级合并预览(只读,不产生改动) */
export interface ClassMergePreview {
  source: { id: number; name: string; note?: string | null }
  target: { id: number; name: string; note?: string | null }
  counts: {
    tasks: number
    error_records: number
    roster: number
    homework_items: number
    homework_records: number
    exams: number
    exam_papers: number
  }
  roster: { total: number; move: number; fill_id: number; duplicate: number; conflict: number; plan: MergeRosterPlanItem[] }
  homework_items: { total: number; move: number; merge_records: number; plan: MergeHomeworkPlanItem[] }
}

/** 班级合并执行结果 */
export interface ClassMergeResult {
  source: { id: number; name: string }
  target: { id: number; name: string }
  moved: { tasks: number; exams: number }
  roster: { moved: number; filled_id: number; deduplicated: number; conflicts: number }
  homework: { moved_items: number; merged_items: number; moved_records: number }
  log_id: number
}

// ================= 花名册与上传指派(方向二 / 四) =================

/** 学生指派方式:recognize=自动识别(现状) | filename=按文件名 | order=按名单顺序 */
export type AssignMode = 'recognize' | 'filename' | 'order'

/** 花名册成员(顺序 = 导入顺序 = “按名单顺序指派”所用顺序) */
export interface RosterMember {
  id: number
  name: string
  student_id?: string | null
}

/** 花名册导入结果 */
export interface RosterImportResult {
  imported: number
  updated: number
  total: number
}

/** 学生信息修改请求(事后纠错) */
export interface StudentUpdatePayload {
  student_name: string
  student_id?: string | null
}

// ================= 设置中心(统一配置中心) =================

/** 设置项(含元数据与当前值;敏感字段只回传掩码) */
export interface SettingsField {
  key: string
  group: string
  label: string
  control: 'switch' | 'number' | 'text' | 'url' | 'select' | 'secret'
  description: string
  choices: string[]
  min_value?: number | null
  max_value?: number | null
  restart_required: boolean
  sensitive: boolean
  exploratory: boolean
  audience: 'normal' | 'developer'
  value?: boolean | number | string | null
  default_value?: boolean | number | string | null
  has_value: boolean
  masked: string
}

/** 设置分组 */
export interface SettingsGroup {
  id: string
  title: string
  description: string
  fields: SettingsField[]
}

/** 设置视图(含模式状态与探索项快照状态) */
export interface SettingsView {
  groups: SettingsGroup[]
  mock_mode: boolean
  dev_mode: boolean
  encryption_enabled: boolean
  snapshot: { available: boolean; keys: string[]; captured_at?: string | null }
  /** 大模型自动适配记录:端点|模型 -> 已被判定不支持的参数(可清除) */
  llm_learned_params: Record<string, string[]>
}

/** 设置更新请求(仅传入的字段生效;敏感字段 None=不改、空串=清除) */
export type SettingsUpdatePayload = Record<string, boolean | number | string | null>

/** 设置更新结果 */
export interface SettingsUpdateResult {
  applied: string[]
  cleared: string[]
  restart_required: string[]
  message: string
}

/** 探索项一键回退结果 */
export interface SettingsRollbackResult {
  restored: string[]
  message: string
}

/** 管线连通性测试 */
export interface ConnectivityResult {
  ok: boolean
  target: string
  latency_ms?: number | null
  detail: string
}

/** 提示词微调(附录 + 最终提示词预览) */
export interface PromptAppendices {
  appendices: Record<string, string>
  previews: Record<string, string>
  max_chars: number
}

// ================= 数据加密 =================

/** 加密状态 */
export interface EncryptionStatus {
  enabled: boolean
  locked: boolean
  has_password: boolean
  recovery_configured: boolean
  /** 开发模式万能密码是否可用(仅开发模式下可能为 true;生产恒 false) */
  dev_master_enabled: boolean
  migration: {
    running: boolean
    mode?: string | null
    done: number
    total: number
    error?: string | null
  }
}

/** 加密操作通用结果 */
export interface EncryptionActionResult {
  ok: boolean
  detail: string
  data: Record<string, unknown>
}

// ================= 作业台账 =================

export type LedgerScoringMode = 'LEVEL' | 'SCORE' | 'FLAG' | 'STARS'

/** 登记项 */
export interface LedgerItem {
  id: number
  class_id?: number | null
  name: string
  category?: string | null
  scoring_mode: LedgerScoringMode
  config: Record<string, unknown>
  sort_order: number
  archived: number
  created_at: string
}

/** 批量登记条目 */
export interface LedgerEntry {
  student_name: string
  student_id?: string | null
  value: string
  note?: string | null
}

/** 批量登记结果 */
export interface LedgerBatchResult {
  created: number
  updated: number
  deleted: number
  student_synced: number
}

/** 登记记录 */
export interface LedgerRecord {
  id: number
  item_id: number
  item_name?: string | null
  class_id?: number | null
  student_name: string
  student_id?: string | null
  value: string
  score_value?: number | null
  record_date: string
  note?: string | null
  created_at: string
  updated_at: string
}

/** 登记明细分页响应(后端契约字段为 items) */
export interface LedgerRecordListResponse {
  total: number
  items: LedgerRecord[]
}

/** 汇总单元格 */
export interface LedgerCell {
  value: string
  score_value?: number | null
  record_date: string
  count: number
}

/** 班级汇总学生行 */
export interface LedgerStudentRow {
  student_name: string
  student_id?: string | null
  record_count: number
  overall_avg?: number | null
  latest_date?: string | null
  by_item: Record<string, LedgerCell>
}

/** 按登记项统计 */
export interface LedgerItemStat {
  item_id: number
  name: string
  scoring_mode: string
  count: number
  student_count: number
  avg_score_value?: number | null
  distribution: Record<string, number>
}

/** 班级台账汇总 */
export interface LedgerClassSummary {
  class_id?: number | null
  class_name?: string | null
  date_from?: string | null
  date_to?: string | null
  record_count: number
  student_count: number
  items: LedgerItem[]
  item_stats: LedgerItemStat[]
  students: LedgerStudentRow[]
}

/** 登记用学生选项 */
export interface LedgerStudentOption {
  name: string
  student_id?: string | null
}

// ================= 考试统计 =================

export type ExamStatus = 'DRAFT' | 'PROCESSING' | 'READY' | 'REPORTED'
export type ExamPaperStatus = 'PENDING' | 'PROCESSING' | 'DONE' | 'FAILED'

/** 逐题结果条目 */
export interface ExamQuestion {
  no: string
  part?: string | null
  max_score?: number | null
  score?: number | null
  knowledge_tag?: string | null
  canonical_type?: string | null
  note?: string | null
}

/** 学生考卷 */
export interface ExamPaper {
  id: number
  exam_id: number
  student_name: string
  student_id?: string | null
  image_paths: string[]
  ocr_status: ExamPaperStatus
  ocr_error?: string | null
  total_score?: number | null
  question_results: ExamQuestion[]
  teacher_edited: number
  created_at: string
  updated_at: string
}

/** 考试列表条目 */
export interface ExamBrief {
  id: number
  class_id?: number | null
  class_name?: string | null
  name: string
  exam_date: string
  subject: string
  full_score: number
  exam_type?: string | null
  note?: string | null
  status: ExamStatus
  paper_count: number
  done_count: number
  created_at: string
}

/** 考试详情 */
export interface ExamDetail extends ExamBrief {
  papers: ExamPaper[]
  has_report: boolean
}

/** 考试分析报告 */
export interface ExamReport {
  exam_id: number
  report_markdown: string
  stats: Record<string, unknown>
  generated_at: string
}

// ================= 统一统计层 =================

export type StatisticsSource = 'correction' | 'exam' | 'ledger'

/** 总表评估条目 */
export interface GradebookEntry {
  date: string
  source: StatisticsSource
  label: string
  raw: string
  percent?: number | null
}

/** 总表学生行 */
export interface GradebookRow {
  student_name: string
  student_id?: string | null
  record_count: number
  source_average: Partial<Record<StatisticsSource, number | null>>
  overall_percent?: number | null
  latest_percent?: number | null
  delta?: number | null
  entries: GradebookEntry[]
}

/** 学生成绩总表 */
export interface Gradebook {
  filters: Record<string, unknown>
  student_count: number
  students: GradebookRow[]
}

/** 分布统计 */
export interface DistributionResult {
  source: string
  count: number
  average?: number | null
  highest?: number | null
  lowest?: number | null
  pass_rate?: number | null
  buckets: { label: string; count: number }[]
  class_id?: number | null
  target_id?: number | null
}

/** 排行榜 */
export interface RankingsResult {
  class_id?: number | null
  combined: { student_name: string; student_id?: string | null; average: number; count: number }[]
  exam_ranking: {
    exam_name?: string | null
    rows: { student_name: string; student_id?: string | null; total_score: number; full_score: number; percent: number }[]
  }
  progress: { student_name: string; student_id?: string | null; delta: number; latest: number; previous: number }[]
  decline: { student_name: string; delta: number; latest: number; previous: number }[]
  coverage: { student_name: string; student_id?: string | null; covered_items: number; item_total: number; coverage: number }[]
}

/** 趋势分析 */
export interface TrendsResult {
  class_id?: number | null
  student_selected?: { student_name: string; student_id?: string | null } | null
  monthly: { month: string; average?: number | null; count: number }[]
  student_entries: { date: string; source: StatisticsSource; label: string; percent?: number | null }[]
}

// ================= 示范学习(风格迁移) =================

/** 批改风格画像 */
export interface StyleProfile {
  id: number
  name: string
  source_task_id?: number | null
  source_student_name?: string | null
  style_json: Record<string, unknown>
  narrative: string
  status: 'active' | 'inactive'
  created_at: string
  updated_at: string
}

/** 示范学习当前状态 */
export interface StyleStatus {
  active?: StyleProfile | null
  profile_count: number
}

// ================= 教学分析(#2 / #3) =================

/** 典型错例 */
export interface ErrorExample {
  student_name: string
  original_text: string
  corrected_text: string
}

/** 单个考点分类的聚合统计 */
export interface ErrorCategoryStat {
  category: string
  label: string
  count: number
  task_count: number
  student_count: number
  percentage: number
  examples: ErrorExample[]
}

/** 得分分布桶 */
export interface ScoreBucket {
  label: string
  count: number
}

/** 班级共性错因诊断报告 */
export interface ClassDiagnosis {
  filters: Record<string, unknown>
  task_count: number
  student_count: number
  error_total: number
  average_score?: number | null
  score_basis?: string | null
  score_distribution: ScoreBucket[]
  level_distribution: ScoreBucket[]
  category_stats: ErrorCategoryStat[]
  teaching_summary_markdown: string
}

/** 学生批改时间线条目 */
export interface StudentTimelineItem {
  task_id: number
  created_at: string
  overall_score: string
  error_count: number
  top_category_label?: string | null
  assignment_name?: string | null
}

/** 学生画像与错题本 */
export interface StudentProfile {
  student_name: string
  student_id?: string | null
  task_count: number
  first_seen?: string | null
  last_seen?: string | null
  average_score?: number | null
  score_basis?: string | null
  error_total: number
  category_stats: ErrorCategoryStat[]
  recurring: string[]
  timeline: StudentTimelineItem[]
}

/** 学生档案(列表) */
export interface StudentOut {
  id: number
  name: string
  student_id?: string | null
  class_name?: string | null
  created_at: string
}

// ================= 批注核对(#13 方向一) =================

/** 错误片段在转录全文中的定位区间(闭开区间 [start, end)) */
export interface AnnotationSegment {
  start: number
  end: number
  error_index: number
}

/** 单条错误的批注详情(供内联提示展示) */
export interface AnnotationItem {
  index: number
  category_label: string
  canonical_type?: string | null
  error_type: string
  original_text: string
  corrected_text: string
  explanation?: string | null
}

/** 转录批注定位响应(批注核对视图数据源,前端零字符串匹配) */
export interface AnnotationResponse {
  transcribed_text: string
  segments: AnnotationSegment[]
  unlocated: number[]
  annotations: AnnotationItem[]
}

// ================= 健康检查 =================

/** 管线配置摘要 */
export interface PipelineStatus {
  name: string
  ready: boolean
  [key: string]: unknown
}

/** /api/health 响应 */
export interface HealthResponse {
  status: string
  mock_mode: boolean
  allow_auto_fallback: boolean
  default_pipeline: PipelineChoice
  /** 批改工作台默认评分标准(设置中心可配置) */
  default_grading_standard: GradingStandard
  /** 批改工作台默认细致度(设置中心可配置) */
  default_detail_level: DetailLevel
  startup_data_picker?: boolean
  pipeline_a: PipelineStatus
  pipeline_b: PipelineStatus
}

// ================= 前端展示辅助 =================

/** 管线展示元信息 */
export interface PipelineMeta {
  choice: PipelineChoice
  title: string
  subtitle: string
  description: string
  /** 适用场景标签 */
  tags: string[]
}

/** 管线元信息常量(供选择器展示) */
export const PIPELINE_META: Record<PipelineChoice, PipelineMeta> = {
  PIPELINE_A_LOCAL: {
    choice: 'PIPELINE_A_LOCAL',
    title: '管线 A · 本地 VLM',
    subtitle: '零成本 / 高隐私',
    description: '学校自托管 Qwen3.8-27B,单次执行:图像直接输入,一次产出转录、评分与报告。',
    tags: ['数据不出内网', '零 API 成本', '离线可用'],
  },
  PIPELINE_B_CLOUD: {
    choice: 'PIPELINE_B_CLOUD',
    title: '管线 B · 云端解耦',
    subtitle: '高精度 / 深度推理',
    description: 'Qwen2.5-VL / Azure OCR 转录 + DeepSeek 评分,两段式流程,支持人工复核转录。',
    tags: ['潦草手写适配', '支持人工复核', '深度语法分析'],
  },
}

/** 状态展示元信息 */
export const STATUS_META: Record<TaskStatus, { label: string; color: 'default' | 'info' | 'warning' | 'success' | 'error' | 'primary' }> = {
  PENDING: { label: '排队中', color: 'default' },
  PROCESSING: { label: '批改中', color: 'info' },
  WAITING_REVIEW: { label: '待人工复核', color: 'warning' },
  COMPLETED: { label: '已完成', color: 'success' },
  FAILED: { label: '失败', color: 'error' },
}

/** 阶段展示文案 */
export const STAGE_LABEL: Record<TaskStage, string> = {
  UPLOADED: '等待处理',
  OCR: '识别转录',
  GRADING: '评分分析',
  RENDERING: '生成报告',
  DONE: '完成',
}

/** 评分标准展示文案 */
export const STANDARD_LABEL: Record<GradingStandard, string> = {
  GAOKAO: '高考 25 分制',
  DSD: 'DSD / CEFR',
}

/** 细致度展示文案 */
export const DETAIL_LABEL: Record<DetailLevel, string> = {
  LOW: '低 · 仅标错',
  MEDIUM: '中 · 标错+修改',
  HIGH: '高 · 保姆级解析',
}

/* ================= 练习卷(依据历史作业错因生成) ================= */

/** 题型选项(后端为唯一来源) */
export interface PracticeQuestionType {
  key: string
  label: string
}

/** 生成参数选项 */
export interface PracticeOptions {
  question_types: PracticeQuestionType[]
  min_count: number
  max_count: number
  default_count: number
}

/** 作业引用(name 为空 = 未命名作业分组) */
export interface PracticeAssignmentRef {
  class_id?: number | null
  name?: string | null
}

/** 可选作业来源(聚合摘要) */
export interface PracticeSourceItem {
  class_id?: number | null
  class_name?: string | null
  name?: string | null
  task_count: number
  date_from?: string | null
  date_to?: string | null
  top_categories: string[]
}

/** 练习卷摘要(列表) */
export interface PracticeSheetBrief {
  id: number
  class_id?: number | null
  class_name?: string | null
  title: string
  params: { question_types?: string[]; count?: number }
  question_count: number
  model: string
  created_at: string
}

/** 结构化题目 */
export interface PracticeQuestion {
  type: string
  no: string
  stem: string
  answer: string
  explanation?: string | null
}

/** 练习卷完整内容 */
export interface PracticeSheet extends PracticeSheetBrief {
  source: Record<string, unknown>
  questions: PracticeQuestion[]
  worksheet_markdown: string
  answer_markdown: string
}

/** 生成请求 */
export interface PracticeGeneratePayload {
  scope: 'selected' | 'all'
  assignments: PracticeAssignmentRef[]
  question_types: string[]
  count: number
  class_id?: number | null
  title?: string | null
}

/* ================= 维护:存档图片压缩 ================= */

/** 存量压缩进度 */
export interface ImageCompressProgress {
  running: boolean
  done: number
  total: number
  changed: number
  skipped: number
  before_bytes: number
  after_bytes: number
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
}

/* ================= 维护:数据维护(一键清除 / 一键恢复示例数据) ================= */

/** 一键清除所有数据的删除计数(键为业务口径;app_settings 不动) */
export interface MaintenanceClearResult {
  classes: number
  roster: number
  students: number
  tasks: number
  errors: number
  exams: number
  exam_papers: number
  exam_reports: number
  ledger_items: number
  ledger_records: number
  practice_sheets: number
  style_profiles: number
  handwriting_samples: number
  handwriting_models: number
  merge_logs: number
  files: number
  bytes: number
}

/** 一键恢复示例数据的写入计数 */
export interface MaintenanceSeedResult {
  classes: number
  roster: number
  students: number
  tasks: number
  errors: number
  exams: number
  exam_papers: number
  ledger_items: number
  ledger_records: number
  practice_sheets: number
  style_profiles: number
  files: number
  bytes: number
}
