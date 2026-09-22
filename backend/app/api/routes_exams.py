"""考试统计路由(考卷识别与班级分析)

- GET/POST /api/exams                     考试列表 / 新建
- GET/PUT/DELETE /api/exams/{id}          详情 / 更新 / 删除(需 confirm)
- POST /api/exams/{id}/papers             批量上传考卷(文件名匹配花名册;同名续传多页)
- PUT /api/exams/{id}/papers/{pid}        人工修订逐题结果(teacher_edited=1,人工优先)
- POST /api/exams/{id}/papers/{pid}/retry 重新识别
- POST /api/exams/{id}/report             生成/重新生成分析报告(持久化)
- GET  /api/exams/{id}/report             查看报告
- GET  /api/exams/{id}/report/export      下载报告 Markdown

说明:听力部分不产生条目(见 EXAM_OCR Prompt 与解析兜底);所有接口挂数据解锁守卫(main.py)。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_exam_service
from app.api.routes_corrections import _cleanup_saved, _save_upload, _validate_upload_request
from app.config import settings
from app.db.database import get_db
from app.models.db_models import Exam, ExamPaper, ExamReport, SchoolClass
from app.models.schemas import (
    ExamBrief,
    ExamCreate,
    ExamDetail,
    ExamPaperOut,
    ExamPaperUpdate,
    ExamReportOut,
    ExamUpdate,
)
from app.services.error_taxonomy import normalize_error_type
from app.services.exam_service import ExamService, _LISTENING_RE, generate_exam_report
from app.services.student_assignment import assign_from_filename, load_roster
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)

router = APIRouter(tags=["考试统计"])


async def _get_exam_or_404(db: AsyncSession, exam_id: int) -> Exam:
    exam = await db.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail=f"考试 {exam_id} 不存在")
    return exam


async def _get_paper_or_404(db: AsyncSession, exam_id: int, paper_id: int) -> ExamPaper:
    paper = await db.get(ExamPaper, paper_id)
    if paper is None or paper.exam_id != exam_id:
        raise HTTPException(status_code=404, detail=f"考卷 {paper_id} 不存在")
    return paper


async def _class_name_of(db: AsyncSession, class_id: int | None) -> str | None:
    if class_id is None:
        return None
    cls = await db.get(SchoolClass, class_id)
    return cls.name if cls else None


async def _brief_of(db: AsyncSession, exam: Exam, counts: dict) -> ExamBrief:
    return ExamBrief(
        id=exam.id,
        class_id=exam.class_id,
        class_name=await _class_name_of(db, exam.class_id),
        name=exam.name,
        exam_date=exam.exam_date,
        subject=exam.subject,
        full_score=exam.full_score,
        exam_type=exam.exam_type,
        note=exam.note,
        status=exam.status,
        paper_count=counts.get("total", 0),
        done_count=counts.get("done", 0),
        created_at=exam.created_at,
    )


# ---------------------------------------------------------
# 考试 CRUD
# ---------------------------------------------------------
@router.get("/exams", response_model=list[ExamBrief], summary="考试列表")
async def list_exams(
    class_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[ExamBrief]:
    """考试列表(含考卷数量统计;按考试日期倒序)"""
    stmt = select(Exam).order_by(Exam.exam_date.desc(), Exam.id.desc())
    if class_id is not None:
        stmt = stmt.where(Exam.class_id == class_id)
    exams = (await db.execute(stmt)).scalars().all()
    if not exams:
        return []
    exam_ids = [e.id for e in exams]
    rows = (
        await db.execute(
            select(
                ExamPaper.exam_id,
                func.count(ExamPaper.id),
                func.sum(case((ExamPaper.ocr_status == "DONE", 1), else_=0)),
            )
            .where(ExamPaper.exam_id.in_(exam_ids))
            .group_by(ExamPaper.exam_id)
        )
    ).all()
    counts = {exam_id: {"total": total or 0, "done": int(done or 0)} for exam_id, total, done in rows}
    return [await _brief_of(db, exam, counts.get(exam.id, {})) for exam in exams]


@router.post("/exams", response_model=ExamBrief, summary="新建考试")
async def create_exam(payload: ExamCreate, db: AsyncSession = Depends(get_db)) -> ExamBrief:
    """新建一场考试(可选归属班级;满分默认 100)"""
    if payload.class_id is not None:
        cls = await db.get(SchoolClass, payload.class_id)
        if cls is None:
            raise HTTPException(status_code=400, detail=f"班级 {payload.class_id} 不存在")
    exam = Exam(
        class_id=payload.class_id,
        name=payload.name.strip(),
        exam_date=payload.exam_date,
        subject=(payload.subject or "德语").strip() or "德语",
        full_score=payload.full_score,
        exam_type=(payload.exam_type or "").strip() or None,
        note=(payload.note or "").strip() or None,
        status="DRAFT",
    )
    db.add(exam)
    await db.commit()
    await db.refresh(exam)
    logger.info("考试 %s 已创建:%s", exam.id, exam.name)
    return await _brief_of(db, exam, {})


@router.get("/exams/{exam_id}", response_model=ExamDetail, summary="考试详情")
async def get_exam(exam_id: int, db: AsyncSession = Depends(get_db)) -> ExamDetail:
    """考试详情:基本信息 + 全部考卷(含 OCR 状态与逐题结果)+ 报告状态"""
    exam = await _get_exam_or_404(db, exam_id)
    papers = (
        await db.execute(select(ExamPaper).where(ExamPaper.exam_id == exam_id).order_by(ExamPaper.id))
    ).scalars().all()
    report = (await db.execute(select(ExamReport.id).where(ExamReport.exam_id == exam_id))).first()
    brief = await _brief_of(
        db, exam,
        {"total": len(papers), "done": sum(1 for p in papers if p.ocr_status == "DONE")},
    )
    return ExamDetail(
        **brief.model_dump(),
        papers=[ExamPaperOut.model_validate(p) for p in papers],
        has_report=report is not None,
    )


@router.put("/exams/{exam_id}", response_model=ExamBrief, summary="更新考试")
async def update_exam(exam_id: int, payload: ExamUpdate, db: AsyncSession = Depends(get_db)) -> ExamBrief:
    """更新考试基本信息(姓名/日期/满分/类型/备注)"""
    exam = await _get_exam_or_404(db, exam_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(exam, field, value)
    await db.commit()
    await db.refresh(exam)
    papers = (await db.execute(select(ExamPaper).where(ExamPaper.exam_id == exam_id))).scalars().all()
    return await _brief_of(
        db, exam,
        {"total": len(papers), "done": sum(1 for p in papers if p.ocr_status == "DONE")},
    )


@router.delete("/exams/{exam_id}", summary="删除考试")
async def delete_exam(
    exam_id: int,
    confirm: bool = Query(default=False, description="需显式传 true 二次确认"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除考试及其考卷/报告(同时清理考卷图片文件;不可恢复)"""
    if not confirm:
        raise HTTPException(status_code=400, detail="删除考试不可恢复,请确认后重试(confirm=true)")
    exam = await _get_exam_or_404(db, exam_id)
    papers = (await db.execute(select(ExamPaper).where(ExamPaper.exam_id == exam_id))).scalars().all()
    removed_files = 0
    for paper in papers:
        for rel in paper.image_paths or []:
            try:
                (settings.upload_path / rel).unlink(missing_ok=True)
                removed_files += 1
            except OSError:  # noqa: PERF203 —— 清理失败不影响删除
                pass
    report = (await db.execute(select(ExamReport).where(ExamReport.exam_id == exam_id))).scalars().first()
    if report is not None:
        await db.delete(report)
    for paper in papers:
        await db.delete(paper)
    await db.delete(exam)
    await db.commit()
    logger.info("考试 %s 已删除(考卷 %s,图片文件 %s)", exam_id, len(papers), removed_files)
    return {"deleted": exam_id, "papers": len(papers), "files": removed_files}


# ---------------------------------------------------------
# 考卷上传 / 修订 / 重试
# ---------------------------------------------------------
@router.post("/exams/{exam_id}/papers", response_model=list[ExamPaperOut], summary="上传考卷")
async def upload_papers(
    exam_id: int,
    files: list[UploadFile] = File(description="考卷/答题卡图片(每个文件一名学生)"),
    student_name: str | None = Form(default=None, description="单文件时的手动指派姓名(可选)"),
    student_id: str | None = Form(default=None, description="单文件时的手动指派学号(可选)"),
    db: AsyncSession = Depends(get_db),
    exam_service: ExamService = Depends(get_exam_service),
) -> list[ExamPaperOut]:
    """批量上传考卷:文件名匹配花名册(学号优先/姓名兜底)→ 未匹配则取文件名;

    同名考卷再次上传视为续传多页(追加图片并重新识别);上传后自动入队 OCR。
    """
    exam = await _get_exam_or_404(db, exam_id)
    _validate_upload_request(files, allow_zip=False)
    roster = await load_roster(db, exam.class_id)
    subdir = f"exam_{exam_id}"
    saved: list[str] = []
    papers: list[ExamPaper] = []
    try:
        for index, file in enumerate(files, start=1):
            filename = file.filename or "unnamed"
            manual = len(files) == 1 and (student_name or "").strip()
            if manual:
                name = (student_name or "").strip()
                sid = (student_id or "").strip() or None
            else:
                assignment = assign_from_filename(filename, roster)
                if assignment.matched:
                    name, sid = assignment.student_name or "", assignment.student_id
                else:
                    name, sid = Path(filename).stem.strip() or f"未命名-{index}", (student_id or "").strip() or None
            rel = await _save_upload(file, subdir, index)
            saved.append(rel)
            paper = (
                await db.execute(
                    select(ExamPaper).where(ExamPaper.exam_id == exam_id, ExamPaper.student_name == name)
                )
            ).scalars().first()
            if paper is None:
                paper = ExamPaper(
                    exam_id=exam_id, student_name=name, student_id=sid,
                    image_paths=[rel], ocr_status="PENDING",
                )
                db.add(paper)
            else:
                paper.image_paths = list(paper.image_paths or []) + [rel]
                if sid and not paper.student_id:
                    paper.student_id = sid
                paper.ocr_status = "PENDING"
                paper.ocr_error = None
            await upsert_student(db, name, sid)
            papers.append(paper)
        exam.status = "PROCESSING"
        await db.commit()
        for paper in papers:
            await db.refresh(paper)
    except HTTPException:
        _cleanup_saved(saved)
        raise
    except Exception:
        _cleanup_saved(saved)
        raise
    for paper in papers:
        await exam_service.enqueue(paper.id)
    logger.info("考试 %s 上传 %s 份考卷(已入队识别)", exam_id, len(papers))
    return [ExamPaperOut.model_validate(p) for p in papers]


@router.post("/exams/{exam_id}/papers/{paper_id}/retry", response_model=ExamPaperOut, summary="重新识别考卷")
async def retry_paper(
    exam_id: int,
    paper_id: int,
    db: AsyncSession = Depends(get_db),
    exam_service: ExamService = Depends(get_exam_service),
) -> ExamPaperOut:
    """重新识别单份考卷(失败重试或人工修订前重跑)"""
    exam = await _get_exam_or_404(db, exam_id)
    paper = await _get_paper_or_404(db, exam_id, paper_id)
    paper.ocr_status = "PENDING"
    paper.ocr_error = None
    exam.status = "PROCESSING"
    await db.commit()
    await db.refresh(paper)
    await exam_service.enqueue(paper.id)
    return ExamPaperOut.model_validate(paper)


@router.put("/exams/{exam_id}/papers/{paper_id}", response_model=ExamPaperOut, summary="人工修订考卷")
async def update_paper(
    exam_id: int,
    paper_id: int,
    payload: ExamPaperUpdate,
    db: AsyncSession = Depends(get_db),
) -> ExamPaperOut:
    """人工修订(教师修订后 teacher_edited=1,人工值优先,重跑识别不覆盖)"""
    await _get_exam_or_404(db, exam_id)
    paper = await _get_paper_or_404(db, exam_id, paper_id)

    if payload.student_name is not None:
        new_name = payload.student_name.strip()
        conflict = (
            await db.execute(
                select(ExamPaper).where(
                    ExamPaper.exam_id == exam_id,
                    ExamPaper.student_name == new_name,
                    ExamPaper.id != paper_id,
                )
            )
        ).scalars().first()
        if conflict is not None:
            raise HTTPException(status_code=400, detail=f"该考试中已存在学生「{new_name}」的考卷")
        paper.student_name = new_name
    if payload.student_id is not None:
        paper.student_id = payload.student_id.strip() or None

    if payload.question_results is not None:
        normalized: list[dict] = []
        for q in payload.question_results:
            item = q.model_dump()
            part = (item.get("part") or "").strip()
            if part and _LISTENING_RE.search(part):
                continue  # 听力题不纳入统计(与其他入口口径一致)
            tag = (item.get("knowledge_tag") or "").strip() or None
            item["knowledge_tag"] = tag
            item["canonical_type"] = (
                item.get("canonical_type") or (normalize_error_type(tag).value if tag else None)
            )
            normalized.append(item)
        paper.question_results = normalized
        paper.teacher_edited = 1
        if payload.total_score is None:
            scored = [q["score"] for q in normalized if isinstance(q.get("score"), (int, float))]
            if scored:
                paper.total_score = round(sum(scored), 2)

    if payload.total_score is not None:
        paper.total_score = payload.total_score
        paper.teacher_edited = 1

    if paper.student_name and paper.student_name != "未知":
        await upsert_student(db, paper.student_name, paper.student_id)
    await db.commit()
    await db.refresh(paper)
    logger.info("考卷 %s 已人工修订(teacher_edited=1)", paper_id)
    return ExamPaperOut.model_validate(paper)


# ---------------------------------------------------------
# 报告
# ---------------------------------------------------------
@router.post("/exams/{exam_id}/report", response_model=ExamReportOut, summary="生成分析报告")
async def create_report(exam_id: int, db: AsyncSession = Depends(get_db)) -> ExamReportOut:
    """聚合全部已识别考卷,生成(或重新生成)分析报告并持久化"""
    try:
        report = await generate_exam_report(db, exam_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ExamReportOut(
        exam_id=report.exam_id,
        report_markdown=report.report_markdown,
        stats=report.stats or {},
        generated_at=report.generated_at,
    )


@router.get("/exams/{exam_id}/report", response_model=ExamReportOut, summary="查看分析报告")
async def get_report(exam_id: int, db: AsyncSession = Depends(get_db)) -> ExamReportOut:
    """查看已生成的报告(未生成时 404)"""
    await _get_exam_or_404(db, exam_id)
    report = (
        await db.execute(select(ExamReport).where(ExamReport.exam_id == exam_id))
    ).scalars().first()
    if report is None:
        raise HTTPException(status_code=404, detail="该考试尚未生成分析报告")
    return ExamReportOut(
        exam_id=report.exam_id,
        report_markdown=report.report_markdown,
        stats=report.stats or {},
        generated_at=report.generated_at,
    )


@router.get("/exams/{exam_id}/report/export", summary="导出报告 Markdown")
async def export_report(exam_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """下载报告 Markdown 文件"""
    await _get_exam_or_404(db, exam_id)
    report = (
        await db.execute(select(ExamReport).where(ExamReport.exam_id == exam_id))
    ).scalars().first()
    if report is None:
        raise HTTPException(status_code=404, detail="该考试尚未生成分析报告")
    return Response(
        content=report.report_markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="exam_report_{exam_id}.md"'},
    )
