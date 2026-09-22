# 架构细节与扩展指南

## 1. 双管线架构总览

```
[ 前端上传(批量图片/ZIP) ] → [ 图片预处理:EXIF/增强/裁边 ]
        │
[ 策略工厂 get_pipeline(config) ]
        │
  ┌─────┴──────────────────────┐
  ▼                            ▼
管线 A: LocalVLMPipeline      管线 B: DecoupledCloudPipeline
 默认单次:图 → 全字段 JSON     OCR(转录)→ [人工复核] → LLM(评分)
 (开启复核时:两管线行为一致——识别 → [人工复核] → 评分,仅后端模型不同)
  └─────┬──────────────────────┘
        ▼
[ 统一 Schema: EssayCorrectionResult ]
        │
[ 错因标准化 annotate_errors → canonical_type ]
        │
[ report_renderer 确定性渲染(教师版 + 学生版订正单) ]
        │
[ SQLite 落库:结果 JSON + error_records(按 canonical_type) ]
        │
   ┌────┴─────────────────────┐
   ▼                          ▼
[ analytics_service 班级诊断 ] [ analytics_service 学生画像 ]
 (讲评摘要/分布/典型错例)      (复现错因/时间线/个体分布)
```

## 2. 策略模式代码契约

```python
# app/pipelines/base.py
class AbstractCorrectionPipeline(ABC):
    choice: PipelineChoice

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome: ...
```

- `CorrectionContext`:task_id / image_paths / config / ocr_result / on_progress
- `CorrectionOutcome`:waiting_review / completed_result / ocr_result / pipeline_display_name
- 异常体系:`PipelineError` → `PipelineNetworkError`(触发 A→B 转移)/ `PipelineParseError` / `PipelineConfigError`

### 两管线的两阶段与人工复核(开启复核时行为完全一致)

1. 若 `ctx.ocr_result` 有值(教师已复核)→ 跳过 OCR,直接评分;
2. 否则先 `OcrClient.extract()` 转录;
3. 若 `require_ocr_review=True` → 返回 `waiting_review=True`,任务置 `WAITING_REVIEW`,等待
   `POST /api/tasks/{id}/ocr-confirm`;
4. 评分阶段调用 DeepSeek 文本 LLM(含一次"修复重试")。

## 3. 错因分类标准化(#1,教学分析的地基)

```
LLM 自由文本 error_type("Word Order" / "语序错误" / "Satzbau")
        │  normalize_error_type()
        ▼
四级匹配:英文键名 → 中文标签 → 别名表(中/英/德) → 长别名子串扫描 → 兜底 OTHER
        │
        ▼
ErrorCategory(19 类固定考点:VERB_POSITION / CASE_DECLENSION / PREPOSITION …)
```

- `annotate_errors()` 在 `finalize_result()` 中自动调用,填充 `ErrorItem.canonical_type` 并落库到 `error_records.canonical_type`;
- **所有分析统计一律按 canonical_type 聚合**,`error_type` 仅作原文展示;
- `CATEGORY_TEACHING_TIPS`:分类 → 练习建议,供讲评摘要与学生版报告复用。

## 4. 教学分析聚合(#2 / #3)

`services/analytics_service.py`(纯函数 + AsyncSession):

| 函数 | 输入 | 输出 |
|---|---|---|
| `build_class_diagnosis` | class_id / batch_id / 日期范围 | ClassDiagnosis(统计卡片、得分分布、category_stats + 典型错例、**讲评摘要 Markdown**) |
| `build_student_profile` | student_id 或 name | StudentProfile(平均分、category_stats、**复现错因**、时间线) |

- 得分解析:正则提取 `"18 / 25"` 数值得分(同基准取原值平均,混合基准换算百分制);CEFR 等级(`"B1 Pass"`)单独做等级分布;
- 复现错因:同一考点出现在 ≥2 个不同任务;
- 讲评摘要由后端确定性渲染(`_render_teaching_summary`),含"高频错因 Top N + 典型错例 + 讲评建议顺序 + 得分分布"。

## 5. 统一 Schema(EssayCorrectionResult)

| 字段 | 类型 | 说明 |
|---|---|---|
| student_name | str | 学生姓名(失败时 "未知") |
| student_id | str? | 学号 |
| transcribed_text | str | 手写作文完整转录 |
| overall_score | str | "18 / 25" 或 "B1 Pass" |
| overall_comment | str | 总体评价 |
| errors | ErrorItem[] | original_text / corrected_text / error_type / **canonical_type** / explanation? |
| highlights | str[] | 词汇句型亮点 |
| markdown_report | str | 后端渲染的完整 Markdown(双管线结构一致) |

## 6. Markdown 报告模板

**教师版**(`render_markdown_report`):

```
# 德语作文批改报告 - {姓名} / {学号}
**运行管线:** … | **考试标准:** … | **细致度:** …
**综合得分:** 18 / 25
---
### 📝 总体评价
### 🔍 详细批改      (原句 ❌ / 修改 ✅ / 错因解析(LOW/MEDIUM/HIGH 三档裁剪))
### 📊 词汇与句型亮点/短板   (薄弱点按 canonical_type 频次统计)
```

**学生版**(`render_student_report`,#11):"订正单"结构 —— 订正清单(原句→修正→小提示)、
做得好的地方、**下一步练习重点**(基于错因类别的练习建议)、教师寄语;弱化分数。
由 `TaskDetail.student_report` 按需渲染(不落库,始终与教师版同源)。

## 7. 任务状态机

```
PENDING → PROCESSING(stage: OCR → GRADING → RENDERING) → COMPLETED
                │                                   └→ FAILED(可 retry)
                └→ WAITING_REVIEW(仅管线B+复核开关)→ ocr-confirm → 继续评分
```

- 启动时**孤儿任务自愈**:残留 `PROCESSING` → 重置 `PENDING` 并重新入队(`recover_orphaned_tasks`);
- `retry` 允许状态:FAILED / WAITING_REVIEW / PROCESSING;
- `pipeline_choice`(教师所选)vs `pipeline_used`(实际执行);`fallback_triggered` 记录 A→B 转移。

## 8. 数据库表与迁移

| 表 | 用途 |
|---|---|
| correction_tasks | 主表:配置、状态、图片、OCR 中间结果、结果 JSON、**class_id / assignment_name / topic** |
| classes | 班级实体(name 唯一 + note) |
| students | 学生档案(批改自动 upsert) |
| error_records | 错题记录(**canonical_type** 标准化列 + teacher 统计维度) |

**Alembic 迁移机制**(`db/database.py::_run_migrations_sync`):
- 全新库 → `upgrade head` 直接建表;
- 存量库(有业务表无 `alembic_version`)→ 先 `stamp` 基线版本再升级,数据保留;
- `migrations/versions/` 已有三个版本:baseline → error canonical taxonomy → classes & task metadata;
- 所有 ALTER 使用 `render_as_batch=True`(SQLite 兼容);新增非空列必须带 `server_default`。

## 9. 扩展步骤(完整版)

### 新增一条管线

1. `schemas.py`:`PipelineChoice` 枚举加值;
2. `app/pipelines/` 新建子类,返回前调用 `finalize_result()`(自动完成错因标准化);
3. `factory.py` 注册;4. `config.py` + `.env.example` 补配置;
5. 前端 `types/index.ts::PIPELINE_META` 与选择器加选项;6. 补测试。

### 新增 OCR 提供者

1. `ocr_client.py` 仿 `_extract_azure()` 增加适配器,输出统一 `OcrExtractionResult`;
2. `config.ocr_provider` Literal 加值;3. `OcrClient.extract()` 分发接入。

### 新增考点分类 / 评分标准

- 考点:`error_taxonomy.ErrorCategory` 加成员 + `CATEGORY_LABELS` + `_register` 别名 + `CATEGORY_TEACHING_TIPS`;
- 标准:`GradingStandard` 加值 + `prompts._grading_standard_text()` 分支 + `report_renderer._STANDARD_LABELS` + 前端选项。

### 数据模型变更(必须走迁移)

1. 修改 `db_models.py`;2. `alembic revision --autogenerate -m "描述"`;
3. 检查生成文件(非空列补 `server_default`;新外键显式命名);4. 重启应用自动升级;5. 补测试。

## 10. 关键设计决策记录

| 决策 | 原因 |
|---|---|
| markdown_report 后端渲染,LLM 只出 JSON | 双管线输出绝对一致 |
| 错因落库前必须标准化 | 分析统计的可靠性前提;别名表兼容历史数据 |
| 学生版报告按需渲染不落库 | 始终与教师版同源,避免两份数据漂移 |
| 分析统计在 Python 层聚合 | 单机数据量级小,清晰优先;SQL 聚合复杂度高 |
| 进程内 asyncio 队列而非 Celery | Windows 兼容性;`queue_service.py` 保留扩展点 |
| 故障转移仅限网络/超时错误 | Parse 错误换管线同样失败,由修复重试处理 |
| 迁移启动时自动执行 + 存量库 stamp | 非技术用户零操作升级,数据不丢 |
| 图片预处理"宁少勿错" | 裁边/增强失败自动回退原图,绝不阻断上传 |
