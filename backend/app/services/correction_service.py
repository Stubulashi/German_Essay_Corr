"""批改任务编排服务(状态机 + 双管线调度 + A→B 故障转移)

职责:
1. 从数据库加载任务,组装 `CorrectionContext`(相对路径 -> 绝对路径);
2. 通过策略工厂获取管线并执行(进度实时回写数据库);
3. 处理三种结果:完成入库 / 等待人工复核挂起 / 失败;
4. 故障转移:管线 A 抛出 `PipelineNetworkError` 且允许时,自动改用管线 B 重跑,
   记录 WARNING 日志并置 `fallback_triggered=true`;
5. 成功后:写入学生档案(upsert)与错题记录(为二期错题本/共性错因分析积累数据)。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, settings as global_settings
from app.models.db_models import CorrectionTask, ErrorRecord
from app.models.schemas import (
    CorrectionConfig,
    DetailLevel,
    GradingStandard,
    OcrExtractionResult,
    PipelineChoice,
)
from app.pipelines.base import (
    AbstractCorrectionPipeline,
    CorrectionContext,
    PipelineError,
    PipelineNetworkError,
)
from app.pipelines.factory import get_pipeline
from app.services.audit_service import audit
from app.services.ocr_anomaly import analyze_transcription
from app.services.report_renderer import finalize_result
from app.services.student_assignment import load_roster
from app.services.student_info_resolver import resolve_identity
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)


class CorrectionService:
    """批改任务编排服务"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings | None = None,
    ):
        self._session_factory = session_factory
        self._settings = settings or global_settings

    # ---------------------------------------------------------
    # 主流程
    # ---------------------------------------------------------
    async def process_task(self, task_id: int) -> None:
        """处理一个批改任务(由队列工作池调用)

        本方法永不向外抛异常(所有异常内部消化并写入任务状态),
        保证队列工作池的稳定性。
        """
        async with self._session_factory() as session:
            task = await session.get(CorrectionTask, task_id)
            if task is None:
                logger.error("任务 %s 不存在,跳过处理", task_id)
                return
            if task.status in ("WAITING_REVIEW", "COMPLETED"):
                logger.info("任务 %s 当前状态为 %s,无需处理", task_id, task.status)
                return

            # ---------- 组装上下文 ----------
            # 边界优化:若任务曾发生 A→B 故障转移且已完成 OCR 复核,
            # 重跑时直接使用实际执行过的管线(B),避免再次重复失败的 A 管线
            pipeline_raw = task.pipeline_choice
            if task.ocr_result and task.pipeline_used:
                pipeline_raw = task.pipeline_used
            config = CorrectionConfig(
                pipeline_choice=PipelineChoice(pipeline_raw),
                grading_standard=GradingStandard(task.grading_standard),
                detail_level=DetailLevel(task.detail_level),
                require_ocr_review=bool(task.require_ocr_review),
            )
            image_paths = [self._settings.upload_path / p for p in (task.image_paths or [])]
            # 若存在(人工复核后的)OCR 结果,构造为 OcrExtractionResult 传入上下文
            ocr_result: OcrExtractionResult | None = None
            if task.ocr_result and task.require_ocr_review:
                ocr_result = OcrExtractionResult.model_validate(task.ocr_result)

            # 进度回调:每个阶段更新数据库(短会话提交,避免长事务占用 SQLite)
            async def on_progress(stage: str, progress: float) -> None:
                await self._update_task(task_id, stage=stage, progress=progress)

            ctx = CorrectionContext(
                task_id=task_id,
                image_paths=image_paths,
                config=config,
                ocr_result=ocr_result,
                on_progress=on_progress,
            )

            # 标记为处理中
            await self._update_task(task_id, status="PROCESSING", stage="UPLOADED", progress=0.0, error_message=None)

        # ---------- 执行管线(含故障转移;管线 A 转录异常时整管线重跑) ----------
        s = self._settings
        report = None
        anomaly_attempt = 0
        while True:
            try:
                outcome, used_pipeline, fallback_used = await self._execute_with_fallback(ctx)
            except PipelineError as e:
                logger.error("任务 %s 批改失败:%s", task_id, e)
                await self._update_task(task_id, status="FAILED", error_message=str(e))
                return
            except Exception as e:  # 兜底:任何未预期异常都不允许击穿队列工作池
                logger.exception("任务 %s 发生未预期异常", task_id)
                await self._update_task(task_id, status="FAILED", error_message=f"内部错误:{e}")
                return

            report = None
            if outcome.completed_result is not None:
                report = analyze_transcription(outcome.completed_result.transcribed_text)
            if (
                report is None
                or not report.anomalous
                or used_pipeline != PipelineChoice.PIPELINE_A_LOCAL
                or ctx.ocr_result is not None  # 教师确认的转录不再触发整管线重跑
                or anomaly_attempt >= s.ocr_anomaly_max_retry
            ):
                break
            anomaly_attempt += 1
            logger.warning(
                "任务 %s 管线 A 转录异常(得分 %s:%s),整管线自动重跑(第 %s 次)",
                task_id,
                report.score,
                "、".join(report.reasons),
                anomaly_attempt,
            )
            await audit(
                "pipeline.anomaly_rerun",
                ok=True,
                detail=(
                    f"任务 {task_id};得分 {report.score};"
                    f"信号:{'、'.join(report.reasons)};第 {anomaly_attempt} 次"
                ),
                source="worker",
            )

        # 管线 A 异常重试用尽:降级为可见提示(纯文本增量,不改结构)并重渲染报告
        if (
            report is not None
            and report.anomalous
            and used_pipeline == PipelineChoice.PIPELINE_A_LOCAL
            and ctx.ocr_result is None  # 教师确认的转录不追加识别质量提示
            and outcome.completed_result is not None
        ):
            advisory = (
                "【识别质量提示】本次识别可能存在畸变(信号:"
                + "、".join(report.reasons)
                + "),建议对照原图核对或使用「重新批改」触发整管线重跑。"
            )
            comment = outcome.completed_result.overall_comment or ""
            if "【识别质量提示】" not in comment:
                outcome.completed_result.overall_comment = (comment + "\n\n" + advisory).strip()
                finalize_result(
                    result=outcome.completed_result,
                    pipeline_choice=ctx.config.pipeline_choice,
                    pipeline_display_name=outcome.pipeline_display_name or "",
                    standard=ctx.config.grading_standard,
                    detail=ctx.config.detail_level,
                )
            await audit(
                "pipeline.anomaly_degraded",
                ok=True,
                detail=f"任务 {task_id};得分 {report.score};信号:{'、'.join(report.reasons)}",
                source="worker",
            )
            logger.warning("任务 %s 管线 A 异常重试用尽,已追加识别质量提示", task_id)

        # ---------- 落库结果 ----------
        if outcome.waiting_review and outcome.ocr_result is not None:
            # 管线 B 开启人工复核:挂起,等待教师确认转录
            await self._update_task(
                task_id,
                status="WAITING_REVIEW",
                stage="OCR",
                progress=0.5,
                ocr_result=outcome.ocr_result.model_dump(),
                pipeline_used=used_pipeline.value,
                fallback_triggered=1 if fallback_used else 0,
            )
            logger.info("任务 %s 等待人工复核 OCR 结果", task_id)
            return

        if outcome.completed_result is not None:
            await self._finalize_success(
                task_id=task_id,
                result=outcome.completed_result,
                ocr_result=outcome.ocr_result,
                pipeline_used=used_pipeline,
                fallback_used=fallback_used,
            )

    # ---------------------------------------------------------
    # 管线执行与故障转移
    # ---------------------------------------------------------
    async def _execute_with_fallback(
        self, ctx: CorrectionContext
    ) -> tuple:
        """执行管线;管线 A 网络失败时按配置自动转移至管线 B

        Returns:
            (outcome, 实际使用管线枚举, 是否发生转移)
        """
        pipeline: AbstractCorrectionPipeline = get_pipeline(ctx.config, self._settings)
        try:
            outcome = await pipeline.correct(ctx)
            return outcome, ctx.config.pipeline_choice, False
        except PipelineNetworkError as e:
            # 仅管线 A 允许转移到管线 B
            can_fallback = (
                ctx.config.pipeline_choice == PipelineChoice.PIPELINE_A_LOCAL
                and self._settings.allow_auto_fallback
            )
            if not can_fallback:
                raise
            logger.warning(
                "管线 A 网络失败,自动故障转移至管线 B(任务 %s):%s", ctx.task_id, e
            )
            fallback_config = ctx.config.model_copy(
                update={"pipeline_choice": PipelineChoice.PIPELINE_B_CLOUD}
            )
            fallback_ctx = CorrectionContext(
                task_id=ctx.task_id,
                image_paths=ctx.image_paths,
                config=fallback_config,
                ocr_result=ctx.ocr_result,  # A 场景通常为 None,由 B 自行 OCR
                on_progress=ctx.on_progress,
            )
            fallback_pipeline = get_pipeline(fallback_config, self._settings)
            outcome = await fallback_pipeline.correct(fallback_ctx)
            return outcome, PipelineChoice.PIPELINE_B_CLOUD, True

    # ---------------------------------------------------------
    # 成功落库(结果 + 学生档案 + 错题记录)
    # ---------------------------------------------------------
    async def _finalize_success(
        self,
        task_id: int,
        result,
        ocr_result: OcrExtractionResult | None,
        pipeline_used: PipelineChoice,
        fallback_used: bool,
    ) -> None:
        """批改成功的统一收尾:更新任务、upsert 学生、写入错题记录"""
        async with self._session_factory() as session:
            task = await session.get(CorrectionTask, task_id)
            if task is None:
                return

            task.status = "COMPLETED"
            task.stage = "DONE"
            task.progress = 1.0

            # ---------- 身份决策链(预指派 > 名单对齐 > 识别;方向二/四) ----------
            # 在落库前统一解析学生身份,必要时重渲染报告头部(保持 UI 与落库一致)
            await self._apply_identity_policy(session, task, result)

            task.result = result.model_dump()
            task.student_name = result.student_name
            task.student_id = result.student_id
            task.pipeline_used = pipeline_used.value
            task.fallback_triggered = 1 if fallback_used else 0
            if ocr_result is not None:
                task.ocr_result = ocr_result.model_dump()

            # ---------- 学生档案 upsert(与花名册导入/纠错共用同一规则) ----------
            student = await upsert_student(session, result.student_name, result.student_id)

            # ---------- 错题记录(为二期共性错因分析积累数据) ----------
            for err in result.errors:
                session.add(
                    ErrorRecord(
                        task_id=task_id,
                        student_name=result.student_name,
                        student_id=result.student_id,
                        error_type=err.error_type,
                        canonical_type=err.canonical_type or "OTHER",  # 标准化分类(渲染前已填充)
                        original_text=err.original_text,
                        corrected_text=err.corrected_text,
                    )
                )

            await session.commit()
            logger.info(
                "任务 %s 批改完成(管线:%s,故障转移:%s,学生:%s)",
                task_id,
                pipeline_used.value,
                "是" if fallback_used else "否",
                student.name,
            )

    # ---------------------------------------------------------
    # 崩溃恢复(孤儿任务自愈)
    # ---------------------------------------------------------
    async def recover_orphaned_tasks(self) -> list[int]:
        """服务重启后恢复中断的任务

        场景:断电、进程崩溃、误关闭服务时,部分任务会滞留 PROCESSING 状态。
        策略:启动时将全部 PROCESSING 任务重置为 PENDING 并返回 ID 列表,
        由调用方重新入队(注意与 WAITING_REVIEW 严格区分,复核中的任务不得重置)。

        Returns:
            需要重新入队的任务 ID 列表
        """
        async with self._session_factory() as session:
            stmt = select(CorrectionTask).where(CorrectionTask.status == "PROCESSING")
            tasks = (await session.execute(stmt)).scalars().all()
            ids: list[int] = []
            for task in tasks:
                task.status = "PENDING"
                task.stage = "UPLOADED"
                task.progress = 0.0
                task.error_message = None
                ids.append(task.id)
            if ids:
                await session.commit()
                logger.warning("检测到 %s 个中断任务,已重置为排队中:%s", len(ids), ids)
        return ids

    # ---------------------------------------------------------
    # 身份决策链(方向二/四)
    # ---------------------------------------------------------
    async def _apply_identity_policy(self, session: AsyncSession, task: CorrectionTask, result) -> None:
        """在落库前解析最终学生身份并同步到 result

        决策链(从强到弱):
        1. 上传时预指派(教师明确指定) → 始终以预指派为准;
        2. 花名册学号/姓名对齐(任务关联班级有花名册时);
        3. 抬头文本包含名单姓名;
        4. LLM 识别结果;
        5. 兜底“未知”。

        若身份与管线输出不一致,重渲染 Markdown 报告(报告头部姓名保持一致)。
        """
        roster = await load_roster(session, task.class_id)
        resolved = resolve_identity(
            preassigned_name=(task.student_name if task.student_name and task.student_name != "未知" else None),
            preassigned_id=task.student_id,
            llm_name=result.student_name,
            llm_id=result.student_id,
            transcribed_text=result.transcribed_text,
            roster=roster,
        )
        changed = (
            resolved.student_name != result.student_name
            or (resolved.student_id or None) != (result.student_id or None)
        )
        if changed:
            logger.info(
                "任务 %s 身份解析(%s):%s / %s(识别原值:%s / %s)",
                task.id, resolved.source, resolved.student_name, resolved.student_id,
                result.student_name, result.student_id,
            )
        result.student_name = resolved.student_name
        result.student_id = resolved.student_id

        # 身份变化且有报告 → 重渲染(使用默认展示名兑底,不影响已保存的教师编辑版)
        if changed and result.markdown_report:
            finalize_result(
                result=result,
                pipeline_choice=PipelineChoice(task.pipeline_used or task.pipeline_choice),
                pipeline_display_name="",
                standard=GradingStandard(task.grading_standard),
                detail=DetailLevel(task.detail_level),
            )

    # ---------------------------------------------------------
    # 任务更新工具
    # ---------------------------------------------------------
    async def _update_task(self, task_id: int, **fields) -> None:
        """用独立的短会话更新任务字段(避免长事务,降低 SQLite 锁冲突)"""
        async with self._session_factory() as session:
            task = await session.get(CorrectionTask, task_id)
            if task is None:
                return
            for key, value in fields.items():
                setattr(task, key, value)
            await session.commit()

    # ---------------------------------------------------------
    # 供 API 复用的辅助逻辑
    # ---------------------------------------------------------
    @staticmethod
    def resolve_image_paths(relative_paths: list[str], upload_dir: Path) -> list[Path]:
        """相对路径列表 -> 绝对路径列表(过滤不存在的文件)"""
        paths = []
        for rel in relative_paths:
            p = upload_dir / rel
            if p.exists():
                paths.append(p)
            else:
                logger.warning("任务图片不存在,已跳过:%s", p)
        return paths
