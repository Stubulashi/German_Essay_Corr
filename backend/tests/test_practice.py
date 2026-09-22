"""练习卷生成测试(来源聚合 / 参数校验 / mock 生成契约 / 解析容错 / CRUD / 提示词契约)

数据来源与断言基线均为 mock 模式确定性与同源只读行为;不触达真实 LLM。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes_practice
from app.config import settings
from app.models.db_models import Base, CorrectionTask, ErrorRecord, PracticeSheet, SchoolClass
from app.models.schemas import PracticeAssignmentRef, PracticeGenerateRequest
from app.pipelines.prompts import PRACTICE_SYSTEM_PROMPT, build_practice_user_message
from app.services import practice_service
from app.services.practice_service import PracticeError, parse_practice_output


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'practice.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db):
    """两个作业分组(命名 ×2 与未命名 ×1)+ 同源错因记录"""
    cls = SchoolClass(name="高二(3)班")
    db.add(cls)
    await db.flush()
    tasks = [
        CorrectionTask(
            class_id=cls.id, student_name="李明", status="COMPLETED", stage="DONE", progress=1.0,
            image_paths=["a.png"], assignment_name="第一次月考作文", topic="Meine Sommerferien",
            result={"errors": [
                {"original_text": "mit meine Familie", "corrected_text": "mit meiner Familie",
                 "error_type": "名词变格", "canonical_type": "CASE_DECLENSION", "explanation": None},
                {"original_text": "Ich gehe gestern", "corrected_text": "Ich ging gestern",
                 "error_type": "时态", "canonical_type": "TENSE", "explanation": None},
            ]},
        ),
        CorrectionTask(
            class_id=cls.id, student_name="王芳", status="COMPLETED", stage="DONE", progress=1.0,
            image_paths=["b.png"], assignment_name="第一次月考作文", topic="Meine Sommerferien",
            result={"errors": [
                {"original_text": "mit meine Familie", "corrected_text": "mit meiner Familie",
                 "error_type": "名词变格", "canonical_type": "CASE_DECLENSION", "explanation": None},
            ]},
        ),
        CorrectionTask(
            class_id=cls.id, student_name="赵强", status="COMPLETED", stage="DONE", progress=1.0,
            image_paths=["c.png"], assignment_name=None, topic=None,
            result={"errors": [
                {"original_text": "schreiben falsch", "corrected_text": "schreiben korrekt",
                 "error_type": "拼写", "canonical_type": "SPELLING", "explanation": None},
            ]},
        ),
        CorrectionTask(  # 未完成任务:不得进入来源
            class_id=cls.id, student_name="未完成", status="PENDING", stage="UPLOADED",
            image_paths=["d.png"], assignment_name="第一次月考作文",
        ),
    ]
    db.add_all(tasks)
    await db.flush()
    for task in tasks[:3]:
        for error in (task.result or {}).get("errors") or []:
            db.add(ErrorRecord(
                task_id=task.id, student_name=task.student_name,
                error_type=error["error_type"], canonical_type=error["canonical_type"],
                original_text=error["original_text"], corrected_text=error["corrected_text"],
            ))
    await db.commit()
    return cls


class TestSources:
    """来源聚合(一次/多次/全选基础)"""

    async def test_grouped_with_top_categories_and_class_filter(self, db):
        cls = await _seed(db)
        rows = await practice_service.build_sources(db)
        by_key = {(row["class_id"], row["name"]): row for row in rows}
        assert (cls.id, "第一次月考作文") in by_key
        assert (cls.id, None) in by_key  # 未命名作业独立分组
        monthly = by_key[(cls.id, "第一次月考作文")]
        assert monthly["task_count"] == 2  # 未完成任务不计入
        assert monthly["class_name"] == "高二(3)班"
        assert "名词变格" in monthly["top_categories"]

        filtered = await practice_service.build_sources(db, class_id=cls.id)
        assert all(row["class_id"] == cls.id for row in filtered)

    async def test_error_records_same_source(self, db):
        await _seed(db)
        rows = await practice_service.build_sources(db)
        named = next(row for row in rows if row["name"] == "第一次月考作文")
        assert named["top_categories"][0] == "名词变格"  # 2 次,高于时态 1 次


class TestParseAndGuards:
    """解析容错与参数校验"""

    def test_parse_fenced_and_normalize(self):
        raw = (
            "```json\n"
            '{"title": "T", "worksheet_markdown": "WS", "answer_markdown": "AN",'
            ' "questions": [{"type": "语法填空", "no": 1, "stem": "S1", "answer": "A1"}]}\n```'
        )
        parsed = parse_practice_output(raw)
        assert parsed["questions"][0]["type"] == "grammar"  # 中文标签 -> 键
        assert parsed["questions"][0]["no"] == "1"
        assert parsed["questions"][0]["explanation"] is None

    def test_parse_empty_raises(self):
        with pytest.raises(ValueError):
            parse_practice_output('{"questions": []}')

    def test_request_validation(self):
        with pytest.raises(Exception):  # 数量低于下限(pydantic)
            PracticeGenerateRequest(question_types=["grammar"], count=3)

    async def test_unknown_type_rejected(self, db):
        await _seed(db)
        payload = PracticeGenerateRequest(scope="all", question_types=["listening"], count=5)
        with pytest.raises(PracticeError):
            await practice_service.generate_sheet(db, payload)

    async def test_empty_selection_rejected(self, db):
        await _seed(db)
        payload = PracticeGenerateRequest(scope="selected", assignments=[], question_types=["grammar"], count=5)
        with pytest.raises(PracticeError):
            await practice_service.generate_sheet(db, payload)

    async def test_no_match_scope_rejected(self, db):
        await _seed(db)
        payload = PracticeGenerateRequest(
            scope="selected",
            assignments=[PracticeAssignmentRef(class_id=None, name="不存在的作业")],
            question_types=["grammar"],
            count=5,
        )
        with pytest.raises(PracticeError):
            await practice_service.generate_sheet(db, payload)


class TestGenerateAndCrud:
    """mock 生成契约 / 落库 / 列表详情删除(路由级)"""

    async def test_generate_selected_group_contract(self, db, monkeypatch):
        cls = await _seed(db)
        monkeypatch.setattr(settings, "mock_mode", True)
        payload = PracticeGenerateRequest(
            scope="selected",
            assignments=[PracticeAssignmentRef(class_id=cls.id, name="第一次月考作文")],
            question_types=["grammar", "vocabulary"],
            count=6,
            class_id=cls.id,
        )
        out = await routes_practice.generate_practice_sheet(payload, db=db)
        assert out.question_count == 6
        assert out.class_name == "高二(3)班"
        assert out.model == "mock"
        assert out.worksheet_markdown and "试题" in out.worksheet_markdown
        assert out.answer_markdown and "标准答案" in out.answer_markdown
        assert len(out.questions) == 6
        assert {question.type for question in out.questions} <= {"grammar", "vocabulary"}
        assert [question.no for question in out.questions] == [str(i) for i in range(1, 7)]
        assert out.source["task_count"] == 2
        assert out.source["scope"] == "selected"
        assert out.params["count"] == 6

    async def test_scope_all_counts_everything(self, db, monkeypatch):
        await _seed(db)
        monkeypatch.setattr(settings, "mock_mode", True)
        payload = PracticeGenerateRequest(scope="all", question_types=["correction"], count=5)
        out = await routes_practice.generate_practice_sheet(payload, db=db)
        assert out.source["task_count"] == 3  # 三个已完成任务(含未命名)

    async def test_list_get_delete_flow(self, db, monkeypatch):
        await _seed(db)
        monkeypatch.setattr(settings, "mock_mode", True)
        payload = PracticeGenerateRequest(scope="all", question_types=["writing"], count=5)
        created = await routes_practice.generate_practice_sheet(payload, db=db)

        listed = await routes_practice.list_practice_sheets(class_id=None, db=db)
        assert [sheet.id for sheet in listed] == [created.id]

        detail = await routes_practice.get_practice_sheet(created.id, db=db)
        assert detail.title == created.title

        with pytest.raises(Exception):  # 未确认删除 -> 400
            await routes_practice.delete_practice_sheet(created.id, confirm=False, db=db)
        result = await routes_practice.delete_practice_sheet(created.id, confirm=True, db=db)
        assert result == {"deleted": created.id}
        with pytest.raises(Exception):  # 删除后查无 -> 404
            await routes_practice.get_practice_sheet(created.id, db=db)
        assert await db.get(PracticeSheet, created.id) is None


class TestPromptContract:
    """命题提示词与用户消息(六区结构 / 契约 / 证据注入)"""

    def test_system_prompt_sections_and_contract(self):
        assert "命题老师" in PRACTICE_SYSTEM_PROMPT
        assert "【输出前自检】" in PRACTICE_SYSTEM_PROMPT
        assert '"questions"' in PRACTICE_SYSTEM_PROMPT
        assert "通用考点" in PRACTICE_SYSTEM_PROMPT  # 证据不足兜底

    def test_user_message_carries_evidence_and_quota(self):
        message = build_practice_user_message(
            question_types=["grammar", "translation"],
            type_labels=practice_service.TYPE_LABELS,
            count=8,
            category_distribution=[("名词变格", 5), ("时态", 2)],
            error_samples=["1. [名词变格] mit meine Familie → mit meiner Familie"],
            topics=["Meine Sommerferien"],
        )
        assert "总题数:8" in message
        assert "语法填空(grammar)" in message
        assert "名词变格 ×5" in message
        assert "mit meine Familie" in message
        assert "题号必须一一对应" in message
