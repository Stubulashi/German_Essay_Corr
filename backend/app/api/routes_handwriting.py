"""手写样本与本地手写模型路由

- GET    /api/classes/{id}/handwriting/sentence       生成抄写素材(题句+正文句)
- GET    /api/classes/{id}/handwriting/samples        样本列表
- POST   /api/classes/{id}/handwriting/samples        批量上传样本(异步处理训练)
- GET    /api/classes/{id}/handwriting/train/status   处理进度(含真实 ETA)
- GET    /api/classes/{id}/handwriting/model          模型概览(含本机画像/档位)
- GET    /api/handwriting/samples/{id}/crop           样本姓名区小图(列表展示)
- PUT    /api/handwriting/samples/{id}/bind           指定学生(待指定样本)
- DELETE /api/handwriting/samples/{id}                删除样本(重采)
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.models.db_models import HandwritingModel, HandwritingSample, SchoolClass
from app.models.schemas import (
    HandwritingBindRequest,
    HandwritingModelOut,
    HandwritingSampleItemOut,
    HandwritingSampleUploadOut,
    HandwritingSentenceOut,
    HandwritingTrainStatusOut,
)
from app.services.audit_service import audit
from app.services.handwriting_service import (
    build_model_overview,
    generate_practice_material,
    handwriting_service,
    invalidate_model_cache,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["handwriting"])

_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_MAX_SAMPLE_BYTES = 20 * 1024 * 1024


async def _ensure_class(db: AsyncSession, class_id: int) -> SchoolClass:
    school_class = await db.get(SchoolClass, class_id)
    if school_class is None:
        raise HTTPException(status_code=404, detail=f"班级 {class_id} 不存在")
    return school_class


@router.get(
    "/classes/{class_id}/handwriting/sentence",
    response_model=HandwritingSentenceOut,
    summary="生成抄写素材",
)
async def handwriting_sentence(
    class_id: int, db: AsyncSession = Depends(get_db)
) -> HandwritingSentenceOut:
    """本地句库随机生成题目句与正文句(离线,零模型依赖)"""
    await _ensure_class(db, class_id)
    return HandwritingSentenceOut(**generate_practice_material())


@router.get(
    "/classes/{class_id}/handwriting/samples",
    response_model=list[HandwritingSampleItemOut],
    summary="样本列表",
)
async def list_handwriting_samples(
    class_id: int, db: AsyncSession = Depends(get_db)
) -> list[HandwritingSampleItemOut]:
    await _ensure_class(db, class_id)
    rows = (
        await db.execute(
            select(HandwritingSample)
            .where(HandwritingSample.class_id == class_id)
            .order_by(HandwritingSample.id)
        )
    ).scalars().all()
    return [
        HandwritingSampleItemOut(
            id=row.id,
            status=row.status,
            student_name=row.student_name,
            student_id=row.student_id,
            has_crop=bool(row.name_crop_path),
            has_features=bool(row.features),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/classes/{class_id}/handwriting/samples",
    response_model=HandwritingSampleUploadOut,
    summary="批量上传手写样本",
)
async def upload_handwriting_samples(
    class_id: int,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
) -> HandwritingSampleUploadOut:
    """保存样本原图并入队处理(找平/识别/对账/特征提取;进度可查)"""
    await _ensure_class(db, class_id)
    if not files:
        raise HTTPException(status_code=400, detail="请选择样本图片")
    subdir = f"handwriting/class_{class_id}"
    target_dir = settings.upload_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_ids: list[int] = []
    for file in files:
        filename = file.filename or "sample.jpg"
        suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"不支持的样本格式:{filename}")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"空文件:{filename}")
        if len(content) > _MAX_SAMPLE_BYTES:
            raise HTTPException(status_code=400, detail=f"样本过大(>20MB):{filename}")
        safe_name = f"raw_{uuid.uuid4().hex[:10]}{suffix}"
        (target_dir / safe_name).write_bytes(content)
        sample = HandwritingSample(
            class_id=class_id,
            image_path=f"{subdir}/{safe_name}",
            status="pending_bind",
        )
        db.add(sample)
        await db.flush()
        saved_ids.append(sample.id)
    await db.commit()
    handwriting_service.schedule(saved_ids)
    invalidate_model_cache(class_id)
    await audit(
        "handwriting.samples_uploaded",
        ok=True,
        detail=f"班级 {class_id};受理 {len(saved_ids)} 份样本",
        source="api",
    )
    return HandwritingSampleUploadOut(accepted=len(saved_ids), sample_ids=saved_ids)


@router.get(
    "/classes/{class_id}/handwriting/train/status",
    response_model=HandwritingTrainStatusOut,
    summary="训练/处理进度",
)
async def handwriting_train_status(
    class_id: int, db: AsyncSession = Depends(get_db)
) -> HandwritingTrainStatusOut:
    await _ensure_class(db, class_id)
    snapshot = handwriting_service.progress()
    model = await db.get(HandwritingModel, class_id)
    pending = len(
        (
            await db.execute(
                select(HandwritingSample.id).where(
                    HandwritingSample.class_id == class_id,
                    HandwritingSample.status == "pending_bind",
                )
            )
        ).all()
    )
    return HandwritingTrainStatusOut(
        running=bool(snapshot["running"]),
        done=snapshot["done"],
        total=snapshot["total"],
        percent=snapshot["percent"],
        eta_seconds=snapshot["eta_seconds"],
        stage=snapshot["stage"],
        errors=snapshot["errors"],
        recent_avg_seconds=snapshot["recent_avg_seconds"],
        model_ready=bool(model and model.samples_count > 0),
        samples_count=model.samples_count if model else 0,
        pending_bind=pending,
    )


@router.get(
    "/classes/{class_id}/handwriting/model",
    response_model=HandwritingModelOut,
    summary="模型概览",
)
async def handwriting_model_overview(
    class_id: int, db: AsyncSession = Depends(get_db)
) -> HandwritingModelOut:
    await _ensure_class(db, class_id)
    data = await build_model_overview(class_id)
    return HandwritingModelOut(**data)


@router.get("/handwriting/samples/{sample_id}/crop", summary="样本姓名区小图")
async def handwriting_sample_crop(
    sample_id: int, db: AsyncSession = Depends(get_db)
):
    sample = await db.get(HandwritingSample, sample_id)
    if sample is None or not sample.name_crop_path:
        raise HTTPException(status_code=404, detail="样本姓名区小图不存在")
    file_path = settings.upload_path / sample.name_crop_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="样本姓名区小图已丢失")
    return FileResponse(file_path, media_type="image/jpeg")


@router.put("/handwriting/samples/{sample_id}/bind", summary="指定学生")
async def bind_handwriting_sample(
    sample_id: int,
    payload: HandwritingBindRequest,
    db: AsyncSession = Depends(get_db),
):
    """人工指定待绑定样本的学生(对账未命中时的兜底)"""
    sample = await db.get(HandwritingSample, sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail=f"样本 {sample_id} 不存在")
    sample.student_name = payload.student_name.strip() or None
    sample.student_id = (payload.student_id or "").strip() or None
    sample.status = "ok" if sample.student_name else "pending_bind"
    await db.commit()
    await handwriting_service._refresh_model(sample.class_id)
    invalidate_model_cache(sample.class_id)
    await audit(
        "handwriting.sample_bound",
        ok=True,
        detail=f"sample {sample_id};班级 {sample.class_id};状态 {sample.status}",
        source="api",
    )
    return {"sample_id": sample_id, "status": sample.status}


@router.delete("/handwriting/samples/{sample_id}", summary="删除样本")
async def delete_handwriting_sample(
    sample_id: int, db: AsyncSession = Depends(get_db)
):
    sample = await db.get(HandwritingSample, sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail=f"样本 {sample_id} 不存在")
    class_id = sample.class_id
    for rel in (sample.image_path, sample.name_crop_path):
        if rel:
            (settings.upload_path / rel).unlink(missing_ok=True)
    await db.delete(sample)
    await db.commit()
    await handwriting_service._refresh_model(class_id)
    invalidate_model_cache(class_id)
    await audit(
        "handwriting.sample_deleted",
        ok=True,
        detail=f"sample {sample_id};班级 {class_id}",
        source="api",
    )
    return {"deleted": sample_id}
