"""API 依赖注入工具

- get_*_service:从 app.state 取出全局单例服务(在 main.py 的 lifespan 中创建);
- require_settings_admin:设置类接口防护(默认仅本机回环;配置访问令牌后强制携带并恒定时比较);
- require_unlocked:数据类接口防护(已设置加密口令但尚未解锁时返回 423 Locked)。
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from app.config import settings
from app.services import crypto_runtime
from app.services.correction_service import CorrectionService
from app.services.exam_service import ExamService
from app.services.queue_service import QueueService

#: 视为"本机"的来源地址(含测试客户端)
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def get_queue_service(request: Request) -> QueueService:
    """获取任务队列服务实例"""
    return request.app.state.queue_service


def get_correction_service(request: Request) -> CorrectionService:
    """获取批改编排服务实例"""
    return request.app.state.correction_service


def get_exam_service(request: Request) -> ExamService:
    """获取考试识别服务实例"""
    return request.app.state.exam_service


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in _LOOPBACK_HOSTS


async def require_settings_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """设置类接口权限校验

    - 未配置访问令牌:仅本机(回环)可访问,其他来源 403;
    - 已配置访问令牌:一律要求 X-Admin-Token 且使用恒定时比较(防时序攻击)。
    """
    token = (settings.settings_admin_token or "").strip()
    if token:
        if not x_admin_token or not secrets.compare_digest(x_admin_token.strip(), token):
            raise HTTPException(
                status_code=401,
                detail="访问令牌缺失或错误(请在设置面板填入访问令牌,或由管理员在 .env 配置 SETTINGS_ADMIN_TOKEN)",
            )
        return
    if not _is_loopback(request):
        raise HTTPException(
            status_code=403,
            detail="设置接口仅限本机访问;如需远程管理,请先配置访问令牌(SETTINGS_ADMIN_TOKEN)",
        )


async def require_unlocked() -> None:
    """数据类接口防护:加密已启用/设口令但进程尚未解锁时拒绝访问(423)"""
    if crypto_runtime.is_locked():
        raise HTTPException(
            status_code=423,
            detail="学生数据已加密,请先在「运行配置 → 数据加密」中输入口令解锁",
        )
