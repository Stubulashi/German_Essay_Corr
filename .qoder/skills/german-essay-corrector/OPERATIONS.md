# 配置项、API 与操作手册

## 1. .env 配置表(backend/.env)

| 键 | 默认值 | 说明 |
|---|---|---|
| `DEFAULT_PIPELINE` | PIPELINE_A_LOCAL | 默认管线(A=本地 VLM,B=云端解耦) |
| `ALLOW_AUTO_FALLBACK` | true | A 网络失败时是否自动切 B |
| `MOCK_MODE` | false | true=演示模式,不调用真实模型 |
| `LOCAL_VLM_BASE_URL` | http://127.0.0.1:8000/v1 | 本地 vLLM(默认 8000)/ Ollama(11434)端点 |
| `LOCAL_VLM_API_KEY` | sk-local | 本地端点占位密钥 |
| `LOCAL_VLM_MODEL` | /models/Qwen3.8-27B-Q4_K_M | 本地模型名 |
| `LOCAL_VLM_TIMEOUT` | 600 | 本地推理超时(秒) |
| `OCR_PROVIDER` | vlm_openai | OCR 提供者:vlm_openai / azure |
| `OCR_BASE_URL` | dashscope 兼容地址 | Qwen2.5-VL 端点 |
| `OCR_API_KEY` | (空) | OCR 密钥 |
| `OCR_MODEL` | qwen2.5-vl-72b-instruct | OCR 模型 |
| `AZURE_OCR_ENDPOINT` / `AZURE_OCR_KEY` | (空) | Azure CV 配置 |
| `DEEPSEEK_BASE_URL` / `API_KEY` | (空 key) | 评分 LLM |
| `DEEPSEEK_MODEL` / `REASONING_MODEL` | deepseek-chat / deepseek-reasoner | 常规 / 深度推理模型 |
| `DEEPSEEK_USE_REASONING` | false | 是否使用推理模型评分 |
| `MAX_CONCURRENT_TASKS` | 2 | 并发批改数 |
| `DATABASE_URL` | sqlite+aiosqlite:///./data/corrector.db | 数据库(升级自动迁移) |
| `UPLOAD_DIR` | ./data/uploads | 图片存储目录 |
| `SERVER_PORT` | 8765 | 后端端口 |
| `IMAGE_PREPROCESS` | true | 上传图片自动预处理(EXIF 纠偏/增强/保守裁边) |
| `LOCAL_VLM_/OCR_/DEEPSEEK_<超参>` | auto | 大模型超参三态:auto 自动适配 / omit 不发送该键 / 具体值;<超参>=TEMPERATURE / TOP_P / MAX_TOKENS / PRESENCE_PENALTY / FREQUENCY_PENALTY / SEED / STOP / RESPONSE_FORMAT / STREAM / EXTRA_PARAMS(自定义键值,分号分隔) |
| `PIPELINE_A/OCR/DEEPSEEK` 参数适配 | 自动 | 模型画像 + “400 剔除学习”(`app_settings.llm_unsupported_params`,设置中心「自动适配记录」可查看/清除) |

## 2. 启动、迁移与部署

```powershell
# 后端(在 backend/ 目录)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # 首次配置
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
# 启动时自动执行数据库迁移(存量库自动接入,不丢数据)

# 数据库迁移常用命令
.venv\Scripts\alembic current                       # 当前版本
.venv\Scripts\alembic upgrade head                  # 手动升级
.venv\Scripts\alembic revision --autogenerate -m "描述"  # 改模型后生成迁移

# 前端(在 frontend/ 目录)
npm install
npm run dev                   # 开发模式,http://localhost:5173
npm run build                 # 生产构建(产物 dist/)
```

## 3. API 速查

### 系统与批改

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 配置状态:mock 模式、管线可用性、模型名 |
| POST | `/api/corrections` | 单篇批改(multipart:files + 管线/标准/细致度/复核 + `class_id`/`assignment_name`/`topic`) |
| POST | `/api/corrections/batch` | 批量批改(每张图=一篇;ZIP 自动展开;同样支持班级/作业元数据) |

### 任务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tasks` | 筛选:`status` / `batch_id` / `class_id` / `created_from` / `created_to` |
| GET | `/api/tasks/{id}` | 详情(OCR 中间结果 / 结果 / **学生版报告** student_report) |
| GET | `/api/tasks/{id}/images/{idx}` | 原图 |
| POST | `/api/tasks/{id}/ocr-confirm` | 管线 B:提交校订转录继续评分 |
| POST | `/api/tasks/{id}/retry` | 重试(FAILED / WAITING_REVIEW / PROCESSING) |
| PUT | `/api/tasks/{id}/report` | 保存编辑版(`null`=恢复原始报告) |
| POST | `/api/tasks/batch-retry` | 批量重试(`{task_ids:[…]}`) |
| POST | `/api/tasks/batch-delete` | 批量删除(含错题记录与图片清理;处理中/排队中自动跳过) |
| POST | `/api/tasks/export-reports` | 导出报告 ZIP(优先教师编辑版) |
| POST | `/api/tasks/export-grades` | 导出成绩表 CSV(UTF-8 BOM,Excel 直开) |

### 班级 / 教学分析 / 学生

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/classes` | 班级列表(含任务数) |
| POST | `/api/classes` | 创建班级(`{name, note?}`,名称唯一) |
| DELETE | `/api/classes/{id}` | 删除班级(有关联任务时 409) |
| GET | `/api/analytics/class-diagnosis` | 共性错因诊断(筛选:`class_id`/`batch_id`/`date_from`/`date_to`) |
| GET | `/api/students` | 学生列表 |
| GET | `/api/students/profile` | 学生画像(`student_id` 或 `name`;含复现错因/时间线) |
| GET | `/api/students/{student_id}/errors` | 学生错题记录 |

### 维护(设置中心 · 基础运行;均需设置管理员 + 数据已解锁)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/maintenance/compress-images` | 存量图片批量压缩(幂等可重跑) |
| POST | `/api/maintenance/clear-demo-data?confirm=true` | 仅删演示模式(Mock)产生的数据(真实数据不受影响) |
| POST | `/api/maintenance/clear-all-data?confirm=true` | 一键清除所有数据(15 张业务表 + uploads 全部文件;`app_settings` 保留;不可恢复) |
| POST | `/api/maintenance/seed-demo-data?confirm=true` | 一键恢复所有示例数据(先清空再写入:2 班级/12 学生/24 完成 + 1 待复核任务/1 考试/台账/练习卷/风格画像/25 张示例答卷图) |

### 请求示例(PowerShell)

```powershell
# 单篇批改(带班级与作业元数据)
curl.exe -X POST http://127.0.0.1:8765/api/corrections `
  -F "files=@essay.jpg" `
  -F "pipeline_choice=PIPELINE_A_LOCAL" -F "grading_standard=GAOKAO" -F "detail_level=HIGH" `
  -F "class_id=1" -F "assignment_name=第一次月考作文"

# 班级讲评诊断
curl.exe "http://127.0.0.1:8765/api/analytics/class-diagnosis?class_id=1"

# 学生画像
curl.exe "http://127.0.0.1:8765/api/students/profile?student_id=20260123"

# 批量导出成绩表
curl.exe -X POST http://127.0.0.1:8765/api/tasks/export-grades `
  -H "Content-Type: application/json" -d "{\"task_ids\":[1,2,3]}" -o grades.csv
```

## 4. UI 操作流程

1. **工作台(/)**:选管线/评分标准/细致度;可指定班级(＋号现场新建)、作业名称、作文题目;
   单篇=多页图片,批量=每张一篇或 ZIP。
2. **审阅页(/review/:id)**:左原图(缩放/拖拽/多页)右报告;
   - **教师版/学生版**切换(学生版=订正单,弱化分数);
   - 编辑保存 / 恢复系统原始报告 / 下载 .md / **打印**(A4 样式);
   - 管线 B 待复核时显示转录校对区,「确认并继续评分」。
3. **队列页(/queue)**:状态/班级/批次/日期筛选;复选框多选 → 批量重试/删除;
   导出报告(ZIP)、导出成绩表(CSV)、重试全部失败。
4. **班级分析(/analytics)**:按班级/日期筛选 → 统计卡片 + 错因分布图 + 得分分布 + 错因明细(典型错例悬停看修正);
   右侧**讲评摘要**可复制/下载(备课讲评直接用)。
5. **学生错题本(/students)**:搜索学生 → 画像(平均分/批改次数/复现错因警告) + 个体错因分布 + 批改时间线(点击跳报告)。

## 5. 无真实模型时的演示(MOCK_MODE)

```ini
# backend/.env
MOCK_MODE=true
```

启动后一切照常:批改返回样例数据(含错因与完整报告),管线 B 复核流程、班级分析、错题本均可完整演示。

## 6. 故障排查手册

| 症状 | 原因与处理 |
|---|---|
| 启动报迁移错误 | 检查 `migrations/versions/`;`alembic current` 是否落后;必要时备份后手动 `upgrade head` |
| `/api/health` pipeline_b ready=false | `OCR_API_KEY`/`DEEPSEEK_API_KEY` 未填;补全或 MOCK_MODE |
| 任务长时间 PROCESSING | 本地 VLM 排队/超时;查看后端日志;`LOCAL_VLM_TIMEOUT` 默认 600s |
| 服务重启后任务重新排队 | 属预期:孤儿任务自愈(中断任务自动恢复),非重复批改 |
| FAILED "JSON 解析/校验失败(已修复重试一次)" | 模型输出质量差;日志含原始输出;建议换更强模型 |
| 班级分析没有数据 | 只统计已完成任务;检查班级/日期筛选 |
| 学生画像 404 | 该生无已完成任务;或改用姓名查询 |
| 导出 ZIP 400 "没有可导出的报告" | 所选任务均无结果 |
| 上传图片识别率差 | 确认 `IMAGE_PREPROCESS=true`;拍摄时避免强阴影与严重透视 |
| 批改失败“服务返回错误状态 400 … Parameter 'xxx' is not supported” | 端点/模型不支持该超参:系统会自动剔除该参数、重试一次并记住(设置中心 →「自动适配记录」可查看/清除);若仍失败:在对应管线分组把该项设为 omit(不发送)或更换模型;日志含 `llm.request`(每次实际发送)与 `llm.param_stripped` |
| 误点「一键清除所有数据」/「一键恢复所有示例数据」 | 均不可恢复且恢复示例数据会先清空现有数据(执行前有二次确认);应急可用 `backend/backups/` 备份或设置中心「班级数据包」导出件找回;需要保留当前数据时请先取消并导出 |
| 数据库想重置 | 停止后端,删除 `backend/data/corrector.db` 与 `uploads/`,重启自动迁移重建 |
