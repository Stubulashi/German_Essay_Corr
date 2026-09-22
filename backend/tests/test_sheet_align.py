"""标准答题卷自动找平测试(纯 PIL 合成,无网络)

覆盖:旋转标准卷 → 找平为规范画布且主/次定位块与信息区就位;无误照片字节不变;
小图/损坏图回退;开关关闭时路由钩子直通;姓名区裁剪三态。
几何与模板/后端共享:px = 1500/210 ≈ 7.1429/mm;主块中心 20mm→142.9;次块中心 (16,72)/(194,72)mm。
"""

import io

from PIL import Image, ImageDraw

from app.config import settings
from app.services.sheet_align import (
    CANON_HEIGHT,
    CANON_WIDTH,
    align_standard_sheet,
    crop_name_region,
    crop_name_region_ex,
)

#: 与前端模板一致的 mm→合成 px 比例(150dpi 折算:1mm≈5.9px,取整方便断言)
MM = 5.9
PAGE_W, PAGE_H = int(210 * MM), int(297 * MM)  # ≈1239×1752
MARGIN = int(12 * MM)  # 主定位块外侧边距 ≈70px
BLOCK = int(16 * MM)  # 主定位块边长 ≈94px
SUB_BLOCK = int(8 * MM)  # 次定位块边长 ≈47px


def _make_sheet_bytes(*, rotate_deg: float = 0.0, with_markers: bool = True, continuation: bool = False) -> bytes:
    """合成标准卷(可带旋转/续写类型带):四角主块 + 信息区次块 + 信息区内容 + 正文灰线"""
    image = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(image)
    if with_markers:
        for x, y in (
            (MARGIN, MARGIN),
            (PAGE_W - MARGIN - BLOCK, MARGIN),
            (PAGE_W - MARGIN - BLOCK, PAGE_H - MARGIN - BLOCK),
            (MARGIN, PAGE_H - MARGIN - BLOCK),
        ):
            draw.rectangle([x, y, x + BLOCK, y + BLOCK], fill=(0, 0, 0))
        # 次定位块 ×2(y=68mm,左右各一)
        draw.rectangle([MARGIN, int(68 * MM), MARGIN + SUB_BLOCK, int(68 * MM) + SUB_BLOCK], fill=(0, 0, 0))
        draw.rectangle(
            [PAGE_W - MARGIN - SUB_BLOCK, int(68 * MM), PAGE_W - MARGIN, int(68 * MM) + SUB_BLOCK],
            fill=(0, 0, 0),
        )
        # 信息区(姓名/学号文字块,y 44-54mm → 规范 px 314-386,落在 NAME_REGION 内)
        draw.rectangle([MARGIN + 10, int(44 * MM), MARGIN + 90, int(54 * MM)], fill=(0, 0, 0))
        draw.rectangle([int(62 * MM), int(44 * MM), int(92 * MM), int(54 * MM)], fill=(0, 0, 0))
        # 正文书写线(灰色,不干扰黑块检测)
        for index in range(21):
            y = int((84 + index * 9) * MM)
            draw.line([MARGIN, y, PAGE_W - MARGIN, y], fill=(150, 150, 150), width=2)
        # 续写纸类型带:底带 L/R 两块(位串 101)
        if continuation:
            for center_x_mm in (90, 120):
                cx, cy = center_x_mm * MM, 277 * MM
                half = 4 * MM
                draw.rectangle([cx - half, cy - half, cx + half, cy + half], fill=(0, 0, 0))
    if rotate_deg:
        image = image.rotate(rotate_deg, expand=True, fillcolor="white", resample=Image.BICUBIC)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _dark_in_window(gray: Image.Image, box: tuple[int, int, int, int]) -> int:
    crop = gray.crop(box)
    return sum(1 for value in crop.tobytes() if value <= 90)


def _make_plain_photo_with_edge_band() -> bytes:
    """合成“桌面背景黑带”照片(无任何定位块):右缘一条黑色竖带

    回归背景:该形态曾诱导旧检测把黑带当作“四角块”造成假找平(changed=True);
    修复后必须原样回退(changed=False),且不得产出姓名区裁剪。
    """
    image = Image.new("RGB", (1650, 2200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([1520, 0, 1650, 1100], fill=(10, 10, 10))
    draw.line([60, 300, 1560, 300], fill=(120, 120, 120), width=3)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class TestAlignStandardSheet:
    def test_rotated_sheet_is_aligned_to_canonical_canvas(self):
        rotated = _make_sheet_bytes(rotate_deg=8)
        out, changed, page_type = align_standard_sheet(rotated)
        assert changed is True
        assert page_type == "home"
        with Image.open(io.BytesIO(out)) as aligned:
            assert aligned.size == (CANON_WIDTH, CANON_HEIGHT)
            gray = aligned.convert("L")
            # 主定位块中心窗口应重新变黑(理论规范坐标 142.9 / 1357.1 / 1978.1)
            for cx, cy in (
                (143, 143),
                (CANON_WIDTH - 143, 143),
                (CANON_WIDTH - 143, CANON_HEIGHT - 143),
                (143, CANON_HEIGHT - 143),
            ):
                window = (cx - 40, cy - 40, cx + 40, cy + 40)
                assert _dark_in_window(gray, window) > 200, f"主块窗口未就位:{window}"
            # 次定位块应落在 (114,514)/(1386,514) 窗口
            for cx, cy in ((114, 514), (1386, 514)):
                window = (cx - 20, cy - 20, cx + 20, cy + 20)
                assert _dark_in_window(gray, window) > 400, f"次块窗口未就位:{window}"
            # 信息区(姓名/学号文字)应落在规范坐标 (86,310)-(1086,416)
            assert _dark_in_window(gray, (86, 310, 1086, 416)) > 300

    def test_plain_photo_unchanged(self):
        """无定位块的普通图 → 原字节返回"""
        image = Image.new("RGB", (900, 1300), "white")
        draw = ImageDraw.Draw(image)
        for index in range(12):  # 模拟手写行的灰线(无黑块)
            draw.line([60, 100 + index * 60, 840, 100 + index * 60], fill=(120, 120, 120), width=3)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        raw = buffer.getvalue()
        out, changed, page_type = align_standard_sheet(raw)
        assert changed is False and out == raw and page_type == "home"

    def test_photo_with_edge_band_not_aligned(self):
        """桌面黑带照片不得“假找平”(块真实性校验:黑带/背景不是方块)"""
        raw = _make_plain_photo_with_edge_band()
        out, changed, page_type = align_standard_sheet(raw)
        assert changed is False and out == raw and page_type == "home"

    def test_tiny_image_unchanged(self):
        buffer = io.BytesIO()
        Image.new("RGB", (100, 100), "black").save(buffer, "JPEG")
        raw = buffer.getvalue()
        out, changed, page_type = align_standard_sheet(raw)
        assert changed is False and out == raw and page_type == "home"

    def test_corrupted_bytes_unchanged(self):
        raw = b"\x00\x01not-an-image"
        out, changed, page_type = align_standard_sheet(raw)
        assert changed is False and out == raw and page_type == "home"


class TestNameCrop:
    def test_sheet_crop_by_landmarks(self):
        """标准卷 → 依据检测到的定位点找平后按规范几何裁剪(宽 900)"""
        data, meta = crop_name_region_ex(_make_sheet_bytes())
        assert data is not None and meta["basis"] == "landmarks"
        assert len(meta["main_points"]) == 4  # 裁切依据 = 实测四角块心
        with Image.open(io.BytesIO(data)) as cropped:
            assert cropped.format == "JPEG"
            assert cropped.width == 900
            assert 80 <= cropped.height <= 120, cropped.size

    def test_canvas_crop_by_canonical_anchors(self):
        """已找平画布 → 四角理论锚定检测通过后按规范几何裁剪"""
        canvas_bytes, changed, _ = align_standard_sheet(_make_sheet_bytes())
        assert changed is True
        data, meta = crop_name_region_ex(canvas_bytes)
        assert data is not None and meta["basis"] == "canonical"
        assert len(meta["main_points"]) == 4
        with Image.open(io.BytesIO(data)) as cropped:
            assert cropped.width == 900

    def test_plain_photo_without_landmarks_returns_none(self):
        """无定位点的普通照片 → 不裁(返回 None;不做与定位点无关的启发式裁剪)"""
        image = Image.new("RGB", (900, 1300), "white")
        ImageDraw.Draw(image).text((60, 80), "Name: Li Ming", fill="black")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        data, meta = crop_name_region_ex(buffer.getvalue())
        assert data is None and meta["basis"] == "none"

    def test_edge_band_photo_no_crop(self):
        """背景黑带照片 → 不裁(黑带不得伪装成定位点)"""
        data, meta = crop_name_region_ex(_make_plain_photo_with_edge_band())
        assert data is None and meta["basis"] == "none"

    def test_corrupted_bytes_return_none(self):
        assert crop_name_region(b"\x00\x01not-an-image") is None


class TestPageTypeBand:
    def test_continuation_paper_decoded(self):
        """续写纸(位串 101)→ continuation;旋转 8° 后仍稳定"""
        out, changed, page_type = align_standard_sheet(_make_sheet_bytes(continuation=True))
        assert (changed, page_type) == (True, "continuation")
        out2, changed2, page_type2 = align_standard_sheet(
            _make_sheet_bytes(rotate_deg=8, continuation=True)
        )
        assert (changed2, page_type2) == (True, "continuation")

    def test_home_sheet_decoded_as_home(self):
        """普通首页(无底带)→ home(兼容旧卷)"""
        _, changed, page_type = align_standard_sheet(_make_sheet_bytes())
        assert (changed, page_type) == (True, "home")

    def test_partial_band_falls_back_to_home(self):
        """未登记组合(只画 L 块)→ home(安全回退,不误判续写)"""
        import io as _io

        from PIL import Image as _Image

        image = _Image.open(_io.BytesIO(_make_sheet_bytes())).convert("RGB")
        from PIL import ImageDraw as _ImageDraw

        draw = _ImageDraw.Draw(image)
        cx, cy, half = 90 * MM, 277 * MM, 4 * MM
        draw.rectangle([cx - half, cy - half, cx + half, cy + half], fill=(0, 0, 0))
        buffer = _io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        _, changed, page_type = align_standard_sheet(buffer.getvalue())
        assert changed is True and page_type == "home"


class TestAlignSwitchHook:
    async def test_switch_off_passthrough(self, monkeypatch):
        """开关关闭 → 路由钩子直通(即使图是标准卷),类型固定 home"""
        from app.api.routes_corrections import _maybe_align_sheet

        monkeypatch.setattr(settings, "sheet_align_enabled", False)
        sheet = _make_sheet_bytes(rotate_deg=8)
        content, page_type = await _maybe_align_sheet(sheet)
        assert content == sheet and page_type == "home"

    async def test_switch_on_with_plain_photo_passthrough(self, monkeypatch):
        from app.api.routes_corrections import _maybe_align_sheet

        monkeypatch.setattr(settings, "sheet_align_enabled", True)
        sheet = _make_sheet_bytes(with_markers=False)
        content, page_type = await _maybe_align_sheet(sheet)
        assert content == sheet and page_type == "home"
