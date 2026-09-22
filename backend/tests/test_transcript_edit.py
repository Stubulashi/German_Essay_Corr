"""转录原文修订测试

覆盖:
- 保存后 result.transcribed_text 与系统报告同步刷新(含错例上下文重定位);
- 教师编辑版报告与教师寄语不受影响;学生版按需同步;
- 批注核对接口与修订后的转录全文保持一致(区间定位基于新文本);
- 守卫:无结果任务 400;空白文本被校验拒绝。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes_tasks
from app.models.db_models import Base, CorrectionTask
from app.models.schemas import TranscriptUpdateRequest


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'trans.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _result(total_text: str) -> dict:
    return {
        "student_name": "李明",
        "student_id": "S001",
        "transcribed_text": total_text,
        "overall_score": "18 / 25",
        "overall_comment": "整体不错",
        "errors": [
            {
                "original_text": "mit meine Familie",
                "corrected_text": "mit meiner Familie",
                "error_type": "名词变格",
                "canonical_type": "CASE_DECLENSION",
                "explanation": "mit 接第三格",
            }
        ],
        "highlights": [],
        "markdown_report": "# 系统报告",
    }


async def _seed(db, *, with_result: bool = True) -> CorrectionTask:
    task = CorrectionTask(
        student_name="李明",
        student_id="S001",
        status="COMPLETED" if with_result else "PENDING",
        stage="DONE" if with_result else "UPLOADED",
        progress=1.0 if with_result else 0.0,
        image_paths=["t1/a.png"],
        result=_result("Ich war mit meine Familie im Urlaub.") if with_result else None,
        edited_report="# 教师编辑版",
        teacher_message="继续加油!",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


class TestTranscriptUpdate:
    """保存路径与下游一致性"""

    async def test_save_refreshes_result_and_keeps_teacher_edits(self, db):
        task = await _seed(db)
        detail = await routes_tasks.update_transcript(
            task_id=task.id,
            payload=TranscriptUpdateRequest(
                transcribed_text="Ich war mit meiner Familie im Urlaub, es war schoen."
            ),
            db=db,
        )
        # 结果与系统报告已同步刷新
        assert detail.result is not None
        assert detail.result.transcribed_text.startswith("Ich war mit meiner Familie")
        assert detail.result.markdown_report and detail.result.markdown_report != "# 系统报告"
        # 教师编辑版与寄语保留
        assert detail.edited_report == "# 教师编辑版"
        assert detail.teacher_message == "继续加油!"
        # 学生版按需渲染仍可用
        assert detail.student_report and "### 四、教师寄语" in detail.student_report

    async def test_annotation_view_consistent_with_new_text(self, db):
        task = await _seed(db)
        await routes_tasks.update_transcript(
            task_id=task.id,
            payload=TranscriptUpdateRequest(
                transcribed_text="Neu: mit meine Familie war es schoen."
            ),
            db=db,
        )
        annotation = await routes_tasks.get_task_annotation(task_id=task.id, db=db)
        assert annotation.transcribed_text == "Neu: mit meine Familie war es schoen."
        # 错误片段仍可定位(基于新文本重新计算)
        assert len(annotation.segments) == 1 and annotation.unlocated == []

    async def test_unmatched_fragments_fall_into_unlocated(self, db):
        task = await _seed(db)
        await routes_tasks.update_transcript(
            task_id=task.id,
            payload=TranscriptUpdateRequest(transcribed_text="Ein voellig anderer Text ohne den Fehler."),
            db=db,
        )
        annotation = await routes_tasks.get_task_annotation(task_id=task.id, db=db)
        assert annotation.segments == []
        assert annotation.unlocated == [0]  # 信息不丢失,降入兜底清单


class TestGuards:
    """守卫与校验"""

    async def test_requires_result(self, db):
        task = await _seed(db, with_result=False)
        with pytest.raises(Exception):
            await routes_tasks.update_transcript(
                task_id=task.id,
                payload=TranscriptUpdateRequest(transcribed_text="x"),
                db=db,
            )

    async def test_blank_text_rejected(self, db):
        task = await _seed(db)
        with pytest.raises(Exception):
            await routes_tasks.update_transcript(
                task_id=task.id,
                payload=TranscriptUpdateRequest(transcribed_text="   "),
                db=db,
            )
