"""强制性姓名识别(FORCE_NAME_RECOGNITION)测试:解析 / 探测 / 端点 / 设置同步

全部使用桩与内存数据;不触真实引擎、网络与真实数据。
"""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.config import settings
from app.models.schemas import SettingsUpdateRequest
from app.services import name_pre_ocr as pre_module
from app.services.name_pre_ocr import parse_age, probe_personal_info


def _image_bytes(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), color).save(buffer, "JPEG")
    return buffer.getvalue()


def _zip_bytes(*entries: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=name)


class TestParseAge:
    def test_alter_keyword(self):
        assert parse_age("Alter: 16") == "16"

    def test_age_cn_with_suffix(self):
        assert parse_age("年龄 17 岁") == "17"

    def test_suffix_only_with_number_before(self):
        assert parse_age("16 岁") == "16"

    def test_no_keyword_returns_none(self):
        assert parse_age("20260123 Li Ming") is None

    def test_out_of_range_returns_none(self):
        assert parse_age("Alter: 999") is None


class TestProbePersonalInfo:
    def test_ok_with_name_age_id(self, monkeypatch):
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(
            pre_module, "crop_name_region_ex", lambda _c: (b"jpeg", {"basis": "landmarks"})
        )
        monkeypatch.setattr(
            pre_module,
            "recognize_lines",
            lambda _p: [("Name: Li Ming", 0.9), ("Alter: 16", 0.9), ("ID: 20260123", 0.9)],
        )
        outcome = probe_personal_info(b"image-bytes")
        assert outcome["status"] == "ok"
        assert outcome["name"] == "Li Ming"
        assert outcome["age"] == "16"
        assert outcome["student_id"] == "20260123"

    def test_empty_when_no_personal_info(self, monkeypatch):
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(pre_module, "crop_name_region_ex", lambda _c: (None, {"basis": "none"}))
        monkeypatch.setattr(pre_module, "_scaled_full_image", lambda _c: b"full")
        monkeypatch.setattr(pre_module, "recognize_lines", lambda _p: [("Das ist ein Satz.", 0.9)])
        outcome = probe_personal_info(b"image-bytes")
        assert outcome["status"] == "empty"

    def test_missing_component(self, monkeypatch):
        monkeypatch.setattr(pre_module, "rapid_available", lambda: False)
        outcome = probe_personal_info(b"image-bytes")
        assert outcome["status"] == "error"
        assert outcome["note"] == "missing-component"

    def test_crop_before_recognize_and_input_is_crop_result(self, monkeypatch):
        """顺序契约:先依定位点裁剪→再对裁剪产物识别(识别输入非原图)"""
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        calls: list[str] = []
        seen: dict = {}

        def fake_crop(_content):
            calls.append("crop")
            return b"CROP-ONLY", {"basis": "landmarks", "main_points": [[1.0, 1.0]] * 4}

        def fake_recognize(path):
            calls.append("recognize")
            seen["payload"] = Path(path).read_bytes()
            return [("Name: Li Ming", 0.9)]

        monkeypatch.setattr(pre_module, "crop_name_region_ex", fake_crop)
        monkeypatch.setattr(pre_module, "recognize_lines", fake_recognize)
        outcome = probe_personal_info(b"ORIGINAL-IMAGE")
        assert calls == ["crop", "recognize"]
        assert seen["payload"] == b"CROP-ONLY"  # 识别输入=裁剪产物,而非原图
        assert outcome["basis"] == "landmarks"
        assert outcome["name"] == "Li Ming"

    def test_no_landmark_fallback_uses_scaled_full_and_continues(self, monkeypatch):
        """无定位点:回退整图且不中断;识别输入=缩放整图产物"""
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        seen: dict = {}
        monkeypatch.setattr(
            pre_module,
            "crop_name_region_ex",
            lambda _c: (None, {"basis": "none", "note": "未检出有效定位块(无定位点依据)"}),
        )
        monkeypatch.setattr(pre_module, "_scaled_full_image", lambda _c: b"FULL-ONLY")

        def fake_recognize(path):
            seen["payload"] = Path(path).read_bytes()
            return [("Name: Wang Wei", 0.9)]

        monkeypatch.setattr(pre_module, "recognize_lines", fake_recognize)
        outcome = probe_personal_info(b"ORIGINAL-IMAGE")
        assert seen["payload"] == b"FULL-ONLY"
        assert outcome["basis"] == "full-image"
        assert outcome["status"] in {"ok", "empty"}  # 流程未中断(error 才失败)
        assert outcome["name"] == "Wang Wei"

    def test_canonical_basis_passthrough(self, monkeypatch):
        """规范画布:裁剪依据 canonical 透传"""
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(pre_module, "crop_name_region_ex", lambda _c: (b"jpeg", {"basis": "canonical"}))
        monkeypatch.setattr(pre_module, "recognize_lines", lambda _p: [("Name: Li Ming", 0.9)])
        outcome = probe_personal_info(b"image")
        assert outcome["basis"] == "canonical"

    def test_qr_priority_short_circuits(self, monkeypatch):
        """二维码命中:直接产出身份,不触发裁剪与 OCR"""
        monkeypatch.setattr(
            pre_module, "decode_qr_identity", lambda _c: {"student_id": "S1", "name": "Li Ming"}
        )
        monkeypatch.setattr(
            pre_module, "crop_name_region_ex", lambda _c: pytest.fail("二维码命中时不应触发裁剪")
        )
        monkeypatch.setattr(pre_module, "recognize_lines", lambda _p: pytest.fail("二维码命中时不应触发 OCR"))
        outcome = probe_personal_info(b"image")
        assert outcome["basis"] == "qr"
        assert outcome["name"] == "Li Ming"
        assert outcome["student_id"] == "S1"
        assert outcome["status"] == "ok"

    def test_qr_miss_falls_back_to_existing_chain(self, monkeypatch):
        """二维码未命中:原样回退既有"依定位点裁剪→识别"链"""
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(pre_module, "decode_qr_identity", lambda _c: None)
        monkeypatch.setattr(pre_module, "crop_name_region_ex", lambda _c: (b"jpeg", {"basis": "landmarks"}))
        monkeypatch.setattr(pre_module, "recognize_lines", lambda _p: [("Name: Wang Wei", 0.9)])
        outcome = probe_personal_info(b"image")
        assert outcome["basis"] == "landmarks"
        assert outcome["name"] == "Wang Wei"

    def test_qr_exception_silently_falls_back(self, monkeypatch):
        """二维码解码异常:静默回退,不中断识别"""
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)

        def broken(_c):
            raise RuntimeError("boom")

        monkeypatch.setattr(pre_module, "decode_qr_identity", broken)
        monkeypatch.setattr(pre_module, "crop_name_region_ex", lambda _c: (None, {"basis": "none"}))
        monkeypatch.setattr(pre_module, "_scaled_full_image", lambda _c: b"full")
        monkeypatch.setattr(pre_module, "recognize_lines", lambda _p: [("Name: Zhang San", 0.9)])
        outcome = probe_personal_info(b"image")
        assert outcome["basis"] == "full-image"
        assert outcome["name"] == "Zhang San"


class TestProbeEndpoint:
    async def test_switch_off_403(self, monkeypatch):
        monkeypatch.setattr(settings, "force_name_recognition", False)
        from app.api.routes_corrections import probe_identity

        with pytest.raises(HTTPException) as excinfo:
            await probe_identity(files=[_upload("a.jpg", _image_bytes())])
        assert excinfo.value.status_code == 403

    async def test_missing_component_503(self, monkeypatch):
        monkeypatch.setattr(settings, "force_name_recognition", True)
        monkeypatch.setattr(pre_module, "rapid_available", lambda: False)
        from app.api.routes_corrections import probe_identity

        with pytest.raises(HTTPException) as excinfo:
            await probe_identity(files=[_upload("a.jpg", _image_bytes())])
        assert excinfo.value.status_code == 503

    async def test_single_image_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "force_name_recognition", True)
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(
            pre_module,
            "probe_personal_info",
            lambda _c: {"name": "Li Ming", "age": "16", "student_id": None, "status": "ok", "note": ""},
        )
        from app.api.routes_corrections import probe_identity

        response = await probe_identity(files=[_upload("a.jpg", _image_bytes())])
        assert len(response.items) == 1
        item = response.items[0]
        assert (item.index, item.name, item.age, item.status) == (0, "Li Ming", "16", "ok")

    async def test_probe_identity_passes_basis(self, monkeypatch):
        """端点透传识别依据 basis(顺序契约的可观测性)"""
        monkeypatch.setattr(settings, "force_name_recognition", True)
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(
            pre_module,
            "probe_personal_info",
            lambda _c: {
                "name": "Li Ming",
                "age": None,
                "student_id": None,
                "status": "ok",
                "note": "",
                "basis": "landmarks",
            },
        )
        from app.api.routes_corrections import probe_identity

        response = await probe_identity(files=[_upload("a.jpg", _image_bytes())])
        assert response.items[0].basis == "landmarks"

    async def test_zip_two_pages_in_order(self, monkeypatch):
        monkeypatch.setattr(settings, "force_name_recognition", True)
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        calls: list[int] = []

        def fake_probe(content: bytes) -> dict:
            calls.append(len(content))
            return {"name": "Wang Wei", "age": None, "student_id": None, "status": "ok", "note": ""}

        monkeypatch.setattr(pre_module, "probe_personal_info", fake_probe)
        from app.api.routes_corrections import probe_identity

        payload = _zip_bytes(("p1.jpg", _image_bytes()), ("p2.jpg", _image_bytes("gray")))
        response = await probe_identity(files=[_upload("batch.zip", payload)])
        assert [item.index for item in response.items] == [0, 1]
        assert all(item.name == "Wang Wei" for item in response.items)
        assert len(calls) == 2  # 每页各探测一次


class TestSettingsSync:
    def test_field_registered(self):
        from app.services.settings_service import FIELDS

        specs = {spec.key: spec for spec in FIELDS}
        assert "FORCE_NAME_RECOGNITION" in specs
        assert specs["FORCE_NAME_RECOGNITION"].group == "upload"
        assert specs["FORCE_NAME_RECOGNITION"].control == "switch"

    def test_update_request_accepts(self):
        assert SettingsUpdateRequest(force_name_recognition=True).force_name_recognition is True
