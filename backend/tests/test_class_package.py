"""班级数据包导出/导入测试(方向三)

验证:
- 导出 ZIP 结构(manifest.json + images/);
- 同库导入(重名自动改名)后任务/错因/图片完整重建;
- format_version 过高的包被拒绝;
- 非法 ZIP 被拒绝。
"""

import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import Base, CorrectionTask, ErrorRecord, SchoolClass
from app.services.class_package_service import (
    ClassPackageError,
    export_class_package,
    import_class_package,
)


@pytest.fixture
async def db_session(tmp_path):
    """临时文件数据库会话"""
    db_file = tmp_path / "package.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def upload_root(tmp_path) -> Path:
    """临时上传目录 + 两个假图片文件"""
    root = tmp_path / "uploads"
    (root / "task_a").mkdir(parents=True)
    (root / "task_a" / "001.png").write_bytes(b"fake-png-1")
    (root / "task_a" / "002.png").write_bytes(b"fake-png-2")
    return root


async def _seed_class(session, upload_root: Path) -> SchoolClass:
    """构造一个班级 + 一个已完成任务(含结果与错因)+ 图片"""
    cls = SchoolClass(name="高二(3)班德语", note="2026 秋")
    session.add(cls)
    await session.flush()

    task = CorrectionTask(
        class_id=cls.id,
        student_name="李明",
        student_id="S001",
        assignment_name="第一次月考作文",
        status="COMPLETED",
        stage="DONE",
        progress=1.0,
        image_paths=["task_a/001.png", "task_a/002.png"],
        result={
            "student_name": "李明",
            "student_id": "S001",
            "transcribed_text": "Meine Sommerferien ...",
            "overall_score": "18 / 25",
            "overall_comment": "总体良好",
            "errors": [],
            "highlights": [],
            "markdown_report": "# 德语作文批改报告 - 李明",
        },
    )
    session.add(task)
    await session.flush()
    session.add(
        ErrorRecord(
            task_id=task.id,
            student_name="李明",
            student_id="S001",
            error_type="动词位序",
            canonical_type="VERB_POSITION",
            original_text="dass er kommt",
            corrected_text="dass er kommt(gemacht)",
        )
    )
    await session.commit()
    return cls


class TestExportImport:
    """导出 / 导入往返测试"""

    async def test_export_package_structure(self, db_session, upload_root):
        """导出的 ZIP 应包含 manifest.json 与图片"""
        cls = await _seed_class(db_session, upload_root)
        buffer = await export_class_package(db_session, cls.id, upload_root=upload_root)

        with zipfile.ZipFile(buffer) as zf:
            names = zf.namelist()
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

        assert "manifest.json" in names
        assert "images/task_a/001.png" in names
        assert "images/task_a/002.png" in names
        assert manifest["format_version"] == 1
        assert manifest["class"]["name"] == "高二(3)班德语"
        assert len(manifest["tasks"]) == 1
        assert manifest["tasks"][0]["images"] == ["task_a/001.png", "task_a/002.png"]
        assert len(manifest["error_records"]) == 1
        assert manifest["students"][0]["student_id"] == "S001"

    async def test_import_rebuilds_everything(self, db_session, upload_root):
        """导入后:班级重命名、任务/结果/错因/图片完整重建"""
        cls = await _seed_class(db_session, upload_root)
        buffer = await export_class_package(db_session, cls.id, upload_root=upload_root)
        zip_bytes = buffer.getvalue()

        stats = await import_class_package(
            db_session, zip_bytes, upload_root=upload_root
        )

        # 重名班级自动改名
        assert stats.renamed is True
        assert stats.class_name.startswith("高二(3)班德语")
        assert stats.class_name != "高二(3)班德语"
        assert stats.task_count == 1
        assert stats.error_count == 1
        assert stats.image_count == 2

        # 新任务:挂到新班级、状态已完成、结果保留、图片路径已重写且文件存在
        new_task = (
            await db_session.execute(
                select(CorrectionTask).where(CorrectionTask.class_id == stats.class_id)
            )
        ).scalars().one()
        assert new_task.status == "COMPLETED"
        assert new_task.result is not None
        assert new_task.result["overall_score"] == "18 / 25"
        assert new_task.assignment_name == "第一次月考作文"
        for rel in new_task.image_paths:
            assert rel.startswith("imported_")
            assert (upload_root / rel).exists()

        # 错因记录已关联到新任务
        new_errors = (
            await db_session.execute(
                select(ErrorRecord).where(ErrorRecord.task_id == new_task.id)
            )
        ).scalars().all()
        assert len(new_errors) == 1
        assert new_errors[0].canonical_type == "VERB_POSITION"

    async def test_import_rejects_newer_format(self, db_session, upload_root):
        """format_version 高于支持上限的包被拒绝"""
        manifest = {
            "format_version": 99,
            "class": {"name": "未来班级"},
            "tasks": [],
            "error_records": [],
            "students": [],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

        with pytest.raises(ClassPackageError, match="更新版本"):
            await import_class_package(db_session, buf.getvalue(), upload_root=upload_root)

    async def test_import_rejects_invalid_zip(self, db_session, upload_root):
        """非 ZIP 字节流被拒绝"""
        with pytest.raises(ClassPackageError):
            await import_class_package(db_session, b"not a zip at all", upload_root=upload_root)

    async def test_export_missing_class(self, db_session, upload_root):
        """导出不存在的班级报业务异常"""
        with pytest.raises(ClassPackageError, match="不存在"):
            await export_class_package(db_session, 99999, upload_root=upload_root)
