"""本地二维码编解码测试:负载往返 / 真图解码 / 旋转 / 端点生成

解码用真实 cv2、生成用真实 qrcode(均已随包分发);不触网络与真实数据。
"""

import io

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import Base, ClassRoster, SchoolClass
from app.services.qr_identity import (
    build_qr_payload,
    decode_qr_identity,
    parse_qr_payload,
    qr_decode_available,
    qr_render_available,
    render_qr_png,
)


class TestPayload:
    def test_build_parse_roundtrip(self):
        payload = build_qr_payload("20260123", "Li Ming")
        assert payload == "V1|20260123|Li Ming"
        assert parse_qr_payload(payload) == {"student_id": "20260123", "name": "Li Ming"}

    def test_name_only(self):
        assert parse_qr_payload(build_qr_payload(None, "Wang Wei")) == {
            "student_id": None,
            "name": "Wang Wei",
        }

    def test_foreign_or_invalid_returns_none(self):
        assert parse_qr_payload("https://example.com") is None
        assert parse_qr_payload("V2|1|a") is None
        assert parse_qr_payload("V1|") is None
        assert parse_qr_payload("V1||") is None
        assert parse_qr_payload(None) is None


class TestDecodeReal:
    def test_roundtrip_decode(self):
        """生成 → 解码:身份完整往返(真实 cv2 + qrcode)"""
        assert qr_decode_available() and qr_render_available()
        png = render_qr_png(build_qr_payload("20260123", "Li Ming"))
        assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
        assert decode_qr_identity(png) == {"student_id": "20260123", "name": "Li Ming"}

    def test_rotated_photo_decode(self):
        """旋转 15° 的卷面照片仍可解码(透视矫正路径)"""
        png = render_qr_png(build_qr_payload("S001", "Zhang San"))
        image = Image.open(io.BytesIO(png)).convert("RGB").rotate(15, expand=True, fillcolor="white")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=92)
        assert decode_qr_identity(buffer.getvalue()) == {"student_id": "S001", "name": "Zhang San"}

    def test_foreign_qr_ignored(self):
        """非本系统格式二维码:可解码但不产出身份(返回 None,由上层回退)"""
        png = render_qr_png("https://example.com/hello")
        assert decode_qr_identity(png) is None

    def test_garbage_bytes(self):
        assert decode_qr_identity(b"not-an-image") is None

    def test_plain_image_without_qr(self):
        buffer = io.BytesIO()
        Image.new("RGB", (300, 200), "white").save(buffer, "JPEG")
        assert decode_qr_identity(buffer.getvalue()) is None


async def _make_roster_db(tmp_path):
    """临时库 + 班级 + 花名册成员;返回 (engine, factory, member_id)"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'qr.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        school_class = SchoolClass(name="测试班")
        session.add(school_class)
        await session.flush()
        member = ClassRoster(class_id=school_class.id, name="Li Ming", student_id="20260123")
        session.add(member)
        await session.commit()
        member_id = member.id
    return engine, factory, member_id


class TestRosterQrEndpoint:
    async def test_qr_png_ok_and_decodable(self, tmp_path):
        """端点生成的 PNG 可被解码回同一身份(生成→下载→解码 全链)"""
        engine, factory, member_id = await _make_roster_db(tmp_path)
        from app.api.routes_classes import get_roster_qr_png

        async with factory() as session:
            response = await get_roster_qr_png(member_id, db=session)
        assert response.media_type == "image/png"
        body = bytes(response.body)
        assert body[:8] == b"\x89PNG\r\n\x1a\n"
        assert decode_qr_identity(body) == {"student_id": "20260123", "name": "Li Ming"}
        await engine.dispose()

    async def test_qr_png_member_not_found(self, tmp_path):
        engine, factory, _member_id = await _make_roster_db(tmp_path)
        from app.api.routes_classes import get_roster_qr_png

        async with factory() as session:
            with pytest.raises(HTTPException) as excinfo:
                await get_roster_qr_png(999999, db=session)
        assert excinfo.value.status_code == 404
        await engine.dispose()
