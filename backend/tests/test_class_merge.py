"""班级合并测试(来源标注 / 学生匹配 / 去重 / 回滚 / 联动一致性)

覆盖:
- 合并预览:数据规模统计与花名册匹配方案(move / fill_id / duplicate / conflict);
- 合并执行:任务与考试改挂、花名册去重与学号补全、台账同名项合并、来源班级标记;
- 联动一致性:错因记录经任务跟随;合并后班级分析口径基于目标班级;
- 护栏:同班级合并 / 重复合并 / 已并入班级作为目标 均被拒绝;
- 失败回滚:执行中途异常时数据完整回滚(原子性)。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import (
    Base,
    ClassMergeLog,
    ClassRoster,
    CorrectionTask,
    ErrorRecord,
    Exam,
    ExamPaper,
    HomeworkItem,
    HomeworkRecord,
    SchoolClass,
)
from app.services import class_merge_service
from app.services.class_merge_service import ClassMergeError


@pytest.fixture
async def db(tmp_path):
    """临时文件数据库会话"""
    db_file = tmp_path / "merge.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db) -> tuple[SchoolClass, SchoolClass]:
    """构造:来源班级 S 与目标班级 T 及各类重叠数据"""
    src = SchoolClass(name="高二(3)班-旧", note="并入来源")
    tgt = SchoolClass(name="高二(3)班", note="正式班级")
    db.add_all([src, tgt])
    await db.flush()

    # ---- 花名册:同名补学号 / 同名冲突 / 同名重复 / 新增 ----
    db.add_all(
        [
            ClassRoster(class_id=src.id, name="李明", student_id="S001"),   # 目标缺学号 -> fill_id
            ClassRoster(class_id=src.id, name="王芳", student_id="S999"),   # 学号冲突 -> conflict
            ClassRoster(class_id=src.id, name="赵强", student_id=None),     # 完全重复 -> duplicate
            ClassRoster(class_id=src.id, name="钱七", student_id="S007"),   # 目标没有 -> move
            ClassRoster(class_id=tgt.id, name="李明", student_id=None),
            ClassRoster(class_id=tgt.id, name="王芳", student_id="S002"),
            ClassRoster(class_id=tgt.id, name="赵强", student_id=None),
        ]
    )

    # ---- 任务与错因(错因经任务关联跟随) ----
    task = CorrectionTask(
        class_id=src.id, student_name="李明", status="COMPLETED", stage="DONE",
        progress=1.0, image_paths=["s/1.png"], result={"overall_score": "18 / 25"},
    )
    db.add(task)
    await db.flush()
    db.add(
        ErrorRecord(
            task_id=task.id, student_name="李明", error_type="动词位序",
            canonical_type="VERB_POSITION", original_text="a", corrected_text="b",
        )
    )

    # ---- 台账:同名项(记录改挂)+ 独有项(随班改挂) ----
    item_same_src = HomeworkItem(class_id=src.id, name="书面作业", scoring_mode="LEVEL", config={})
    item_uniq_src = HomeworkItem(class_id=src.id, name="朗读打卡", scoring_mode="FLAG", config={})
    item_same_tgt = HomeworkItem(class_id=tgt.id, name="书面作业", scoring_mode="LEVEL", config={})
    db.add_all([item_same_src, item_uniq_src, item_same_tgt])
    await db.flush()
    db.add_all(
        [
            HomeworkRecord(
                item_id=item_same_src.id, class_id=src.id, student_name="李明",
                value="A", score_value=95.0, record_date=__import__("datetime").date(2026, 9, 1),
            ),
            HomeworkRecord(
                item_id=item_uniq_src.id, class_id=src.id, student_name="王芳",
                value="done", score_value=100.0, record_date=__import__("datetime").date(2026, 9, 2),
            ),
        ]
    )

    # ---- 考试 ----
    exam = Exam(class_id=src.id, name="期中考试", exam_date=__import__("datetime").date(2026, 9, 10))
    db.add(exam)
    await db.flush()
    db.add(
        ExamPaper(
            exam_id=exam.id, student_name="李明", ocr_status="DONE",
            total_score=85.0, question_results=[],
        )
    )
    await db.commit()
    return src, tgt


class TestMergePreview:
    """合并预览:统计与匹配方案"""

    async def test_preview_counts_and_plan(self, db):
        src, tgt = await _seed(db)
        preview = await class_merge_service.build_merge_preview(db, src.id, tgt.id)

        assert preview["counts"]["tasks"] == 1
        assert preview["counts"]["error_records"] == 1
        assert preview["counts"]["roster"] == 4
        assert preview["counts"]["homework_items"] == 2
        assert preview["counts"]["homework_records"] == 2
        assert preview["counts"]["exams"] == 1
        assert preview["counts"]["exam_papers"] == 1

        roster = preview["roster"]
        assert roster["move"] == 1 and roster["fill_id"] == 1
        assert roster["duplicate"] == 1 and roster["conflict"] == 1

        homework = preview["homework_items"]
        assert homework["move"] == 1 and homework["merge_records"] == 1


class TestMergeExecute:
    """合并执行:数据搬运 / 去重 / 来源标注 / 联动"""

    async def test_execute_moves_and_dedupes(self, db):
        src, tgt = await _seed(db)
        result = await class_merge_service.execute_merge(db, src.id, tgt.id)

        assert result["moved"]["tasks"] == 1
        assert result["moved"]["exams"] == 1
        assert result["roster"] == {"moved": 1, "filled_id": 1, "deduplicated": 1, "conflicts": 1}

        # 任务改挂目标班级;错因记录经任务关联自动跟随
        task = (await db.execute(select(CorrectionTask))).scalars().one()
        assert task.class_id == tgt.id
        err = (await db.execute(select(ErrorRecord))).scalars().one()
        assert err.task_id == task.id

        # 花名册:同名去重后仅剩目标记录;李明补全学号;钱七迁入
        roster = (await db.execute(select(ClassRoster).where(ClassRoster.class_id == tgt.id))).scalars().all()
        by_name = {m.name: m for m in roster}
        assert set(by_name) == {"李明", "王芳", "赵强", "钱七"}
        assert by_name["李明"].student_id == "S001"      # 学号补全
        assert by_name["王芳"].student_id == "S002"      # 冲突以目标为准
        assert by_name["钱七"].student_id == "S007"

        # 台账:同名项记录改挂目标项,来源同名项删除;独有项随班改挂
        items = (await db.execute(select(HomeworkItem))).scalars().all()
        names = sorted(i.name for i in items)
        assert names == ["书面作业", "朗读打卡"]
        records = (await db.execute(select(HomeworkRecord))).scalars().all()
        assert all(r.class_id == tgt.id for r in records)
        target_item = next(i for i in items if i.name == "书面作业")
        assert target_item.class_id == tgt.id
        assert all(r.item_id in {i.id for i in items} for r in records)

        # 考试改挂
        exam = (await db.execute(select(Exam))).scalars().one()
        assert exam.class_id == tgt.id

        # 来源班级标记为已并入(不删除)
        await db.refresh(src)
        assert src.merged_into_id == tgt.id and src.merged_at is not None

        # 合并日志写入(含冲突明细)
        log = (await db.execute(select(ClassMergeLog))).scalars().one()
        assert log.source_class_id == src.id and log.target_class_id == tgt.id
        assert log.stats["roster"]["conflicts"] == 1
        assert log.stats["roster_conflicts"][0]["name"] == "王芳"

    async def test_guards(self, db):
        src, tgt = await _seed(db)
        with pytest.raises(ClassMergeError):
            await class_merge_service.execute_merge(db, src.id, src.id)  # 同班级
        with pytest.raises(LookupError):
            await class_merge_service.execute_merge(db, 99999, tgt.id)   # 不存在

        await class_merge_service.execute_merge(db, src.id, tgt.id)
        # 重复合并来源 / 已并入班级作为目标,均应被拒绝
        with pytest.raises(ClassMergeError):
            await class_merge_service.execute_merge(db, src.id, tgt.id)
        other = SchoolClass(name="其他班")
        db.add(other)
        await db.commit()
        with pytest.raises(ClassMergeError):
            await class_merge_service.execute_merge(db, other.id, src.id)

    async def test_rollback_on_failure(self, db, monkeypatch):
        """执行中途异常 -> 数据完整回滚(任务仍属来源班级、无日志、无标记)"""
        src, tgt = await _seed(db)
        # 注意:rollback 会使会话内对象过期,先捕获 id 供断言使用(避免同步懒加载)
        src_id, tgt_id = src.id, tgt.id

        async def _boom(*args, **kwargs):
            raise RuntimeError("模拟内部故障")

        monkeypatch.setattr(class_merge_service, "_move_exams", _boom)
        with pytest.raises(ClassMergeError, match="回滚"):
            await class_merge_service.execute_merge(db, src_id, tgt_id)

        task = (await db.execute(select(CorrectionTask))).scalars().one()
        assert task.class_id == src_id  # 已回滚:任务归属未变
        await db.refresh(src)
        assert src.merged_into_id is None
        assert (await db.execute(select(ClassMergeLog))).scalars().first() is None
        # 花名册未发生任何去重/搬运
        roster_src = (await db.execute(select(ClassRoster).where(ClassRoster.class_id == src_id))).scalars().all()
        assert len(roster_src) == 4

    async def test_merge_then_analytics_scope(self, db):
        """合并后:班级维度数据全部归属于目标班级(联动一致性)"""
        src, tgt = await _seed(db)
        await class_merge_service.execute_merge(db, src.id, tgt.id)

        src_tasks = (await db.execute(select(CorrectionTask).where(CorrectionTask.class_id == src.id))).scalars().all()
        tgt_tasks = (await db.execute(select(CorrectionTask).where(CorrectionTask.class_id == tgt.id))).scalars().all()
        assert len(src_tasks) == 0 and len(tgt_tasks) == 1

        logs = await class_merge_service.list_merge_logs(db)
        assert len(logs) == 1 and logs[0]["source_class_name"] == src.name
