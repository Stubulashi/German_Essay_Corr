"""学生档案共享服务

把"学生 upsert"规则收敛为全项目唯一实现(学号优先、姓名兜底),
供批改收尾、班级数据包导入、花名册导入、学生信息纠错等多处复用。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import Student


async def upsert_student(
    db: AsyncSession,
    name: str,
    student_id: str | None,
) -> Student:
    """按 学号(优先)或 姓名 匹配学生档案,不存在则创建

    与批改流程历史行为保持一致:
    - 学号存在时按学号匹配;未命中(或未提供)时按姓名匹配;
    - 命中且档案缺少学号时补充;
    - 均未命中则新建。
    """
    student: Student | None = None
    if student_id:
        res = await db.execute(select(Student).where(Student.student_id == student_id))
        student = res.scalars().first()
    if student is None and name and name != "未知":
        res = await db.execute(select(Student).where(Student.name == name))
        student = res.scalars().first()
    if student is None:
        student = Student(name=name or "未知", student_id=student_id)
        db.add(student)
        await db.flush()
    elif student_id and not student.student_id:
        student.student_id = student_id
    return student
