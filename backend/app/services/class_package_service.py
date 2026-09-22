"""班级数据包服务(#方向三:班级作为综合数据库单元)

能力:
- 导出:把一个班级的全部数据(任务 + 批改结果 + 错因记录 + 学生 + 原图)
  打包为可移植 ZIP,包含 manifest.json 数据契约;
- 导入:在另一台电脑上解包重建(班级 / 任务 / 图片 / 错因 / 学生),
  实现"换电脑 / 换人只带一个数据包,导入即用"。

manifest.json(format_version=1)结构:
{
  "format_version": 1,                  # 数据包格式版本(导入端兼容判断)
  "exported_at": "ISO 时间",
  "app_schema_revision": "alembic 版本号",
  "class": {"name": str, "note": str|null},
  "students": [{"name", "student_id"}],
  "tasks": [{"source_id", ...全部业务字段, "images": ["相对 uploads 的 POSIX 路径"]}],
  "error_records": [{"source_task_id", "student_name", ...}]
}
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import CorrectionTask, ErrorRecord, SchoolClass
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)

#: 当前支持的数据包格式版本(导入端拒绝更高版本)
SUPPORTED_FORMAT_VERSION = 1

#: 数据包大小上限(200 MB,防止异常文件拖垮服务)
MAX_PACKAGE_SIZE = 200 * 1024 * 1024


class ClassPackageError(Exception):
    """班级数据包业务异常(路由层转换为 HTTP 400)"""


@dataclass
class ImportStats:
    """导入结果统计"""

    class_id: int
    class_name: str
    task_count: int
    error_count: int
    image_count: int
    renamed: bool


# =============================================================
# 工具
# =============================================================


def _iso(value: datetime | None) -> str | None:
    """datetime -> ISO 字符串(导出用)"""
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    """ISO 字符串 -> datetime(容错:解析失败返回 None)"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        # 统一为 UTC 感知时间(旧数据可能无时区)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _safe_relative(rel: str) -> str | None:
    """校验并规范化相对路径(拒绝绝对路径与 .. 穿越;返回 POSIX 风格)

    安全:防止恶意 ZIP 携带路径穿越条目写到 uploads 目录之外。
    """
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel:
        return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _get_schema_revision() -> str | None:
    """读取当前程序的 Alembic 头版本(失败时返回 None,不阻断导出)"""
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        from app.config import BACKEND_DIR

        cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 schema 版本失败(忽略):%s", e)
        return None


# =============================================================
# 导出
# =============================================================


async def export_class_package(
    db: AsyncSession,
    class_id: int,
    upload_root: Path | None = None,
) -> io.BytesIO:
    """导出一个班级的数据包,返回 ZIP 字节流(内存中构建)

    - 仅导出已有批改结果(result 非空)的任务,保证导入端可直接重建为已完成;
    - 图片以相对 uploads 的路径存入包内 images/ 目录,与任务字段一一对应。
    """
    upload_root = upload_root or settings.upload_path
    cls = await db.get(SchoolClass, class_id)
    if cls is None:
        raise ClassPackageError(f"班级 {class_id} 不存在")

    task_stmt = (
        select(CorrectionTask)
        .where(CorrectionTask.class_id == class_id, CorrectionTask.result.is_not(None))
        .order_by(CorrectionTask.id)
    )
    tasks = (await db.execute(task_stmt)).scalars().all()
    task_ids = [t.id for t in tasks]
    records: list[ErrorRecord] = []
    if task_ids:
        rec_stmt = select(ErrorRecord).where(ErrorRecord.task_id.in_(task_ids))
        records = list((await db.execute(rec_stmt)).scalars().all())

    # ---------- 学生列表(从任务推导去重:学号优先,姓名兜底) ----------
    students: list[dict] = []
    seen_keys: set[str] = set()
    for t in tasks:
        key = (t.student_id or "").strip() or (t.student_name or "").strip()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        students.append({"name": t.student_name or "未知", "student_id": t.student_id})

    # ---------- manifest ----------
    task_payloads: list[dict] = []
    for t in tasks:
        image_rels = [rel for rel in (_safe_relative(r) for r in (t.image_paths or [])) if rel]
        task_payloads.append(
            {
                "source_id": t.id,
                "batch_id": t.batch_id,
                "student_name": t.student_name,
                "student_id": t.student_id,
                "pipeline_choice": t.pipeline_choice,
                "pipeline_used": t.pipeline_used,
                "grading_standard": t.grading_standard,
                "detail_level": t.detail_level,
                "require_ocr_review": t.require_ocr_review,
                "fallback_triggered": t.fallback_triggered,
                "assignment_name": t.assignment_name,
                "topic": t.topic,
                "result": t.result,
                "ocr_result": t.ocr_result,
                "edited_report": t.edited_report,
                "created_at": _iso(t.created_at),
                "updated_at": _iso(t.updated_at),
                "images": image_rels,
            }
        )

    manifest = {
        "format_version": SUPPORTED_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app_schema_revision": _get_schema_revision(),
        "class": {"name": cls.name, "note": cls.note},
        "students": students,
        "tasks": task_payloads,
        "error_records": [
            {
                "source_task_id": r.task_id,
                "student_name": r.student_name,
                "student_id": r.student_id,
                "error_type": r.error_type,
                "canonical_type": r.canonical_type,
                "original_text": r.original_text,
                "corrected_text": r.corrected_text,
                "created_at": _iso(r.created_at),
            }
            for r in records
        ],
    }

    # ---------- 打包(manifest + 图片) ----------
    buffer = io.BytesIO()
    missing_images = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in tasks:
            for rel in (_safe_relative(r) for r in (t.image_paths or [])):
                if not rel:
                    continue
                file_path = upload_root / rel
                if file_path.exists():
                    zf.write(file_path, f"images/{rel}")
                else:
                    missing_images += 1
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    buffer.seek(0)

    if missing_images:
        logger.warning("导出班级 %s:有 %s 张图片文件缺失,已跳过", cls.name, missing_images)
    logger.info(
        "导出班级数据包:%s(任务 %s,错因 %s,学生 %s)",
        cls.name,
        len(tasks),
        len(records),
        len(students),
    )
    return buffer


# =============================================================
# 导入
# =============================================================


async def _unique_class_name(db: AsyncSession, base_name: str) -> tuple[str, bool]:
    """生成不冲突的班级名(重名时追加"(导入)"后缀)"""
    base_name = (base_name or "导入班级").strip() or "导入班级"
    cleaned = base_name if len(base_name) <= 100 else base_name[:100]
    candidate = cleaned
    renamed = False
    suffix = 1
    while True:
        exists = await db.execute(select(SchoolClass).where(SchoolClass.name == candidate))
        if exists.scalars().first() is None:
            return candidate, renamed
        renamed = True
        suffix += 1
        candidate = f"{cleaned}(导入{'' if suffix == 2 else suffix - 1})"
        if len(candidate) > 128:  # 防超长,截断重试
            candidate = f"{cleaned[:100]}(导入{suffix})"


async def import_class_package(
    db: AsyncSession,
    zip_bytes: bytes,
    upload_root: Path | None = None,
    new_name: str | None = None,
) -> ImportStats:
    """导入班级数据包,重建班级与全部数据

    Raises:
        ClassPackageError: 包结构不正确 / 版本过高 / 校验失败
    """
    if len(zip_bytes) > MAX_PACKAGE_SIZE:
        raise ClassPackageError("数据包超过 200MB 上限,无法导入")
    upload_root = upload_root or settings.upload_path

    # ---------- 1. 读取与校验 manifest ----------
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except KeyError as e:
                raise ClassPackageError("数据包缺少 manifest.json,不是有效的班级数据包") from e
            except json.JSONDecodeError as e:
                raise ClassPackageError("数据包的 manifest.json 格式损坏,无法解析") from e

            version = int(manifest.get("format_version") or 0)
            if version < 1:
                raise ClassPackageError("数据包缺少格式版本号,无法识别")
            if version > SUPPORTED_FORMAT_VERSION:
                raise ClassPackageError(
                    f"数据包由更新版本的程序导出(格式 {version}),"
                    "请先升级“批改器”到最新版本后再导入。"
                )

            class_info = manifest.get("class") or {}
            base_name = new_name or class_info.get("name") or "导入班级"

            # ---------- 2. 建班级(重名自动处理) ----------
            final_name, renamed = await _unique_class_name(db, base_name)
            cls = SchoolClass(name=final_name, note=class_info.get("note"))
            db.add(cls)
            await db.flush()

            # ---------- 3. 逐任务重建(含图片解压) ----------
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            import_dir = f"imported_{stamp}"
            member_names = set(zf.namelist())
            id_map: dict[int, int] = {}
            image_count = 0

            for data in manifest.get("tasks") or []:
                # 解压并重写图片路径(统一收拢到 imported_ 子目录,避免与原文件冲突)
                new_paths: list[str] = []
                for rel in data.get("images") or []:
                    safe_rel = _safe_relative(str(rel))
                    if safe_rel is None:
                        continue
                    arcname = f"images/{safe_rel}"
                    if arcname not in member_names:
                        continue
                    target = upload_root / import_dir / safe_rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(arcname))
                    new_paths.append(f"{import_dir}/{safe_rel}")
                    image_count += 1

                task = CorrectionTask(
                    batch_id=data.get("batch_id"),
                    class_id=cls.id,
                    assignment_name=data.get("assignment_name"),
                    topic=data.get("topic"),
                    student_name=data.get("student_name") or "未知",
                    student_id=data.get("student_id"),
                    pipeline_choice=data.get("pipeline_choice") or "PIPELINE_A_LOCAL",
                    pipeline_used=data.get("pipeline_used"),
                    grading_standard=data.get("grading_standard") or "GAOKAO",
                    detail_level=data.get("detail_level") or "MEDIUM",
                    require_ocr_review=int(data.get("require_ocr_review") or 0),
                    status="COMPLETED",
                    stage="DONE",
                    fallback_triggered=int(data.get("fallback_triggered") or 0),
                    progress=1.0,
                    image_paths=new_paths,
                    ocr_result=data.get("ocr_result"),
                    result=data.get("result"),
                    edited_report=data.get("edited_report"),
                    created_at=_parse_dt(data.get("created_at")) or datetime.now(timezone.utc),
                    updated_at=_parse_dt(data.get("updated_at")) or datetime.now(timezone.utc),
                )
                db.add(task)
                await db.flush()
                source_id = data.get("source_id")
                if source_id is not None:
                    id_map[int(source_id)] = task.id

            # ---------- 4. 错因记录(关联到新任务 ID) ----------
            error_count = 0
            for rec in manifest.get("error_records") or []:
                source_task_id = rec.get("source_task_id")
                new_task_id = id_map.get(int(source_task_id)) if source_task_id is not None else None
                if new_task_id is None:
                    continue
                db.add(
                    ErrorRecord(
                        task_id=new_task_id,
                        student_name=rec.get("student_name") or "未知",
                        student_id=rec.get("student_id"),
                        error_type=rec.get("error_type") or "Unknown",
                        canonical_type=rec.get("canonical_type") or "OTHER",
                        original_text=rec.get("original_text") or "",
                        corrected_text=rec.get("corrected_text") or "",
                        created_at=_parse_dt(rec.get("created_at")) or datetime.now(timezone.utc),
                    )
                )
                error_count += 1

            # ---------- 5. 学生档案合并(与批改流程共用 upsert 规则) ----------
            for stu in manifest.get("students") or []:
                await upsert_student(db, stu.get("name") or "未知", stu.get("student_id"))

            await db.commit()
    except zipfile.BadZipFile as e:
        raise ClassPackageError("上传的文件不是有效的 ZIP 数据包") from e

    logger.info(
        "导入班级数据包完成:%s(任务 %s,错因 %s,图片 %s%s)",
        final_name,
        len(id_map),
        error_count,
        image_count,
        ",原名冲突已重命名" if renamed else "",
    )
    return ImportStats(
        class_id=cls.id,
        class_name=cls.name,
        task_count=len(id_map),
        error_count=error_count,
        image_count=image_count,
        renamed=renamed,
    )
