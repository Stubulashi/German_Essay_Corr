"""数据加密服务(学生本地数据加密 / 忘记密码 Plan B)

密钥层级:
- 口令 --scrypt--> KEK;随机 DEK 被 KEK 包裹(AES-GCM)存 app_settings(`encryption_keywrap`);
- 恢复密钥 --scrypt--> KEK2 包裹 DEK 存(`encryption_recovery_wrap`);
- DEK 仅驻留内存(crypto_runtime);进程重启后处于"锁定"态,需口令解锁。

加密范围(与 models/encrypted_types 配合,见 docs/报告):
- 确定性加密(支持等值查询/唯一约束):各表 student_name / student_id;
- 随机加密:批改结果/转录/报告、错因原文、台账备注、考卷逐题、考试报告、考试备注;
- 不加密:主键/时间戳/数值统计量/类别词表/图片路径(统计依赖或非个人数据)。

迁移与轮换:
- migrate(encrypt):逐表加密存量明文,幂等可重跑,后台任务 + 进度;
- migrate(decrypt):反向还原(用于"关闭加密并还原明文");
- 恢复重置:强制轮换 DEK 并全量重加密。

Plan B(忘记密码):
1) 恢复密钥重置(推荐):rewrap + 轮换 DEK + 全量重加密;
2) 管理员重置(改口令):旧口令可用时直接换包;
3) 终极兜底:归档密文数据库副本 -> 清空业务数据重建空库。

开发模式万能密码(仅调试用途):
- 门禁:DEV_MODE + DEV_MASTER_ENABLED + ≥64 位十六进制长 Hash 三者同时满足;
- 登记:门禁生效时在 setup/改口令/恢复重置/正常解锁时自动维护 `encryption_dev_master_wrap`
  (复用同一包裹格式;生产模式不读不写);
- 使用:同一解锁入口提交该长 Hash 即可解包 DEK;失败结果与普通口令错误不可区分。
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import re
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.config import BACKEND_DIR, settings
from app.models.db_models import (
    AppSetting,
    ClassRoster,
    CorrectionTask,
    ErrorRecord,
    Exam,
    ExamPaper,
    ExamReport,
    HomeworkItem,
    HomeworkRecord,
    SchoolClass,
    Student,
)
from app.models.encrypted_types import EncryptedJSON
from app.services import crypto_runtime, crypto_service, env_manager
from app.services.audit_service import audit

logger = logging.getLogger(__name__)

KEY_KEYWRAP = "encryption_keywrap"
KEY_RECOVERY = "encryption_recovery_wrap"
#: 仅开发模式:万能密码兜底包裹(app_settings 键;生产模式不读不写)
KEY_DEV_MASTER_WRAP = "encryption_dev_master_wrap"
#: 口令与万能密码校验失败的统一提示(两条路径必须逐字一致,不泄露差异信息)
_ERR_WRONG_PASSWORD = "口令错误(或密钥数据已损坏)"
#: 万能密码最小长度(十六进制字符数;SHA-256 级强度)
_DEV_MASTER_MIN_HEX = 64
ENV_PATH = BACKEND_DIR / ".env"
DB_FILE = BACKEND_DIR / "data" / "corrector.db"
ARCHIVE_DIR = BACKEND_DIR / "data"

#: 加密覆盖的表与列(迁移/轮换的唯一清单)
_SCOPES: list[tuple[type, tuple[str, ...]]] = [
    (CorrectionTask, ("student_name", "student_id", "result", "ocr_result", "edited_report", "topic", "assignment_name", "prev_overall_score", "teacher_message")),
    (Student, ("name", "student_id")),
    (ClassRoster, ("name", "student_id")),
    (ErrorRecord, ("student_name", "student_id", "original_text", "corrected_text")),
    (HomeworkRecord, ("student_name", "student_id", "note")),
    (ExamPaper, ("student_name", "student_id", "question_results")),
    (ExamReport, ("report_markdown",)),
    (Exam, ("note",)),
]

#: 后台迁移进度(内存态)
_migration: dict = {
    "running": False, "mode": None, "done": 0, "total": 0,
    "error": None, "started_at": None, "finished_at": None,
}


def migration_progress() -> dict:
    return dict(_migration)


# ---------------------------------------------------------
# 口令与包裹
# ---------------------------------------------------------
def validate_password(password: str) -> str:
    """口令强度校验(≥8 位,含字母与数字,拒绝控制字符)"""
    text = (password or "").strip()
    if len(text) < 8:
        raise ValueError("口令至少 8 位")
    if not any(c.isalpha() for c in text) or not any(c.isdigit() for c in text):
        raise ValueError("口令需同时包含字母与数字")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("口令不能包含控制字符")
    return text


def _encode_wrap(wrapped: bytes, salt: bytes, *, n: int, r: int, p: int) -> str:
    return json.dumps(
        {
            "v": 1,
            "kdf": {
                "name": "scrypt", "n": n, "r": r, "p": p,
                "salt": base64.b64encode(salt).decode("ascii"),
            },
            "wrapped": base64.b64encode(wrapped).decode("ascii"),
        },
        ensure_ascii=False,
    )


def _decode_wrap(raw: str) -> dict:
    data = json.loads(raw)
    kdf = data.get("kdf") or {}
    return {
        "n": int(kdf.get("n", crypto_service.SCRYPT_N)),
        "r": int(kdf.get("r", crypto_service.SCRYPT_R)),
        "p": int(kdf.get("p", crypto_service.SCRYPT_P)),
        "salt": base64.b64decode(kdf.get("salt") or ""),
        "wrapped": base64.b64decode(data.get("wrapped") or ""),
    }


async def _get_row(db: AsyncSession, key: str) -> AppSetting | None:
    return await db.get(AppSetting, key)


async def _save_row(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def _persist_enabled(value: bool) -> None:
    """把总开关写回 .env 并热更新配置单例"""
    env_manager.write_env_atomic(ENV_PATH, {"ENCRYPTION_ENABLED": "true" if value else "false"})
    settings.encryption_enabled = value
    crypto_runtime.set_enabled(value)


# ---------------------------------------------------------
# 状态 / 生命周期
# ---------------------------------------------------------
async def load_state(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """应用启动时加载加密状态(由 lifespan 调用)"""
    async with session_factory() as session:
        wrap = await session.get(AppSetting, KEY_KEYWRAP)
        wrap_present = wrap is not None and bool(wrap.value)
    crypto_runtime.configure(enabled=bool(settings.encryption_enabled), wrap_present=wrap_present, dek=None)
    if wrap_present:
        logger.warning("数据加密已启用:请通过设置面板输入口令解锁(当前处于锁定状态)")
    else:
        logger.info("数据加密:未启用")
    if dev_master_available():
        logger.warning("开发模式万能密码兜底已启用(仅限本地调试;生产环境请关闭 DEV_MASTER_ENABLED)")


async def status(db: AsyncSession) -> dict:
    """加密状态(供设置面板与解锁拦截使用;不含任何敏感值)"""
    recovery = await db.get(AppSetting, KEY_RECOVERY)
    return {
        "enabled": crypto_runtime.is_enabled(),
        "locked": crypto_runtime.is_locked(),
        "has_password": crypto_runtime.is_wrap_present(),
        "recovery_configured": recovery is not None and bool(recovery.value),
        "dev_master_enabled": dev_master_available(),
        "migration": migration_progress(),
    }


async def setup(db: AsyncSession, password: str) -> dict:
    """首次启用:设置口令、生成并包裹 DEK、打开总开关(不自动加密存量数据)"""
    if await _get_row(db, KEY_KEYWRAP):
        raise ValueError("已设置过口令;如需更换请使用「修改口令」或先禁用加密")
    pwd = validate_password(password)
    dek = crypto_service.new_dek()
    salt = crypto_service.new_salt()
    kek = crypto_service.derive_kek(pwd, salt)
    await _save_row(db, KEY_KEYWRAP, _encode_wrap(crypto_service.wrap_key(dek, kek), salt,
                                                  n=crypto_service.SCRYPT_N, r=crypto_service.SCRYPT_R, p=crypto_service.SCRYPT_P))
    await db.commit()
    _persist_enabled(True)
    crypto_runtime.configure(enabled=True, wrap_present=True, dek=dek)
    await _ensure_dev_master_wrap(db, dek)  # 开发门禁生效时同步登记万能密码包裹
    await audit("encryption.setup", detail="已启用数据加密")
    logger.info("数据加密已启用(等待存量迁移)")
    return await status(db)


async def _unwrap_with_password(db: AsyncSession, password: str, key: str = KEY_KEYWRAP) -> bytes:
    row = await db.get(AppSetting, key)
    if row is None or not row.value:
        raise ValueError("尚未设置加密口令")
    wrap = _decode_wrap(row.value)
    kek = crypto_service.derive_kek(password, wrap["salt"], n=wrap["n"], r=wrap["r"], p=wrap["p"])
    try:
        return crypto_service.unwrap_key(wrap["wrapped"], kek)
    except crypto_service.CryptoError as e:
        raise ValueError(_ERR_WRONG_PASSWORD) from e


# ---------------------------------------------------------
# 开发模式万能密码(兜底解锁;门禁不满足时全部为 no-op/不可用)
# ---------------------------------------------------------
def _dev_master_value() -> str | None:
    """读取规范化(小写)的万能密码;未同时满足"开发模式 + 专用开关 + ≥64 位十六进制"时返回 None

    生产模式(dev_mode=false)因此恒为 None:不注册入口、不响应请求、不读写开发包裹。
    """
    if not (settings.dev_mode and settings.dev_master_enabled):
        return None
    value = (settings.dev_master_password or "").strip().lower()
    if len(value) < _DEV_MASTER_MIN_HEX or not re.fullmatch(r"[0-9a-f]+", value):
        return None
    return value


def dev_master_available() -> bool:
    """万能密码能力当前是否可用(仅供状态接口做只读布尔展示;不泄露密码或其摘要)"""
    return _dev_master_value() is not None


async def _unwrap_with_dev_master(db: AsyncSession, candidate: str) -> bytes:
    """万能密码解包 DEK(仅开发模式;复用既有 scrypt 包裹格式与元数据)

    - 命中校验使用 hmac.compare_digest(常量时间比较),防时序侧信道;
    - 任何失败(未命中/包裹缺失/包裹损坏)一律抛与普通口令错误逐字一致的异常。
    """
    master = _dev_master_value()
    if master is None or not hmac.compare_digest(
        (candidate or "").strip().lower().encode("utf-8"), master.encode("utf-8")
    ):
        raise ValueError(_ERR_WRONG_PASSWORD)
    row = await db.get(AppSetting, KEY_DEV_MASTER_WRAP)
    if row is None or not row.value:
        raise ValueError(_ERR_WRONG_PASSWORD)
    wrap = _decode_wrap(row.value)
    kek = crypto_service.derive_kek(master, wrap["salt"], n=wrap["n"], r=wrap["r"], p=wrap["p"])
    try:
        return crypto_service.unwrap_key(wrap["wrapped"], kek)
    except crypto_service.CryptoError as e:
        raise ValueError(_ERR_WRONG_PASSWORD) from e


async def _ensure_dev_master_wrap(db: AsyncSession, dek: bytes) -> None:
    """开发模式自愈:保证万能密码包裹与当前 DEK 一致(缺失或过期时重建)

    仅在门禁生效时读/写;生产模式直接返回,不产生任何读写(既有行为逐字节不变)。
    """
    master = _dev_master_value()
    if master is None:
        return
    row = await db.get(AppSetting, KEY_DEV_MASTER_WRAP)
    if row is not None and row.value:
        try:
            wrap = _decode_wrap(row.value)
            kek = crypto_service.derive_kek(master, wrap["salt"], n=wrap["n"], r=wrap["r"], p=wrap["p"])
            if crypto_service.unwrap_key(wrap["wrapped"], kek) == dek:
                return
        except (ValueError, crypto_service.CryptoError):
            pass  # 包裹缺失/损坏:走重建
    salt = crypto_service.new_salt()
    kek = crypto_service.derive_kek(master, salt)
    await _save_row(db, KEY_DEV_MASTER_WRAP, _encode_wrap(crypto_service.wrap_key(dek, kek), salt,
                                                          n=crypto_service.SCRYPT_N, r=crypto_service.SCRYPT_R, p=crypto_service.SCRYPT_P))
    await db.commit()
    logger.info("开发模式万能密码包裹已登记/刷新(仅限本地调试环境)")


async def unlock(db: AsyncSession, password: str) -> dict:
    """解锁:验口令并把 DEK 载入内存(仅开发模式下可回退到万能密码兜底)"""
    via_dev_master = False
    try:
        dek = await _unwrap_with_password(db, password)
    except ValueError:
        # 万能密码回退:仅"开发门禁生效 + 已设口令"时尝试;失败结果与普通口令错误完全一致
        if not (dev_master_available() and crypto_runtime.is_wrap_present()):
            raise
        dek = await _unwrap_with_dev_master(db, password)
        via_dev_master = True
    crypto_runtime.configure(
        enabled=bool(settings.encryption_enabled), wrap_present=crypto_runtime.is_wrap_present(), dek=dek
    )
    if via_dev_master:
        await audit("encryption.unlock", ok=True, detail="解锁成功(开发模式万能密码)")
        logger.warning("数据加密已通过开发模式万能密码解锁(仅限本地调试)")
    else:
        await audit("encryption.unlock", ok=True, detail="解锁成功")
        await _ensure_dev_master_wrap(db, dek)  # 门禁生效时登记/自愈;否则无任何读写
        logger.info("数据加密已解锁")
    return await status(db)


async def lock() -> None:
    """锁定:清空内存中的 DEK(重启效果相同)"""
    crypto_runtime.set_dek(None)
    await audit("encryption.lock", detail="已锁定")
    logger.info("数据加密已锁定")


async def change_password(db: AsyncSession, old_password: str, new_password: str) -> dict:
    """修改口令(仅重新包裹 DEK,不轮换密钥、不重加密数据)"""
    dek = await _unwrap_with_password(db, old_password)
    pwd = validate_password(new_password)
    salt = crypto_service.new_salt()
    kek = crypto_service.derive_kek(pwd, salt)
    await _save_row(db, KEY_KEYWRAP, _encode_wrap(crypto_service.wrap_key(dek, kek), salt,
                                                  n=crypto_service.SCRYPT_N, r=crypto_service.SCRYPT_R, p=crypto_service.SCRYPT_P))
    await db.commit()
    crypto_runtime.set_dek(dek)
    await _ensure_dev_master_wrap(db, dek)  # 开发门禁生效时同步登记/自愈
    await audit("encryption.change_password", detail="口令已更新")
    return await status(db)


# ---------------------------------------------------------
# 迁移(存量加密 / 还原明文)
# ---------------------------------------------------------
def _raw_is_ciphertext(raw: object) -> bool:
    """原始库值是否已是密文(兼容 JSON 列被引号包裹的形态)"""
    return isinstance(raw, str) and ("encv1:" in raw[:12] or "encd1:" in raw[:12])


def _raw_is_empty(model: type, col: str, raw: object) -> bool:
    """原始值是否视为空(不参与迁移)

    注意:SQLAlchemy JSON 列对 None 的默认序列化形态是字符串 'null'
    (none_as_null=False),必须识别为空值,否则会被误判为未加密数据。
    """
    if raw is None or raw == "":
        return True
    column_type = model.__table__.c[col].type
    if isinstance(column_type, EncryptedJSON) and raw == "null":
        return True
    return False


async def _count_raw_rows(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _migrate_table(session: AsyncSession, model: type, cols: tuple[str, ...], mode: str) -> int:
    """单表迁移:逐行判断是否需写入;ORM 回写以触发透明加解密"""
    table = model.__tablename__
    col_list = ", ".join(cols)
    rows = (await session.execute(text(f"SELECT id, {col_list} FROM {table}"))).all()
    changed = 0
    for row in rows:
        row_id = row[0]
        raws = row[1:]
        if mode == "encrypt":
            needs = any(
                not _raw_is_empty(model, col, v) and not _raw_is_ciphertext(v)
                for col, v in zip(cols, raws)
            )
        else:
            needs = any(_raw_is_ciphertext(v) for v in raws)
        if not needs:
            continue
        obj = await session.get(model, row_id)
        if obj is None:
            continue
        for col in cols:
            value = getattr(obj, col)
            setattr(obj, col, value)
            if value is not None:
                flag_modified(obj, col)
        changed += 1
        if changed % 200 == 0:
            await session.flush()
    await session.commit()
    return changed


async def _run_migration(session_factory: async_sessionmaker[AsyncSession], mode: str) -> None:
    """后台迁移任务(带进度;幂等可重跑;单表失败即整体报错,已完成批次保留)"""
    _migration.update(running=True, mode=mode, done=0, total=0, error=None,
                      started_at=datetime.now(timezone.utc).isoformat(), finished_at=None)
    try:
        async with session_factory() as session:
            total = 0
            for model, _cols in _SCOPES:
                total += await _count_raw_rows(session, model)
            _migration["total"] = total
            done = 0
            for model, cols in _SCOPES:
                done += await _migrate_table(session, model, cols, mode)
                _migration["done"] = done
        logger.info("加密迁移完成:mode=%s,处理 %s 行", mode, _migration["done"])
        await audit(f"encryption.migrate_{mode}", detail=f"处理 {_migration['done']} 行")
    except Exception as e:  # noqa: BLE001 —— 记录进度并停止
        _migration["error"] = str(e)[:300]
        logger.exception("加密迁移失败")
        await audit(f"encryption.migrate_{mode}", ok=False, detail=str(e)[:200])
    finally:
        _migration["running"] = False
        _migration["finished_at"] = datetime.now(timezone.utc).isoformat()


async def start_migration(session_factory: async_sessionmaker[AsyncSession], mode: str) -> None:
    """启动迁移(mode=encrypt 需已解锁;decrypt 会先关闭写加密再还原)"""
    if _migration["running"]:
        raise ValueError("已有迁移任务进行中,请等待完成")
    if not crypto_runtime.is_unlocked():
        raise ValueError("请先解锁(输入口令)再执行迁移")
    if mode == "encrypt" and not crypto_runtime.is_enabled():
        crypto_runtime.set_enabled(True)
    if mode == "decrypt":
        # 还原明文 = 关闭写加密后逐行解密(总开关同步落盘)
        _persist_enabled(False)
    asyncio.create_task(_run_migration(session_factory, mode))


# ---------------------------------------------------------
# 关闭 / 恢复密钥 / Plan B
# ---------------------------------------------------------
async def disable(session_factory: async_sessionmaker[AsyncSession], db: AsyncSession,
                  password: str, mode: str, confirm: bool) -> dict:
    """关闭加密:decrypt_all=还原全部明文后彻底关闭;keep_ciphertext=停止新写入加密,保留密文

    - keep_ciphertext 模式下 wrap 保留:重启后仍处于锁定态,需解锁才能读取旧密文;
    - decrypt_all 完成后删除包裹记录,恢复"完全无需口令"的体验。
    """
    if not confirm:
        raise ValueError("请先确认关闭加密的影响(confirm=true)")
    await _unwrap_with_password(db, password)  # 验口令(错误则中止)
    await db.rollback()  # 结束读事务,后续迁移使用独立会话写入
    if mode == "decrypt_all":
        if not crypto_runtime.is_unlocked():
            raise ValueError("请先解锁后再执行还原")
        # 先关写加密,再逐行还原(保证回写为明文)
        _persist_enabled(False)
        await _run_migration(session_factory, "decrypt")
        if _migration["error"]:
            raise ValueError(f"还原明文失败(已保留密文):{_migration['error']}")
        for key in (KEY_KEYWRAP, KEY_RECOVERY, KEY_DEV_MASTER_WRAP):
            row = await db.get(AppSetting, key)
            if row is not None:
                await db.delete(row)
        await db.commit()
        crypto_runtime.configure(enabled=False, wrap_present=False, dek=None)
        await audit("encryption.disable", detail="decrypt_all")
        return await status(db)
    # keep_ciphertext
    _persist_enabled(False)
    await audit("encryption.disable", detail="keep_ciphertext")
    return await status(db)


def _format_recovery_key() -> tuple[str, str]:
    """生成一次性恢复密钥(40 位 hex,8 组 x 5 展示)"""
    raw = secrets.token_hex(20)
    pretty = "-".join(raw[i : i + 5] for i in range(0, 40, 5))
    return raw, pretty


def _normalize_recovery_key(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch not in "- ")


async def generate_recovery_key(db: AsyncSession) -> str:
    """生成恢复密钥(仅此一次返回;同时包裹 DEK 存储)"""
    if not crypto_runtime.is_wrap_present():
        raise ValueError("请先设置加密口令(启用加密)再生成恢复密钥")
    if not crypto_runtime.is_unlocked():
        raise ValueError("请先解锁后再生成恢复密钥")
    raw, pretty = _format_recovery_key()
    salt = crypto_service.new_salt()
    kek2 = crypto_service.derive_kek(raw, salt)
    wrapped = crypto_service.wrap_key(crypto_runtime.get_dek(), kek2)
    await _save_row(db, KEY_RECOVERY, _encode_wrap(wrapped, salt,
                                                   n=crypto_service.SCRYPT_N, r=crypto_service.SCRYPT_R, p=crypto_service.SCRYPT_P))
    await db.commit()
    await audit("encryption.recovery_generate", detail="恢复密钥已生成(仅一次展示)")
    return pretty


async def _reencrypt_all(session_factory: async_sessionmaker[AsyncSession], old_dek: bytes, new_dek: bytes) -> int:
    """全量重加密(轮换 DEK):先以旧密钥读入内存,切换密钥后回写"""
    async with session_factory() as session:
        loaded: list[tuple[object, tuple[str, ...]]] = []
        for model, cols in _SCOPES:
            objs = (await session.execute(select(model))).scalars().all()
            loaded.extend((obj, cols) for obj in objs)
        crypto_runtime.set_dek(new_dek)
        count = 0
        for obj, cols in loaded:
            for col in cols:
                value = getattr(obj, col)
                setattr(obj, col, value)
                if value is not None:
                    flag_modified(obj, col)
            count += 1
        await session.commit()
        return count


async def recovery_reset(session_factory: async_sessionmaker[AsyncSession], db: AsyncSession,
                         recovery_key: str, new_password: str) -> dict:
    """恢复密钥重置口令:解包 -> 强制轮换 DEK -> 全量重加密 -> 重打包"""
    if not crypto_runtime.is_wrap_present():
        raise ValueError("尚未启用加密,无需重置")
    pwd = validate_password(new_password)
    normalized = _normalize_recovery_key(recovery_key)
    if len(normalized) != 40:
        raise ValueError("恢复密钥格式不正确(应为 40 位,8 组 x 5 位)")

    row = await db.get(AppSetting, KEY_RECOVERY)
    if row is None or not row.value:
        raise ValueError("尚未生成过恢复密钥;请使用「密文归档重建」兜底方案")
    wrap = _decode_wrap(row.value)
    # 结束本会话的读事务:避免与 _reencrypt_all 的独立写入会话在 SQLite 下互锁
    await db.rollback()
    kek2 = crypto_service.derive_kek(normalized, wrap["salt"], n=wrap["n"], r=wrap["r"], p=wrap["p"])
    try:
        old_dek = crypto_service.unwrap_key(wrap["wrapped"], kek2)
    except crypto_service.CryptoError as e:
        raise ValueError("恢复密钥错误") from e

    new_dek = crypto_service.new_dek()
    rows = await _reencrypt_all(session_factory, old_dek, new_dek)

    # 重打包:口令 + 恢复密钥(同一恢复密钥继续有效)
    salt = crypto_service.new_salt()
    kek = crypto_service.derive_kek(pwd, salt)
    await _save_row(db, KEY_KEYWRAP, _encode_wrap(crypto_service.wrap_key(new_dek, kek), salt,
                                                  n=crypto_service.SCRYPT_N, r=crypto_service.SCRYPT_R, p=crypto_service.SCRYPT_P))
    salt2 = crypto_service.new_salt()
    kek2_new = crypto_service.derive_kek(normalized, salt2)
    await _save_row(db, KEY_RECOVERY, _encode_wrap(crypto_service.wrap_key(new_dek, kek2_new), salt2,
                                                   n=crypto_service.SCRYPT_N, r=crypto_service.SCRYPT_R, p=crypto_service.SCRYPT_P))
    await db.commit()
    _persist_enabled(True)
    crypto_runtime.configure(enabled=True, wrap_present=True, dek=new_dek)
    await _ensure_dev_master_wrap(db, new_dek)  # DEK 已轮换:刷新万能密码包裹
    await audit("encryption.recovery_reset", detail=f"已轮换密钥并重加密 {rows} 行")
    logger.info("恢复重置完成:轮换 DEK,重加密 %s 行", rows)
    return await status(db)


def _archive_ciphertext_copy() -> Path:
    """归档密文数据库副本(使用 SQLite 在线备份 API,兼容 WAL)"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = ARCHIVE_DIR / f"archive_{stamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / "corrector.db"

    def _backup() -> None:
        if not DB_FILE.is_file():
            raise ValueError("数据库文件不存在,无需归档")
        src = sqlite3.connect(str(DB_FILE))
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        uploads = BACKEND_DIR / "data" / "uploads"
        if uploads.is_dir():
            shutil.copytree(uploads, archive_dir / "uploads", dirs_exist_ok=True)

    _backup()
    return archive_dir


async def planb_archive_reinit(db: AsyncSession, confirm_phrase: str) -> dict:
    """Plan B 终极兜底:归档密文副本 -> 清空业务数据重建空库(恢复密钥/口令一并清除)

    - 归档位置:backend/data/archive_{时间戳}/corrector.db(+ uploads 副本);
    - 图片文件保留在 uploads 原目录(如需彻底清理请手动删除);
    - 执行后系统回到"未加密、空数据"状态,可重新初始化。
    """
    if (confirm_phrase or "").strip() != "清空重建":
        raise ValueError('确认短语不正确(请完整输入"清空重建")')
    archive_dir = await asyncio.to_thread(_archive_ciphertext_copy)
    wipe_order = (
        ErrorRecord, CorrectionTask, ExamPaper, ExamReport, Exam,
        HomeworkRecord, HomeworkItem, ClassRoster, Student, SchoolClass,
    )
    wiped = 0
    for model in wipe_order:
        result = await db.execute(delete(model))
        wiped += result.rowcount or 0
    for key in (KEY_KEYWRAP, KEY_RECOVERY, KEY_DEV_MASTER_WRAP):
        row = await db.get(AppSetting, key)
        if row is not None:
            await db.delete(row)
    await db.commit()
    _persist_enabled(False)
    crypto_runtime.configure(enabled=False, wrap_present=False, dek=None)
    await audit("encryption.planb_reinit", detail=f"归档 {archive_dir.name},清空 {wiped} 行")
    logger.warning("Plan B 归档重建完成:归档目录 %s,清空 %s 行业务数据", archive_dir, wiped)
    return {"archive_dir": str(archive_dir), "wiped_rows": wiped}
