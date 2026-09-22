"""手写样本模型测试(句库/特征描述子/档位推荐/近邻匹配/模型聚合)

全部本地计算与桩,不触真实端点与真实数据。
"""

import io
import re

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Base, HandwritingModel, HandwritingSample
from app.services import handwriting_service as hs


def _name_image(text: str, dx: int = 0, dy: int = 0) -> bytes:
    """合成姓名区图(手写场景的印刷体替身;同文本两次生成应高度相似)"""
    image = Image.new("RGB", (600, 140), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 56)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    draw.text((40 + dx, 40 + dy), text, font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class TestSentenceLibrary:
    def test_generate_material_in_library(self):
        data = hs.generate_practice_material()
        assert data["topic"] in hs.TOPICS
        assert data["sentence"] in hs.SENTENCES
        assert data["topic"] and data["sentence"]


class TestFeatureDescriptor:
    def test_dimension_and_stability(self):
        vector = hs.describe_features(_name_image("Li Ming"))
        assert vector is not None and len(vector) == hs._FEATURE_FULL
        again = hs.describe_features(_name_image("Li Ming"))
        assert again is not None
        assert max(abs(a - b) for a, b in zip(vector, again)) < 1e-6  # 确定性

    def test_blank_image_returns_none(self):
        buffer = io.BytesIO()
        Image.new("RGB", (300, 100), "white").save(buffer, "PNG")
        assert hs.describe_features(buffer.getvalue()) is None

    def test_feature_slice_by_level(self):
        vector = hs.describe_features(_name_image("Wang Wei"))
        assert vector is not None
        assert hs.feature_slice(vector, "medium").shape[0] == hs._FEATURE_SEGMENT
        assert hs.feature_slice(vector, "precise").shape[0] == hs._FEATURE_FULL


class TestLevelRecommendation:
    def test_recommend_matrix(self, monkeypatch):
        cases = [
            ((8, 16.0), "precise"),
            ((4, 8.0), "medium"),
            ((2, 4.0), "light"),
            ((8, None), "medium"),
        ]
        for (cores, ram), expected in cases:
            monkeypatch.setattr(hs, "_total_memory_gb", lambda r=ram: r)
            monkeypatch.setattr(hs.os, "cpu_count", lambda c=cores: c)
            assert hs.system_profile()["recommended_level"] == expected

    def test_manual_level_wins(self, monkeypatch):
        monkeypatch.setattr(hs, "_total_memory_gb", lambda: 4.0)
        monkeypatch.setattr(hs.os, "cpu_count", lambda: 2)
        monkeypatch.setattr(settings, "handwriting_ocr_level", "precise")
        assert hs.effective_level() == "precise"  # 手动优先
        monkeypatch.setattr(settings, "handwriting_ocr_level", "auto")
        assert hs.effective_level() == "light"  # auto 回落到真实推荐


class TestNeighborMatching:
    def _entries(self, level: str = "medium"):
        same_a = hs.describe_features(_name_image("Li Ming"))
        same_b = hs.describe_features(_name_image("Li Ming", dx=3, dy=2))
        other = hs.describe_features(_name_image("Wang Wei"))
        assert same_a and same_b and other
        return [
            ("李明", "20260001", same_a),
            ("李明", "20260001", same_b),
            ("王伟", "20260002", other),
        ]

    def test_match_same_student(self):
        entries = self._entries()
        probe = _name_image("Li Ming", dx=1, dy=1)
        result = hs.match_candidate(probe, entries, "medium")
        assert result is not None and result[0] == "李明"

    def test_match_precise_knn(self):
        entries = self._entries()
        probe = _name_image("Li Ming", dx=2, dy=0)
        result = hs.match_candidate(probe, entries, "precise")
        assert result is not None and result[0] == "李明"

    def test_no_entries_returns_none(self):
        assert hs.match_candidate(_name_image("Li Ming"), [], "medium") is None

    def test_blank_crop_returns_none(self):
        buffer = io.BytesIO()
        Image.new("RGB", (200, 80), "white").save(buffer, "PNG")
        assert hs.match_candidate(buffer.getvalue(), self._entries(), "medium") is None


class TestModelRefresh:
    async def test_refresh_counts_bound_samples_only(self, tmp_path, monkeypatch):
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(hs, "SessionLocal", factory, raising=False)
        import app.db.database as database

        monkeypatch.setattr(database, "SessionLocal", factory)

        from app.models.db_models import SchoolClass

        async with factory() as session:
            session.add(SchoolClass(name="测试班"))
            await session.commit()
            session.add_all(
                [
                    HandwritingSample(
                        class_id=1, student_name="李明", student_id=None,
                        image_path="x/a.jpg", status="ok", features=[0.1] * hs._FEATURE_FULL,
                    ),
                    HandwritingSample(
                        class_id=1, student_name=None, student_id=None,
                        image_path="x/b.jpg", status="pending_bind", features=None,
                    ),
                ]
            )
            await session.commit()
        await hs.handwriting_service._refresh_model(1)
        async with factory() as session:
            model = await session.get(HandwritingModel, 1)
            assert model is not None
            assert model.samples_count == 1 and model.students_count == 1
        await engine.dispose()
