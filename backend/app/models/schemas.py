"""统一数据 Schema 模块(双管线标准化接口)

本模块定义了系统的核心数据契约:
- 无论走管线 A(本地 VLM 单次执行)还是管线 B(云端 OCR + LLM 两段式),
  LLM 输出最终都会被解析并校验为统一的 `EssayCorrectionResult` 模型。
- 前端渲染、数据库存储均基于此 Schema,保证"切换管线不破坏渲染"。
"""

from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# =============================================================
# 枚举定义
# =============================================================


class PipelineChoice(str, Enum):
    """管线选择:双管线热切换的核心开关"""

    PIPELINE_A_LOCAL = "PIPELINE_A_LOCAL"  # 管线 A:本地统一 VLM(零成本 / 高隐私)
    PIPELINE_B_CLOUD = "PIPELINE_B_CLOUD"  # 管线 B:云端解耦(高精度 / 深度推理)


class GradingStandard(str, Enum):
    """评分标准"""

    GAOKAO = "GAOKAO"  # 高考德语:25 分制,严格语法检查(语序/变格/框型结构)
    DSD = "DSD"  # DSD/CEFR:段落结构、逻辑衔接、关联词使用


class DetailLevel(str, Enum):
    """批改细致度"""

    LOW = "LOW"  # 仅标注错误
    MEDIUM = "MEDIUM"  # 标错 + 修改
    HIGH = "HIGH"  # 保姆级解析(标错 + 修改 + 中文语法解析)


class TaskStatus(str, Enum):
    """批改任务状态机"""

    PENDING = "PENDING"  # 排队中
    PROCESSING = "PROCESSING"  # 处理中(具体阶段见 TaskStage)
    WAITING_REVIEW = "WAITING_REVIEW"  # OCR 完成,等待教师人工复核
    COMPLETED = "COMPLETED"  # 已完成
    FAILED = "FAILED"  # 失败


class TaskStage(str, Enum):
    """任务处理阶段(用于前端进度展示)"""

    UPLOADED = "UPLOADED"  # 已上传,等待处理
    OCR = "OCR"  # 正在识别/转录(管线 B 第一阶段;管线 A 内部也在做同样的事)
    GRADING = "GRADING"  # 正在评分与生成报告
    RENDERING = "RENDERING"  # 正在渲染 Markdown 报告
    DONE = "DONE"  # 全部完成


# =============================================================
# 核心业务模型(与规格严格一致)
# =============================================================


class ErrorItem(BaseModel):
    """单条错误条目

    对应规格中的 ErrorItem:
    - original_text:  学生原文中的错误片段
    - corrected_text: 修正后的德语表达(detail_level == LOW 时可为空)
    - error_type:     错误类型,如 "Word Order" / "Case Ending" / "Vocabulary"
    - explanation:    中文语法解析(detail_level == HIGH 时必填)
    """

    original_text: str = Field(description="学生原文中的错误片段")
    corrected_text: str = Field(default="", description="修正后的德语表达")
    error_type: str = Field(description="错误类型,如 Word Order / Case Ending / Vocabulary")
    explanation: Optional[str] = Field(default=None, description="中文语法解析(细致度为 HIGH 时包含)")
    canonical_type: Optional[str] = Field(
        default=None,
        description="标准化考点分类键名(由 error_taxonomy 归一化填充,供统计聚合使用)",
    )


class EssayCorrectionResult(BaseModel):
    """标准化批改结果(双管线统一输出契约)

    该模型是系统输入/输出统一性的核心保证:
    任何管线的 LLM 输出都必须能反序列化为本模型,`markdown_report`
    字段由后端 `report_renderer.py` 确定性渲染后写入。
    """

    student_name: str = Field(description="学生姓名(识别失败时为 '未知')")
    student_id: Optional[str] = Field(default=None, description="学号(可选)")
    transcribed_text: str = Field(description="手写作文的完整转录文本")
    overall_score: str = Field(description="综合得分,如 '18 / 25' 或 'B1 Pass'")
    overall_comment: str = Field(description="总体评价")
    errors: List[ErrorItem] = Field(default_factory=list, description="错误清单")
    highlights: List[str] = Field(default_factory=list, description="词汇与句型亮点")
    markdown_report: str = Field(default="", description="后端预渲染的完整 Markdown 报告")


class OcrExtractionResult(BaseModel):
    """管线 B 第一阶段(OCR)输出模型

    仅包含"转录"相关的信息,不包含评分。
    """

    student_name: str = Field(default="未知", description="学生姓名")
    student_id: Optional[str] = Field(default=None, description="学号")
    transcribed_text: str = Field(default="", description="手写作文转录文本")
    # ---------- OCR 契约 v2(可选字段;旧任务/旧解析输出为 None,零破坏) ----------
    recognition_quality: Optional[str] = Field(
        default=None, description="整体识别质量:high | medium | low"
    )
    quality_note: Optional[str] = Field(
        default=None, description="低识别质量时的中文说明(建议人工核对)"
    )


class IdentityProbeItem(BaseModel):
    """个人信息探测结果项(工作台“强制性姓名识别”;仅展示,不落库不入任务)"""

    index: int = Field(description="展开后的页序(单文件=0;ZIP/PDF=展开序)")
    source_name: str = Field(default="", description="原始文件名(仅供核对)")
    name: Optional[str] = Field(default=None, description="识别到的姓名")
    age: Optional[str] = Field(default=None, description="识别到的年龄")
    student_id: Optional[str] = Field(default=None, description="识别到的学号")
    status: str = Field(default="error", description="ok | empty(未识别到个人信息) | error")
    basis: Optional[str] = Field(
        default=None,
        description="识别输入来源:qr(二维码解码) | canonical/landmarks(依定位点裁剪) | full-image(无定位点整图回退)",
    )


class IdentityProbeResponse(BaseModel):
    """个人信息探测响应(只读端点;不建任务、不写库)"""

    items: List[IdentityProbeItem] = Field(default_factory=list)


class GradingLLMOutput(BaseModel):
    """评分 LLM 输出的中间模型(未含 markdown_report)

    LLM 只负责产出结构化字段,Markdown 报告由后端确定性渲染。
    """

    student_name: str = Field(default="未知", description="学生姓名")
    student_id: Optional[str] = Field(default=None, description="学号")
    transcribed_text: str = Field(default="", description="转录文本(评分阶段通常回填)")
    overall_score: str = Field(default="", description="综合得分")
    overall_comment: str = Field(default="", description="总体评价")
    errors: List[ErrorItem] = Field(default_factory=list, description="错误清单")
    highlights: List[str] = Field(default_factory=list, description="亮点清单")


# =============================================================
# 请求 / 响应模型
# =============================================================


class CorrectionConfig(BaseModel):
    """单次批改的参数配置(前端"动态控制矩阵"的数据载体)"""

    pipeline_choice: PipelineChoice = Field(
        default=PipelineChoice.PIPELINE_A_LOCAL, description="选择的管线"
    )
    grading_standard: GradingStandard = Field(
        default=GradingStandard.GAOKAO, description="评分标准:高考 / DSD"
    )
    detail_level: DetailLevel = Field(
        default=DetailLevel.MEDIUM, description="批改细致度:LOW / MEDIUM / HIGH"
    )
    require_ocr_review: bool = Field(
        default=False,
        description="OCR 完成后是否暂停,等待教师人工复核转录文本(两条管线均支持)",
    )


class OcrConfirmRequest(BaseModel):
    """教师人工复核 OCR 转录结果后的提交内容(管线 B)"""

    student_name: str = Field(description="教师确认/修正后的学生姓名")
    student_id: Optional[str] = Field(default=None, description="教师确认/修正后的学号")
    transcribed_text: str = Field(description="教师确认/修正后的作文转录文本")


class ReportUpdateRequest(BaseModel):
    """教师编辑报告后保存的请求体

    markdown_report 为 null 时表示"恢复系统原始报告"(清除教师编辑版)。
    """

    markdown_report: Optional[str] = Field(
        default=None, description="教师编辑后的 Markdown 报告全文;null 表示清除编辑版、恢复原始报告"
    )


# =============================================================
# 转录批注定位(#13 批注核对视图)
# =============================================================


class AnnotationSegment(BaseModel):
    """错误片段在转录全文中的定位区间(闭开区间 [start, end),单位:字符偏移)"""

    start: int
    end: int
    error_index: int


class AnnotationItem(BaseModel):
    """单条错误的完整批注信息(供前端内联提示展示)"""

    index: int
    category_label: str = Field(description="标准化考点分类的中文标签,如 动词位序")
    canonical_type: Optional[str] = None
    error_type: str
    original_text: str
    corrected_text: str
    explanation: Optional[str] = None


class AnnotationResponse(BaseModel):
    """转录批注定位响应(批注核对视图数据源)

    前端零字符串匹配:仅按 segments 区间切分渲染,未定位条目以清单兜底。
    """

    transcribed_text: str
    segments: List[AnnotationSegment] = Field(description="按位置升序的定位区间列表")
    unlocated: List[int] = Field(default_factory=list, description="未能定位的错误下标(清单兜底)")
    annotations: List[AnnotationItem] = Field(description="全部错误的批注详情(按下标索引)")


class TaskBrief(BaseModel):
    """任务简要信息(用于列表展示)"""

    id: int
    batch_id: Optional[str] = None
    student_name: str
    student_id: Optional[str] = None
    pipeline_choice: PipelineChoice
    pipeline_used: Optional[PipelineChoice] = None  # 实际执行的管线(可能因故障转移而不同)
    grading_standard: GradingStandard
    detail_level: DetailLevel
    status: TaskStatus
    stage: TaskStage
    overall_score: Optional[str] = None
    fallback_triggered: bool = False  # 是否发生过 A -> B 故障转移
    error_message: Optional[str] = None
    # ---------- 班级与作业元数据 ----------
    class_id: Optional[int] = None
    class_name: Optional[str] = None  # 由列表查询 join 班级表填充
    assignment_name: Optional[str] = None
    topic: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskDetail(TaskBrief):
    """任务完整信息(含图片路径、OCR 中间结果、批改结果)"""

    image_paths: List[str] = Field(default_factory=list, description="作文图片的访问路径列表")
    ocr_result: Optional[OcrExtractionResult] = Field(
        default=None, description="OCR 中间结果(供人工复核;两条管线通用)"
    )
    result: Optional[EssayCorrectionResult] = Field(default=None, description="标准化批改结果")
    edited_report: Optional[str] = Field(default=None, description="教师编辑后的 Markdown 报告(优先展示)")
    student_report: Optional[str] = Field(
        default=None,
        description="学生版报告(订正单,后端按需渲染,弱化分数;#11)",
    )
    require_ocr_review: bool = Field(default=False, description="创建任务时是否开启了 OCR 人工复核")
    # ---------- 重新批改追溯(已完成任务原地重跑) ----------
    recorrect_count: int = Field(default=0, description="重新批改次数")
    last_recorrect_at: Optional[datetime] = Field(default=None, description="最近一次重新批改时间")
    prev_overall_score: Optional[str] = Field(default=None, description="上一次批改总分(差异提示)")
    prev_error_count: Optional[int] = Field(default=None, description="上一次批改错因数(差异提示)")
    teacher_message: Optional[str] = Field(
        default=None, description="学生版报告教师寄语(空=系统默认寄语)"
    )


class TaskListResponse(BaseModel):
    """任务列表响应"""

    total: int = Field(description="符合筛选条件的任务总数")
    items: List[TaskBrief] = Field(description="任务列表")


class BatchCreateResponse(BaseModel):
    """批量创建任务后的响应"""

    batch_id: str = Field(description="批次 ID,用于队列页筛选")
    task_ids: List[int] = Field(description="本批次创建的任务 ID 列表")


class SingleCreateResponse(BaseModel):
    """单篇创建任务后的响应"""

    task_id: int = Field(description="新建任务的 ID,前端据此跳转审阅页")


class RetryResponse(BaseModel):
    """重试响应"""

    task_id: int = Field(description="被重试的任务 ID")
    status: TaskStatus = Field(description="重试后任务进入的状态")


class BatchTaskRequest(BaseModel):
    """批量操作(重试 / 删除)请求体"""

    task_ids: List[int] = Field(description="任务 ID 列表", min_length=1)


class BatchRetryResponse(BaseModel):
    """批量重试响应"""

    retried: List[int] = Field(description="已重置并入队的任务 ID")
    skipped: List[int] = Field(description="状态不允许重试而跳过的任务 ID")


class BatchDeleteResponse(BaseModel):
    """批量删除响应"""

    deleted: List[int] = Field(description="已删除的任务 ID")
    skipped: List[int] = Field(description="处理中/排队中而跳过删除的任务 ID")


class HealthResponse(BaseModel):
    """健康检查 / 配置状态响应(供前端设置抽屉展示)"""

    status: str = Field(default="ok", description="服务状态")
    mock_mode: bool = Field(description="是否处于演示模式")
    allow_auto_fallback: bool = Field(description="是否启用 A->B 自动故障转移")
    default_pipeline: PipelineChoice = Field(description="默认管线")
    default_grading_standard: str = Field(default="GAOKAO", description="批改工作台默认评分标准")
    default_detail_level: str = Field(default="MEDIUM", description="批改工作台默认细致度")
    startup_data_picker: bool = Field(default=True, description="启动时显示班级数据包选择")
    pipeline_a: dict = Field(description="管线 A 配置摘要")
    pipeline_b: dict = Field(description="管线 B 配置摘要")


# =============================================================
# 班级与教学分析模型(#2 / #4)
# =============================================================


class ClassCreate(BaseModel):
    """创建班级请求"""

    name: str = Field(min_length=1, max_length=128, description="班级名称(唯一)")
    note: Optional[str] = Field(default=None, max_length=255, description="备注(年级/学期等)")


class ClassOut(BaseModel):
    """班级信息(含任务数统计与合并标记)"""

    id: int
    name: str
    note: Optional[str] = None
    task_count: int = 0
    # 合并标记:来源班级被并入目标班级后非空(用于筛选器徽章与合并入口过滤)
    merged_into_id: Optional[int] = Field(default=None, description="已并入的目标班级 ID")
    merged_at: Optional[datetime] = Field(default=None, description="并入时间")
    created_at: datetime

    model_config = {"from_attributes": True}


class ClassMergeRequest(BaseModel):
    """班级合并请求(把来源班级的数据合并到目标班级)"""

    source_class_id: int = Field(description="来源班级 ID(合并后标记为已并入)")
    target_class_id: int = Field(description="目标班级 ID(接收全部数据)")
    confirm: bool = Field(default=False, description="必须显式传 true 才会执行(二次确认)")


class ClassImportResult(BaseModel):
    """班级数据包导入结果(方向三)"""

    class_id: int = Field(description="新建班级的 ID")
    class_name: str = Field(description="最终使用的班级名称(重名时自动加后缀)")
    task_count: int = Field(description="导入的任务数")
    error_count: int = Field(description="导入的错因记录数")
    image_count: int = Field(description="解压的图片文件数")
    renamed: bool = Field(default=False, description="是否因重名而自动重命名")


class RosterMemberOut(BaseModel):
    """花名册成员(方向四)"""

    id: int
    name: str
    student_id: Optional[str] = None

    model_config = {"from_attributes": True}


class RosterImportResult(BaseModel):
    """花名册导入结果"""

    imported: int = Field(description="新增成员数")
    updated: int = Field(description="更新成员数(补全学号等)")
    total: int = Field(description="导入后班级花名册总人数")


class StudentUpdateRequest(BaseModel):
    """修改任务学生信息(事后纠错,方向四)"""

    student_name: str = Field(min_length=1, max_length=128, description="学生姓名")
    student_id: Optional[str] = Field(default=None, max_length=64, description="学号(可选)")


class ClassPackageManifest(BaseModel):
    """班级数据包 manifest 数据契约(format_version=1,方向三)

    导出端写入 ZIP 内的 manifest.json;导入端仅强校验 format_version,
    其余字段容错读取(extra=ignore),保证跨版本向前兼容。
    """

    model_config = {"extra": "ignore"}

    format_version: int = Field(default=1, description="数据包格式版本(导入端拒绝更高版本)")
    exported_at: Optional[str] = Field(default=None, description="导出时间(ISO 字符串)")
    app_schema_revision: Optional[str] = Field(default=None, description="导出时的 Alembic 版本号")
    class_info: dict = Field(default_factory=dict, alias="class", description="班级信息 {name, note}")
    students: List[dict] = Field(default_factory=list, description="学生列表 [{name, student_id}]")
    tasks: List[dict] = Field(default_factory=list, description="任务列表(含 result/ocr_result/images 等全部字段)")
    error_records: List[dict] = Field(default_factory=list, description="错因记录(以 source_task_id 关联任务)")


class ErrorExample(BaseModel):
    """典型错例(供讲评摘要展示)"""

    student_name: str
    original_text: str
    corrected_text: str


class ErrorCategoryStat(BaseModel):
    """单个考点分类的聚合统计"""

    category: str = Field(description="标准化分类键名,如 VERB_POSITION")
    label: str = Field(description="中文标签,如 动词位序")
    count: int = Field(description="出现总次数")
    task_count: int = Field(description="涉及任务数")
    student_count: int = Field(description="涉及学生数")
    percentage: float = Field(description="该错因占全部错因的比例(0-100,保留 1 位)")
    examples: List[ErrorExample] = Field(default_factory=list, description="典型错例(最多 3 条)")


class ScoreBucket(BaseModel):
    """得分分布桶"""

    label: str
    count: int


class ClassDiagnosis(BaseModel):
    """班级共性错因诊断报告(#2)

    支持三种维度筛选:class_id(按班)/ batch_id(按上传批次)/ 时间范围。
    """

    # 筛选回显
    filters: dict = Field(default_factory=dict, description="本次统计的筛选条件回显")
    # 样本概览
    task_count: int = Field(description="样本任务数(已完成)")
    student_count: int = Field(description="涉及学生数")
    error_total: int = Field(description="错因总条数")
    average_score: Optional[float] = Field(default=None, description="平均分(仅数值型得分,如高考 25 分制)")
    score_basis: Optional[str] = Field(default=None, description="平均分基准,如 '满分 25'")
    score_distribution: List[ScoreBucket] = Field(default_factory=list, description="得分分布")
    level_distribution: List[ScoreBucket] = Field(default_factory=list, description="CEFR 等级分布(DSD 场景)")
    # 错因聚合
    category_stats: List[ErrorCategoryStat] = Field(default_factory=list, description="各考点错因统计(按频次降序)")
    # 讲评摘要(后端确定性渲染的 Markdown)
    teaching_summary_markdown: str = Field(default="", description="讲评摘要 Markdown")
    # ---- 跨模块联动概览(with_ledger / with_exam 查询参数开启,缺省 None 向后兼容) ----
    ledger_overview: Optional["LedgerClassSummary"] = Field(
        default=None, description="作业台账概览(with_ledger=true 时返回)"
    )
    exam_overview: Optional["ExamOverview"] = Field(
        default=None, description="考试统计概览(with_exam=true 时返回)"
    )


class StudentTimelineItem(BaseModel):
    """学生时间线条目(#3;含考试条目,数据联动见作业台账/考试统计设计)"""

    kind: Literal["correction", "exam"] = Field(default="correction", description="条目来源:批改任务 / 考试")
    task_id: Optional[int] = Field(default=None, description="批改任务 ID(kind=correction)")
    exam_id: Optional[int] = Field(default=None, description="考试 ID(kind=exam)")
    created_at: datetime
    overall_score: str
    error_count: int
    top_category_label: Optional[str] = Field(default=None, description="本次最主要的错因分类/考试最弱考点")
    assignment_name: Optional[str] = None


class StudentProfile(BaseModel):
    """学生画像与错题本(#3)"""

    student_name: str
    student_id: Optional[str] = None
    task_count: int = Field(description="已完成批改次数")
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    average_score: Optional[float] = Field(default=None, description="平均分(数值型得分)")
    score_basis: Optional[str] = Field(default=None, description="平均分基准")
    error_total: int = Field(description="累计错因条数")
    category_stats: List[ErrorCategoryStat] = Field(default_factory=list, description="各考点错因统计")
    recurring: List[str] = Field(default_factory=list, description="复现错因(出现≥2次的考点中文标签)")
    timeline: List[StudentTimelineItem] = Field(default_factory=list, description="时间线(批改任务 + 考试,按时间正序)")
    # ---- 跨模块联动(读时聚合,见作业台账/考试统计) ----
    homework_summary: Optional["LedgerStudentSummary"] = Field(
        default=None, description="作业台账摘要(实时聚合;无记录时为 None)"
    )
    exam_summary: Optional["ExamStudentSummary"] = Field(
        default=None, description="考试记录摘要(实时聚合;无记录时为 None)"
    )


# =============================================================
# 错题记录与学生档案输出模型
# =============================================================


class ErrorRecordOut(BaseModel):
    """错题记录(二期错题本/共性错因分析的基础数据)"""

    id: int
    student_name: str
    student_id: Optional[str] = None
    task_id: int
    error_type: str
    canonical_type: str = "OTHER"
    original_text: str
    corrected_text: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StudentOut(BaseModel):
    """学生信息(二期错题本索引)"""

    id: int
    name: str
    student_id: Optional[str] = None
    class_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# =============================================================
# 作业台账(日常作业登记与汇总)
# =============================================================

#: 计分模式字面量(LEVEL=等级制 / SCORE=分值制 / FLAG=完成度 / STARS=星级)
ScoringMode = Literal["LEVEL", "SCORE", "FLAG", "STARS"]


class LedgerItemCreate(BaseModel):
    """新建登记项(登记项与计分口径可配置)"""

    class_id: Optional[int] = Field(default=None, description="归属班级;空=全局模板项")
    name: str = Field(min_length=1, max_length=128)
    category: Optional[str] = Field(default=None, max_length=32, description="分类标签(书面作业/背诵/听写/课堂表现/订正/其他)")
    scoring_mode: ScoringMode = Field(default="LEVEL")
    config: dict = Field(default_factory=dict, description="模式配置(等级档位/满分/步长等)")
    sort_order: int = Field(default=0)


class LedgerItemUpdate(BaseModel):
    """更新登记项(仅传入的字段生效)"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    category: Optional[str] = Field(default=None, max_length=32)
    scoring_mode: Optional[ScoringMode] = None
    config: Optional[dict] = None
    sort_order: Optional[int] = None


class LedgerItemOut(BaseModel):
    """登记项输出"""

    id: int
    class_id: Optional[int] = None
    name: str
    category: Optional[str] = None
    scoring_mode: str
    config: dict = Field(default_factory=dict)
    sort_order: int = 0
    archived: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class LedgerEntryIn(BaseModel):
    """批量登记的单条学生条目"""

    student_name: str = Field(min_length=1, max_length=128)
    student_id: Optional[str] = Field(default=None, max_length=64)
    value: str = Field(default="", max_length=64, description="登记值;空=撤销该条记录")
    note: Optional[str] = Field(default=None, max_length=255)


class LedgerBatchRequest(BaseModel):
    """批量登记请求(同一登记项 + 同一日期 + 多名学生)"""

    item_id: int
    record_date: date
    class_id: Optional[int] = Field(
        default=None, description="登记归属班级(可覆盖登记项默认归属;用于全局模板项)"
    )
    entries: List[LedgerEntryIn] = Field(min_length=1, max_length=500)


class LedgerBatchResult(BaseModel):
    """批量登记结果"""

    created: int
    updated: int
    deleted: int
    student_synced: int = Field(description="同步学生档案的条数")


class LedgerRecordOut(BaseModel):
    """登记记录输出(带登记项名称便于展示)"""

    id: int
    item_id: int
    item_name: Optional[str] = None
    class_id: Optional[int] = None
    student_name: str
    student_id: Optional[str] = None
    value: str
    score_value: Optional[float] = None
    record_date: date
    note: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LedgerRecordUpdate(BaseModel):
    """单条纠错"""

    value: str = Field(max_length=64)
    note: Optional[str] = Field(default=None, max_length=255)


class LedgerRecordListResponse(BaseModel):
    """登记明细分页响应"""

    total: int
    items: List[LedgerRecordOut] = Field(default_factory=list)


class LedgerCellOut(BaseModel):
    """汇总矩阵单元格(该生该项在时间范围内的最近一次登记)"""

    value: str
    score_value: Optional[float] = None
    record_date: date
    count: int = 1


class LedgerStudentOption(BaseModel):
    """登记用学生选项(花名册优先)"""

    name: str
    student_id: Optional[str] = None


class LedgerStudentRow(BaseModel):
    """班级汇总中的学生行(学生×登记项矩阵)"""

    student_name: str
    student_id: Optional[str] = None
    record_count: int = 0
    overall_avg: Optional[float] = Field(default=None, description="范围内容量归一均分(0-100)")
    latest_date: Optional[date] = None
    by_item: dict = Field(default_factory=dict, description="item_id(str) -> LedgerCellOut")


class LedgerItemStat(BaseModel):
    """按登记项的汇总统计"""

    item_id: int
    name: str
    scoring_mode: str
    count: int
    student_count: int
    avg_score_value: Optional[float] = None
    distribution: dict = Field(default_factory=dict, description="登记值 -> 次数")


class LedgerClassSummary(BaseModel):
    """班级台账汇总(批量、长期、可统计)"""

    class_id: Optional[int] = None
    class_name: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    record_count: int = 0
    student_count: int = 0
    items: List[LedgerItemOut] = Field(default_factory=list)
    item_stats: List[LedgerItemStat] = Field(default_factory=list)
    students: List[LedgerStudentRow] = Field(default_factory=list)


class LedgerPersonItem(BaseModel):
    """学生个人台账:单项聚合"""

    item_id: int
    name: str
    scoring_mode: str
    count: int
    avg_score_value: Optional[float] = None
    latest_value: str = ""
    latest_date: Optional[date] = None


class LedgerStudentSummary(BaseModel):
    """学生个人台账摘要(供学生画像读时聚合)"""

    record_count: int = 0
    average_score_value: Optional[float] = None
    items: List[LedgerPersonItem] = Field(default_factory=list)
    recent: List[LedgerRecordOut] = Field(default_factory=list)


class LedgerPresetRequest(BaseModel):
    """一键创建预设登记项"""

    class_id: Optional[int] = Field(default=None, description="创建到指定班级;空=全局模板")


# =============================================================
# 考试统计(考卷识别与班级分析)
# =============================================================


class ExamCreate(BaseModel):
    """新建考试"""

    class_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=128)
    exam_date: date
    subject: str = Field(default="德语", max_length=32)
    full_score: float = Field(default=100.0, gt=0, le=1000)
    exam_type: Optional[str] = Field(default=None, max_length=32, description="月考/期中/期末/其他")
    note: Optional[str] = Field(default=None, max_length=255)


class ExamUpdate(BaseModel):
    """更新考试(仅传入的字段生效)"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    exam_date: Optional[date] = None
    subject: Optional[str] = Field(default=None, max_length=32)
    full_score: Optional[float] = Field(default=None, gt=0, le=1000)
    exam_type: Optional[str] = Field(default=None, max_length=32)
    note: Optional[str] = Field(default=None, max_length=255)


class ExamQuestion(BaseModel):
    """逐题结果条目(人工修订输入校验;听力题不产生条目)"""

    no: str = Field(max_length=32, description="题号")
    part: Optional[str] = Field(default=None, max_length=64, description="题型/板块(语法/阅读/写作/…)")
    max_score: Optional[float] = Field(default=None, ge=0, le=1000)
    score: Optional[float] = Field(default=None, ge=0, le=1000)
    knowledge_tag: Optional[str] = Field(default=None, max_length=64, description="考点标签(与错因分类词表一致)")
    canonical_type: Optional[str] = Field(default=None, max_length=64, description="标准化考点键名(服务端归一化)")
    note: Optional[str] = Field(default=None, max_length=255)


class ExamPaperUpdate(BaseModel):
    """人工修订考卷(教师修订后 teacher_edited=1,以人工值为准)"""

    student_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    student_id: Optional[str] = Field(default=None, max_length=64)
    total_score: Optional[float] = Field(default=None, ge=0, le=1000)
    question_results: Optional[List[ExamQuestion]] = None


class ExamPaperOut(BaseModel):
    """学生考卷记录输出"""

    id: int
    exam_id: int
    student_name: str
    student_id: Optional[str] = None
    image_paths: List[str] = Field(default_factory=list)
    ocr_status: str
    ocr_error: Optional[str] = None
    total_score: Optional[float] = None
    question_results: List[dict] = Field(default_factory=list)
    teacher_edited: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExamBrief(BaseModel):
    """考试列表条目"""

    id: int
    class_id: Optional[int] = None
    class_name: Optional[str] = None
    name: str
    exam_date: date
    subject: str
    full_score: float
    exam_type: Optional[str] = None
    note: Optional[str] = None
    status: str
    paper_count: int = 0
    done_count: int = Field(default=0, description="OCR 已完成的考卷数")
    created_at: datetime


class ExamDetail(ExamBrief):
    """考试详情(含考卷列表与报告状态)"""

    papers: List[ExamPaperOut] = Field(default_factory=list)
    has_report: bool = False


class ExamReportOut(BaseModel):
    """考试分析报告(持久化)"""

    exam_id: int
    report_markdown: str
    stats: dict = Field(default_factory=dict)
    generated_at: datetime


class ExamKnowledgeStat(BaseModel):
    """考试知识点失分聚合条目"""

    canonical_type: str
    label: str
    count: int = Field(description="失分题次数")
    student_count: int = 0
    tip: Optional[str] = Field(default=None, description="讲评建议")


class ExamStudentEntry(BaseModel):
    """学生单场考试记录"""

    exam_id: int
    exam_name: str
    exam_date: date
    total_score: Optional[float] = None
    full_score: float
    percent: Optional[float] = Field(default=None, description="得分率(0-100)")
    class_id: Optional[int] = None


class ExamStudentSummary(BaseModel):
    """学生考试记录摘要(供学生画像读时聚合)"""

    paper_count: int = 0
    average_percent: Optional[float] = None
    entries: List[ExamStudentEntry] = Field(default_factory=list)
    knowledge_stats: List[ExamKnowledgeStat] = Field(default_factory=list)


class ExamOverviewItem(BaseModel):
    """班级考试概览条目"""

    exam_id: int
    name: str
    exam_date: date
    paper_count: int = 0
    average_percent: Optional[float] = None
    pass_rate: Optional[float] = Field(default=None, description="及格率(按设置中心及格线口径)")
    report_ready: bool = False


class ExamOverview(BaseModel):
    """班级考试统计概览(供班级分析联动)"""

    exam_count: int = 0
    items: List[ExamOverviewItem] = Field(default_factory=list)


# ---- 前向引用重建(台账/考试类型定义在引用方之后,需显式重建) ----
ClassDiagnosis.model_rebuild()
StudentProfile.model_rebuild()
StudentTimelineItem.model_rebuild()


# =============================================================
# 示范学习(示例范文风格迁移)
# =============================================================


class StyleLearnRequest(BaseModel):
    """从已完成批改任务归纳风格画像"""

    task_id: int = Field(description="示例范文的批改任务 ID(须为已完成状态)")
    name: Optional[str] = Field(default=None, max_length=128, description="画像名称(缺省自动命名)")


class StyleProfileUpdate(BaseModel):
    """编辑风格画像(仅名称与注入文本,结构化维度由归纳生成)"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    narrative: Optional[str] = Field(default=None, max_length=2000, description="注入批改 Prompt 的风格描述")


class StyleProfileOut(BaseModel):
    """风格画像输出"""

    id: int
    name: str
    source_task_id: Optional[int] = None
    source_student_name: Optional[str] = None
    style_json: dict = Field(default_factory=dict, description="四维度结构化画像")
    narrative: str = ""
    status: str = Field(description="active(生效中) | inactive")
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StyleStatus(BaseModel):
    """示范学习当前状态(供工作台 chip 与设置中心展示)"""

    active: Optional[StyleProfileOut] = Field(default=None, description="当前生效的画像;未启用时为 None")
    profile_count: int = Field(default=0, description="画像总数")


# =============================================================
# 设置中心(统一配置:视图 / 更新 / 连通性测试 / 提示词微调)
# =============================================================


class SettingsFieldOut(BaseModel):
    """设置项(含元数据与当前值;敏感字段只回传掩码)"""

    key: str = Field(description=".env 键名(与 config.py 属性一一对应)")
    group: str
    label: str
    control: str = Field(description="控件类型:switch|number|text|url|select|secret")
    description: str = ""
    choices: List[str] = Field(default_factory=list)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    restart_required: bool = False
    sensitive: bool = False
    exploratory: bool = Field(default=False, description="探索性设置(支持恢复默认/一键回退)")
    audience: str = Field(default="normal", description="normal|developer(开发者项仅在开发模式可见可改)")
    value: bool | int | float | str | None = Field(default=None, description="当前值(敏感字段为 None)")
    default_value: bool | int | float | str | None = Field(default=None, description="系统默认值")
    has_value: bool = Field(default=False, description="敏感字段:是否已配置")
    masked: str = Field(default="", description="敏感字段:脱敏预览(如 sk-***ab12)")


class SettingsGroupOut(BaseModel):
    id: str
    title: str
    description: str = ""
    fields: List[SettingsFieldOut] = Field(default_factory=list)


class SettingsViewResponse(BaseModel):
    """设置中心视图(分组 + 当前值 + 模式状态 + 探索项快照状态)"""

    groups: List[SettingsGroupOut] = Field(default_factory=list)
    mock_mode: bool = False
    dev_mode: bool = False
    encryption_enabled: bool = False
    snapshot: dict = Field(default_factory=dict, description="{available, keys, captured_at}")
    llm_learned_params: Dict[str, List[str]] = Field(
        default_factory=dict, description="大模型自动适配记录:端点|模型 -> 已被判定不支持的参数"
    )


class SettingsUpdateRequest(BaseModel):
    """更新设置(仅传入的字段生效;敏感字段 None=不改、空串=清除、其余=设置)"""

    model_config = {"extra": "forbid"}

    default_pipeline: Optional[Literal["PIPELINE_A_LOCAL", "PIPELINE_B_CLOUD"]] = None
    mock_mode: Optional[bool] = None
    allow_auto_fallback: Optional[bool] = None
    max_concurrent_tasks: Optional[int] = None
    local_vlm_base_url: Optional[str] = None
    local_vlm_api_key: Optional[str] = None
    local_vlm_model: Optional[str] = None
    local_vlm_timeout: Optional[float] = None
    local_ocr_only: Optional[bool] = None
    ocr_provider: Optional[Literal["vlm_openai", "azure"]] = None
    ocr_base_url: Optional[str] = None
    ocr_api_key: Optional[str] = None
    ocr_model: Optional[str] = None
    ocr_timeout: Optional[float] = None
    azure_ocr_endpoint: Optional[str] = None
    azure_ocr_key: Optional[str] = None
    deepseek_base_url: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    deepseek_model: Optional[str] = None
    deepseek_reasoning_model: Optional[str] = None
    deepseek_timeout: Optional[float] = None
    deepseek_use_reasoning: Optional[bool] = None
    image_preprocess: Optional[bool] = None
    image_shadow_removal: Optional[bool] = None
    image_grayscale: Optional[bool] = None
    max_batch_files: Optional[int] = None
    max_upload_total_mb: Optional[int] = None
    upload_dir: Optional[str] = None
    database_url: Optional[str] = None
    server_port: Optional[int] = None
    settings_admin_token: Optional[str] = None
    dev_mode: Optional[bool] = None
    default_grading_standard: Optional[Literal["GAOKAO", "DSD"]] = None
    default_detail_level: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    score_pass_line: Optional[int] = None
    student_report_show_score: Optional[bool] = None
    student_report_show_tips: Optional[bool] = None
    image_compress_enabled: Optional[bool] = None
    image_compress_max_side: Optional[int] = None
    image_compress_quality: Optional[int] = None
    sheet_align_enabled: Optional[bool] = None
    name_pre_ocr_enabled: Optional[bool] = None
    force_name_recognition: Optional[bool] = None
    handwriting_ocr_level: Optional[Literal["auto", "light", "medium", "precise"]] = None
    ui_profile: Optional[Literal["balanced", "efficiency", "premium"]] = None
    startup_data_picker: Optional[bool] = None
    ocr_anomaly_enabled: Optional[bool] = None
    ocr_anomaly_threshold: Optional[int] = None
    ocr_anomaly_max_retry: Optional[int] = None
    ocr_anomaly_force_review: Optional[bool] = None
    # ---- 大模型超参数(三态:auto=自动适配 / omit=不发送该键 / 具体值) ----
    local_vlm_temperature: Optional[str] = None
    local_vlm_top_p: Optional[str] = None
    local_vlm_max_tokens: Optional[str] = None
    local_vlm_presence_penalty: Optional[str] = None
    local_vlm_frequency_penalty: Optional[str] = None
    local_vlm_seed: Optional[str] = None
    local_vlm_stop: Optional[str] = None
    local_vlm_response_format: Optional[str] = None
    local_vlm_stream: Optional[str] = None
    local_vlm_extra_params: Optional[str] = None
    ocr_temperature: Optional[str] = None
    ocr_top_p: Optional[str] = None
    ocr_max_tokens: Optional[str] = None
    ocr_presence_penalty: Optional[str] = None
    ocr_frequency_penalty: Optional[str] = None
    ocr_seed: Optional[str] = None
    ocr_stop: Optional[str] = None
    ocr_response_format: Optional[str] = None
    ocr_stream: Optional[str] = None
    ocr_extra_params: Optional[str] = None
    deepseek_temperature: Optional[str] = None
    deepseek_top_p: Optional[str] = None
    deepseek_max_tokens: Optional[str] = None
    deepseek_presence_penalty: Optional[str] = None
    deepseek_frequency_penalty: Optional[str] = None
    deepseek_seed: Optional[str] = None
    deepseek_stop: Optional[str] = None
    deepseek_response_format: Optional[str] = None
    deepseek_stream: Optional[str] = None
    deepseek_extra_params: Optional[str] = None


class SettingsUpdateResult(BaseModel):
    applied: List[str] = Field(default_factory=list)
    cleared: List[str] = Field(default_factory=list)
    restart_required: List[str] = Field(default_factory=list)
    message: str = ""


class SettingsResetRequest(BaseModel):
    key: str = Field(description=".env 键名(仅探索性设置支持恢复默认)")


class SettingsRollbackResult(BaseModel):
    restored: List[str] = Field(default_factory=list)
    message: str = ""


class ConnectivityRequest(BaseModel):
    target: Literal["pipeline_a", "ocr", "deepseek"]
    overrides: Optional[dict] = Field(default=None, description="未保存的候选值(field 名 -> 值),便于先测后存")


class ConnectivityResult(BaseModel):
    ok: bool
    target: str
    latency_ms: Optional[int] = None
    detail: str = ""


class PromptAppendicesOut(BaseModel):
    appendices: dict = Field(default_factory=dict, description="kind -> 追加指令文本")
    previews: dict = Field(default_factory=dict, description="kind -> 拼装后的完整系统提示词")
    max_chars: int = 2000


class PromptAppendicesUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    pipeline_a: Optional[str] = Field(default=None, max_length=2000)
    ocr: Optional[str] = Field(default=None, max_length=2000)
    grading: Optional[str] = Field(default=None, max_length=2000)


# =============================================================
# 数据加密(学生数据保护 / 忘记密码 Plan B)
# =============================================================


class EncryptionStatusOut(BaseModel):
    enabled: bool = False
    locked: bool = Field(default=False, description="已设置口令但尚未解锁")
    has_password: bool = Field(default=False, description="是否已设置口令(密钥已包裹存储)")
    recovery_configured: bool = Field(default=False)
    dev_master_enabled: bool = Field(
        default=False, description="开发模式万能密码是否可用(仅开发模式下可能为 true;生产恒 false,绝不返回密码或其摘要)"
    )
    migration: dict = Field(default_factory=dict, description="迁移进度 {running, mode, done, total, error}")


class EncryptionSetupRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128, description="至少 8 位,建议字母+数字")


class EncryptionUnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class EncryptionChangeRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class EncryptionMigrateRequest(BaseModel):
    mode: Literal["encrypt", "decrypt"] = "encrypt"


class EncryptionDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    mode: Literal["keep_ciphertext", "decrypt_all"] = "decrypt_all"
    confirm: bool = False


class EncryptionRecoveryGenerateOut(BaseModel):
    recovery_key: str = Field(description="一次性恢复密钥,请立即离线保存;仅此一次展示")


class EncryptionRecoveryResetRequest(BaseModel):
    recovery_key: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class EncryptionPlanBRequest(BaseModel):
    confirm_phrase: str = Field(description='必须为 "清空重建" 才会执行')


class EncryptionActionResult(BaseModel):
    ok: bool = True
    detail: str = ""
    data: dict = Field(default_factory=dict)


# =============================================================
# 重新批改(已完成任务原地重跑)与教师寄语
# =============================================================


class TaskRecorrectRequest(BaseModel):
    """重新批改请求:仅传入的字段覆盖任务原配置,未传入项一律沿用原值

    注:示范学习风格画像、提示词微调附录与其他运行配置由服务端在批改时
    实时读取(与工作台流程一致),无需随请求传递。
    """

    pipeline_choice: Optional[Literal["PIPELINE_A_LOCAL", "PIPELINE_B_CLOUD"]] = None
    grading_standard: Optional[Literal["GAOKAO", "DSD"]] = None
    detail_level: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    require_ocr_review: Optional[bool] = None
    student_name: Optional[str] = Field(default=None, max_length=128)
    student_id: Optional[str] = Field(default=None, max_length=64)
    class_id: Optional[int] = Field(default=None, ge=1)
    assignment_name: Optional[str] = Field(default=None, max_length=128)
    topic: Optional[str] = Field(default=None, max_length=2000)


class TeacherMessageUpdate(BaseModel):
    """学生版"教师寄语"更新(空串 / None = 恢复系统默认寄语)"""

    teacher_message: Optional[str] = Field(default=None, max_length=500)


class TranscriptUpdateRequest(BaseModel):
    """转录原文修订(教师校正识别偏差;保存后重渲染系统报告,教师编辑版不受影响)"""

    transcribed_text: str = Field(min_length=1, max_length=50000)


# =============================================================
# 练习卷(从历史作业错因数据生成;只读溯源,不写回统计)
# =============================================================


class PracticeQuestionTypeOut(BaseModel):
    key: str
    label: str


class PracticeOptionsOut(BaseModel):
    """生成参数选项(题型集合为唯一来源,前端动态拉取)"""

    question_types: List[PracticeQuestionTypeOut] = Field(default_factory=list)
    min_count: int = 5
    max_count: int = 50
    default_count: int = 10


class PracticeAssignmentRef(BaseModel):
    """作业引用(class_id + name;name 为空表示「未命名作业」分组)"""

    class_id: Optional[int] = None
    name: Optional[str] = Field(default=None, max_length=128)


class PracticeSourceItem(BaseModel):
    """可选作业来源(聚合摘要:任务数 / 日期区间 / 高频错因 Top3)"""

    class_id: Optional[int] = None
    class_name: Optional[str] = None
    name: Optional[str] = None
    task_count: int = 0
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    top_categories: List[str] = Field(default_factory=list)


class PracticeSourcesOut(BaseModel):
    assignments: List[PracticeSourceItem] = Field(default_factory=list)


class PracticeGenerateRequest(BaseModel):
    """生成练习卷请求:来源(一次/多次/全部作业)+ 题型多选 + 数量"""

    scope: Literal["selected", "all"] = "selected"
    assignments: List[PracticeAssignmentRef] = Field(default_factory=list)
    question_types: List[str] = Field(default_factory=list, min_length=1)
    count: int = Field(default=10, ge=5, le=50)
    class_id: Optional[int] = Field(default=None, description="目标班级(仅用于归档筛选,可空)")
    title: Optional[str] = Field(default=None, max_length=128)


class PracticeQuestion(BaseModel):
    """结构化题目(与试卷/答案 Markdown 同源)"""

    type: str
    no: str
    stem: str
    answer: str
    explanation: Optional[str] = None


class PracticeSheetBrief(BaseModel):
    """练习卷摘要(列表视图)"""

    id: int
    class_id: Optional[int] = None
    class_name: Optional[str] = None
    title: str
    params: dict = Field(default_factory=dict)
    question_count: int = 0
    model: str = ""
    created_at: datetime


class PracticeSheetOut(PracticeSheetBrief):
    """练习卷完整内容(试卷本体 + 标准答案 + 结构化题目 + 来源快照)"""

    source: dict = Field(default_factory=dict)
    questions: List[PracticeQuestion] = Field(default_factory=list)
    worksheet_markdown: str = ""
    answer_markdown: str = ""


# =============================================================
# 手写样本模型(花名册“手写模型”采集与训练)与全局状态
# =============================================================
class HandwritingSentenceOut(BaseModel):
    """抄写素材(题目句 + 正文句;本地句库生成)"""

    topic: str
    sentence: str


class HandwritingSampleItemOut(BaseModel):
    """手写样本列表项"""

    id: int
    status: str
    student_name: Optional[str] = None
    student_id: Optional[str] = None
    has_crop: bool = False
    has_features: bool = False
    created_at: datetime


class HandwritingSampleUploadOut(BaseModel):
    """样本上传受理结果"""

    accepted: int
    sample_ids: List[int] = Field(default_factory=list)


class HandwritingTrainStatusOut(BaseModel):
    """训练/样本处理进度(含真实 ETA;数据不足时为 null)"""

    running: bool
    done: int = 0
    total: int = 0
    percent: float = 0.0
    eta_seconds: Optional[int] = None
    stage: str = ""
    errors: int = 0
    recent_avg_seconds: Optional[float] = None
    model_ready: bool = False
    samples_count: int = 0
    pending_bind: int = 0


class HandwritingModelOut(BaseModel):
    """班级手写模型概览(含本机画像与档位)"""

    class_id: int
    ready: bool = False
    samples_count: int = 0
    students_count: int = 0
    pending_bind: int = 0
    level: str = "light"
    configured_level: Optional[str] = None
    updated_at: Optional[str] = None
    cores: int = 0
    ram_gb: Optional[float] = None
    recommended_level: str = "light"


class HandwritingBindRequest(BaseModel):
    """手写样本指定学生"""

    student_name: str = Field(min_length=1, max_length=64)
    student_id: Optional[str] = Field(default=None, max_length=64)


class SystemProfileOut(BaseModel):
    """本机配置与档位推荐(auto 档推荐依据)"""

    cores: int
    ram_gb: Optional[float] = None
    recommended_level: str
    configured_level: str
    effective_level: str


class ActiveTaskOut(BaseModel):
    """全局状态:单个活跃任务(真实进度)"""

    kind: str
    label: str
    percent: Optional[float] = None
    eta_seconds: Optional[int] = None
    status_text: str = ""


class CheckupOut(BaseModel):
    """真实自检结果(严禁占位式假提示)"""

    ok: bool
    summary: str
    ok_items: List[str] = Field(default_factory=list)
    problems: List[str] = Field(default_factory=list)
    checked_at: str


class StatusOverviewOut(BaseModel):
    """全局状态总览(真实进度 + 真实自检)"""

    active: List[ActiveTaskOut] = Field(default_factory=list)
    checkup: CheckupOut
