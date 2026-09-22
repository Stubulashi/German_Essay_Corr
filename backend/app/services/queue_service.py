"""批量异步任务队列(进程内 asyncio 工作池)

设计说明:
- 采用 `asyncio.Queue` + N 个常驻工作协程(数量由 MAX_CONCURRENT_TASKS 决定);
- 应用启动时随 lifespan 创建,关闭时优雅退出;
- 相比 Celery:零外部依赖、Windows 兼容性好,适合单机教师工具;
  若未来需要分布式部署,可将本模块替换为 Celery/RQ,接口保持不变(扩展点)。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from app.services.correction_service import CorrectionService

logger = logging.getLogger(__name__)


class QueueService:
    """批改任务队列服务"""

    def __init__(self, correction_service: CorrectionService, max_concurrent: int = 2):
        self._correction_service = correction_service
        self._max_concurrent = max(1, max_concurrent)
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        #: 在途任务 id(排队中/处理中;入队去重,防止同一任务被多 worker 并发处理)
        self._active: set[int] = set()
        #: 最近处理耗时(供全局状态条估算 ETA)
        self._durations: deque[float] = deque(maxlen=20)

    @property
    def pending_count(self) -> int:
        """当前排队中的任务数(供 /api/health 展示)"""
        return self._queue.qsize()

    @property
    def recent_avg_seconds(self) -> float | None:
        """最近任务平均处理耗时(秒;样本不足时为 None,如实缺省)"""
        if not self._durations:
            return None
        return sum(self._durations) / len(self._durations)

    # ---------------------------------------------------------
    # 生命周期
    # ---------------------------------------------------------
    async def start(self) -> None:
        """启动工作池(由 FastAPI lifespan 调用)"""
        if self._running:
            return
        self._running = True
        for i in range(self._max_concurrent):
            worker = asyncio.create_task(self._worker_loop(i), name=f"corrector-worker-{i}")
            self._workers.append(worker)
        logger.info("批改队列工作池已启动(并发数:%s)", self._max_concurrent)

    async def stop(self) -> None:
        """优雅关闭:取消所有工作协程"""
        self._running = False
        for worker in self._workers:
            worker.cancel()
        # 等待所有协程退出(忽略取消异常)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("批改队列工作池已停止")

    # ---------------------------------------------------------
    # 入队与工作循环
    # ---------------------------------------------------------
    async def enqueue(self, task_id: int) -> None:
        """将任务加入队列(立即返回,不阻塞请求;同一任务在途时幂等忽略)"""
        if task_id in self._active:
            logger.warning("任务 %s 已在队列/处理中,忽略重复入队", task_id)
            return
        self._active.add(task_id)
        await self._queue.put(task_id)
        logger.debug("任务 %s 已入队(当前排队:%s)", task_id, self._queue.qsize())

    async def _worker_loop(self, worker_index: int) -> None:
        """单个工作协程:循环取任务并处理

        注意:process_task 内部已消化所有异常,此处仅做最后的防御性保护。
        """
        while self._running:
            try:
                task_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            started = time.monotonic()
            try:
                logger.debug("工作协程 %s 开始处理任务 %s", worker_index, task_id)
                await self._correction_service.process_task(task_id)
            except asyncio.CancelledError:
                # 关闭时中断当前任务:任务保持 PROCESSING,重启后可由教师手动重试
                logger.warning("工作协程 %s 被取消,任务 %s 处理中断", worker_index, task_id)
                break
            except Exception:
                # 理论上不可达(process_task 已兜底),保留日志防止静默丢失
                logger.exception("工作协程 %s 处理任务 %s 时发生未捕获异常", worker_index, task_id)
            finally:
                self._durations.append(time.monotonic() - started)
                self._active.discard(task_id)
                self._queue.task_done()
