"""数据库 ORM 模型定义(SQLAlchemy 2.0 声明式)

包含四张核心表:
- classes:          班级(教学分析/筛选的归属维度)
- correction_tasks: 批改任务(系统主表)
- students:         学生档案(为二期错题本预留)
- error_records:    错题记录(批改完成后自动写入,为二期错题本/共性错因分析积累数据)
"""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 透明加解密类型(设置中心"数据加密":启用后自动加密/解密;未启用时为直通,行为不变)
from app.models.encrypted_types import EncryptedDeterministic, EncryptedJSON, EncryptedText


def utc_now() -> datetime:
    """返回当前 UTC 时间(数据库统一使用 UTC 存储)"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """ORM 基类"""

    pass


class SchoolClass(Base):
    """班级表(教学闭环的分析与筛选维度)

    教师可创建多个班级(如"高二(3)班德语""),任务与学生据此归属;
    班级共性错因分析、按班筛选队列、批量导出等均以本表为维度。
    """

    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # 备注:年级、学期、教材说明等
    note: Mapped[str | None] = mapped_column(String(255), default=None)
    # ---------- 合并标记(班级合并功能) ----------
    # 来源班级被并入目标班级后:指向目标班级 ID;为空表示未合并
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), index=True, default=None
    )
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClassMergeLog(Base):
    """班级合并日志(来源标注与可追溯性)

    - 每次成功合并写入一条:记录来源/目标班级与各项数据的搬运/去重统计;
    - 采用纯整数字段(不加外键):即使班级后续被删除,日志仍可追溯;
    - 合并本身在单个事务内执行,失败自动回滚,不会产生半完成状态。
    """

    __tablename__ = "class_merge_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_class_id: Mapped[int] = mapped_column(Integer, index=True)
    source_class_name: Mapped[str] = mapped_column(String(128))
    target_class_id: Mapped[int] = mapped_column(Integer, index=True)
    target_class_name: Mapped[str] = mapped_column(String(128))
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClassRoster(Base):
    """班级花名册(方向四:上传时的学生指派与识别对齐基础)

    教师导入的应到学生名单(姓名,学号);与 students 表(历史档案)分离:
    - 本表表达"这个班有哪些人"(可提前建立,不依赖批改);
    - 上传时按文件名/按顺序匹配本表 → 预填任务的 student_name/student_id。
    """

    __tablename__ = "class_roster"
    __table_args__ = (
        # 同一班级内姓名唯一(学号可选;重名场景由教师用学号区分) —— 保留学号字段但仅按姓名去重
        UniqueConstraint("class_id", "name", name="uq_class_roster_class_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    name: Mapped[str] = mapped_column(EncryptedDeterministic(128))
    student_id: Mapped[str | None] = mapped_column(EncryptedDeterministic(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CorrectionTask(Base):
    """批改任务表(系统主表)

    一行 = 一位学生的一份作文批改任务(可包含多页图片)。
    """

    __tablename__ = "correction_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 批次 ID:批量上传时自动生成,单篇上传为 None
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)

    # ---------- 班级与作业元数据 ----------
    # 归属班级(可空:未指定班级的个人使用场景)
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), index=True, default=None
    )
    # 作业批次名称(如 "第一次月考作文")
    assignment_name: Mapped[str | None] = mapped_column(EncryptedText, default=None)
    # 作文题目/要求描述
    topic: Mapped[str | None] = mapped_column(EncryptedText, default=None)

    # ---------- 学生信息 ----------
    student_name: Mapped[str] = mapped_column(EncryptedDeterministic(128), default="未知")
    student_id: Mapped[str | None] = mapped_column(EncryptedDeterministic(64), default=None)

    # ---------- 批改配置(动态控制矩阵) ----------
    pipeline_choice: Mapped[str] = mapped_column(String(32), default="PIPELINE_A_LOCAL")
    pipeline_used: Mapped[str | None] = mapped_column(String(32), default=None)  # 实际执行管线
    grading_standard: Mapped[str] = mapped_column(String(16), default="GAOKAO")
    detail_level: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    require_ocr_review: Mapped[int] = mapped_column(Integer, default=0)  # 是否开启 OCR 人工复核(0/1)

    # ---------- 状态机 ----------
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    # 是否发生了 A -> B 故障转移
    fallback_triggered: Mapped[int] = mapped_column(Integer, default=0)

    # ---------- 重新批改追溯(已完成任务原地重跑) ----------
    recorrect_count: Mapped[int] = mapped_column(Integer, default=0)
    last_recorrect_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # 上一次批改的结果摘要(用于重新批改完成后的差异提示)
    prev_overall_score: Mapped[str | None] = mapped_column(EncryptedText, default=None)
    prev_error_count: Mapped[int | None] = mapped_column(Integer, default=None)
    # 阶段性进度(0.0 ~ 1.0,用于前端进度条)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    # ---------- 数据字段(JSON 存储) ----------
    # 作文图片的相对路径列表(相对 UPLOAD_DIR)
    image_paths: Mapped[list] = mapped_column(JSON, default=list)
    # 管线 B 的 OCR 中间结果 {"student_name":..., "student_id":..., "transcribed_text":...}
    ocr_result: Mapped[dict | None] = mapped_column(EncryptedJSON, default=None)
    # 标准化批改结果(EssayCorrectionResult 的 dict 形式)
    result: Mapped[dict | None] = mapped_column(EncryptedJSON, default=None)
    # 教师手工编辑后的完整 Markdown 报告(优先于 result.markdown_report 展示)
    edited_report: Mapped[str | None] = mapped_column(EncryptedText, default=None)
    # 学生版报告"教师寄语"(教师可编辑;为空时使用系统默认寄语)
    teacher_message: Mapped[str | None] = mapped_column(EncryptedText, default=None)

    # ---------- 时间戳 ----------
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Student(Base):
    """学生档案表(二期错题本的索引基础)

    批改完成后自动 upsert(按 学号 或 姓名 匹配)。
    """

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(EncryptedDeterministic(128), index=True)
    student_id: Mapped[str | None] = mapped_column(EncryptedDeterministic(64), index=True, default=None)
    class_name: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ErrorRecord(Base):
    """错题记录表(二期"错题本"与"班级共性错因分析"的数据基础)

    批改完成后,从 EssayCorrectionResult.errors 逐条自动写入。
    canonical_type 为标准化考点分类键名(由 error_taxonomy 归一化),聚合统计以它为准。
    """

    __tablename__ = "error_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("correction_tasks.id"), index=True)
    student_name: Mapped[str] = mapped_column(EncryptedDeterministic(128), index=True)
    student_id: Mapped[str | None] = mapped_column(EncryptedDeterministic(64), index=True, default=None)
    error_type: Mapped[str] = mapped_column(String(64), index=True)
    # 标准化分类键名(如 VERB_POSITION),分析统计的主维度
    canonical_type: Mapped[str] = mapped_column(String(64), index=True, default="OTHER")
    original_text: Mapped[str] = mapped_column(EncryptedText, default="")
    corrected_text: Mapped[str] = mapped_column(EncryptedText, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# =============================================================
# 作业台账(日常作业登记与汇总)
# =============================================================


class HomeworkItem(Base):
    """作业台账:登记项定义(登记项与计分口径可配置、可扩展)

    - class_id 为空 = 全局模板项(所有班级可选);非空 = 班级私有项;
    - scoring_mode 决定登记值的形态与归一化规则(见 ledger_service):
      LEVEL(等级制) / SCORE(分值制) / FLAG(完成度) / STARS(星级);
    - config 存该模式的配置(等级档位、满分、步长等);
    - 唯一性按 (class_id, name) 在服务层校验(有记录时不物理删除,仅归档)。
    """

    __tablename__ = "homework_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), index=True, default=None
    )
    name: Mapped[str] = mapped_column(String(128))
    # 分类标签(书面作业 / 背诵 / 听写 / 课堂表现 / 订正 / 其他)
    category: Mapped[str | None] = mapped_column(String(32), default=None)
    scoring_mode: Mapped[str] = mapped_column(String(16), default="LEVEL")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HomeworkRecord(Base):
    """作业台账:登记记录

    - value 存原始值("A" / "85" / "done" / "4"),score_value 为归一化 0-100(便于跨模式统计);
    - 唯一约束(item_id, student_name, record_date):同一天同一项同一学生只保留一条,
      重复登记即更新(upsert,由 ledger_service 实现);
    - student_name/student_id 与批改任务同口径,并同步 upsert students 档案。
    """

    __tablename__ = "homework_records"
    __table_args__ = (
        UniqueConstraint(
            "item_id", "student_name", "record_date", name="uq_homework_record_item_student_date"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("homework_items.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), index=True, default=None
    )
    student_name: Mapped[str] = mapped_column(EncryptedDeterministic(128), index=True)
    student_id: Mapped[str | None] = mapped_column(EncryptedDeterministic(64), index=True, default=None)
    value: Mapped[str] = mapped_column(String(64), default="")
    score_value: Mapped[float | None] = mapped_column(Float, default=None)
    record_date: Mapped[date] = mapped_column(Date, index=True)
    note: Mapped[str | None] = mapped_column(EncryptedText, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


# =============================================================
# 考试统计(考卷识别与班级分析)
# =============================================================


class Exam(Base):
    """考试:一场班级考试(如"期中考试")

    status 流转:DRAFT(建档)→ PROCESSING(有考卷在识别)→ READY(全部识别完成)→ REPORTED(已生成报告)。
    """

    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), index=True, default=None
    )
    name: Mapped[str] = mapped_column(String(128))
    exam_date: Mapped[date] = mapped_column(Date, index=True)
    subject: Mapped[str] = mapped_column(String(32), default="德语")
    full_score: Mapped[float] = mapped_column(Float, default=100.0)
    # 考试类型(月考 / 期中 / 期末 / 其他;可空)
    exam_type: Mapped[str | None] = mapped_column(String(32), default=None)
    note: Mapped[str | None] = mapped_column(EncryptedText, default=None)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExamPaper(Base):
    """考试:单个学生的考卷记录(OCR 结果 + 人工修订)

    - question_results 为"除听力外"的逐题结果数组:
      [{no, part, max_score, score, knowledge_tag, canonical_type, note}];
    - teacher_edited=1 表示教师已人工修订,人工值优先于 OCR 值;
    - 唯一约束(exam_id, student_name):一场考试每位学生一份卷。
    """

    __tablename__ = "exam_papers"
    __table_args__ = (
        UniqueConstraint("exam_id", "student_name", name="uq_exam_paper_exam_student"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), index=True)
    student_name: Mapped[str] = mapped_column(EncryptedDeterministic(128), index=True)
    student_id: Mapped[str | None] = mapped_column(EncryptedDeterministic(64), index=True, default=None)
    image_paths: Mapped[list] = mapped_column(JSON, default=list)
    ocr_status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    ocr_error: Mapped[str | None] = mapped_column(Text, default=None)
    total_score: Mapped[float | None] = mapped_column(Float, default=None)
    question_results: Mapped[list] = mapped_column(EncryptedJSON, default=list)
    teacher_edited: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ExamReport(Base):
    """考试:分析报告(持久化快照,每场考试一份,重新生成即覆盖)"""

    __tablename__ = "exam_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), unique=True, index=True)
    report_markdown: Mapped[str] = mapped_column(EncryptedText, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# =============================================================
# 示范学习(示例范文风格迁移)
# =============================================================


class AppSetting(Base):
    """应用级键值配置(设置中心:提示词附录 / 探索性设置回退快照 / 加密密钥包裹等)

    与 .env 的分工:.env 保存环境/连接类配置;本表保存多行文本与
    运行时可变的元数据(无需触碰 .env,随数据库一起备份)。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class StyleProfile(Base):
    """示范学习:批改风格画像(从示例范文的批改结果归纳固化)

    - style_json:结构化四维度(评分尺度 / 点评语气 / 修改偏好 / 表达习惯);
    - narrative:自然语言风格描述(注入批改 Prompt 的文本,教师可编辑,限长);
    - status:active | inactive(单一生效,由 style_learning_service 保证);
    - source_task_id 为弱关联(不加外键约束):归纳产物已固化,
      源任务被删除后画像仍可正常使用。
    """

    __tablename__ = "style_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    source_task_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    source_student_name: Mapped[str | None] = mapped_column(String(128), default=None)
    style_json: Mapped[dict] = mapped_column(JSON, default=dict)
    narrative: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="inactive", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


# =============================================================
# 练习卷(从历史作业错因数据生成)与存档图片压缩(无表结构)
# =============================================================


class PracticeSheet(Base):
    """练习卷(依据历史作业错因数据生成的练习卷 + 标准答案)

    - source:生成依据快照(scope / 作业清单 / 错因分布 / 主题),只读溯源、不写回统计;
    - params:生成参数(题型集合与数量);
    - content:结构化题目数组 {"questions": [{type,no,stem,answer,explanation}]};
    - worksheet_markdown / answer_markdown:展示形态(试卷本体与标准答案,与 questions 同源);
    - class_id:目标班级弱分类(可空,仅用于归档筛选)。
    """

    __tablename__ = "practice_sheets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id", name="fk_practice_sheets_class_id"), index=True, default=None
    )
    title: Mapped[str] = mapped_column(String(128))
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    worksheet_markdown: Mapped[str] = mapped_column(Text, default="")
    answer_markdown: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HandwritingSample(Base):
    """学生手写样本(花名册“手写模型”的采集样本)

    教师让学生用标准卷抄写素材后回传;每份样本经找平/裁姓名区/识别后:
    - student_name/student_id:对账得到的归属(加密列,与花名册同款);
    - name_crop_path:姓名区小图(相对 uploads 的路径,供预识别展示与特征重算);
    - features:姓名区图像特征签名(JSON 数组;待指定学生前可能为空);
    - status:ok(已绑定) | pending_bind(待教师指定学生)。
    """

    __tablename__ = "handwriting_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id", name="fk_handwriting_samples_class_id"), index=True
    )
    student_name: Mapped[str | None] = mapped_column(EncryptedDeterministic(128), default=None)
    student_id: Mapped[str | None] = mapped_column(EncryptedDeterministic(64), default=None)
    image_path: Mapped[str] = mapped_column(String(255))
    name_crop_path: Mapped[str | None] = mapped_column(String(255), default=None)
    features: Mapped[list | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending_bind")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HandwritingModel(Base):
    """班级手写模型概览(样本特征库的聚合元数据;每班一行)

    训练/样本处理完成后更新;level 记录构建时采用的识别档位快照。
    """

    __tablename__ = "handwriting_models"

    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id", name="fk_handwriting_models_class_id"), primary_key=True
    )
    samples_count: Mapped[int] = mapped_column(Integer, default=0)
    students_count: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str] = mapped_column(String(16), default="light")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
