"""示范学习路由(示例范文风格迁移)

- 从已完成批改任务归纳风格画像(归纳完成默认自动启用,单一生效);
- 画像管理:列表 / 查看 / 编辑(narrative) / 启用 / 停用 / 删除;
- /api/style/status 供工作台 chip 与后续设置中心展示当前生效状态。

错误映射:LookupError -> 404;ValueError -> 400;RuntimeError(模型未配置等) -> 502。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.schemas import (
    StyleLearnRequest,
    StyleProfileOut,
    StyleProfileUpdate,
    StyleStatus,
)
from app.services import style_learning_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["示范学习"])


def _not_found(e: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(e))


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------
# 画像管理
# ---------------------------------------------------------
@router.get("/style/profiles", response_model=list[StyleProfileOut], summary="风格画像列表")
async def list_profiles(db: AsyncSession = Depends(get_db)) -> list[StyleProfileOut]:
    """列出全部风格画像(生效中优先,其余按创建时间倒序)"""
    profiles = await style_learning_service.list_profiles(db)
    return [StyleProfileOut.model_validate(p) for p in profiles]


@router.post("/style/profiles/learn", response_model=StyleProfileOut, summary="从示例范文归纳风格")
async def learn_from_task(
    payload: StyleLearnRequest, db: AsyncSession = Depends(get_db)
) -> StyleProfileOut:
    """对一份【已完成】的批改任务做风格归纳,生成画像并自动启用

    - 归纳产物固化(结构化四维度 + 叙事文本),与源任务解耦;
    - 启用为单一生效:自动停用其他画像,后续批改任务即时生效;
    - mock 模式下返回确定性样例,便于演示与验收。
    """
    try:
        profile = await style_learning_service.learn_from_task(
            db, task_id=payload.task_id, name=payload.name
        )
    except LookupError as e:
        raise _not_found(e) from e
    except ValueError as e:
        raise _bad_request(e) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"风格归纳失败:{e}") from e
    return StyleProfileOut.model_validate(profile)


@router.get("/style/profiles/{profile_id}", response_model=StyleProfileOut, summary="查看风格画像")
async def get_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> StyleProfileOut:
    """查看单个画像详情(含四维度结构与注入文本)"""
    try:
        profile = await style_learning_service.get_profile(db, profile_id)
    except LookupError as e:
        raise _not_found(e) from e
    return StyleProfileOut.model_validate(profile)


@router.put("/style/profiles/{profile_id}", response_model=StyleProfileOut, summary="编辑风格画像")
async def update_profile(
    profile_id: int, payload: StyleProfileUpdate, db: AsyncSession = Depends(get_db)
) -> StyleProfileOut:
    """编辑画像名称 / 注入文本(narrative);生效中的画像编辑后即刻同步缓存"""
    try:
        profile = await style_learning_service.update_profile(
            db, profile_id, name=payload.name, narrative=payload.narrative
        )
    except LookupError as e:
        raise _not_found(e) from e
    return StyleProfileOut.model_validate(profile)


@router.post("/style/profiles/{profile_id}/activate", response_model=StyleProfileOut, summary="启用画像")
async def activate_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> StyleProfileOut:
    """启用画像(单一生效:自动停用其他画像;后续批改任务即时生效)"""
    try:
        profile = await style_learning_service.activate(db, profile_id)
    except LookupError as e:
        raise _not_found(e) from e
    return StyleProfileOut.model_validate(profile)


@router.post("/style/profiles/{profile_id}/deactivate", response_model=StyleProfileOut, summary="停用画像")
async def deactivate_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> StyleProfileOut:
    """停用画像(停用生效中的画像后,后续批改立即恢复默认风格)"""
    try:
        profile = await style_learning_service.deactivate(db, profile_id)
    except LookupError as e:
        raise _not_found(e) from e
    return StyleProfileOut.model_validate(profile)


@router.delete("/style/profiles/{profile_id}", summary="删除画像")
async def delete_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """删除画像(若删除的是生效中画像,后续批改恢复默认风格)"""
    try:
        await style_learning_service.delete_profile(db, profile_id)
    except LookupError as e:
        raise _not_found(e) from e
    return {"deleted": profile_id}


# ---------------------------------------------------------
# 状态
# ---------------------------------------------------------
@router.get("/style/status", response_model=StyleStatus, summary="示范学习状态")
async def style_status(db: AsyncSession = Depends(get_db)) -> StyleStatus:
    """当前生效画像与画像总数(供工作台 chip 与设置中心展示)"""
    active, count = await style_learning_service.get_status(db)
    return StyleStatus(
        active=StyleProfileOut.model_validate(active) if active else None,
        profile_count=count,
    )
