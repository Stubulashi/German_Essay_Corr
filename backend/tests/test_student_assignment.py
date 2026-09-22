"""上传指派与姓名解析测试(方向二 / 四)

覆盖:
- 花名册文本解析(分隔符、期头跳过、去重);
- 按文件名匹配(学号精确 > 姓名精确 > 文件名包含);
- 按名单顺序匹配(起始序号、越界);
- 姓名/学号决策链(预指派 > 名单学号 > 名单姓名 > 抬头包含 > LLM > 未知);
- correction_service 落库前身份决策链(预指派优先、名单对齐纠正识别错误)。
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models.db_models import Base, ClassRoster, CorrectionTask, SchoolClass
from app.services.correction_service import CorrectionService
from app.services.student_assignment import (
    assign_from_filename,
    assign_from_order,
    normalize_fullwidth,
    parse_roster_text,
)
from app.services.student_info_resolver import extract_from_head, resolve_identity


def _roster() -> list[ClassRoster]:
    """构造测试花名册(不入库,直接内存对象)"""
    return [
        ClassRoster(id=1, class_id=1, name="李明", student_id="20260101"),
        ClassRoster(id=2, class_id=1, name="王芳", student_id="20260102"),
        ClassRoster(id=3, class_id=1, name="赵强", student_id=None),
    ]


class TestParseRosterText:
    """花名册文本解析"""

    def test_basic_lines(self):
        text = "李明,20260101\n王芳,20260102\n赵强"
        assert parse_roster_text(text) == [
            ("李明", "20260101"),
            ("王芳", "20260102"),
            ("赵强", None),
        ]

    def test_header_and_blank_and_dup(self):
        text = "姓名,学号\n\n李明,20260101\n李明,20260101\n张三 20260099"
        result = parse_roster_text(text)
        assert ("李明", "20260101") in result
        assert len([n for n, _ in result if n == "李明"]) == 1  # 去重
        assert ("张三", "20260099") in result

    def test_fullwidth_digits(self):
        result = parse_roster_text("李明,２０２６０１０１")
        assert result == [("李明", "20260101")]


class TestAssignFromFilename:
    """按文件名匹配花名册"""

    def test_id_token_match(self):
        result = assign_from_filename("20260102_扫描件.jpg", _roster())
        assert result.matched and result.student_name == "王芳"
        assert result.student_id == "20260102"

    def test_name_token_match(self):
        result = assign_from_filename("高二3班_赵强_作文.png", _roster())
        assert result.matched and result.student_name == "赵强"

    def test_name_contained_in_stem(self):
        result = assign_from_filename("作文-王芳-最终版.jpg", _roster())
        assert result.matched and result.student_name == "王芳"

    def test_no_match_falls_back(self):
        result = assign_from_filename("IMG_0001.jpg", _roster())
        assert not result.matched
        assert result.student_name is None


class TestAssignFromOrder:
    """按名单顺序匹配"""

    def test_sequential(self):
        roster = _roster()
        assert assign_from_order(roster, 1, 0).student_name == "李明"
        assert assign_from_order(roster, 1, 1).student_name == "王芳"
        assert assign_from_order(roster, 2, 0).student_name == "王芳"

    def test_out_of_range(self):
        result = assign_from_order(_roster(), 1, 5)
        assert not result.matched and result.student_name is None


class TestResolveIdentity:
    """姓名/学号决策链"""

    def test_preassigned_wins(self):
        result = resolve_identity(
            preassigned_name="王芳", preassigned_id="20260102",
            llm_name="李明", llm_id=None, transcribed_text="", roster=_roster(),
        )
        assert result.student_name == "王芳" and result.source == "preassigned"

    def test_roster_id_alignment(self):
        result = resolve_identity(
            preassigned_name=None, preassigned_id=None,
            llm_name="李朋",  # 名字识别错了
            llm_id="20260101",  # 但学号正确
            transcribed_text="", roster=_roster(),
        )
        assert result.student_name == "李明" and result.source == "roster_id"

    def test_roster_name_alignment(self):
        result = resolve_identity(
            preassigned_name=None, preassigned_id=None,
            llm_name="王芳", llm_id=None, transcribed_text="", roster=_roster(),
        )
        assert result.student_name == "王芳" and result.source == "roster_name"

    def test_head_text_contains_roster_name(self):
        result = resolve_identity(
            preassigned_name=None, preassigned_id=None,
            llm_name="未知", llm_id=None,
            transcribed_text="姓名:小明\n我的好朋友赵强...", roster=_roster(),
        )
        assert result.student_name == "赵强" and result.source == "roster_name"

    def test_llm_fallback_without_roster(self):
        result = resolve_identity(
            preassigned_name=None, preassigned_id=None,
            llm_name="陈晨", llm_id="20260088", transcribed_text="", roster=[],
        )
        assert result.student_name == "陈晨" and result.source == "llm"

    def test_unknown_fallback(self):
        result = resolve_identity(
            preassigned_name=None, preassigned_id=None,
            llm_name="未知", llm_id=None, transcribed_text="", roster=[],
        )
        assert result.student_name == "未知" and result.source == "unknown"


class TestExtractFromHead:
    """抬头区域提取"""

    def test_patterns(self):
        name, sid, _ = extract_from_head("姓名:李明  学号:２０２６０１０１\n正文开始……")
        assert name == "李明"
        assert sid == "20260101"

    def test_only_first_lines(self):
        text = "\n".join(["行1", "行2", "行3", "行4", "行5", "姓名:王芳"])
        name, _, _ = extract_from_head(text)
        assert name is None  # 第 6 行不再扫描


def test_normalize_fullwidth():
    assert normalize_fullwidth("ＡＢＣ１２３") == "ABC123"


# ---------------------------------------------------------
# 落库前身份决策链(correction_service)
# ---------------------------------------------------------
@pytest.fixture
async def session_factory(tmp_path):
    """临时文件数据库 + 会话工厂"""
    db_file = tmp_path / "assign.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_task(
    session_factory, *, student_name: str, roster: list[tuple[str, str | None]] | None = None
) -> int:
    """建一个班级(可带花名册)+ 一个任务,返回任务 ID"""
    async with session_factory() as session:
        cls = SchoolClass(name=f"测试班-{student_name}-{len(roster or [])}")
        session.add(cls)
        await session.flush()
        for name, sid in roster or []:
            session.add(ClassRoster(class_id=cls.id, name=name, student_id=sid))
        task = CorrectionTask(
            class_id=cls.id, student_name=student_name,
            image_paths=["t/1.png"], status="PENDING",
        )
        session.add(task)
        await session.commit()
        return task.id


async def _apply_policy(session_factory, task_id: int, result: SimpleNamespace) -> SimpleNamespace:
    """执行 _apply_identity_policy(与 _finalize_success 中的调用方式一致)"""
    service = CorrectionService(session_factory=session_factory, settings=Settings())
    async with session_factory() as session:
        task = await session.get(CorrectionTask, task_id)
        await service._apply_identity_policy(session, task, result)
    return result


class TestIdentityPolicy:
    """correction_service 落库前的身份决策链"""

    async def test_preassigned_wins_over_llm(self, session_factory):
        """预指派存在时始终以预指派为准(不覆盖);识别结果仅对比记录"""
        task_id = await _seed_task(session_factory, student_name="王芳")
        result = SimpleNamespace(
            student_name="李明", student_id="20260101",
            transcribed_text="", markdown_report="",
        )
        await _apply_policy(session_factory, task_id, result)
        assert result.student_name == "王芳"
        assert result.student_id is None

    async def test_roster_id_alignment_corrects_misread_name(self, session_factory):
        """识别姓名错了但学号正确 → 用花名册姓名纠正,避免错误归属"""
        task_id = await _seed_task(
            session_factory, student_name="未知", roster=[("李明", "20260101")]
        )
        result = SimpleNamespace(
            student_name="李朋", student_id="20260101",
            transcribed_text="", markdown_report="",
        )
        await _apply_policy(session_factory, task_id, result)
        assert result.student_name == "李明"
        assert result.student_id == "20260101"

    async def test_unknown_kept_without_evidence(self, session_factory):
        """无预指派、无名单、无识别结果时保持“未知”(不猜测)"""
        task_id = await _seed_task(session_factory, student_name="未知")
        result = SimpleNamespace(
            student_name="未知", student_id=None,
            transcribed_text="", markdown_report="",
        )
        await _apply_policy(session_factory, task_id, result)
        assert result.student_name == "未知"
