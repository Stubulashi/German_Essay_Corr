"""示例数据服务(维护动作:一键清除所有数据 / 一键恢复所有示例数据)

职责:
- clear_all_business_data:清空全部业务数据(批改任务与错因、学生、班级与花名册、
  考试与报告、台账、练习卷、示范学习画像、手写样本、合并日志)与 uploads 下全部
  存档文件;保留 app_settings(设置中心配置、提示词附录、加密密钥包裹、大模型
  自动适配记录等系统级元数据),保证"数据加密/设置中心"等系统能力继续可用;
- seed_demo_data:先执行同一清空流程,再写入一整套完整、可直接演示的示例数据
  (2 个示例班级 + 花名册 + 批改任务与报告/错因 + 1 个待复核任务 + 1 场考试与
  报告 + 台账登记项与记录 + 练习卷 + 示范学习画像 + 示例答卷图片)。

设计约定:
- 复用既有契约与渲染链路(finalize_result / generate_exam_report /
  ledger_service.create_presets / practice_service.render_markdown /
  style_learning_service 的样例画像),示例数据与真实数据同构,前端零适配;
- 全部确定性数据(固定名单/分数/错因),重复执行结果一致(先清空后重建);
- 图片为本地合成的示意答卷(不触任何外部服务),便于离线演示"审阅分屏";
- 破坏性动作由路由层要求 confirm=true,审计与日志在路由层统一写入。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    ClassMergeLog,
    ClassRoster,
    CorrectionTask,
    ErrorRecord,
    Exam,
    ExamPaper,
    ExamReport,
    HandwritingModel,
    HandwritingSample,
    HomeworkItem,
    HomeworkRecord,
    PracticeSheet,
    SchoolClass,
    Student,
    StyleProfile,
)
from app.models.schemas import (
    DetailLevel,
    ErrorItem,
    EssayCorrectionResult,
    GradingStandard,
    OcrExtractionResult,
    PipelineChoice,
)
from app.services import ledger_service, style_learning_service
from app.services.error_taxonomy import canonical_label
from app.services.exam_service import _mock_exam_payload, generate_exam_report
from app.services.practice_service import render_markdown
from app.services.report_renderer import finalize_result
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)

#: 全部业务表(清空顺序:子表在前;键名为对外统计口径;app_settings 不在其中 —— 系统配置与密钥必须保留)
CLEAR_ORDER: tuple[tuple[str, type], ...] = (
    ("errors", ErrorRecord),
    ("tasks", CorrectionTask),
    ("exam_reports", ExamReport),
    ("exam_papers", ExamPaper),
    ("exams", Exam),
    ("ledger_records", HomeworkRecord),
    ("ledger_items", HomeworkItem),
    ("practice_sheets", PracticeSheet),
    ("handwriting_samples", HandwritingSample),
    ("handwriting_models", HandwritingModel),
    ("style_profiles", StyleProfile),
    ("roster", ClassRoster),
    ("merge_logs", ClassMergeLog),
    ("students", Student),
    ("classes", SchoolClass),
)

#: 示例班级(名称, 备注)
DEMO_CLASSES: tuple[tuple[str, str], ...] = (
    ("高二(3)班 · 德语", "示例班级 · 德语选修(演示批改 / 台账 / 考试全链路)"),
    ("高二(4)班 · 德语", "示例班级 · 德语必修"),
)

#: 示例花名册:({班级下标: (姓名, 学号, 图像用拉丁转写)})
DEMO_ROSTERS: tuple[tuple[tuple[str, str, str], ...], ...] = (
    (
        ("李明", "20260123", "Li Ming"),
        ("王芳", "20260124", "Wang Fang"),
        ("张伟", "20260125", "Zhang Wei"),
        ("刘洋", "20260126", "Liu Yang"),
        ("陈静", "20260127", "Chen Jing"),
        ("赵强", "20260128", "Zhao Qiang"),
    ),
    (
        ("孙悦", "20260211", "Sun Yue"),
        ("周杰", "20260212", "Zhou Jie"),
        ("吴敏", "20260213", "Wu Min"),
        ("郑浩", "20260214", "Zheng Hao"),
        ("何雨", "20260215", "He Yu"),
        ("许佳", "20260216", "Xu Jia"),
    ),
)

#: 示例作文(与演示模式(mock)样例同一篇,保证"演示模式"与"示例数据"观感一致)
_TRANSCRIPT_A = (
    "Meine Sommerferien\n\n"
    "In den Sommerferien habe ich mit meine Familie nach Beijing gefahren. "
    "Wir haben viele Sehenswürdigkeiten besucht, zum Beispiel die Große Mauer. "
    "Ich denke, dass die Reise war sehr interessant. "
    "Obwohl das Wetter war heiß, wir hatten viel Spaß. "
    "Nächste Jahr möchte ich wieder dorthin fahren."
)
_TRANSCRIPT_B = (
    "Meine Lieblingsstadt\n\n"
    "Meine Lieblingsstadt ist Heidelberg. Ich habe dort zwei Wochen gelebt, "
    "als ich in der zehnten Klasse war. Die Altstadt liegt am Fluss, und die Burg ist sehr berühmt. "
    "Weil ich mag die ruhige Atmosphäre, möchte ich dort später studieren. "
    "Ich glaube, dass Heidelberg ist eine der schönsten Städte in Deutschland."
)

#: 示例作业(名称, 题目, 评分标准, 细致度, 转录文本, 得分档, 评语)
DEMO_ASSIGNMENTS: tuple[dict, ...] = (
    {
        "name": "示例 · 第一次月考作文",
        "batch_id": "demo-monthly-1",
        "topic": "Meine Sommerferien(我的暑假)",
        "standard": GradingStandard.GAOKAO,
        "detail": DetailLevel.HIGH,
        "transcript": _TRANSCRIPT_A,
        "scores": ("18 / 25", "21 / 25", "15 / 25", "23 / 25", "17 / 25", "20 / 25"),
        "comment": (
            "作文内容切题、叙事完整,能够使用从句与让步结构,值得肯定。"
            "主要扣分点集中在:介词后变格词尾、dass/obwohl 从句的动词末位语序与形容词词尾。"
            "建议专项复习框型结构与名词变格表。"
        ),
    },
    {
        "name": "示例 · 期中测评作文",
        "batch_id": "demo-midterm-1",
        "topic": "Meine Lieblingsstadt(我最喜欢的城市)",
        "standard": GradingStandard.DSD,
        "detail": DetailLevel.MEDIUM,
        "transcript": _TRANSCRIPT_B,
        "scores": ("B1 Pass", "B1 Pass", "A2 Pass", "B2 Pass", "A2 Pass", "B1 Pass"),
        "comment": (
            "整体达到 CEFR B1 水平:能就熟悉话题进行连贯描述并给出个人观点,段落结构清晰。"
            "从句语序与变格准确率仍有提升空间,建议加强 Satzklammer 与 Dativ 的针对性练习。"
        ),
    },
)

#: 示例错因池(原文, 修正, 错因标签, 中文解析)
_DEMO_ERROR_POOL: tuple[tuple[str, str, str, str], ...] = (
    ("mit meine Familie", "mit meiner Familie", "名词变格", "介词 mit 要求第三格(Dativ):meine → meiner。"),
    (
        "dass die Reise war sehr interessant",
        "dass die Reise sehr interessant war",
        "动词位序",
        "dass 引导的从句中变位动词必须放在句末(动词末位规则)。",
    ),
    (
        "Obwohl das Wetter war heiß, wir hatten viel Spaß",
        "Obwohl das Wetter heiß war, hatten wir viel Spaß",
        "动词位序",
        "obwohl 从句动词置于句末;主句因从句前置而采用倒装。",
    ),
    ("Nächste Jahr", "Nächstes Jahr", "形容词词尾", "Jahr 为中性名词(das Jahr),形容词词尾应为 -es。"),
    (
        "Weil ich mag die ruhige Atmosphäre",
        "Weil ich die ruhige Atmosphäre mag",
        "动词位序",
        "weil 引导的原因从句中变位动词位于句末。",
    ),
    ("Ich warte für den Bus", "Ich warte auf den Bus", "介词搭配", "固定搭配 warten auf + 第四格。"),
    ("Ich habe keine Zeit genug", "Ich habe nicht genug Zeit", "否定", "否定词 nicht 应置于程度副词 genug 之前。"),
)

#: 示例亮点池(按学生下标轮转取用)
_DEMO_HIGHLIGHTS: tuple[str, ...] = (
    "zum Beispiel —— 举例衔接词使用自然",
    "Ich denke, dass ... —— 表达了个人观点,句式意识良好",
    "Obwohl ... —— 敢于使用让步从句,篇章逻辑有层次",
    "liegt am Fluss —— 动词搭配准确,表达地道",
    "einer der schönsten Städte —— 最高级结构使用正确",
)

#: 示例台账登记日期(相对今天的天数偏移)
_DEMO_LEDGER_DAY_OFFSETS: tuple[int, ...] = (14, 7, 2)

#: 各计分模式的示例值表(按 学生 + 登记项 + 日期 轮转取用)
_DEMO_LEDGER_VALUES: dict[str, tuple[str, ...]] = {
    "LEVEL": ("A", "A", "B", "B", "C", "A"),
    "SCORE": ("95", "85", "90", "75", "90", "80"),
    "FLAG": ("done", "done", "late", "missing", "done", "done"),
    "STARS": ("5", "4", "4", "3", "5", "4"),
}

#: 示例练习卷题目(与错因画像呼应:从句语序 / 名词变格 / 介词搭配)
_DEMO_PRACTICE_QUESTIONS: tuple[dict, ...] = (
    {
        "type": "grammar",
        "no": "1",
        "stem": "Nachdem er ___ (aufstehen), frühstückt er jeden Morgen.",
        "answer": "aufgestanden war",
        "explanation": "Nachdem 从句表示先发生的动作,使用过去完成时;可分动词过去分词 aufgestanden 与 war 构成框型结构。",
    },
    {
        "type": "grammar",
        "no": "2",
        "stem": "Ich helfe ___ (der Freund), weil er mir oft hilft.",
        "answer": "dem Freund",
        "explanation": "helfen 支配第三格(Dativ):der Freund → dem Freund。",
    },
    {
        "type": "vocabulary",
        "no": "3",
        "stem": "Er hat sich ___ die Prüfung vorbereitet. (auf / für / über)",
        "answer": "auf",
        "explanation": "固定搭配:sich auf eine Prüfung vorbereiten(为考试做准备)。",
    },
    {
        "type": "correction",
        "no": "4",
        "stem": "Obwohl er müde war, er arbeitete weiter.",
        "answer": "Obwohl er müde war, arbeitete er weiter.",
        "explanation": "从句前置时主句倒装:变位动词 arbeitete 应紧跟从句之后。",
    },
    {
        "type": "correction",
        "no": "5",
        "stem": "Ich habe mehr Zeit gebraucht, als ich erwartet habe.(判断对错,如有误请改正)",
        "answer": "正确(无需修改)",
        "explanation": "als 比较从句中动词居末,原句语序正确。",
    },
    {
        "type": "translation",
        "no": "6",
        "stem": "中译德:尽管下雨了,我们还是去公园散步了。",
        "answer": "Obwohl es geregnet hat, sind wir trotzdem im Park spazieren gegangen.",
        "explanation": "让步从句 obwohl 引导 + 主句倒装;trotzdem 强化转折语义。",
    },
    {
        "type": "cloze",
        "no": "7",
        "stem": "Wenn ich mehr Zeit ___, würde ich jeden Tag Deutsch lesen. (habe / hätte / hatte)",
        "answer": "hätte",
        "explanation": "非真实条件句用第二虚拟式:主句 würde + 条件从句 hätte。",
    },
    {
        "type": "writing",
        "no": "8",
        "stem": "Schreibe 3 Sätze über deinen letzten Ausflug(用 Perfekt 描述你最近一次出游)。",
        "answer": (
            "示例:Letztes Wochenende bin ich mit meiner Familie ins Gebirge gefahren. "
            "Wir haben den ganzen Tag gewandert und viele Fotos gemacht. "
            "Am Abend waren wir sehr müde, aber glücklich."
        ),
        "explanation": "重点检查 Perfekt 的框型结构(haben/sein + 过去分词)与时间状语的一致性。",
    },
)


# ---------------------------------------------------------
# 一键清除所有数据
# ---------------------------------------------------------
async def clear_all_business_data(db: AsyncSession, upload_dir: Path) -> dict[str, int]:
    """清空全部业务数据与 uploads 存档文件(保留 app_settings;返回各项删除数量)

    说明:
    - 表清理在单事务内完成(失败自动回滚,不会留下半清空状态);
    - 文件清理在事务外先统计后删除,失败仅告警(不阻断);
    - 结束后刷新生效风格内存缓存(避免批改继续注入已删除画像)。
    """
    counts: dict[str, int] = {}
    for name, model in CLEAR_ORDER:
        result = await db.execute(delete(model))
        counts[name] = result.rowcount or 0
    await db.commit()
    # 示范学习内存缓存同步失效(数据库已无生效画像)
    await style_learning_service.reload_cache(db)
    files, freed = await asyncio.to_thread(purge_upload_files, upload_dir)
    counts["files"] = files
    counts["bytes"] = freed
    logger.info("全部数据已清除:%s", {key: value for key, value in counts.items() if value})
    return counts


def purge_upload_files(upload_dir: Path) -> tuple[int, int]:
    """删除 uploads 目录下的全部文件与子目录,返回 (删除文件数, 释放字节数)"""
    root = Path(upload_dir)
    if not root.exists():
        return 0, 0
    files = 0
    freed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            freed += path.stat().st_size
            path.unlink()
            files += 1
        except OSError as error:
            logger.warning("删除文件失败(已跳过):%s(%s)", path, error)
    for sub in sorted(root.iterdir()):
        if sub.is_dir():
            shutil.rmtree(sub, ignore_errors=True)
    return files, freed


# ---------------------------------------------------------
# 一键恢复所有示例数据
# ---------------------------------------------------------
async def seed_demo_data(db: AsyncSession, upload_dir: Path) -> dict[str, int]:
    """先清空全部业务数据,再写入一整套示例数据(返回写入数量与图片统计)"""
    await clear_all_business_data(db, upload_dir)
    counts = {
        "classes": 0, "roster": 0, "students": 0, "tasks": 0, "errors": 0,
        "exams": 0, "exam_papers": 0, "ledger_items": 0, "ledger_records": 0,
        "practice_sheets": 0, "style_profiles": 0, "files": 0, "bytes": 0,
    }

    # ---------- 1. 班级与花名册 ----------
    classes: list[SchoolClass] = []
    for name, note in DEMO_CLASSES:
        cls = SchoolClass(name=name, note=note)
        db.add(cls)
        classes.append(cls)
    await db.flush()
    for class_idx, cls in enumerate(classes):
        for name, student_id, _latin in DEMO_ROSTERS[class_idx]:
            db.add(ClassRoster(class_id=cls.id, name=name, student_id=student_id))
            counts["roster"] += 1
    counts["classes"] = len(classes)

    # ---------- 2. 学生档案(与批改收尾同一 upsert 规则) ----------
    for class_idx, cls in enumerate(classes):
        for name, student_id, _latin in DEMO_ROSTERS[class_idx]:
            student = await upsert_student(db, name, student_id)
            student.class_name = cls.name
            counts["students"] += 1
    await db.commit()

    # ---------- 3. 示例答卷图片(本地合成,不触外部服务) ----------
    upload_root = Path(upload_dir)
    upload_root.mkdir(parents=True, exist_ok=True)
    images: dict[tuple[int, int, int], str] = {}
    page_index = 0
    for class_idx, _cls in enumerate(classes):
        for assignment_idx, assignment in enumerate(DEMO_ASSIGNMENTS):
            for student_idx, (_name, _sid, latin) in enumerate(DEMO_ROSTERS[class_idx]):
                page_index += 1
                rel = await asyncio.to_thread(
                    _render_demo_sheet,
                    upload_root,
                    page_index,
                    latin,
                    assignment["transcript"],
                )
                images[(class_idx, assignment_idx, student_idx)] = rel
                counts["files"] += 1
    page_index += 1
    review_rel = await asyncio.to_thread(
        _render_demo_sheet,
        upload_root,
        page_index,
        DEMO_ROSTERS[0][1][2],
        _TRANSCRIPT_A,
    )
    counts["files"] += 1

    # ---------- 4. 批改任务(已完成 + 报告/错因;含 1 个待人工复核) ----------
    now = datetime.now(timezone.utc)
    pending_errors: list[tuple[CorrectionTask, EssayCorrectionResult]] = []
    for class_idx, cls in enumerate(classes):
        for assignment_idx, assignment in enumerate(DEMO_ASSIGNMENTS):
            for student_idx, (name, student_id, _latin) in enumerate(DEMO_ROSTERS[class_idx]):
                result = _build_demo_result(class_idx, assignment_idx, student_idx, assignment)
                pipeline = (
                    PipelineChoice.PIPELINE_B_CLOUD
                    if class_idx == 1 and assignment_idx == 1
                    else PipelineChoice.PIPELINE_A_LOCAL
                )
                created_at = (
                    now
                    - timedelta(days=21 if assignment_idx == 0 else 7)
                    + timedelta(hours=(class_idx + student_idx) * 3 + assignment_idx)
                )
                task = CorrectionTask(
                    batch_id=f"{assignment['batch_id']}-{class_idx + 1}",
                    class_id=cls.id,
                    assignment_name=assignment["name"],
                    topic=assignment["topic"],
                    student_name=name,
                    student_id=student_id,
                    pipeline_choice=pipeline.value,
                    pipeline_used=pipeline.value,
                    grading_standard=assignment["standard"].value,
                    detail_level=assignment["detail"].value,
                    require_ocr_review=0,
                    status="COMPLETED",
                    stage="DONE",
                    progress=1.0,
                    image_paths=[images[(class_idx, assignment_idx, student_idx)]],
                    ocr_result=OcrExtractionResult(
                        student_name=name,
                        student_id=student_id,
                        transcribed_text=assignment["transcript"],
                        recognition_quality="high",
                    ).model_dump(),
                    result=result.model_dump(),
                    created_at=created_at,
                    updated_at=created_at + timedelta(minutes=6),
                )
                db.add(task)
                pending_errors.append((task, result))
                counts["tasks"] += 1

    # 待人工复核任务(演示"识别完成 → 教师确认"的复核流程;不自动评分)
    review_name, review_id, _latin = DEMO_ROSTERS[0][1]
    review_task = CorrectionTask(
        batch_id="demo-review-1",
        class_id=classes[0].id,
        assignment_name="示例 · 待复核抽查",
        topic="Meine Sommerferien(我的暑假)",
        student_name=review_name,
        student_id=review_id,
        pipeline_choice=PipelineChoice.PIPELINE_B_CLOUD.value,
        pipeline_used=PipelineChoice.PIPELINE_B_CLOUD.value,
        grading_standard=GradingStandard.GAOKAO.value,
        detail_level=DetailLevel.MEDIUM.value,
        require_ocr_review=1,
        status="WAITING_REVIEW",
        stage="OCR",
        progress=0.5,
        image_paths=[review_rel],
        ocr_result=OcrExtractionResult(
            student_name=review_name,
            student_id=review_id,
            transcribed_text=_TRANSCRIPT_A,
            recognition_quality="medium",
            quality_note="示例数据:该任务停在人工复核阶段,供演示复核流程。",
        ).model_dump(),
        created_at=now - timedelta(hours=5),
        updated_at=now - timedelta(hours=5),
    )
    db.add(review_task)
    await db.flush()

    for task, result in pending_errors:
        for error in result.errors:
            db.add(
                ErrorRecord(
                    task_id=task.id,
                    student_name=result.student_name,
                    student_id=result.student_id,
                    error_type=error.error_type,
                    canonical_type=error.canonical_type or "OTHER",
                    original_text=error.original_text,
                    corrected_text=error.corrected_text,
                )
            )
            counts["errors"] += 1
    await db.commit()

    # ---------- 5. 考试与报告(复用考试统计的确定性载荷与报告生成) ----------
    exam = Exam(
        class_id=classes[0].id,
        name="示例 · 期中考试(德语)",
        exam_date=(now - timedelta(days=10)).date(),
        subject="德语",
        full_score=100.0,
        exam_type="期中",
        note="示例考试数据(听力不计入统计)",
        status="DRAFT",
    )
    db.add(exam)
    await db.flush()
    papers: list[ExamPaper] = []
    for name, student_id, _latin in DEMO_ROSTERS[0]:
        paper = ExamPaper(
            exam_id=exam.id,
            student_name=name,
            student_id=student_id,
            image_paths=[],
            ocr_status="DONE",
            teacher_edited=0,
        )
        db.add(paper)
        papers.append(paper)
    await db.flush()
    for paper in papers:
        payload = _mock_exam_payload(paper.id, exam.id)
        paper.total_score = payload["total_score"]
        paper.question_results = payload["questions"]
    await db.commit()
    counts["exams"] = 1
    counts["exam_papers"] = len(papers)
    await generate_exam_report(db, exam.id)  # 持久化报告并把考试置为 REPORTED

    # ---------- 6. 台账(预设登记项 + 三轮登记记录) ----------
    items = await ledger_service.create_presets(db, class_id=classes[0].id)
    counts["ledger_items"] = len(items)
    ledger_days = [(now - timedelta(days=offset)).date() for offset in _DEMO_LEDGER_DAY_OFFSETS]
    for item_idx, item in enumerate(items):
        values = _DEMO_LEDGER_VALUES.get(item.scoring_mode, _DEMO_LEDGER_VALUES["LEVEL"])
        for date_idx, record_date in enumerate(ledger_days):
            for student_idx, (name, student_id, _latin) in enumerate(DEMO_ROSTERS[0]):
                value = values[(student_idx + item_idx + date_idx) % len(values)]
                score = ledger_service.normalize_score_value(
                    item.scoring_mode, item.config or {}, value
                )
                if score is None:
                    continue
                db.add(
                    HomeworkRecord(
                        item_id=item.id,
                        class_id=classes[0].id,
                        student_name=name,
                        student_id=student_id,
                        value=value,
                        score_value=score,
                        record_date=record_date,
                    )
                )
                counts["ledger_records"] += 1
    await db.commit()

    # ---------- 7. 练习卷(与示例错因画像同源) ----------
    distribution = await _category_distribution(
        db,
        [task.id for task, _result in pending_errors if task.class_id == classes[0].id],
    )
    questions = [dict(question) for question in _DEMO_PRACTICE_QUESTIONS]
    title = "示例练习卷 · 从句语序与名词变格专项"
    db.add(
        PracticeSheet(
            class_id=classes[0].id,
            title=title,
            source={
                "scope": "selected",
                "assignments": [
                    {"class_id": classes[0].id, "name": assignment["name"]}
                    for assignment in DEMO_ASSIGNMENTS
                ],
                "task_count": len(DEMO_ROSTERS[0]) * len(DEMO_ASSIGNMENTS),
                "category_distribution": distribution,
                "topics": [assignment["topic"] for assignment in DEMO_ASSIGNMENTS],
            },
            params={
                "question_types": sorted({question["type"] for question in questions}),
                "count": len(questions),
            },
            content={"questions": questions},
            worksheet_markdown=render_markdown(title, questions, answers=False),
            answer_markdown=render_markdown(title, questions, answers=True),
            model="demo",
        )
    )
    counts["practice_sheets"] = 1

    # ---------- 8. 示范学习画像(复用 mock 样例,激活以便直接演示风格注入) ----------
    style_payload = style_learning_service._mock_style_payload()  # noqa: SLF001 —— 同项目内复用样例口径
    first_task = pending_errors[0][0]
    db.add(
        StyleProfile(
            name="示例批改风格(演示)",
            source_task_id=first_task.id,
            source_student_name=first_task.student_name,
            style_json=style_payload["style"],
            narrative=style_payload["narrative"],
            status="active",
        )
    )
    counts["style_profiles"] = 1
    await db.commit()
    # 生效风格内存缓存同步刷新(否则要等重启才注入 Prompt)
    await style_learning_service.reload_cache(db)

    counts["bytes"] = sum(
        path.stat().st_size for path in upload_root.rglob("*") if path.is_file()
    )
    logger.info("示例数据已写入:%s", {key: value for key, value in counts.items() if value})
    return counts


# ---------------------------------------------------------
# 内部工具
# ---------------------------------------------------------
def _build_demo_result(
    class_idx: int, assignment_idx: int, student_idx: int, assignment: dict
) -> EssayCorrectionResult:
    """构造一位学生的一次批改结果(确定性;并完成报告渲染与错因标准化)"""
    errors = _demo_errors_for(class_idx, assignment_idx, student_idx, assignment["detail"])
    scores = assignment["scores"]
    result = EssayCorrectionResult(
        student_name=DEMO_ROSTERS[class_idx][student_idx][0],
        student_id=DEMO_ROSTERS[class_idx][student_idx][1],
        transcribed_text=assignment["transcript"],
        overall_score=scores[(student_idx + class_idx + assignment_idx) % len(scores)],
        overall_comment=assignment["comment"],
        errors=errors,
        highlights=[
            _DEMO_HIGHLIGHTS[(student_idx + class_idx) % len(_DEMO_HIGHLIGHTS)],
            _DEMO_HIGHLIGHTS[(student_idx + class_idx + 2) % len(_DEMO_HIGHLIGHTS)],
        ],
    )
    finalize_result(
        result=result,
        pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
        pipeline_display_name="本地 VLM(示例数据)",
        standard=assignment["standard"],
        detail=assignment["detail"],
    )
    return result


def _demo_errors_for(
    class_idx: int, assignment_idx: int, student_idx: int, detail: DetailLevel
) -> list[ErrorItem]:
    """从示例错因池轮转取 3~5 条(细致度 LOW/MEDIUM 时省略中文解析)"""
    total = len(_DEMO_ERROR_POOL)
    count = 3 + (student_idx + class_idx) % 3
    start = (student_idx * 2 + assignment_idx + class_idx) % total
    errors: list[ErrorItem] = []
    for step in range(count):
        original, corrected, error_type, explanation = _DEMO_ERROR_POOL[(start + step) % total]
        errors.append(
            ErrorItem(
                original_text=original,
                corrected_text=corrected,
                error_type=error_type,
                explanation=explanation if detail == DetailLevel.HIGH else None,
            )
        )
    return errors


async def _category_distribution(db: AsyncSession, task_ids: list[int]) -> list[dict]:
    """按标准化错因聚合示例任务的错因分布(供练习卷 source 溯源)"""
    if not task_ids:
        return []
    rows = (
        await db.execute(
            select(ErrorRecord.canonical_type, func.count())
            .where(ErrorRecord.task_id.in_(task_ids))
            .group_by(ErrorRecord.canonical_type)
        )
    ).all()
    return [
        {"label": canonical_label(canonical_type), "count": count}
        for canonical_type, count in sorted(rows, key=lambda row: -row[1])
    ]


def _render_demo_sheet(
    upload_root: Path, index: int, latin_name: str, transcript: str
) -> str:
    """合成一张示意答卷图片(白底 + 德语文本),返回相对 uploads 的路径

    说明:图像仅用于演示"审阅分屏"的观感,不参与 OCR;文本一律使用拉丁字符
    (默认字体不支持中文,中文作业名不进入图像)。
    """
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1240, 1754  # A4 @150dpi
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=34)
    body_font = ImageFont.load_default(size=26)
    small_font = ImageFont.load_default(size=20)

    draw.text((90, 90), "Deutsch · Aufsatz", font=title_font, fill=(30, 30, 30))
    draw.text((90, 140), f"Name: {latin_name}", font=body_font, fill=(60, 60, 80))
    draw.text((90, 180), "(Beispielseite · 示例数据)", font=small_font, fill=(150, 150, 150))
    draw.line((90, 220, width - 90, 220), fill=(180, 180, 180), width=2)

    y = 260
    for line in _wrap_text(transcript, 52):
        draw.text((90, y), line, font=body_font, fill=(40, 40, 60))
        y += 42
    for line_y in range(y + 20, height - 120, 46):
        draw.line((90, line_y, width - 90, line_y), fill=(225, 228, 232), width=1)

    subdir = upload_root / f"task_demo_{index:02d}"
    subdir.mkdir(parents=True, exist_ok=True)
    target = subdir / "page_001.jpg"
    image.save(target, "JPEG", quality=85, optimize=True)
    return f"task_demo_{index:02d}/page_001.jpg"


def _wrap_text(text: str, per_line: int) -> list[str]:
    """按词切分长文本(避免右侧溢出);显式换行段落保留为空行"""
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > per_line and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines
