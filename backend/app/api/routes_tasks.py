"""批改任务路由(查询 / 原图 / 人工复核 / 重试 / 批量操作 / 报告编辑)

- GET    /api/tasks                     任务列表(支持状态/批次/日期筛选)
- GET    /api/tasks/{id}                任务详情(含 OCR 中间结果与批改结果)
- GET    /api/tasks/{id}/images/{idx}   原图访问(审阅分屏左侧展示)
- POST   /api/tasks/{id}/ocr-confirm    提交教师校订后的转录,继续评分(两条管线通用)
- POST   /api/tasks/{id}/retry          失败任务重新入队
- PUT    /api/tasks/{id}/report         保存教师编辑后的 Markdown 报告
- POST   /api/tasks/batch-retry         批量重试(失败/待复核/中断)
- POST   /api/tasks/batch-delete        批量删除(含错题记录与图片文件清理)
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue_service
from app.config import settings
from app.db.database import get_db
from app.models.db_models import CorrectionTask, ErrorRecord, SchoolClass
from app.models.schemas import (
    AnnotationItem,
    AnnotationResponse,
    AnnotationSegment,
    BatchDeleteResponse,
    BatchRetryResponse,
    BatchTaskRequest,
    DetailLevel,
    EssayCorrectionResult,
    GradingStandard,
    OcrConfirmRequest,
    OcrExtractionResult,
    PipelineChoice,
    ReportUpdateRequest,
    RetryResponse,
    StudentUpdateRequest,
    TaskBrief,
    TaskDetail,
    TaskListResponse,
    TaskRecorrectRequest,
    TaskStatus,
    TeacherMessageUpdate,
    TranscriptUpdateRequest,
)
from app.services import recorrect_service
from app.services.error_taxonomy import category_label_of
from app.services.queue_service import QueueService
from app.services.report_renderer import finalize_result, locate_error_spans, render_student_report
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)

router = APIRouter(tags=["任务"])


# ---------------------------------------------------------
# 序列化工具
# ---------------------------------------------------------
def _task_to_brief(task: CorrectionTask, class_name: str | None = None) -> TaskBrief:
    """ORM -> 任务简要信息(得分从 result JSON 中提取;class_name 由调用方联表填入)"""
    overall_score = None
    if task.result:
        overall_score = task.result.get("overall_score")
    return TaskBrief(
        id=task.id,
        batch_id=task.batch_id,
        student_name=task.student_name,
        student_id=task.student_id,
        pipeline_choice=task.pipeline_choice,
        pipeline_used=task.pipeline_used,
        grading_standard=task.grading_standard,
        detail_level=task.detail_level,
        status=task.status,
        stage=task.stage,
        overall_score=overall_score,
        fallback_triggered=bool(task.fallback_triggered),
        error_message=task.error_message,
        class_id=task.class_id,
        class_name=class_name,
        assignment_name=task.assignment_name,
        topic=task.topic,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _task_to_detail(task: CorrectionTask, class_name: str | None = None) -> TaskDetail:
    """ORM -> 任务完整信息(含按需渲染的学生版报告)"""
    brief = _task_to_brief(task, class_name=class_name)
    result = EssayCorrectionResult.model_validate(task.result) if task.result else None
    # 学生版报告:结果存在时按需渲染(不落库,保证与教师版始终同源)
    student_report = None
    if result is not None:
        student_report = render_student_report(
            result,
            standard=GradingStandard(task.grading_standard),
            detail=DetailLevel(task.detail_level),
            teacher_message=task.teacher_message,
        )
    return TaskDetail(
        **brief.model_dump(),
        image_paths=list(task.image_paths or []),
        ocr_result=OcrExtractionResult.model_validate(task.ocr_result) if task.ocr_result else None,
        result=result,
        edited_report=task.edited_report,
        student_report=student_report,
        require_ocr_review=bool(task.require_ocr_review),
        recorrect_count=task.recorrect_count or 0,
        last_recorrect_at=task.last_recorrect_at,
        prev_overall_score=task.prev_overall_score,
        prev_error_count=task.prev_error_count,
        teacher_message=task.teacher_message,
    )


async def _class_name_of(db: AsyncSession, class_id: int | None) -> str | None:
    """查询班级名称(无班级时返回 None)"""
    if class_id is None:
        return None
    cls = await db.get(SchoolClass, class_id)
    return cls.name if cls else None


async def _get_task_or_404(db: AsyncSession, task_id: int) -> CorrectionTask:
    """按 ID 获取任务,不存在时返回 404"""
    task = await db.get(CorrectionTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


# ---------------------------------------------------------
# 查询
# ---------------------------------------------------------
@router.get("/tasks", response_model=TaskListResponse, summary="任务列表")
async def list_tasks(
    status: str | None = Query(default=None, description="按状态筛选,如 PENDING/COMPLETED"),
    batch_id: str | None = Query(default=None, description="按批次 ID 筛选"),
    class_id: int | None = Query(default=None, description="按班级 ID 筛选"),
    created_from: str | None = Query(default=None, description="创建时间起(YYYY-MM-DD)"),
    created_to: str | None = Query(default=None, description="创建时间止(YYYY-MM-DD,含当日)"),
    limit: int = Query(default=100, ge=1, le=500, description="每页数量"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    """分页查询任务列表(按创建时间倒序,联表返回班级名称)"""
    # 主查询联表班级名称;计数查询不带连接
    stmt = select(CorrectionTask, SchoolClass.name).outerjoin(
        SchoolClass, CorrectionTask.class_id == SchoolClass.id
    )
    count_stmt = select(func.count(CorrectionTask.id))
    if status:
        stmt = stmt.where(CorrectionTask.status == status)
        count_stmt = count_stmt.where(CorrectionTask.status == status)
    if batch_id:
        stmt = stmt.where(CorrectionTask.batch_id == batch_id)
        count_stmt = count_stmt.where(CorrectionTask.batch_id == batch_id)
    if class_id is not None:
        stmt = stmt.where(CorrectionTask.class_id == class_id)
        count_stmt = count_stmt.where(CorrectionTask.class_id == class_id)
    # 日期范围筛选(按自然日,to 含当日)
    if created_from:
        from_dt = _parse_date_or_400(created_from)
        stmt = stmt.where(CorrectionTask.created_at >= from_dt)
        count_stmt = count_stmt.where(CorrectionTask.created_at >= from_dt)
    if created_to:
        to_dt = _parse_date_or_400(created_to) + timedelta(days=1)
        stmt = stmt.where(CorrectionTask.created_at < to_dt)
        count_stmt = count_stmt.where(CorrectionTask.created_at < to_dt)

    stmt = stmt.order_by(CorrectionTask.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()
    total = (await db.execute(count_stmt)).scalar_one()

    return TaskListResponse(
        total=total,
        items=[_task_to_brief(task, class_name=class_name) for task, class_name in rows],
    )


def _parse_date_or_400(value: str) -> datetime:
    """解析 YYYY-MM-DD 日期字符串(非法时返回 400)"""
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"日期格式应为 YYYY-MM-DD:{value}") from e


@router.get("/tasks/{task_id}", response_model=TaskDetail, summary="任务详情")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskDetail:
    """获取任务完整信息(轮询进度/展示结果/OCR 复核共用)"""
    task = await _get_task_or_404(db, task_id)
    class_name = await _class_name_of(db, task.class_id)
    return _task_to_detail(task, class_name=class_name)


@router.get("/tasks/{task_id}/images/{image_index}", summary="获取任务原图")
async def get_task_image(task_id: int, image_index: int, db: AsyncSession = Depends(get_db)):
    """按索引返回任务的原图文件(审阅分屏左侧展示;媒体类型按内容嗅探)"""
    task = await _get_task_or_404(db, task_id)
    paths = list(task.image_paths or [])
    if image_index < 0 or image_index >= len(paths):
        raise HTTPException(status_code=404, detail=f"图片索引 {image_index} 超出范围")

    file_path = settings.upload_path / paths[image_index]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="图片文件已丢失")
    # 存量压缩可能改变内部编码(如 BMP→JPEG):按魔数嗅探媒体类型,避免扩展名失真
    media_type = await asyncio.to_thread(_sniff_media_type, file_path)
    if media_type:
        return FileResponse(file_path, media_type=media_type)
    return FileResponse(file_path)


@router.get("/tasks/{task_id}/name-crop", summary="姓名/学号信息区小图")
async def get_task_name_crop(task_id: int, db: AsyncSession = Depends(get_db)):
    """裁剪任务第 1 页的姓名/学号信息区小图(审阅页展示;标准卷按规范几何,普通照片启发式)

    - 只读展示功能,不受 SHEET_ALIGN_ENABLED 开关影响(关闭找平时自动退回启发式裁剪);
    - 任何裁剪失败返回 404,前端静默隐藏,不影响主流程。
    """
    task = await _get_task_or_404(db, task_id)
    paths = [settings.upload_path / p for p in (task.image_paths or [])]
    first = next((path for path in paths if path.exists()), None)
    if first is None:
        raise HTTPException(status_code=404, detail="任务无可用图片")
    from app.services.sheet_align import crop_name_region

    data = await asyncio.to_thread(crop_name_region, first.read_bytes())
    if data is None:
        raise HTTPException(status_code=404, detail="姓名区裁剪失败")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


#: 魔数 -> Content-Type(读文件头 16 字节)
_MAGIC_MEDIA_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
    (b"BM", "image/bmp"),
)


def _sniff_media_type(path) -> str | None:
    """按文件头魔数嗅探图片媒体类型(失败返回 None,回退 FileResponse 默认推断)"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return None
    for magic, media_type in _MAGIC_MEDIA_TYPES:
        if head.startswith(magic):
            return media_type
    if head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "image/heic"
    return None


# ---------------------------------------------------------
# 转录批注定位(#13 批注核对视图)
# ---------------------------------------------------------
@router.get(
    "/tasks/{task_id}/annotation",
    response_model=AnnotationResponse,
    summary="转录批注定位(批注核对视图数据源)",
)
async def get_task_annotation(task_id: int, db: AsyncSession = Depends(get_db)) -> AnnotationResponse:
    """返回转录全文的错误片段定位区间与批注详情

    - 定位算法与报告渲染共用(report_renderer.locate_error_spans),前后端零重复实现;
    - 仅已完成且无结果异常的任务返回;未定位条目归入 unlocated 清单。
    """
    task = await _get_task_or_404(db, task_id)
    if not task.result:
        raise HTTPException(status_code=400, detail="任务尚未完成批改,暂无批注数据")

    result = EssayCorrectionResult.model_validate(task.result)
    spans, unlocated = locate_error_spans(result.transcribed_text, result.errors)

    return AnnotationResponse(
        transcribed_text=result.transcribed_text,
        segments=[
            AnnotationSegment(start=s.start, end=s.end, error_index=s.error_index) for s in spans
        ],
        unlocated=unlocated,
        annotations=[
            AnnotationItem(
                index=i,
                category_label=category_label_of(err),
                canonical_type=err.canonical_type,
                error_type=err.error_type,
                original_text=err.original_text,
                corrected_text=err.corrected_text,
                explanation=err.explanation,
            )
            for i, err in enumerate(result.errors)
        ],
    )


# ---------------------------------------------------------
# 人工复核(管线 B)
# ---------------------------------------------------------
@router.post("/tasks/{task_id}/ocr-confirm", response_model=TaskDetail, summary="提交 OCR 复核结果")
async def confirm_ocr(
    task_id: int,
    payload: OcrConfirmRequest,
    db: AsyncSession = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> TaskDetail:
    """教师校订转录文本后提交,任务重新入队进入评分阶段"""
    task = await _get_task_or_404(db, task_id)
    if task.status != TaskStatus.WAITING_REVIEW.value:
        raise HTTPException(
            status_code=400,
            detail=f"任务当前状态为 {task.status},不在待复核状态,无法提交复核结果",
        )

    # 保存教师校订后的转录数据
    task.ocr_result = OcrExtractionResult(
        student_name=payload.student_name.strip() or "未知",
        student_id=(payload.student_id or "").strip() or None,
        transcribed_text=payload.transcribed_text,
    ).model_dump()
    task.student_name = task.ocr_result["student_name"]
    task.student_id = task.ocr_result["student_id"]
    task.status = TaskStatus.PENDING.value
    task.stage = "UPLOADED"
    task.progress = 0.5
    await db.commit()
    await db.refresh(task)

    await queue.enqueue(task.id)
    logger.info("任务 %s OCR 复核已提交,重新入队评分", task_id)
    return _task_to_detail(task)


# ---------------------------------------------------------
# 重试
# ---------------------------------------------------------
@router.post("/tasks/{task_id}/retry", response_model=RetryResponse, summary="重试失败任务")
async def retry_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> RetryResponse:
    """将失败/待复核/中断的任务重置并重新入队"""
    task = await _get_task_or_404(db, task_id)
    if task.status not in (
        TaskStatus.FAILED.value,
        TaskStatus.WAITING_REVIEW.value,
        TaskStatus.PROCESSING.value,  # 中断残留的任务(服务重启后也可手动重试)
    ):
        raise HTTPException(
            status_code=400,
            detail=f"任务当前状态为 {task.status},仅允许重试失败、待复核或中断的任务",
        )

    task.status = TaskStatus.PENDING.value
    task.stage = "UPLOADED"
    task.progress = 0.0
    task.error_message = None
    await db.commit()
    await db.refresh(task)

    await queue.enqueue(task.id)
    logger.info("任务 %s 已重新入队", task_id)
    return RetryResponse(task_id=task.id, status=TaskStatus.PENDING)


# ---------------------------------------------------------
# 报告编辑
# ---------------------------------------------------------
@router.put("/tasks/{task_id}/report", response_model=TaskDetail, summary="保存/恢复教师报告")
async def update_report(
    task_id: int,
    payload: ReportUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskDetail:
    """保存教师手工编辑的 Markdown 报告

    - `markdown_report` 为文本:保存编辑版(与原始报告分开存储,便于回溯);
    - `markdown_report` 为 null:清除编辑版,恢复系统原始报告。
    """
    task = await _get_task_or_404(db, task_id)
    if task.status != TaskStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail="仅已完成的任务可以编辑报告")

    if payload.markdown_report is None:
        task.edited_report = None
        logger.info("任务 %s 已恢复系统原始报告", task_id)
    else:
        task.edited_report = payload.markdown_report
        logger.info("任务 %s 报告已更新(教师编辑版)", task_id)

    await db.commit()
    await db.refresh(task)
    return _task_to_detail(task)


# ---------------------------------------------------------
# 学生信息纠错(方向四:误指派/误识别必须可修)
# ---------------------------------------------------------
@router.put("/tasks/{task_id}/student", response_model=TaskDetail, summary="修改任务学生信息")
async def update_task_student(
    task_id: int,
    payload: StudentUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskDetail:
    """修改任务的学生姓名/学号,并同步:结果 JSON/报告、错题记录、学生档案

    用于纠正上传时的误指派或识别偏差;已保存的教师编辑版报告不被覆盖。
    """
    task = await _get_task_or_404(db, task_id)
    new_name = payload.student_name.strip()
    new_id = (payload.student_id or "").strip() or None

    task.student_name = new_name or "未知"
    task.student_id = new_id

    # 同步结果 JSON 与报告头部(教师编辑版单独存储,不受影响)
    if task.result:
        result = EssayCorrectionResult.model_validate(task.result)
        result.student_name = task.student_name
        result.student_id = task.student_id
        if result.markdown_report:
            finalize_result(
                result=result,
                pipeline_choice=PipelineChoice(task.pipeline_used or task.pipeline_choice),
                pipeline_display_name="",
                standard=GradingStandard(task.grading_standard),
                detail=DetailLevel(task.detail_level),
            )
        task.result = result.model_dump()

    # 同步错题记录(聚合统计维度)
    await db.execute(
        update(ErrorRecord)
        .where(ErrorRecord.task_id == task_id)
        .values(student_name=task.student_name, student_id=task.student_id)
    )
    # 同步学生档案(共用 upsert 规则)
    await upsert_student(db, task.student_name, task.student_id)

    await db.commit()
    await db.refresh(task)
    class_name = await _class_name_of(db, task.class_id)
    logger.info("任务 %s 学生信息已更新:%s / %s", task_id, task.student_name, task.student_id)
    return _task_to_detail(task, class_name=class_name)


# ---------------------------------------------------------
# 重新批改(已完成任务原地重跑)
# ---------------------------------------------------------
@router.post("/tasks/{task_id}/recorrect", response_model=TaskDetail, summary="重新批改(原地重跑)")
async def recorrect_task(
    task_id: int,
    payload: TaskRecorrectRequest,
    db: AsyncSession = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> TaskDetail:
    """对已完成任务原地重跑:复用原图,仅覆盖显式传入的参数

    - 不新建任务/不重复落盘;旧错题记录先清理、完成后按新结果重建;
    - 教师编辑版报告与教师寄语保留;新系统报告覆盖旧系统报告;
    - 风格画像与提示词附录由服务端在批改时实时读取(与工作台一致)。
    """
    task = await _get_task_or_404(db, task_id)
    try:
        await recorrect_service.apply_recorrect(
            db, task, payload.model_dump(exclude_unset=True)
        )
    except recorrect_service.RecorrectError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await queue.enqueue(task.id)
    class_name = await _class_name_of(db, task.class_id)
    logger.info("任务 %s 已重新入队(重新批改)", task_id)
    return _task_to_detail(task, class_name=class_name)


# ---------------------------------------------------------
# 教师寄语(学生版报告,教师可编辑)
# ---------------------------------------------------------
@router.put(
    "/tasks/{task_id}/teacher-message",
    response_model=TaskDetail,
    summary="保存教师寄语(学生版)",
)
async def update_teacher_message(
    task_id: int,
    payload: TeacherMessageUpdate,
    db: AsyncSession = Depends(get_db),
) -> TaskDetail:
    """设置/清除学生版报告"教师寄语"(空 = 恢复系统默认寄语;立即生效于学生版渲染)"""
    task = await _get_task_or_404(db, task_id)
    if not task.result:
        raise HTTPException(status_code=400, detail="任务尚未完成批改,暂无学生版报告可寄语")
    task.teacher_message = (payload.teacher_message or "").strip() or None
    await db.commit()
    await db.refresh(task)
    class_name = await _class_name_of(db, task.class_id)
    logger.info("任务 %s 教师寄语已更新(长度 %s)", task_id, len(task.teacher_message or ""))
    return _task_to_detail(task, class_name=class_name)


# ---------------------------------------------------------
# 转录原文修订(教师校正识别偏差)
# ---------------------------------------------------------
@router.put("/tasks/{task_id}/transcript", response_model=TaskDetail, summary="保存转录原文(教师修订)")
async def update_transcript(
    task_id: int,
    payload: TranscriptUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskDetail:
    """教师修订转录原文:写回 result.transcribed_text 并重渲染系统原始报告

    - 教师编辑版报告(edited_report)按现有约定单独存储,不受影响;
    - 批注核对视图由后端区间定位实时计算(旧片段无法匹配时自动降入兜底清单,
      信息不丢失);学生版报告按需重新渲染,与教师版始终同源。
    """
    task = await _get_task_or_404(db, task_id)
    if not task.result:
        raise HTTPException(status_code=400, detail="任务尚未完成批改,暂无转录原文可修改")
    text = payload.transcribed_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="转录原文不能为空")

    result = EssayCorrectionResult.model_validate(task.result)
    result.transcribed_text = text
    # 重渲染系统原始报告(报告中的错例上下文引用转录全文;教师编辑版不被覆盖)
    if result.markdown_report:
        finalize_result(
            result=result,
            pipeline_choice=PipelineChoice(task.pipeline_used or task.pipeline_choice),
            pipeline_display_name="",
            standard=GradingStandard(task.grading_standard),
            detail=DetailLevel(task.detail_level),
        )
    task.result = result.model_dump()
    await db.commit()
    await db.refresh(task)
    class_name = await _class_name_of(db, task.class_id)
    logger.info("任务 %s 转录原文已修订(长度 %s)", task_id, len(text))
    return _task_to_detail(task, class_name=class_name)


# ---------------------------------------------------------
# 批量操作(#14)
# ---------------------------------------------------------
#: 允许批量重试的状态(失败 / 待复核 / 中断残留)
_RETRYABLE_STATUSES = {
    TaskStatus.FAILED.value,
    TaskStatus.WAITING_REVIEW.value,
    TaskStatus.PROCESSING.value,
}
#: 禁止删除的状态(处理中/排队中,避免与工作协程竞态)
_UNDELETABLE_STATUSES = {TaskStatus.PENDING.value, TaskStatus.PROCESSING.value}


@router.post("/tasks/batch-retry", response_model=BatchRetryResponse, summary="批量重试任务")
async def batch_retry(
    payload: BatchTaskRequest,
    db: AsyncSession = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> BatchRetryResponse:
    """批量重置并重新入队(仅失败/待复核/中断状态的任务)"""
    task_ids = list(dict.fromkeys(payload.task_ids))  # 去重且保持顺序
    stmt = select(CorrectionTask).where(CorrectionTask.id.in_(task_ids))
    tasks = (await db.execute(stmt)).scalars().all()

    retried: list[int] = []
    skipped: list[int] = []
    for task in tasks:
        if task.status in _RETRYABLE_STATUSES:
            task.status = TaskStatus.PENDING.value
            task.stage = "UPLOADED"
            task.progress = 0.0
            task.error_message = None
            retried.append(task.id)
        else:
            skipped.append(task.id)
    # 不存在的 ID 也归入 skipped,便于前端提示
    existing = {t.id for t in tasks}
    skipped.extend(tid for tid in task_ids if tid not in existing)

    await db.commit()
    for tid in retried:
        await queue.enqueue(tid)

    logger.info("批量重试:成功 %s 个,跳过 %s 个", len(retried), len(skipped))
    return BatchRetryResponse(retried=retried, skipped=skipped)


@router.post("/tasks/batch-delete", response_model=BatchDeleteResponse, summary="批量删除任务")
async def batch_delete(
    payload: BatchTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> BatchDeleteResponse:
    """批量删除任务(同步清理错题记录与图片文件;处理中/排队中的任务跳过)"""
    task_ids = list(dict.fromkeys(payload.task_ids))
    stmt = select(CorrectionTask).where(CorrectionTask.id.in_(task_ids))
    tasks = (await db.execute(stmt)).scalars().all()

    deletable = [t for t in tasks if t.status not in _UNDELETABLE_STATUSES]
    skipped = [t.id for t in tasks if t.status in _UNDELETABLE_STATUSES]
    existing = {t.id for t in tasks}
    skipped.extend(tid for tid in task_ids if tid not in existing)

    if deletable:
        delete_ids = [t.id for t in deletable]
        # 1. 先删子表(错题记录),再删主表
        await db.execute(delete(ErrorRecord).where(ErrorRecord.task_id.in_(delete_ids)))
        await db.execute(delete(CorrectionTask).where(CorrectionTask.id.in_(delete_ids)))
        await db.commit()

        # 2. 清理上传文件与空目录(失败不阻断响应)
        file_paths: list = []
        for task in deletable:
            for rel in task.image_paths or []:
                file_paths.append(settings.upload_path / rel)
        for path in file_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as e:  # noqa: PERF203
                logger.warning("删除文件失败(忽略):%s %s", path, e)
        for parent in {p.parent for p in file_paths}:
            try:
                parent.rmdir()  # 仅当目录为空时成功
            except OSError:
                pass

    logger.info("批量删除:删除 %s 个,跳过 %s 个", len(deletable), len(skipped))
    return BatchDeleteResponse(deleted=[t.id for t in deletable], skipped=skipped)


# ---------------------------------------------------------
# 批量导出(#12)
# ---------------------------------------------------------


def _safe_filename(name: str) -> str:
    """清理文件名中的非法字符(供 ZIP 内文件名使用)"""
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "未命名"


@router.post("/tasks/export-reports", summary="批量导出报告(ZIP)")
async def export_reports(
    payload: BatchTaskRequest,
    db: AsyncSession = Depends(get_db),
):
    """将选中任务的 Markdown 报告打包为 ZIP(优先导出教师编辑版)"""
    task_ids = list(dict.fromkeys(payload.task_ids))
    stmt = select(CorrectionTask).where(CorrectionTask.id.in_(task_ids))
    tasks = (await db.execute(stmt)).scalars().all()

    buffer = io.BytesIO()
    exported = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for task in tasks:
            if not task.result:
                continue
            content = task.edited_report or task.result.get("markdown_report", "")
            if not content:
                continue
            student = _safe_filename(task.student_name or "未知")
            arcname = f"批改报告_{student}_{task.id}.md"
            zf.writestr(arcname, content)
            exported += 1
    buffer.seek(0)

    if exported == 0:
        raise HTTPException(status_code=400, detail="所选任务中没有可导出的报告")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logger.info("批量导出报告:%s 份", exported)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="correction_reports_{stamp}.zip"'
        },
    )


@router.post("/tasks/export-grades", summary="批量导出成绩表(CSV)")
async def export_grades(
    payload: BatchTaskRequest,
    db: AsyncSession = Depends(get_db),
):
    """将选中任务的成绩汇总为 CSV(UTF-8 BOM,Excel 可直接打开)"""
    task_ids = list(dict.fromkeys(payload.task_ids))
    stmt = select(CorrectionTask, SchoolClass.name).outerjoin(
        SchoolClass, CorrectionTask.class_id == SchoolClass.id
    ).where(CorrectionTask.id.in_(task_ids))
    rows = (await db.execute(stmt)).all()
    # 按创建时间排序,便于按作业顺序查看
    rows.sort(key=lambda pair: pair[0].created_at)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["任务ID", "学生姓名", "学号", "班级", "作业名称", "评分标准", "细致度", "综合得分", "错因数", "创建时间"]
    )
    for task, class_name in rows:
        result = task.result or {}
        writer.writerow(
            [
                task.id,
                task.student_name or "未知",
                task.student_id or "",
                class_name or "",
                task.assignment_name or "",
                task.grading_standard,
                task.detail_level,
                result.get("overall_score", ""),
                len(result.get("errors", [])),
                task.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logger.info("批量导出成绩表:%s 行", len(rows))
    # 加 BOM 保证 Excel 中文不乱码
    csv_bytes = "\ufeff".encode("utf-8") + output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="grades_{stamp}.csv"'
        },
    )
