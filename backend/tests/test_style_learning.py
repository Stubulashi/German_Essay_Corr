"""示范学习测试(归纳解析 / 单一生效 / 缓存刷新 / Prompt 注入)"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models.db_models import Base, CorrectionTask
from app.models.schemas import DetailLevel, GradingStandard
from app.pipelines.prompts import APPENDIX_HEADER, STYLE_CONTEXT_HEADER, build_pipeline_a_prompt, build_grading_prompt
from app.services import style_learning_service as sls


@pytest.fixture(autouse=True)
def clean_cache():
    """还原生效风格缓存,避免用例间串扰"""
    sls._active_text = ""
    yield
    sls._active_text = ""


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'style.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_completed_task(factory, status: str = "COMPLETED") -> int:
    async with factory() as db:
        task = CorrectionTask(
            student_name="示例学生", status=status, stage="DONE", progress=1.0,
            image_paths=["a.png"],
            result={
                "student_name": "示例学生",
                "transcribed_text": "Meine Sommerferien waren sehr schön.",
                "overall_score": "20 / 25",
                "overall_comment": "整体不错,注意动词位序。",
                "errors": [
                    {"original_text": "mit meine Familie", "corrected_text": "mit meiner Familie",
                     "error_type": "名词变格", "explanation": "mit 接第三格。"},
                ],
                "highlights": ["sehr schön"],
            },
        )
        db.add(task)
        await db.commit()
        return task.id


class TestParseStyleOutput:
    """归纳输出容错解析"""

    def test_fenced_json(self):
        raw = '```json\n{"scoring_scale": {"summary": "严格"}, "narrative": "保持严格"}\n```'
        payload = sls.parse_style_output(raw)
        assert payload["narrative"] == "保持严格"
        assert payload["style"]["scoring_scale"]["summary"] == "严格"

    def test_missing_narrative_falls_back_to_summaries(self):
        raw = '{"tone": {"summary": "语气温和"}}'
        payload = sls.parse_style_output(raw)
        assert "语气温和" in payload["narrative"]

    def test_empty_content_raises(self):
        with pytest.raises(ValueError):
            sls.parse_style_output('{"scoring_scale": {}}')


class TestLearnFlow:
    """归纳流程与缓存"""

    async def test_requires_completed_task(self, factory, monkeypatch):
        monkeypatch.setattr(sls, "global_settings", Settings(mock_mode=True))
        task_id = await _seed_completed_task(factory, status="PENDING")
        async with factory() as db:
            with pytest.raises(ValueError):
                await sls.learn_from_task(db, task_id=task_id)

    async def test_mock_learn_activates_and_caches(self, factory, monkeypatch):
        monkeypatch.setattr(sls, "global_settings", Settings(mock_mode=True))
        task_id = await _seed_completed_task(factory)
        async with factory() as db:
            profile = await sls.learn_from_task(db, task_id=task_id, name="示范风格A")
        assert profile.status == "active"
        assert profile.style_json.get("scoring_scale")
        assert sls.get_active_style_text()  # 缓存已刷新
        assert sls.get_active_style_text() == profile.narrative

    async def test_single_active_and_deactivate(self, factory, monkeypatch):
        monkeypatch.setattr(sls, "global_settings", Settings(mock_mode=True))
        task_id = await _seed_completed_task(factory)
        async with factory() as db:
            first = await sls.learn_from_task(db, task_id=task_id)
            second = await sls.learn_from_task(db, task_id=task_id)
            await db.refresh(first)
            assert first.status == "inactive" and second.status == "active"
            assert sls.get_active_style_text() == second.narrative

            await sls.deactivate(db, second.id)
            assert sls.get_active_style_text() == ""

    async def test_update_active_narrative_refreshes_cache(self, factory, monkeypatch):
        monkeypatch.setattr(sls, "global_settings", Settings(mock_mode=True))
        task_id = await _seed_completed_task(factory)
        async with factory() as db:
            profile = await sls.learn_from_task(db, task_id=task_id)
            await sls.update_profile(db, profile.id, narrative="人工微调后的风格描述")
            assert sls.get_active_style_text() == "人工微调后的风格描述"

    async def test_load_active_cache_from_db(self, factory, monkeypatch):
        monkeypatch.setattr(sls, "global_settings", Settings(mock_mode=True))
        task_id = await _seed_completed_task(factory)
        async with factory() as db:
            profile = await sls.learn_from_task(db, task_id=task_id)
        sls._active_text = ""  # 模拟进程重启
        await sls.load_active_cache(factory)
        assert sls.get_active_style_text() == profile.narrative


class TestPromptInjection:
    """Prompt 注入(风格 + 教师附录,均为空时与现状一致)"""

    def test_base_prompt_unchanged_without_contexts(self):
        prompt = build_pipeline_a_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM)
        assert STYLE_CONTEXT_HEADER not in prompt
        assert APPENDIX_HEADER not in prompt

    def test_style_and_appendix_injected(self):
        prompt = build_grading_prompt(
            GradingStandard.GAOKAO,
            DetailLevel.MEDIUM,
            style_context="保持严格评分尺度",
            appendix="评语最后附一句德语鼓励语",
        )
        assert STYLE_CONTEXT_HEADER in prompt and "保持严格评分尺度" in prompt
        assert APPENDIX_HEADER in prompt and "德语鼓励语" in prompt
        # 注入顺序:风格在前,附录在后
        assert prompt.index(STYLE_CONTEXT_HEADER) < prompt.index(APPENDIX_HEADER)
