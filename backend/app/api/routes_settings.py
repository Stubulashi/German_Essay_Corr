"""设置中心路由(统一配置中心)

- GET  /api/settings            视图(分组 + 元数据 + 脱敏值 + 模式/快照状态)
- PUT  /api/settings            更新(白名单字段;developer 字段需开发模式)
- POST /api/settings/reset      单项恢复默认(仅探索性设置)
- POST /api/settings/rollback   一键回退探索性改动(还原到修改前快照)
- POST /api/settings/llm-params/clear  清空大模型自动适配记录(端点不支持参数的剔除记忆)
- POST /api/settings/test       管线连通性测试(支持未保存的候选值,先测后存)
- GET  /api/settings/prompts    提示词微调附录 + 最终提示词预览
- PUT  /api/settings/prompts    更新附录

全部接口挂 require_settings_admin(默认仅本机回环;配置访问令牌后强制携带)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_settings_admin
from app.db.database import get_db
from app.models.schemas import (
    ConnectivityRequest,
    ConnectivityResult,
    PromptAppendicesOut,
    PromptAppendicesUpdate,
    SettingsResetRequest,
    SettingsRollbackResult,
    SettingsUpdateRequest,
    SettingsUpdateResult,
    SettingsViewResponse,
    SystemProfileOut,
)
from app.services import audit_service, env_manager, llm_params, prompt_overrides, settings_service
from app.services.settings_service import DeveloperOnlyError, SettingsValidationError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["设置中心"], dependencies=[Depends(require_settings_admin)])


@router.get("/settings", response_model=SettingsViewResponse, summary="设置视图")
async def get_settings_view(db: AsyncSession = Depends(get_db)) -> SettingsViewResponse:
    """获取全部配置项视图(按分组返回元数据与当前值;敏感字段仅脱敏预览)"""
    view = settings_service.build_view()
    view.snapshot = await settings_service.snapshot_meta(db)
    return view


@router.put("/settings", response_model=SettingsUpdateResult, summary="更新设置")
async def update_settings(
    payload: SettingsUpdateRequest, db: AsyncSession = Depends(get_db)
) -> SettingsUpdateResult:
    """批量更新配置:写回 .env(保留注释)→ 热生效;返回需重启清单"""
    try:
        return await settings_service.apply_updates(db, payload)
    except DeveloperOnlyError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (SettingsValidationError, env_manager.EnvValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/settings/reset", response_model=SettingsUpdateResult, summary="恢复默认(单项)")
async def reset_setting(
    payload: SettingsResetRequest, db: AsyncSession = Depends(get_db)
) -> SettingsUpdateResult:
    """把某一项探索性设置恢复为系统默认值(写前记录快照,仍可整体回退)"""
    try:
        return await settings_service.reset_exploratory(db, payload.key)
    except (SettingsValidationError, env_manager.EnvValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/settings/rollback", response_model=SettingsRollbackResult, summary="一键回退探索性改动")
async def rollback_settings(db: AsyncSession = Depends(get_db)) -> SettingsRollbackResult:
    """把全部探索性设置的改动批量还原到"本批改动之前"的状态,并清空快照"""
    try:
        result = await settings_service.rollback_exploratory(db)
    except (SettingsValidationError, env_manager.EnvValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SettingsRollbackResult(restored=result.applied, message=result.message)


@router.post("/settings/llm-params/clear", response_model=SettingsUpdateResult, summary="清空自动适配记录")
async def clear_llm_param_learnings(db: AsyncSession = Depends(get_db)) -> SettingsUpdateResult:
    """清空“某端点+模型不支持某参数”的自动适配记录(此后该参数恢复按配置/画像发送)"""
    count = await llm_params.clear_learned(db)
    await audit_service.audit("settings.llm_params_clear", detail=f"清除 {count} 条自动适配记录")
    return SettingsUpdateResult(message=f"已清除 {count} 条自动适配记录")


@router.post("/settings/test", response_model=ConnectivityResult, summary="管线连通性测试")
async def test_connection(payload: ConnectivityRequest) -> ConnectivityResult:
    """测试管线 A / OCR / 评分端点的可达性(5 秒超时、禁重定向;可携带未保存值)"""
    try:
        return await settings_service.test_connectivity(payload.target, payload.overrides)
    except SettingsValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/settings/system-profile", response_model=SystemProfileOut, summary="本机配置与档位推荐")
async def get_system_profile() -> SystemProfileOut:
    """本机核心数/内存与手写识别档位推荐(auto 档推荐依据;手动值优先)"""
    from app.config import settings
    from app.services.handwriting_service import effective_level, system_profile

    profile = system_profile()
    return SystemProfileOut(
        cores=profile["cores"],
        ram_gb=profile["ram_gb"],
        recommended_level=profile["recommended_level"],
        configured_level=settings.handwriting_ocr_level,
        effective_level=effective_level(),
    )


@router.get("/settings/prompts", response_model=PromptAppendicesOut, summary="提示词微调查看")
async def get_prompt_appendices() -> PromptAppendicesOut:
    """查看三个位置的追加指令与"最终系统提示词"预览(含示范学习风格)"""
    from app.services import prompt_overrides as po

    return PromptAppendicesOut(
        appendices={kind: po.get_appendix(kind) for kind in po.KINDS},
        previews=po.build_previews(),
        max_chars=po.MAX_APPENDIX_CHARS,
    )


@router.put("/settings/prompts", response_model=PromptAppendicesOut, summary="提示词微调更新")
async def update_prompt_appendices(
    payload: PromptAppendicesUpdate, db: AsyncSession = Depends(get_db)
) -> PromptAppendicesOut:
    """更新追加指令(空串 = 清除;仅影响基础模板之后的补充段落)"""
    updates = payload.model_dump(exclude_unset=True)
    try:
        appendices = await prompt_overrides.update_appendices(db, updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return PromptAppendicesOut(
        appendices=appendices,
        previews=prompt_overrides.build_previews(),
        max_chars=prompt_overrides.MAX_APPENDIX_CHARS,
    )
