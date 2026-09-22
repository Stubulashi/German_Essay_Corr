"""学生错题本路由(#3)

- GET /api/students                       学生列表
- GET /api/students/profile               学生画像与错题本(学号或姓名查询)
- GET /api/students/{student_id}/errors   某学生的错题记录

说明:画像中的复现错因、错因分布与时间线依赖 #1 的错因分类标准化。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.db_models import ErrorRecord, Student
from app.models.schemas import ErrorRecordOut, StudentOut, StudentProfile
from app.services.analytics_service import build_student_profile

router = APIRouter(tags=["学生"])


@router.get("/students", response_model=list[StudentOut], summary="学生列表")
async def list_students(
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[StudentOut]:
    """查询全部学生档案(批改过程中自动创建)"""
    stmt = select(Student).order_by(Student.created_at.desc()).limit(limit)
    students = (await db.execute(stmt)).scalars().all()
    return [StudentOut.model_validate(s) for s in students]


@router.get("/students/profile", response_model=StudentProfile, summary="学生画像与错题本")
async def get_student_profile(
    student_id: str | None = Query(default=None, description="按学号查询(优先)"),
    name: str | None = Query(default=None, description="按姓名查询(无学号的学生)"),
    db: AsyncSession = Depends(get_db),
) -> StudentProfile:
    """聚合某学生的批改历史:错因分布、复现错因、时间线、平均分"""
    if not student_id and not name:
        raise HTTPException(status_code=400, detail="请提供 student_id 或 name 查询参数")

    profile = await build_student_profile(db, student_id=student_id, name=name)
    if profile is None:
        raise HTTPException(status_code=404, detail="未找到该学生的批改记录")
    return profile


@router.get(
    "/students/{student_id}/errors",
    response_model=list[ErrorRecordOut],
    summary="学生错题记录",
)
async def list_student_errors(
    student_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[ErrorRecordOut]:
    """查询某学生的历史错题(按时间倒序)"""
    stmt = (
        select(ErrorRecord)
        .where(ErrorRecord.student_id == student_id)
        .order_by(ErrorRecord.created_at.desc())
        .limit(limit)
    )
    records = (await db.execute(stmt)).scalars().all()
    return [ErrorRecordOut.model_validate(r) for r in records]
