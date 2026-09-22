"""数据加密路由(学生数据保护 / 忘记密码 Plan B)

- GET  /api/encryption/status            加密状态(含迁移进度)
- POST /api/encryption/setup             首次启用(设置口令)
- POST /api/encryption/unlock / lock     解锁 / 锁定
- POST /api/encryption/change-password   修改口令(仅重新包裹)
- POST /api/encryption/migrate           存量迁移(encrypt 加密 / decrypt 还原明文)
- POST /api/encryption/disable           关闭加密(还原明文 或 保留密文)
- POST /api/encryption/recovery/generate 生成一次性恢复密钥
- POST /api/encryption/recovery/reset    恢复密钥重置口令(强制轮换密钥)
- POST /api/encryption/planb/archive-reinit  Plan B 终极:归档密文 + 清空重建

全部接口挂 require_settings_admin;敏感操作均记审计日志(不含任何密钥/口令)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_settings_admin
from app.db.database import SessionLocal, get_db
from app.models.schemas import (
    EncryptionActionResult,
    EncryptionChangeRequest,
    EncryptionDisableRequest,
    EncryptionMigrateRequest,
    EncryptionPlanBRequest,
    EncryptionRecoveryGenerateOut,
    EncryptionRecoveryResetRequest,
    EncryptionSetupRequest,
    EncryptionStatusOut,
    EncryptionUnlockRequest,
)
from app.services import encryption_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["数据加密"], dependencies=[Depends(require_settings_admin)])


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/encryption/status", response_model=EncryptionStatusOut, summary="加密状态")
async def encryption_status(db: AsyncSession = Depends(get_db)) -> EncryptionStatusOut:
    """当前加密状态:是否启用 / 是否锁定 / 是否已设口令 / 恢复密钥 / 迁移进度"""
    return EncryptionStatusOut(**await encryption_service.status(db))


@router.post("/encryption/setup", response_model=EncryptionStatusOut, summary="启用加密(设置口令)")
async def encryption_setup(
    payload: EncryptionSetupRequest, db: AsyncSession = Depends(get_db)
) -> EncryptionStatusOut:
    """首次启用:设置口令并生成加密密钥(不自动加密存量数据,可随后执行迁移)"""
    try:
        return EncryptionStatusOut(**await encryption_service.setup(db, payload.password))
    except ValueError as e:
        raise _bad_request(e) from e


@router.post("/encryption/unlock", response_model=EncryptionStatusOut, summary="解锁")
async def encryption_unlock(
    payload: EncryptionUnlockRequest, db: AsyncSession = Depends(get_db)
) -> EncryptionStatusOut:
    """输入口令解锁(密钥载入内存;进程重启后需重新解锁)"""
    try:
        return EncryptionStatusOut(**await encryption_service.unlock(db, payload.password))
    except ValueError as e:
        raise _bad_request(e) from e


@router.post("/encryption/lock", response_model=EncryptionStatusOut, summary="锁定")
async def encryption_lock(db: AsyncSession = Depends(get_db)) -> EncryptionStatusOut:
    """立即锁定(清空内存密钥);数据接口将返回 423 直至再次解锁"""
    await encryption_service.lock()
    return EncryptionStatusOut(**await encryption_service.status(db))


@router.post("/encryption/change-password", response_model=EncryptionStatusOut, summary="修改口令")
async def encryption_change_password(
    payload: EncryptionChangeRequest, db: AsyncSession = Depends(get_db)
) -> EncryptionStatusOut:
    """修改口令(仅重新包裹密钥,不重加密数据、不轮换 DEK)"""
    try:
        return EncryptionStatusOut(
            **await encryption_service.change_password(db, payload.old_password, payload.new_password)
        )
    except ValueError as e:
        raise _bad_request(e) from e


@router.post("/encryption/migrate", response_model=EncryptionActionResult, summary="存量迁移")
async def encryption_migrate(payload: EncryptionMigrateRequest) -> EncryptionActionResult:
    """启动存量迁移(后台执行,进度见状态接口):encrypt=加密全部存量;decrypt=还原明文"""
    try:
        await encryption_service.start_migration(SessionLocal, payload.mode)
    except ValueError as e:
        raise _bad_request(e) from e
    return EncryptionActionResult(
        detail="迁移已启动(后台执行,可通过状态接口查看进度)",
        data={"migration": encryption_service.migration_progress()},
    )


@router.post("/encryption/disable", response_model=EncryptionStatusOut, summary="关闭加密")
async def encryption_disable(
    payload: EncryptionDisableRequest, db: AsyncSession = Depends(get_db)
) -> EncryptionStatusOut:
    """关闭加密:decrypt_all=先还原全部明文再彻底关闭;keep_ciphertext=停止新写入加密、保留密文"""
    try:
        return EncryptionStatusOut(
            **await encryption_service.disable(
                SessionLocal, db, payload.password, payload.mode, payload.confirm
            )
        )
    except ValueError as e:
        raise _bad_request(e) from e


@router.post(
    "/encryption/recovery/generate",
    response_model=EncryptionRecoveryGenerateOut,
    summary="生成恢复密钥(仅一次展示)",
)
async def encryption_recovery_generate(db: AsyncSession = Depends(get_db)) -> EncryptionRecoveryGenerateOut:
    """生成一次性恢复密钥(离线保存;用于忘记口令时重置)"""
    try:
        key = await encryption_service.generate_recovery_key(db)
    except ValueError as e:
        raise _bad_request(e) from e
    return EncryptionRecoveryGenerateOut(recovery_key=key)


@router.post("/encryption/recovery/reset", response_model=EncryptionStatusOut, summary="恢复密钥重置口令")
async def encryption_recovery_reset(
    payload: EncryptionRecoveryResetRequest, db: AsyncSession = Depends(get_db)
) -> EncryptionStatusOut:
    """用恢复密钥重置口令:解包 -> 强制轮换密钥 -> 全量重加密 -> 重打包(原恢复密钥继续有效)"""
    try:
        return EncryptionStatusOut(
            **await encryption_service.recovery_reset(
                SessionLocal, db, payload.recovery_key, payload.new_password
            )
        )
    except ValueError as e:
        raise _bad_request(e) from e


@router.post(
    "/encryption/planb/archive-reinit",
    response_model=EncryptionActionResult,
    summary="Plan B 终极:归档密文并清空重建",
)
async def encryption_planb(
    payload: EncryptionPlanBRequest, db: AsyncSession = Depends(get_db)
) -> EncryptionActionResult:
    """无法找回密钥时的兜底:先归档密文数据库副本,再清空业务数据重建空库(输入"清空重建"确认)"""
    try:
        data = await encryption_service.planb_archive_reinit(db, payload.confirm_phrase)
    except ValueError as e:
        raise _bad_request(e) from e
    return EncryptionActionResult(
        detail="已归档密文副本并重建空库;如日后找回恢复密钥,可请管理员从归档中恢复",
        data=data,
    )
