"""FastAPI 应用入口

启动方式(在 backend/ 目录):
    .venv\\Scripts\\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload

lifespan 生命周期:
- 启动:初始化数据库(建表)-> 创建批改编排服务与队列工作池并启动;
- 关闭:优雅停止队列工作池。
"""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    routes_analytics,
    routes_classes,
    routes_corrections,
    routes_encryption,
    routes_exams,
    routes_handwriting,
    routes_health,
    routes_ledger,
    routes_maintenance,
    routes_practice,
    routes_settings,
    routes_statistics,
    routes_status,
    routes_students,
    routes_style,
    routes_tasks,
)
from app.api.deps import require_unlocked
from app.config import settings
from app.db.database import SessionLocal, init_db
from app.services import encryption_service, llm_params, prompt_overrides, style_learning_service
from app.services.correction_service import CorrectionService
from app.services.exam_service import ExamService
from app.services.name_pre_ocr import name_pre_ocr_service
from app.services.handwriting_service import handwriting_service
from app.services.queue_service import QueueService

# 全局日志配置(控制台输出,含时间与级别)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # ---------- 启动 ----------
    logger.info("=== 德语作文批改系统后端启动 ===")
    logger.info(
        "配置摘要:默认管线=%s | 自动故障转移=%s | 演示模式=%s",
        settings.default_pipeline,
        settings.allow_auto_fallback,
        settings.mock_mode,
    )
    if settings.mock_mode:
        logger.warning("当前处于 MOCK 演示模式:所有批改返回样例数据,不调用真实模型")

    # 初始化数据库(建表)
    await init_db()

    # 创建并启动队列工作池
    correction_service = CorrectionService(SessionLocal, settings)
    queue_service = QueueService(correction_service, settings.max_concurrent_tasks)
    app.state.correction_service = correction_service
    app.state.queue_service = queue_service
    await queue_service.start()

    # 姓名预识别服务(上传后即时识名;轻量独立队列,不影响批改并发)
    await name_pre_ocr_service.start()

    # 手写样本服务(样本处理与本地手写模型构建)
    await handwriting_service.start()

    # 考试识别服务(独立队列:考卷 OCR 与报告数据准备)
    exam_service = ExamService(SessionLocal, settings, max_concurrent=1)
    app.state.exam_service = exam_service
    await exam_service.start()

    # 孤儿任务自愈:重启后把中断残留的 PROCESSING 任务重新入队
    recovered = await correction_service.recover_orphaned_tasks()
    for task_id in recovered:
        await queue_service.enqueue(task_id)

    # 考卷孤儿自愈:中断的识别重置并重新入队
    orphan_papers = await exam_service.recover_orphan_papers()
    for paper_id in orphan_papers:
        await exam_service.enqueue(paper_id)

    # 示范学习:加载当前生效的风格画像到内存缓存(供批改管线注入;未启用时为空)
    await style_learning_service.load_active_cache(SessionLocal)

    # 设置中心:加载提示词微调附录(管线 Prompt 注入)
    await prompt_overrides.load_appendix_cache(SessionLocal)

    # 大模型超参:加载“自动适配记录”(端点不支持参数的剔除记忆;后续请求不再发送)
    await llm_params.load_learned_cache(SessionLocal)

    # 数据加密:加载加密状态(存在口令则进入锁定态,数据接口将返回 423 直至解锁)
    await encryption_service.load_state(SessionLocal)

    yield

    # ---------- 关闭 ----------
    await queue_service.stop()
    await name_pre_ocr_service.stop()
    await handwriting_service.stop()
    await exam_service.stop()
    logger.info("=== 后端已停止 ===")


app = FastAPI(
    title="德语教师端智能作文批改系统",
    description=(
        "双管线热切换架构(Strategy Pattern):\n"
        "- 管线 A:本地统一 VLM(零成本 / 高隐私)\n"
        "- 管线 B:云端解耦(OCR + DeepSeek)\n"
        "两条管线输出统一的 EssayCorrectionResult 与 Markdown 报告。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# 开发环境 CORS:允许 Vite dev server 访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由(统一 /api 前缀)
# - 系统 / 设置 / 加密类接口不受"加密锁定"限制(解锁入口必须可达)
app.include_router(routes_health.router, prefix="/api")
app.include_router(routes_status.router, prefix="/api")
app.include_router(routes_settings.router, prefix="/api")
app.include_router(routes_encryption.router, prefix="/api")
# - 维护类接口:自带设置管理员 + 数据解锁双重校验
app.include_router(routes_maintenance.router, prefix="/api")

# - 数据类接口:已设置加密口令但未解锁时返回 423(require_unlocked)
_data_guard = [Depends(require_unlocked)]
app.include_router(routes_corrections.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_tasks.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_classes.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_analytics.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_students.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_ledger.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_style.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_exams.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_statistics.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_practice.router, prefix="/api", dependencies=_data_guard)
app.include_router(routes_handwriting.router, prefix="/api", dependencies=_data_guard)

# ---------- 前端静态托管(离线自包含运行:构建产物由后端直接服务,无需 Node) ----------
# 构建 `frontend/dist` 后,访问 http://127.0.0.1:8765 即得到完整应用(含前端路由刷新支持)
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
_FRONTEND_INDEX = FRONTEND_DIST / "index.html"

# 显式声明静态资源 MIME 类型:个别 Windows 机器的注册表会把 .js 关联成 text/plain,
# 而浏览器对 <script type="module"> 执行严格 MIME 校验,类型异常会导致整页白屏。
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".map")
mimetypes.add_type("font/woff2", ".woff2")

if _FRONTEND_INDEX.is_file():
    _assets_dir = FRONTEND_DIST / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """SPA 回退:非 /api 路径返回前端构建产物(支持前端路由直接刷新)"""
        if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
        # index.html 禁用启发式缓存:避免浏览器缓存旧页面、而旧页面引用的哈希资源
        # 已被新构建覆盖(404)导致白屏;带哈希的 /assets 文件仍按默认缓存协商。
        return FileResponse(_FRONTEND_INDEX, headers={"Cache-Control": "no-cache"})


@app.get("/", summary="服务信息")
async def root() -> dict:
    """根路径:返回服务信息与文档入口"""
    return {
        "name": "德语教师端智能作文批改系统",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/health",
    }
