"""批改创建路由(单篇 / 批量上传)

- POST /api/corrections:       单篇批改(同一学生的多页图片 = 一个任务;可手动指定学生)
- POST /api/corrections/batch: 批量批改(每张图 = 一篇;支持 ZIP 自动展开;
                               支持按文件名 / 按名单顺序指派学生;支持分片提交复用 batch_id)

上传文件统一保存至 UPLOAD_DIR/{子目录}/,数据库中仅存相对路径。

工程保障(方向三):
- 两阶段校验:先全量校验(数量/扩展名/单文件大小/请求总量),通过后才落盘;
- 落盘中途失败自动回滚已保存文件,杜绝孤儿文件;
- ZIP 解压护栏(条目数 / 解压总大小 / 单条大小)与 Windows GBK 文件名兼容。
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
import zipfile
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue_service
from app.config import settings
from app.services.audit_service import audit
from app.services.name_pre_ocr import name_pre_ocr_service
from app.db.database import get_db
from app.models.db_models import CorrectionTask
from app.models.schemas import (
    BatchCreateResponse,
    DetailLevel,
    GradingStandard,
    IdentityProbeItem,
    IdentityProbeResponse,
    PipelineChoice,
    SingleCreateResponse,
)
from app.services.image_compress import compress_image_bytes
from app.services.image_preprocess import heif_available, preprocess_image_bytes
from app.services.pdf_expand import PdfExpandError, expand_pdf, pdf_available
from app.services.queue_service import QueueService
from app.services.student_assignment import (
    assign_from_filename,
    assign_from_order,
    load_roster,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["批改"])

# 允许的图片扩展名(HEIC/HEIF 需要 pillow-heif,见 _check_extension)
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".heic", ".heif"}
# 需要 HEIC 解码组件的扩展名
HEIC_EXTS = {".heic", ".heif"}
# 单文件大小上限(50 MB,防止异常大图拖垮本地推理)
MAX_FILE_SIZE = 50 * 1024 * 1024
# 合法批次 ID(前端分片提交时复用)
_BATCH_ID_RE = re.compile(r"^[0-9a-zA-Z_-]{6,32}$")
# 合法学生指派方式
_ASSIGN_MODES = {"recognize", "filename", "order"}


# ---------------------------------------------------------
# 工具函数:校验(第一阶段,不落盘)
# ---------------------------------------------------------
def _check_extension(filename: str, allow_zip: bool = False) -> str:
    """校验扩展名,返回小写扩展名(非法时抛 400)

    - allow_zip=True 时额外接受 .zip(仅批量端点);
    - HEIC/HEIF 在缺少 pillow-heif 组件时给出明确转存提示。
    """
    ext = Path(filename).suffix.lower()
    if allow_zip and ext == ".zip":
        return ext
    if ext == ".pdf":
        if not pdf_available():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"当前环境未启用 PDF 解析组件,暂不支持上传 PDF({filename});"
                    "请上传图片,或联系管理员升级组件。"
                ),
            )
        return ext
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件类型:{filename}(仅支持 {'/'.join(sorted(ALLOWED_IMAGE_EXTS))}"
                " / .pdf / .zip(仅批量))"
            ),
        )
    if ext in HEIC_EXTS and not heif_available():
        raise HTTPException(
            status_code=400,
            detail=(
                f"当前环境暂不支持 HEIC 照片({filename})。"
                "请在手机上把照片转存为 JPG 后再上传,或联系管理员升级组件。"
            ),
        )
    return ext


def _validate_upload_request(files: list[UploadFile], allow_zip: bool = False) -> None:
    """第一阶段校验:数量 / 扩展名 / 单文件大小 / 请求总量(不读内容、不落盘)

    - 文件数上限 settings.max_batch_files;
    - 单文件上限 MAX_FILE_SIZE(依据 multipart 解析出的 size;缺失时跳过,
      由落盘阶段的 Post-read 校验兜底);
    - 请求总量上限 settings.max_upload_total_bytes(ZIP 包体积计入其中,
      解压后的总大小护栏在 _extract_zip_images 中执行)。
    """
    if not files:
        raise HTTPException(status_code=400, detail="未上传任何文件")
    if len(files) > settings.max_batch_files:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {settings.max_batch_files} 个文件(当前 {len(files)} 个),请分批上传",
        )

    total_size = 0
    for f in files:
        filename = f.filename or "unnamed"
        _check_extension(filename, allow_zip=allow_zip)
        file_size = f.size or 0
        if not filename.lower().endswith(".zip") and file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大(>{MAX_FILE_SIZE // 1024 // 1024}MB):{filename}",
            )
        total_size += file_size

    if total_size > settings.max_upload_total_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"本次上传总量超过 {settings.max_upload_total_mb}MB 上限"
                f"(约 {total_size // 1024 // 1024}MB),请分批上传"
            ),
        )


def _cleanup_saved(relative_paths: list[str]) -> None:
    """回滚:删除本次请求已保存但未转化为任务的文件(失败路径不留孤儿)"""
    for rel in relative_paths:
        try:
            (settings.upload_path / rel).unlink(missing_ok=True)
        except OSError as e:  # noqa: PERF203
            logger.warning("回滚删除失败(忽略):%s %s", rel, e)
    # 空目录按深度优先删除(先子目录后父目录,仅空目录会删除成功)
    parents = {Path(rel).parent for rel in relative_paths}
    for parent in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        try:
            (settings.upload_path / parent).rmdir()
        except OSError:
            pass


# ---------------------------------------------------------
# 工具函数:保存(第二阶段)
# ---------------------------------------------------------
def _sniff_ext(content: bytes) -> str | None:
    """按内容嗅探图片真实格式,返回规范扩展名(无法识别返回 None)

    场景:HEIC 经预处理统一转码为 JPEG 后,若沿用原 `.heic` 扩展名,
    会导致后续读取时的 MIME 推断与实际内容不一致;此处以实际编码为准。
    """
    try:
        with Image.open(io.BytesIO(content)) as img:
            fmt = (img.format or "").upper()
        return {
            "JPEG": ".jpg",
            "PNG": ".png",
            "WEBP": ".webp",
            "BMP": ".bmp",
            "GIF": ".gif",
        }.get(fmt)
    except Exception:  # noqa: BLE001 —— 嗅探失败时保持原扩展名
        return None


async def _maybe_preprocess(content: bytes) -> bytes:
    """按配置执行图片预处理(EXIF 纠偏 / 去阴影 / 灰度化 / 增强 / 裁边)

    预处理为 CPU 操作,放入线程池避免阻塞事件循环;
    preprocess_image_bytes 内部已兜底:任何失败都返回原图。
    """
    if not settings.image_preprocess:
        return content
    return await asyncio.to_thread(
        preprocess_image_bytes,
        content,
        settings.image_shadow_removal,
        settings.image_grayscale,
    )


async def _maybe_compress(content: bytes) -> bytes:
    """存档图片压缩(独立开关;失败自动回退原图)

    在预处理之后、落盘之前执行:磁盘上只存在压缩后版本(无重复落盘),
    随后既有 _sniff_ext 会按实际编码修正扩展名。
    """
    if not settings.image_compress_enabled:
        return content
    output, changed = await asyncio.to_thread(
        compress_image_bytes,
        content,
        max_side=settings.image_compress_max_side,
        quality=settings.image_compress_quality,
    )
    return output if changed else content


def _group_pages(
    entries: list[tuple[str, str, str]],
) -> tuple[list[list[tuple[str, str]]], list[str], int]:
    """按“相邻续写页归上一任务”对落盘页分组

    规则:page_type == "continuation" 且已有组 → 并入上一组;
    否则新开一组;首张即续写 → 独立成组(计入孤儿提示)。
    返回 (组列表, 孤儿文件名列表, 被合并的页数)。
    """
    groups: list[list[tuple[str, str]]] = []
    orphans: list[str] = []
    merged = 0
    for entry_name, rel, page_type in entries:
        if page_type == "continuation" and groups:
            groups[-1].append((entry_name, rel))
            merged += 1
        else:
            groups.append([(entry_name, rel)])
            if page_type == "continuation":
                orphans.append(entry_name)
    return groups, orphans, merged


async def _maybe_align_sheet(content: bytes) -> tuple[bytes, str]:
    """标准答题卷自动找平 + 页底类型带解码(仅命中四角定位块时生效;独立开关)

    在预处理之后、压缩之前执行:找平失败或未命中一律回退原字节;
    普通照片/手机随拍不受影响(下游扩展名嗅探与契约零变化)。
    返回 (内容, 纸张类型);开关关闭/未找平 → 类型固定为 home。
    """
    if not settings.sheet_align_enabled:
        return content, "home"
    from app.services.sheet_align import PAGE_TYPE_HOME, align_standard_sheet

    output, changed, page_type = await asyncio.to_thread(align_standard_sheet, content)
    if not changed:
        return content, PAGE_TYPE_HOME
    return output, page_type


async def _save_upload(file: UploadFile, subdir: str, index: int) -> tuple[str, str]:
    """保存单个上传文件,返回 (相对 UPLOAD_DIR 的相对路径, 纸张类型)"""
    ext = _check_extension(file.filename or "unnamed")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件过大(>50MB):{file.filename}")

    content = await _maybe_preprocess(content)
    content, page_type = await _maybe_align_sheet(content)
    content = await _maybe_compress(content)
    ext = _sniff_ext(content) or ext  # 转码后(如 HEIC→JPEG)以实际编码修正扩展名

    safe_name = f"{index:03d}_{uuid.uuid4().hex[:8]}{ext}"
    target_dir = settings.upload_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target_dir / safe_name, "wb") as f:
        await f.write(content)
    return f"{subdir}/{safe_name}", page_type


def _zip_entry_name(info: zipfile.ZipInfo) -> str:
    """解析 ZIP 条目文件名(兼容 Windows 压缩包的 GBK 编码)

    ZIP 规范中未置 UTF-8 标志位(0x800)的文件名按 CP437 存储,
    Windows 资源管理器压缩的中文名多为 GBK,需要反转码还原。
    """
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            name = name.encode("cp437").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass  # 不是 GBK 的保持原样
    return name


def _extract_zip_images(content: bytes, zip_name: str) -> list[tuple[str, bytes]]:
    """从 ZIP 字节流中提取图片(忽略目录与非图片文件)

    护栏(防解压炸弹):
    - 图片条目数 ≤ settings.max_batch_files;
    - 解压后总大小 ≤ settings.max_upload_total_bytes;
    - 单条目大小 ≤ MAX_FILE_SIZE。
    """
    results: list[tuple[str, bytes]] = []
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            image_infos = [
                info
                for info in zf.infolist()
                if not info.is_dir()
                and Path(_zip_entry_name(info)).suffix.lower()
                in (ALLOWED_IMAGE_EXTS | {".pdf"})
            ]
            if len(image_infos) > settings.max_batch_files:
                raise HTTPException(
                    status_code=400,
                    detail=f"ZIP 内图片过多(>{settings.max_batch_files} 张):{zip_name}",
                )
            for info in image_infos:
                if info.file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"ZIP 内单张图片过大(>50MB):{_zip_entry_name(info)}",
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > settings.max_upload_total_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"ZIP 解压后总大小超过 {settings.max_upload_total_mb}MB 上限:"
                            f"{zip_name},请拆分后上传"
                        ),
                    )
                name = Path(_zip_entry_name(info)).name
                if name.lower().endswith(".pdf"):
                    try:
                        results.extend(expand_pdf(name, zf.read(info)))
                    except PdfExpandError as e:
                        raise HTTPException(status_code=400, detail=str(e)) from e
                else:
                    results.append((name, zf.read(info)))
                if len(results) > settings.max_batch_files:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"ZIP 展开后条目过多(> {settings.max_batch_files} 页):{zip_name},"
                            "请拆分后上传"
                        ),
                    )
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail=f"ZIP 文件损坏:{zip_name}") from e
    if not results:
        raise HTTPException(status_code=400, detail=f"ZIP 中未找到图片文件:{zip_name}")
    return results


async def _save_bytes(content: bytes, filename: str, subdir: str, index: int) -> tuple[str, str]:
    """保存字节流为文件(供 ZIP/PDF 展开出的图片使用),返回 (相对路径, 纸张类型)"""
    ext = _check_extension(filename)
    content = await _maybe_preprocess(content)
    content, page_type = await _maybe_align_sheet(content)
    content = await _maybe_compress(content)
    ext = _sniff_ext(content) or ext  # 转码后以实际编码修正扩展名
    safe_name = f"{index:03d}_{uuid.uuid4().hex[:8]}{ext}"
    target_dir = settings.upload_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target_dir / safe_name, "wb") as f:
        await f.write(content)
    return f"{subdir}/{safe_name}", page_type


# ---------------------------------------------------------
# 工具函数:任务与配置
# ---------------------------------------------------------
def _create_task(
    db: AsyncSession,
    image_paths: list[str],
    config: dict,
    batch_id: str | None = None,
    student_name: str | None = None,
    student_id: str | None = None,
) -> CorrectionTask:
    """创建任务对象并加入会话(未提交)

    student_name/student_id 可由上传时的指派(手动/文件名/顺序)预填;
    未预填时保持 "未知",由批改管线的识别 + 名单对齐回填。
    """
    task = CorrectionTask(
        batch_id=batch_id,
        student_name=(student_name or "").strip() or "未知",
        student_id=(student_id or "").strip() or None,
        pipeline_choice=config["pipeline_choice"].value,
        grading_standard=config["grading_standard"].value,
        detail_level=config["detail_level"].value,
        require_ocr_review=1 if config["require_ocr_review"] else 0,
        status="PENDING",
        stage="UPLOADED",
        image_paths=image_paths,
        # 班级与作业元数据(可选)
        class_id=config.get("class_id"),
        assignment_name=config.get("assignment_name"),
        topic=config.get("topic"),
    )
    db.add(task)
    return task


def _parse_config(
    pipeline_choice: str,
    grading_standard: str,
    detail_level: str,
    require_ocr_review: bool,
    class_id: int | None = None,
    assignment_name: str | None = None,
    topic: str | None = None,
) -> dict:
    """解析并校验前端提交的配置枚举与作业元数据"""
    try:
        return {
            "pipeline_choice": PipelineChoice(pipeline_choice),
            "grading_standard": GradingStandard(grading_standard),
            "detail_level": DetailLevel(detail_level),
            "require_ocr_review": require_ocr_review,
            "class_id": class_id,
            "assignment_name": (assignment_name or "").strip() or None,
            "topic": (topic or "").strip() or None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"配置参数不合法:{e}") from e


async def _validate_class_id(db: AsyncSession, class_id: int | None) -> None:
    """校验班级存在性(存在性错误直接返回 400,避免产生悬挂外键)"""
    if class_id is None:
        return
    from app.models.db_models import SchoolClass

    cls = await db.get(SchoolClass, class_id)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"班级 {class_id} 不存在")


def _parse_assign_mode(value: str | None) -> str:
    """校验学生指派方式(缺省 recognize)"""
    mode = (value or "recognize").strip().lower()
    if mode not in _ASSIGN_MODES:
        raise HTTPException(status_code=400, detail=f"未知的学生指派方式:{value}")
    return mode


def _parse_batch_id(value: str | None) -> str | None:
    """校验可选批次 ID(前端分片提交复用;非法直接 400)"""
    if value is None or not value.strip():
        return None
    batch_id = value.strip()
    if not _BATCH_ID_RE.match(batch_id):
        raise HTTPException(status_code=400, detail="批次 ID 格式不合法(应为 6-32 位字母数字)")
    return batch_id


# ---------------------------------------------------------
# 个人信息探测(强制性姓名识别;只读,不落盘不建任务)
# ---------------------------------------------------------
@router.post(
    "/corrections/probe-identity",
    response_model=IdentityProbeResponse,
    summary="个人信息探测(选文件即识;本地引擎)",
)
async def probe_identity(
    files: list[UploadFile] = File(description="待识别图片 / ZIP / PDF(选择文件瞬间探测)"),
) -> IdentityProbeResponse:
    """工作台选择文件瞬间的本地个人信息探测(姓名/年龄/学号)

    - 仅当设置「强制性姓名识别」开启时可用(关闭 → 403);
    - 强制本地引擎(RapidOCR,离线 CPU),不调用任何云端/远程端点;
    - 纯内存处理:不落盘、不建任务、不入队、不写库。
    """
    if not settings.force_name_recognition:
        raise HTTPException(status_code=403, detail="未开启「强制性姓名识别」")
    # 延迟导入:与 name_pre_ocr 的既有导入风格保持一致(模块向上已引服务类)
    from app.services.name_pre_ocr import probe_personal_info, rapid_available

    if not rapid_available():
        raise HTTPException(
            status_code=503,
            detail="本地 OCR 组件未安装,无法执行识别;请重跑「首次安装 / 一键自检」",
        )

    _validate_upload_request(files, allow_zip=True)

    expanded: list[tuple[str, bytes]] = []
    for item in files:
        name = item.filename or "unnamed"
        ext = _check_extension(name, allow_zip=True)
        content = await item.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件过大(>50MB):{name}")
        if ext == ".zip":
            expanded.extend(_extract_zip_images(content, name))
        elif ext == ".pdf":
            try:
                expanded.extend(expand_pdf(name, content))
            except PdfExpandError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        else:
            expanded.append((name, content))
    if len(expanded) > settings.max_batch_files:
        raise HTTPException(
            status_code=400,
            detail=f"展开后页数超过 {settings.max_batch_files} 上限(共 {len(expanded)} 页),请分批选择",
        )

    items: list[IdentityProbeItem] = []
    for index, (source_name, content) in enumerate(expanded):
        outcome = await asyncio.to_thread(probe_personal_info, content)
        items.append(
            IdentityProbeItem(
                index=index,
                source_name=source_name,
                name=outcome.get("name"),
                age=outcome.get("age"),
                student_id=outcome.get("student_id"),
                status=str(outcome.get("status") or "error"),
                basis=outcome.get("basis"),
            )
        )
    return IdentityProbeResponse(items=items)


# ---------------------------------------------------------
# 单篇批改
# ---------------------------------------------------------
@router.post("/corrections", response_model=SingleCreateResponse, summary="创建单篇批改任务")
async def create_single_correction(
    files: list[UploadFile] = File(description="作文图片或扫描 PDF(同一学生的多页按顺序上传)"),
    pipeline_choice: str = Form(default="PIPELINE_A_LOCAL", description="选择的管线"),
    grading_standard: str = Form(default="GAOKAO", description="评分标准:GAOKAO | DSD"),
    detail_level: str = Form(default="MEDIUM", description="细致度:LOW | MEDIUM | HIGH"),
    require_ocr_review: bool = Form(default=False, description="是否人工复核 OCR"),
    class_id: int | None = Form(default=None, description="归属班级 ID(可选)"),
    assignment_name: str | None = Form(default=None, description="作业名称(可选)"),
    topic: str | None = Form(default=None, description="作文题目/要求(可选)"),
    student_name: str | None = Form(default=None, description="手动指派:学生姓名(可选)"),
    student_id: str | None = Form(default=None, description="手动指派:学号(可选)"),
    db: AsyncSession = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> SingleCreateResponse:
    """单篇批改:支持多页图片(或扫描 PDF 逐页展开)视作同一份作文,可手动指定学生"""
    _validate_upload_request(files, allow_zip=False)

    config = _parse_config(
        pipeline_choice, grading_standard, detail_level, require_ocr_review,
        class_id=class_id, assignment_name=assignment_name, topic=topic,
    )
    await _validate_class_id(db, class_id)
    subdir = f"task_{uuid.uuid4().hex[:12]}"

    saved: list[str] = []
    try:
        image_paths = []
        page_index = 0
        for f in files:
            filename = f.filename or "unnamed"
            if Path(filename).suffix.lower() == ".pdf":
                content = await f.read()
                try:
                    pages = expand_pdf(filename, content)
                except PdfExpandError as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e
                for inner_name, inner_bytes in pages:
                    page_index += 1
                    rel, _page_type = await _save_bytes(inner_bytes, inner_name, subdir, page_index)
                    saved.append(rel)
                    image_paths.append(rel)
            else:
                page_index += 1
                rel, _page_type = await _save_upload(f, subdir, page_index)
                saved.append(rel)
                image_paths.append(rel)
        if len(image_paths) > settings.max_batch_files:
            raise HTTPException(
                status_code=400,
                detail=f"展开后页数超过 {settings.max_batch_files} 上限(共 {len(image_paths)} 页),请分批上传",
            )
    except HTTPException:
        _cleanup_saved(saved)
        raise

    task = _create_task(
        db, image_paths, config,
        student_name=student_name, student_id=student_id,
    )
    await db.commit()
    await db.refresh(task)

    await queue.enqueue(task.id)
    name_pre_ocr_service.schedule([task.id])  # 上传后即时识名(独立轻量队列)
    logger.info(
        "单篇任务 %s 已创建(%s 张图片,预指派:%s)",
        task.id, len(image_paths), task.student_name,
    )
    return SingleCreateResponse(task_id=task.id)


# ---------------------------------------------------------
# 批量批改
# ---------------------------------------------------------
@router.post("/corrections/batch", response_model=BatchCreateResponse, summary="创建批量批改任务")
async def create_batch_correction(
    files: list[UploadFile] = File(description="图片(每张=一篇)/扫描 PDF(每页=一篇)或 ZIP(内部同样展开)"),
    pipeline_choice: str = Form(default="PIPELINE_A_LOCAL", description="选择的管线"),
    grading_standard: str = Form(default="GAOKAO", description="评分标准:GAOKAO | DSD"),
    detail_level: str = Form(default="MEDIUM", description="细致度:LOW | MEDIUM | HIGH"),
    require_ocr_review: bool = Form(default=False, description="是否人工复核 OCR"),
    class_id: int | None = Form(default=None, description="归属班级 ID(可选)"),
    assignment_name: str | None = Form(default=None, description="作业名称(可选)"),
    topic: str | None = Form(default=None, description="作文题目/要求(可选)"),
    assign_mode: str = Form(default="recognize", description="学生指派:recognize|filename|order"),
    assign_order_start: int | None = Form(default=None, description="按名单顺序指派时的起始序号(1-based)"),
    batch_id: str | None = Form(default=None, description="批次 ID(分片提交时复用;缺省自动生成)"),
    db: AsyncSession = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> BatchCreateResponse:
    """批量批改:每张图片创建独立任务(扫描 PDF 每页=一篇);ZIP 自动解压展开;支持学生指派与分片提交"""
    # ---------- 第一阶段:请求级校验(不落盘) ----------
    _validate_upload_request(files, allow_zip=True)

    config = _parse_config(
        pipeline_choice, grading_standard, detail_level, require_ocr_review,
        class_id=class_id, assignment_name=assignment_name, topic=topic,
    )
    await _validate_class_id(db, class_id)
    mode = _parse_assign_mode(assign_mode)
    batch_id = _parse_batch_id(batch_id) or uuid.uuid4().hex[:12]
    subdir = f"batch_{batch_id}"

    # ---------- 第二阶段:落盘(失败自动回滚) ----------
    entries: list[tuple[str, str, str]] = []
    saved: list[str] = []
    index = 0
    try:
        for f in files:
            filename = f.filename or "unnamed"
            if Path(filename).suffix.lower() == ".zip":
                content = await f.read()
                for inner_name, inner_bytes in _extract_zip_images(content, filename):
                    index += 1
                    rel, page_type = await _save_bytes(inner_bytes, inner_name, subdir, index)
                    saved.append(rel)
                    entries.append((inner_name, rel, page_type))
            elif Path(filename).suffix.lower() == ".pdf":
                content = await f.read()
                try:
                    pages = expand_pdf(filename, content)
                except PdfExpandError as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e
                for inner_name, inner_bytes in pages:
                    index += 1
                    rel, page_type = await _save_bytes(inner_bytes, inner_name, subdir, index)
                    saved.append(rel)
                    entries.append((inner_name, rel, page_type))
            else:
                index += 1
                rel, page_type = await _save_upload(f, subdir, index)
                saved.append(rel)
                entries.append((filename, rel, page_type))
        if not entries:
            raise HTTPException(status_code=400, detail="没有可处理的图片文件")
    except HTTPException:
        _cleanup_saved(saved)
        raise

    # ---------- 续写页分组(相邻归页;未找平/未命中一律独立成组,绝不误合并) ----------
    groups, orphans, merged_pages = _group_pages(entries)
    for orphan_name in orphans:
        await audit(
            "upload.continuation_orphan",
            ok=True,
            detail=f"首张即为续写纸,无法归页,按独立任务处理:{orphan_name}",
            source="api",
        )
    for group in groups:
        for entry_name, _rel in group[1:]:
            await audit(
                "upload.page_merge",
                ok=True,
                detail=f"续写页 {entry_name} 并入上一任务(组首 {group[0][0]})",
                source="api",
            )
    if len(groups) > settings.max_batch_files:
        _cleanup_saved(saved)
        raise HTTPException(
            status_code=400,
            detail=f"展开后任务数超过 {settings.max_batch_files} 上限(共 {len(groups)} 篇),请分批上传",
        )
    if merged_pages:
        logger.info("批次 %s:续写页自动归并 %s 页 → 共 %s 个任务", batch_id, merged_pages, len(groups))

    # ---------- 第三阶段:学生指派(方向四) ----------
    # prefill[i] = (姓名 or None, 学号 or None)
    prefill: list[tuple[str | None, str | None]] = [(None, None)] * len(groups)
    if mode == "order":
        if class_id is None:
            raise HTTPException(status_code=400, detail="按名单顺序指派需要先选择归属班级")
        roster = await load_roster(db, class_id)
        if not roster:
            raise HTTPException(
                status_code=400,
                detail="该班级还没有花名册,请先导入名单(工作台「管理花名册」),或改用其他指派方式",
            )
        start = assign_order_start or 1
        prefill = [
            _assignment_tuple(assign_from_order(roster, start, i))
            for i in range(len(groups))
        ]
    elif mode == "filename":
        roster = await load_roster(db, class_id)
        prefill = [
            _assignment_tuple(assign_from_filename(group[0][0], roster)) for group in groups
        ]

    matched_count = sum(1 for name, sid in prefill if name or sid)
    if mode != "recognize":
        logger.info(
            "批次 %s 指派方式 %s:命中花名册 %s/%s 个任务",
            batch_id, mode, matched_count, len(groups),
        )

    # ---------- 第四阶段:创建任务并入队 ----------
    tasks: list[CorrectionTask] = []
    for group, (assigned_name, assigned_id) in zip(groups, prefill):
        tasks.append(
            _create_task(
                db, [rel for _, rel in group], config, batch_id=batch_id,
                student_name=assigned_name, student_id=assigned_id,
            )
        )
    await db.commit()
    for t in tasks:
        await db.refresh(t)

    for t in tasks:
        await queue.enqueue(t.id)
    name_pre_ocr_service.schedule([t.id for t in tasks])  # 上传后即时识名(独立轻量队列)

    logger.info("批次 %s 创建完成:共 %s 个任务", batch_id, len(tasks))
    return BatchCreateResponse(batch_id=batch_id, task_ids=[t.id for t in tasks])


def _assignment_tuple(assignment) -> tuple[str | None, str | None]:
    """Assignment -> (姓名, 学号) 元组(未命中返回 (None, None) 交回识别流程)"""
    if assignment.matched:
        return assignment.student_name, assignment.student_id
    return None, None
