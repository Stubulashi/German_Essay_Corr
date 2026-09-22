"""重新批改与教师寄语测试

覆盖:
- 前置守卫(仅 COMPLETED、原图必须存在、班级校验);
- 状态流转与追溯(回退 PENDING/UPLOADED、清 OCR/故障转移、计数与时间、上期摘要);
- 保留项(教师编辑版报告、教师寄语);
- 错因记录重建与下游联动一致性(画像/班级分析不重复计数);
- 路由层(重新批改入队、教师寄语保存与默认回退);
- 学生版报告寄语渲染。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes_tasks
from app.models.db_models import Base, CorrectionTask, ErrorRecord, SchoolClass
from app.models.schemas import (
    EssayCorrectionResult,
    GradingStandard,
    PipelineChoice,
    TaskRecorrectRequest,
    TeacherMessageUpdate,
)
from app.services import recorrect_service
from app.services.analytics_service import build_class_diagnosis, build_student_profile
from app.services.correction_service import CorrectionService
from app.services.recorrect_service import RecorrectError, apply_recorrect
from app.services.report_renderer import DEFAULT_TEACHER_MESSAGE, render_student_report


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'rec.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def upload_dir(tmp_path, monkeypatch):
    """把上传目录重定向到临时目录(避免写入真实 data/uploads)"""
    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    monkeypatch.setattr(recorrect_service, "settings", SimpleNamespace(upload_path=uploads))
    return uploads


def _result_dict(errors: int = 2, score: str = "18 / 25") -> dict:
    """构造一个可通过 EssayCorrectionResult 校验的结果字典"""
    return {
        "student_name": "李明",
        "student_id": "S001",
        "transcribed_text": "Meine Sommerferien waren sehr schoen.",
        "overall_score": score,
        "overall_comment": "整体不错",
        "errors": [
            {
                "original_text": f"fehler{i}",
                "corrected_text": f"korrigiert{i}",
                "error_type": "拼写",
                "canonical_type": "SPELLING",
                "explanation": None,
            }
            for i in range(errors)
        ],
        "highlights": ["表达流畅"],
        "markdown_report": "# 系统报告 v1",
    }


async def _seed_completed(db, uploads, *, status: str = "COMPLETED", image: bool = True):
    """插入一个已完成任务(含旧错题记录、教师编辑版与寄语)"""
    cls = SchoolClass(name="高二(3)班")
    db.add(cls)
    await db.flush()
    rel = "t1/a.png"
    if image:
        (uploads / "t1").mkdir(exist_ok=True)
        (uploads / rel).write_bytes(b"fake-image")
    task = CorrectionTask(
        class_id=cls.id,
        student_name="李明",
        student_id="S001",
        status=status,
        stage="DONE" if status == "COMPLETED" else "GRADING",
        progress=1.0,
        image_paths=[rel],
        result=_result_dict() if status == "COMPLETED" else None,
        edited_report="# 教师编辑版",
        teacher_message="加油,继续保持!",
        pipeline_choice="PIPELINE_B_CLOUD",
        pipeline_used="PIPELINE_B_CLOUD",
        grading_standard="GAOKAO",
        detail_level="MEDIUM",
        ocr_result={"student_name": "李明", "student_id": "S001", "transcribed_text": "旧 OCR"},
    )
    db.add(task)
    await db.flush()
    for i in range(2):
        db.add(
            ErrorRecord(
                task_id=task.id,
                student_name="李明",
                student_id="S001",
                error_type="拼写",
                canonical_type="SPELLING",
                original_text=f"fehler{i}",
                corrected_text=f"korrigiert{i}",
            )
        )
    await db.commit()
    await db.refresh(task)
    return task, cls


class TestGuards:
    """前置校验"""

    async def test_requires_completed_status(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir, status="FAILED")
            with pytest.raises(RecorrectError):
                await apply_recorrect(db, task, {})

    async def test_requires_existing_images(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir, image=False)
            with pytest.raises(RecorrectError):
                await apply_recorrect(db, task, {})

    async def test_class_must_exist(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir)
            with pytest.raises(RecorrectError):
                await apply_recorrect(db, task, {"class_id": 9999})


class TestApplyRecorrect:
    """状态流转 / 追溯 / 保留项 / 覆盖项"""

    async def test_state_flow_and_audit_trail(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir)
            updated = await apply_recorrect(
                db, task, {"detail_level": "HIGH", "require_ocr_review": True}
            )

            assert updated.status == "PENDING"
            assert updated.stage == "UPLOADED"
            assert updated.progress == 0.0
            assert updated.error_message is None
            assert updated.ocr_result is None and updated.pipeline_used is None
            assert updated.fallback_triggered == 0
            # 追溯:上期摘要与计数
            assert updated.recorrect_count == 1
            assert updated.prev_overall_score == "18 / 25"
            assert updated.prev_error_count == 2
            assert isinstance(updated.last_recorrect_at, datetime)
            # 覆盖项生效 / 未提供项沿用
            assert updated.detail_level == "HIGH"
            assert updated.require_ocr_review == 1
            assert updated.pipeline_choice == "PIPELINE_B_CLOUD"
            assert updated.grading_standard == "GAOKAO"
            # 保留项:教师编辑版与教师寄语不被覆盖;result 供重跑期间查看
            assert updated.edited_report == "# 教师编辑版"
            assert updated.teacher_message == "加油,继续保持!"
            assert updated.result is not None

    async def test_old_error_records_cleared(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir)
            await apply_recorrect(db, task, {})
            rows = (
                await db.execute(select(ErrorRecord).where(ErrorRecord.task_id == task.id))
            ).scalars().all()
            assert rows == []

    async def test_second_recorrect_increments_and_refreshes_snapshot(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir)
            await apply_recorrect(db, task, {})
            # 模拟重跑完成(换成新的结果)后再重跑一次
            task.status = "COMPLETED"
            task.result = _result_dict(errors=1, score="22 / 25")
            await db.commit()
            await apply_recorrect(db, task, {})
            assert task.recorrect_count == 2
            assert task.prev_overall_score == "22 / 25"
            assert task.prev_error_count == 1


class TestDownstreamConsistency:
    """错因记录重建与下游统计一致性(不重复计数)"""

    async def test_rebuild_after_finalize(self, factory, upload_dir):
        async with factory() as db:
            task, cls = await _seed_completed(db, upload_dir)
            task_id = task.id
            await apply_recorrect(db, task, {})

        # 模拟批改完成:同一任务写入新结果(_finalize_success 会重建错因记录)
        new_result = EssayCorrectionResult.model_validate(_result_dict(errors=1, score="20 / 25"))
        service = CorrectionService(factory)
        await service._finalize_success(
            task_id=task_id,
            result=new_result,
            ocr_result=None,
            pipeline_used=PipelineChoice.PIPELINE_B_CLOUD,
            fallback_used=False,
        )

        async with factory() as db:
            rows = (
                await db.execute(select(ErrorRecord).where(ErrorRecord.task_id == task_id))
            ).scalars().all()
            assert len(rows) == 1  # 旧 2 条已清理,仅按新结果重建 1 条

            # 学生画像:error_total 为 1(不重复计数)
            profile = await build_student_profile(db, student_id="S001")
            assert profile is not None
            assert profile.error_total == 1
            # 班级分析:误分类计数一致
            diagnosis = await build_class_diagnosis(db, class_id=cls.id)
            assert diagnosis.error_total == 1


class TestRecorrectRoute:
    """路由层:重新批改入队 + 教师寄语"""

    class _StubQueue:
        def __init__(self):
            self.seen: list[int] = []

        async def enqueue(self, task_id: int) -> None:
            self.seen.append(task_id)

    async def test_route_enqueues_and_returns_detail(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir)
            stub = self._StubQueue()
            detail = await routes_tasks.recorrect_task(
                task_id=task.id,
                payload=TaskRecorrectRequest(detail_level="LOW", topic="Mein Traum"),
                db=db,
                queue=stub,
            )
            assert stub.seen == [task.id]
            assert detail.status == "PENDING"
            assert detail.recorrect_count == 1
            assert detail.topic == "Mein Traum"
            assert detail.prev_overall_score == "18 / 25"

    async def test_route_rejects_non_completed(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir, status="FAILED")
            stub = self._StubQueue()
            with pytest.raises(Exception):
                await routes_tasks.recorrect_task(
                    task_id=task.id,
                    payload=TaskRecorrectRequest(),
                    db=db,
                    queue=stub,
                )
            assert stub.seen == []

    async def test_teacher_message_route_set_and_clear(self, factory, upload_dir):
        async with factory() as db:
            task, _ = await _seed_completed(db, upload_dir)
            detail = await routes_tasks.update_teacher_message(
                task_id=task.id,
                payload=TeacherMessageUpdate(teacher_message="这次进步很大,为你骄傲!"),
                db=db,
            )
            assert detail.teacher_message == "这次进步很大,为你骄傲!"
            assert "这次进步很大,为你骄傲!" in (detail.student_report or "")

            cleared = await routes_tasks.update_teacher_message(
                task_id=task.id,
                payload=TeacherMessageUpdate(teacher_message=""),
                db=db,
            )
            assert cleared.teacher_message is None
            assert DEFAULT_TEACHER_MESSAGE in (cleared.student_report or "")


class TestStudentReportMessage:
    """学生版报告渲染(自定义/默认寄语)"""

    def test_custom_message_rendered(self):
        result = EssayCorrectionResult.model_validate(_result_dict())
        report = render_student_report(
            result, GradingStandard.GAOKAO, __import__("app.models.schemas", fromlist=["DetailLevel"]).DetailLevel.MEDIUM,
            teacher_message="期待你在听力上再加把劲!",
        )
        assert "### 四、教师寄语" in report
        assert "期待你在听力上再加把劲!" in report

    def test_default_message_when_empty(self):
        result = EssayCorrectionResult.model_validate(_result_dict())
        from app.models.schemas import DetailLevel

        report = render_student_report(
            result, GradingStandard.GAOKAO, DetailLevel.MEDIUM, teacher_message=None
        )
        assert DEFAULT_TEACHER_MESSAGE in report
