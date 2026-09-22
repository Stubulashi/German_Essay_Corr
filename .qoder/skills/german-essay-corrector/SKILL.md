---
name: german-essay-corrector
description: 德语教师端智能作文批改系统的开发与操作指南,涵盖双管线策略模式架构约束、统一 Pydantic Schema 契约、错因分类标准化、班级共性错因分析、学生错题本、后端确定性 Markdown 渲染、Alembic 数据库迁移、目录结构、扩展步骤、.env 配置、前后端启动命令、批改与复核流程、故障排查。当在本仓库中开发、调试、部署、运行或使用该批改系统时使用。
---

# 德语教师端智能作文批改系统

教师端工具:批量批改中国高中生的手写德语作文(高考德语 / DSD I / DSD II),
并将批改数据聚合为班级共性错因分析与学生错题本,形成"批改 → 讲评 → 错题追踪"的教学闭环。

## 项目速览

| 项 | 值 |
|---|---|
| 后端 | Python + FastAPI + Pydantic v2 + SQLAlchemy(async) + SQLite + **Alembic 迁移**,端口 **8765** |
| 前端 | React 18 + Vite + TypeScript + MUI(Material Design)+ MUI X Charts,端口 **5173**(proxy `/api` → 8765) |
| 管线 A | 本地 VLM(Qwen3.8-27B,vLLM/Ollama,OpenAI 兼容端点;关闭复核时单次执行图→全字段,开启复核时两段式 识别→人工复核→评分) |
| 管线 B | 云端解耦两段式(Qwen2.5-VL/Azure OCR 转录 → DeepSeek 评分) |
| 教学闭环 | 错因标准化(19 类考点)→ 班级讲评摘要 / 学生错题本(复现错因) |
| 演示模式 | `MOCK_MODE=true` 时无需任何模型端点即可跑通全流程 |

## 目录结构

```
backend/
  alembic.ini / migrations/      # Alembic 迁移(勿手动改已有版本文件)
  app/config.py                  # pydantic-settings 读取 .env
  app/models/schemas.py          # 统一 Schema(唯一输出契约)
  app/models/db_models.py        # ORM:correction_tasks / classes / students / error_records
  app/db/database.py             # 异步引擎 + 启动时自动迁移(含存量库 stamp 逻辑)
  app/pipelines/                 # 策略模式:base / local_vlm / cloud_decoupled / mock / factory / prompts
  app/services/
    llm_client.py / ocr_client.py    # OpenAI 兼容客户端 / OCR 适配器
    parser.py                        # 防御性 JSON 解析
    report_renderer.py               # 教师版 + 学生版 Markdown 渲染
    error_taxonomy.py                # 错因分类标准化 + 练习建议库(CATEGORY_TEACHING_TIPS)
    image_preprocess.py              # 图片预处理(EXIF/增强/裁边)
    analytics_service.py             # 班级诊断 + 学生画像聚合
    correction_service.py            # 编排 + 故障转移 + 孤儿任务恢复
    queue_service.py                 # asyncio 工作池
  app/api/                       # health / corrections / tasks / classes / analytics / students
  tests/                         # pytest(77 用例)
frontend/src/
  api/client.ts                  # axios 封装(含 Blob 导出)
  types/index.ts                 # 与 Pydantic 逐字段对齐的 TS 类型
  pages/                         # 工作台 / 审阅 / 队列 / 班级分析(AnalyticsPage)/ 学生错题本(StudentsPage)
  print.css                      # 报告打印样式(id="print-area" 区域生效)
```

## 不可违背的架构约束

1. **策略模式**:批改逻辑必须实现于 `AbstractCorrectionPipeline` 子类,经 `get_pipeline(config)` 实例化。
2. **LLM 只产出结构化 JSON**:`markdown_report` 由 `report_renderer` 确定性渲染;学生版报告同样由后端渲染。
3. **统一输出契约**:所有管线输出必须能反序列化为 `EssayCorrectionResult`;前端类型逐字段对齐。
4. **错因必须先标准化**:错误条目经 `error_taxonomy.annotate_errors()` 填充 `canonical_type`(在 `finalize_result` 中自动完成)后才可落库/统计;分析统计一律用 `canonical_type` 聚合,禁止用自由文本 `error_type`。
5. **故障转移规则**:仅 `PipelineNetworkError` + `ALLOW_AUTO_FALLBACK=true` 时 A→B;Parse/Config 错误不转移。
6. **数据库变更必须走 Alembic**:修改 `db_models.py` 后运行 `alembic revision --autogenerate`,禁止手动删库或裸改 schema。
7. **中文注释**:所有代码注释、Prompt、UI 文案使用中文。

## 常用命令

```powershell
# 后端
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
.venv\Scripts\python -m pytest tests/ -v

# 数据库迁移(Alembic)
.venv\Scripts\alembic upgrade head                 # 应用迁移(启动时也会自动执行)
.venv\Scripts\alembic revision --autogenerate -m "描述"  # 改模型后生成迁移
.venv\Scripts\alembic current                       # 查看当前版本

# 前端
cd frontend
npm run dev                     # http://localhost:5173
npm run build                   # 类型检查 + 生产构建
```

## 工作流程

### 开发流程(修改代码时)

1. 阅读 `.cursorrules`(完整规格)与本文档约束;
2. 改动管线/解析/渲染逻辑 → 必须跑 `pytest`;
3. 改动数据模型 → 必须生成 Alembic 迁移并重启验证;
4. 改动 API → 同步 `frontend/src/types/index.ts` 与 `frontend/src/api/client.ts`;
5. 完成后用 `MOCK_MODE=true` 冒烟:创建任务 → 查看班级分析 → 查看学生错题本。

### 操作流程(运行系统时)

1. 复制 `backend/.env.example` 为 `backend/.env`,填写端点与密钥(无模型时 `MOCK_MODE=true`);
2. 启动后端(自动迁移)→ 启动前端 → 打开 `http://localhost:5173`;
3. **工作台**:选管线/标准/细致度,可选指定班级(可现场新建)/作业名称 → 上传(单篇多页 / 批量 / ZIP);
4. **人工复核**(任一管线 + 开关):任务暂停在「待人工复核」→ 审阅页校对转录 → 「确认并继续评分」;
5. **队列页**:筛选(状态/班级/批次/日期)→ 多选批量重试/删除 / 导出报告 ZIP / 导出成绩表 CSV;
6. **审阅页**:教师版/学生版报告切换 → 编辑保存 / 恢复原始 / 下载 .md / 打印;
7. **班级分析页**:筛选班级或日期 → 查看统计与图表 → 复制/下载**讲评摘要**用于备课;
8. **学生错题本页**:选择学生 → 查看错因分布、复现错因、批改时间线(可跳转报告)。

## 扩展指南(摘要)

- **新增管线**:实现 `AbstractCorrectionPipeline` 子类 → `PipelineChoice` 加值 → `factory.py` 注册 → 前端 `PIPELINE_META` 加选项。详见 ARCHITECTURE.md。
- **新增 OCR 提供者**:仿照 `ocr_client._extract_azure()` 增加适配器 → `config.ocr_provider` Literal 加值。
- **新增考点分类**:`error_taxonomy.ErrorCategory` 加成员 → `CATEGORY_LABELS`/`_register` 别名/`CATEGORY_TEACHING_TIPS` 同步补充。
- **数据模型变更**:`db_models.py` 修改 → `alembic revision --autogenerate` → 检查生成文件(注意 SQLite 批量模式与 server_default)→ 重启应用。

## UI 规范与内置指南(前端)

- 全站样式统一由 `frontend/src/theme.ts` 提供(设计 Token 唯一来源);新增页面前先阅读 `docs/UI规范.md`(组件约定 / 页面结构 / 文案规范 / 响应式 / 新增页面 checklist);
- 页面首屏标题统一使用 `frontend/src/components/PageHeader.tsx`(标题 / 说明 / 徽章 / 操作),不要再自写 h4/h5;
- 响应式:`md(900px)` 为断点,桌面常驻导航抽屉、小屏临时抽屉 + 汉堡按钮(由 `AppShell.tsx` 统一实现,页面无需处理);
- 内置「使用指南」页(`/guide`,内容数据在 `frontend/src/guide/guideContent.ts`):新增功能模块时,同步在 `GUIDE_SECTIONS` 补充对应分组与条目(保持与导航模块一一对应)。

## 离线运行与班级合并(2026-09 新增)

- **离线自包含**:后端可直接托管前端构建产物(`main.py` 中的 `FRONTEND_DIST` + SPA 回退,访问 8765 根路径);项目内置 `runtime/python` 便携运行时,新电脑零安装即可运行;环境自检:`backend/scripts/selfcheck.py`(或「一键自检.bat」);
- **班级合并**:`app/services/class_merge_service.py` + `/api/classes/merge[/preview|/logs]`;来源班级标记 `merged_into_id` 不删除,合并日志表 `class_merge_logs` 记录冲突明细,单事务失败整体回滚;前端入口在班级分析页「合并班级」;
- **示范学习**:`style_learning_service` 的生效风格经 `get_active_style_text()` 注入两条管线的系统 Prompt(未启用时为空串,行为与现状一致);路由在 `routes_style.py`。

## 设置中心 · 数据加密 · 台账/考试/统计 · 示范学习(2026-09-18 新增)

- **设置中心**(`services/settings_service|env_manager|prompt_overrides` + `api/routes_settings`):注册表驱动(元数据在 `SETTINGS_FIELDS`,当前 80 项;前端 `SettingsDialog` 窗口式:左“大范围”导航 × 右“小项”列表;含“教学与报告”分组与“探索性实验项”聚合面板);更新写回 `backend/.env`(保留注释/原子替换)并热更新 settings 单例;探索项快照与提示词附录存 `app_settings` 表;developer 项后端强制门控(非开发模式 403);设置接口 `require_settings_admin`(回环 + 可选 `SETTINGS_ADMIN_TOKEN`,`dev_mode` 控制开发者项);
- **数据加密**(`services/crypto_service|crypto_runtime|encryption_service` + `models/encrypted_types` + `api/routes_encryption`):AES-256-GCM(`encv1:` 前缀)/AES-256-SIV(`encd1:` 前缀,等值查询可用),DEK 经 HKDF 分离子密钥、scrypt 包裹;密钥包裹存 `app_settings`;锁定态数据接口 423(`require_unlocked`);迁移幂等可重跑;恢复密钥重置强制轮换密钥;Plan B 归档重建到 `data/archive_*`;审计 `data/audit.log`(禁记密钥);开发模式万能密码兜底(2026-09-21):三重门禁(DEV_MODE+DEV_MASTER_ENABLED+≥64 位十六进制长 Hash)下经 `hmac.compare_digest` 校验,解包 `app_settings.encryption_dev_master_wrap`(setup/改口令/恢复重置/正常解锁时自动登记与自愈,生产不读不写);入口复用 `POST /encryption/unlock`,失败信息与普通口令错误逐字一致;状态接口新增只读 `dev_master_enabled`;
- **作业台账**(`services/ledger_service` + `api/routes_ledger`):LEVEL/SCORE/FLAG/STARS 四模式;批量登记 `class_id` 决定记录归属(全局模板项也可归班);汇总矩阵读时聚合;
- **考试统计**(`services/exam_service` + `api/routes_exams`):独立队列(ExamService,孤儿自愈);逐题识别不含听力;报告为确定性 Markdown 快照,重生成即覆盖;
- **统一统计层**(`services/statistics_service` + `api/routes_statistics`):批改/考试/台账三源读时聚合;学生对齐=学号优先/姓名兜底(同名唯一学号自动合并桶);综合平均=各源归一百分比等权;CEFR 不参与数值平均;
- **示范学习**(`services/style_learning_service` + `api/routes_style`):单一生效;narrative 注入管线 Prompt(风格头 `STYLE_CONTEXT_HEADER` + 教师附录头 `APPENDIX_HEADER` 依次叠加,均为空时与默认行为逐字节一致);
- 排查速查(新增):设置保存不生效 → 看是否「需重启」项;加密后接口 423 → `POST /api/encryption/unlock` 解锁;统计人数与预期不符 → 检查花名册学号一致性(同名无人学号时按姓名聚合)。
- **重新批改 / 转录编辑 / 图片编辑 / 设置窗口化(2026-09-18 第三轮)**:`POST /tasks/{id}/recorrect`(`recorrect_service`:仅 COMPLETED、原图必须存在、白名单覆盖、先清 ErrorRecord 再重建、追溯列 `recorrect_count/last_recorrect_at/prev_overall_score/prev_error_count`;教师编辑版与寄语保留);`PUT /tasks/{id}/transcript`(写回并重渲染系统报告,教师编辑版不受影响);`PUT /tasks/{id}/teacher-message`(学生版寄语,空=默认);前端 `ImageEditorDialog`(参数化操作栈:灰度/黑白/旋转/裁剪/姓名栏选区;WeakMap 记录原图可重置;仅编辑版落盘且文件名不变)、`TranscriptPanel`/`AnnotationPanel`(19 类「颜色+9 种线型」唯一映射、图例计数筛选、清单↔全文双向联动、Alt+←/→ 导航)、`SettingsDialog`(窗口式,替代原 SettingsDrawer);及格线口径 `SCORE_PASS_LINE` 由统计/考试共用(默认 60 保持历史行为);工作台默认值 `DEFAULT_GRADING_STANDARD/DEFAULT_DETAIL_LEVEL` 经 `/api/health` 下发;学生版显示项 `STUDENT_REPORT_SHOW_SCORE/SHOW_TIPS`(探索,默认与历史行为一致)。

## 提示词架构(六区块,2026-09-18 全模式重构)

- 全部系统提示词统一为六区:【身份与使命】【工作流程】【本模式规则】【边界情况处理】【输出前自检】【输出契约】;差异对比与字段矩阵见 `docs/提示词模式对照.md`;
- 模式家族(独立撰写,严禁近重复):管线 A=看图一手批改者(`build_pipeline_a_prompt`)、B-识别=纯转录官(`build_ocr_prompt`)、B-评分=只依据给定转录的评卷人(`build_grading_prompt`,含“转录体检”步骤)、考试识别=阅卷机器(`build_exam_ocr_prompt`,含总分自校验)、风格归纳=批改风格归纳师(`STYLE_SUMMARY_SYSTEM_PROMPT`);
- 用户消息集中于 `app/pipelines/prompts.py`:`build_pipeline_a_user_message / build_ocr_user_message / build_grading_user_message(签名不变) / build_exam_user_message`;
- 兼容红线:`_GRADING_JSON_SPEC` 与各模式 JSON 契约逐字节锁定;注入顺序 = 风格(`STYLE_CONTEXT_HEADER`)→ 附录(`APPENDIX_HEADER`);空注入与无注入逐字节一致(由 `tests/test_prompt_modes.py` 固化);修改提示词时必须保持六区结构与上述红线;
- 第六模式(2026-09-18 练习卷):命题老师 `PRACTICE_SYSTEM_PROMPT` + `build_practice_user_message`(证据=错因分布/典型错句/主题;契约 `{title,worksheet_markdown,answer_markdown,questions[{type,no,stem,answer,explanation}]}`);练习卷模块:`practice_sheets` 表(迁移 `b924fa2a598d`)、`services/practice_service.py`、`routes_practice.py`(options/sources/sheets CRUD,挂 _data_guard);存档图片压缩:`services/image_compress.py`(JPEG COM/PNG Software 压缩标记,重跑幂等)+`image_compress_service.py`(存量批量,后台进度)+`routes_maintenance.py`(设置管理员+解锁双校验);上传钩子 `routes_corrections._maybe_compress`;图片端点魔数嗅探 `routes_tasks._sniff_media_type`;设置项 IMAGE_COMPRESS_ENABLED/MAX_SIDE/QUALITY。
- OCR 忠实性与 PDF(2026-09-18 第八轮):OCR 契约升级 v2 五字段(+`recognition_quality` high|medium|low、`quality_note`;`OcrExtractionResult` 默认 None 零破坏,`ocr_client._parse_ocr_output` 解析钳位);OCR 提示词零修正最高红线(反例锚定 `"ich findet"` 必须原样输出)+ 双档标记(`[unleserlich]` 保留;[unsicher:猜测文本] 新增,评分侧不得据此新增错误条目)+ `build_grading_user_message(quality=None,…)` 可选透传(默认逐字节不变);前端 `TranscriptPanel.renderWithMarkers` 红/黄高亮 + `ReviewPage` 复核区低质量 Alert;**PDF 上传**:`services/pdf_expand.py`(pypdfium2 可选组件,页数上限 60、加密/损坏→400,单篇=多页同一份/批量=每页一篇,命名 `stem_pN.jpg`,ZIP 内支持;展开计入 MAX_BATCH_FILES);requirements 含 `pypdfium2`(需同步装入 runtime)。

## 排查速查

| 现象 | 排查方向 |
|---|---|
| 启动报迁移错误 | 检查 `migrations/versions/` 中最新迁移;`alembic current` 对照 `alembic heads` |
| 任务 FAILED 含"超时" | 本地 VLM 未启动/显存不足;检查 `LOCAL_VLM_BASE_URL` 与 `LOCAL_VLM_TIMEOUT` |
| 日志出现"自动故障转移" | 预期行为;任务 `fallback_triggered=true`,报告头显示实际管线 |
| 班级分析无数据 | 仅统计**已完成**任务;确认筛选条件(班级/日期)与任务状态 |
| 学生画像查不到 | 用 `student_id`(优先)或 `name`;未完成的任务不计入 |
| 队列"处理中"任务重启后消失 | 属正常:孤儿任务自愈会将中断任务重置为排队并重新执行 |
| 导出 ZIP 报"没有可导出的报告" | 所选任务均未完成或结果为空 |
| 前端 404/连不上后端 | 确认后端 8765 在运行;代理配置见 `frontend/vite.config.ts` |
| 合并班级报错并提示"已回滚" | 属保护行为:本次合并未产生任何改动;按错误信息处理(如目标班级已被并入)后重试 |
| 新电脑双击启动提示找不到运行环境 | 缺少 runtime 目录:拷贝完整项目,或运行"首次安装.bat"联网安装 |
| 端口 8765 被占用 | 启动脚本自动改用 8766(浏览器同样自动打开);8765/8766 均被占用时按提示先关闭占用程序(「一键停止.bat」覆盖两个端口) |
| 便携分发前的完整性确认 | 运行「一键自检.bat」:必检 13 项依赖(含 cryptography/multipart)、vcruntime 是否随包内嵌、端口占用信息项;净机验收清单见 README「离线自包含运行 - 纯净新电脑验证清单」 |

## 参考文档

- 架构细节与扩展步骤:[ARCHITECTURE.md](ARCHITECTURE.md)
- 配置项、API 示例、操作手册:[OPERATIONS.md](OPERATIONS.md)
- UI 规范(设计 Token / 组件 / 文案 / 新增页面清单):仓库根目录 `docs/UI规范.md`
- 教师侧完整操作手册:仓库根目录 `使用手册.md`(含“使用指南”入口说明)
- 项目完整规格 (Spec):仓库根目录 `.cursorrules`
- 批改意见结构化+启动选择(2026-09-18 第九轮):report_renderer 错误条目改独立引用块(原句/修正/错因解析·小提示;ExplanationParts=_split_explanation 拆 规则/推理/说明,无标记整体兜底);前端 MarkdownReport ReportBlockquote 三色 Callout、TranscriptPanel 弹层同构(utils/explanation.ts);维护端点 POST /maintenance/clear-demo-data(演示样例身份=MOCK_STUDENT_NAME/ID,mock.py 常量;删任务/错因/图片/孤儿学生);启动门禁 STARTUP_DATA_PICKER(config/settings/health 暴露)+StartupDataDialog(导入班级数据包/直接进入/会话跳过,sessionStorage startup-data-picker-skip);RosterImportDialog 占位符中性化。291 tests。
- 连续审阅+队列分组(2026-09-18 第十轮):ReviewPage 上一份/下一份(范围=同批次→同班级+作业;顺序=创建时间升序;fetchTasks 拉兄弟列表按 rangeKey 缓存;[id] 重置块扩展为全量视图重置;←/→ 快捷键忽略输入框与对话框;navigate replace);QueuePage groupedTasks=useMemo 按 作业(含班级)→批次→其他 分组,组头=三态全选+折叠+计数 Chips(渲染层独立,筛选/批量操作零改动)。build 通过。
- 启动解锁流程重构(2026-09-18 第十一轮):UnlockDialog 双模式(受控 open/onUnlocked 供启动编排,非受控保留 423 事件+解锁后 reload;**删除挂载自察自弹**);AppShell 门禁阶段化 wait→picker→checking→unlock→done(finishStartup 异步,选择完成后才 fetchEncryptionStatus;handleStartupUnlocked 写会话跳过;checking/unlock 阶段仅渲染加载圈,子页面仅在 none 挂载);StartupDataDialog 导入遇锁提升提示语。零后端改动。
- OCR 异常检测自愈(2026-09-18 第十二轮):services/ocr_anomaly.py(加权检测:断词粘连+2/标签行+2/畸变词+1/占位符密度+2/自评不符+3,阈值可配;reconcile_quality 纠偏 high→low+note 合并);cloud_decoupled 阶段1 OCR 预算内重跑(OCR_ANOMALY_MAX_RETRY,默认1)+纠偏+异常提前挂起复核(OCR_ANOMALY_FORCE_REVIEW);correction_service 管线 A 整管线重跑+降级追加【识别质量提示】并 re-render;设置项 4 个(pipeline_b_ocr 组:ENABLED/THRESHOLD/MAX_RETRY/FORCE_REVIEW);审计事件 ocr.anomaly_retry/degraded、pipeline.anomaly_rerun/degraded。303 tests。
- macOS 适配+应急版(2026-09-19 第十三轮):根目录「一键启动/停止/自检.command」「首次制作mac运行环境.command」+ backend/scripts/setup_macos_runtime.py(python-build-standalone latest 资产 arm64/x86_64 自适应;依赖=同一 requirements;bash -n+chmod+x+quarantine 清理;非 Windows 分支 selfcheck 已兼容;业务代码零改动);应急版=「应急启动.bat(GBK)/应急启动.command」读取根目录 应急配置.env(明文,已 gitignore;8 键:OCR_*/DEEPSEEK_*)以进程环境变量注入(pydantic-settings env>.env 已实测),不写 backend/.env;文档 docs/macOS便携版制作与使用.md + 手册 §16.17/§16.18。
- 管线A复核对齐(2026-09-20 第十四轮):local_vlm 三分支(ctx.ocr_result→仅评分_grade_confirmed;require_ocr_review→_run_ocr_stage 挂起;否则单次原样);复核态阶段1=OCR角色提示词+本地VLM,阶段2=评分角色提示词+本地VLM(tag PIPELINE_A_LOCAL-OCR/GRADING);ocr_anomaly.run_ocr_with_guard 共享自愈循环(B 改调用处,行为不变);correction_service 重跑/降级条件加 ctx.ocr_result is None 守卫;mock 复核挂起去管线限定;前端 CorrectionPage 开关与提交去 B 限定+工作流文案动态,ReviewPage 复核文案按管线动态;测试 test_pipeline_a_review.py 5 用例;325 tests、冒烟 18/18。
- 视觉误报修复+标准答题卷(2026-09-20 第十五轮):F1 图片存在性守卫(ocr_client.extract/local_vlm._run_ocr_stage→PipelineConfigError);F2 raise_if_vision_failed(ocr_anomaly,pattern 表命中→'端点疑似不支持图片输入'配置错误,不再伪装质量低;A/B 双调用点);F3 llm_client 空 content 区分 reasoning-only 明确报错;F4 settings_service 视觉探针 _check_openai_vision+_classify_vision_probe(test_connectivity ocr 分支自动带图探针);诊断工具 backend/scripts/diag_vision.py(默认 dry,--live 三载荷对比,长期保留);标准答题卷=services/sheet_align.py(纯 PIL:四象限近角优先质心找四角块→QUAD 透视→1500×2121 规范画布内缩 52px;未命中/异常原字节回退)+SHEET_ALIGN_ENABLED 开关+routes_corrections._maybe_align_sheet 钩子(预处理后压缩前,两处落盘链)+前端 /print/answer-sheet 打印页+工作台入口;测试 test_vision_guard.py(10)+test_sheet_align.py(7);342 tests、build OK、冒烟 8/8。
- 视觉发送层jpg归一化(2026-09-20 第十六轮):llm_client.image_to_data_url 统一 JPEG 发送(jpg 小图原字节直发零重编码;png/webp/bmp/heic 自动转码;RGBA 白底合成;>3.5MB 或长边>2400px 自动缩边重编码护栏,防网关静默丢弃;失败原字节回退);一处改四链路生效(A单次/A-OCR/B-OCR/考试);diag_vision 扩为五载荷(jpg/png/裸base64/双图/纯文本,jpg-png 对照定位'只吃 jpg');测试 test_image_payload.py 7 用例;349 tests。
- 答题卷精修+找平校正(2026-09-20 第十七轮):模板重排(grid 两列消除重叠、页眉/页脚、双类多排定位块[主4×16mm+次2×8mm]、书写线 21 行 y84+、显式黑白不随主题);sheet_align 几何修正为精确 mm 映射(px=1500/210)+原分辨率 bbox 精测+3+1 角外推+源四边形外推整页后 QUAD 全幅输出(修正旧版'块心=输出角'裁半块导致的 ~14% 系统偏差;实测端正卷误差≈0px、旋转 8° ≤18px);新增 name-crop 只读端点(找平命中按 NAME_REGION(86,310,1086,416) 裁、普通照启发式,宽 900 JPEG);审阅页姓名小图条+中号主图;ImageEditorDialog 首帧空白修复(redrawRef+rAF 补绘);352 tests、冒烟 11/11。
- 上传预识别+大预览(2026-09-20 第十八轮):services/name_pre_ocr.py(任务创建即后台识名:crop_name_region→引擎链[MOCK短路→已配置端点(OCR位优先/本地VLM次之,复用 build_ocr_prompt/_parse_ocr_output/raise_if_vision_failed)→本地 RapidOCR 兜底(rapidocr_onnxruntime 可选组件,离线CPU,中文+拼音字符集)]→parse_identity(中文/拼音/学号+花名册同班唯一命中纠错)→原子条件更新[仅活跃状态可写;注意 result 为 JSON 列 None 存'null'文本,不可用 IS NULL 条件]→审计 name.pre_ocr);NamePreOcrService(2 worker,lifespan 启停,不占批改并发)+routes_corrections 两处 schedule;设置 NAME_PRE_OCR_ENABLED(默认开);前端:工作台文件列表大预览卡(150px 图区)、审阅页姓名区大预览卡(420px)+预识别中占位、AnswerSheetPage 窄屏分级 zoom(打印还原 100%)。真实本地识图验证:合成姓名区→RapidOCR 输出 'Li Ming'/'20260123' 解析正确。364 tests、冒烟 7/7。
- 续写纸识别与自动并页(2026-09-20 第十九轮):sheet_align 新增页底【类型带】编码定位点(TYPE_BAND_Y_MM=277/三槽位 x=90/105/120mm/8mm;[L,C,R] 位串表驱动 PAGE_TYPE_MAP:101=continuation,无/其余=home 兼容旧卷;_decode_page_type 在找平画布解码;align_standard_sheet 返回三元组 (bytes,changed,page_type));routes_corrections:_group_pages 相邻归页(续写页并入上一组,首张续写=孤儿独立组);阶段三 prefill/阶段四建卡改按组;max_batch_files 按组数;audit upload.page_merge/continuation_orphan;前端 AnswerSheetContinuationPage+工作台入口;契约零改。373 tests、冒烟 7/7(2 张→1 任务 2 页;孤儿回退)。
- 手写样本模型+全局等待态(2026-09-20 第二十轮):handwriting_service(句库素材/326维特征描述子[两阈值段x163:分区墨量18+垂直投影96+水平投影48+宽高比1]/档位推荐[核心x内存矩阵,手动优先]/match_candidate近邻[medium Top1+gap,precise k=3]/样本处理链[找平→crop_name_region→引擎链→花名册对账→特征]→handwriting_samples/handwriting_models 表[迁移 c71d9f4b2a30]);routes_handwriting(句/样本上传/train status/model/bind/delete/crop);name_pre_ocr._enhance_identity 按档位接入;status_service(run_checkup 真实自检:DB/迁移/写测/磁盘/引擎/dist,10s 缓存;build_overview 聚合批改/预识别/压缩/训练真实 percent+ETA);routes_status /status/overview(不加守卫);SystemStatusBar(AppShell,3s 轮询,无任务隐藏,真实短语模板);前端:花名册对话框 Tab 化+HandwritingSection(素材/打印卡/上传/进度/绑定/概览)+HandwritingCardPage(/print/handwriting-card)+SettingsDialog 档位画像行。389 tests、冒烟 12/12。
- UI档位与性能承载(2026-09-20 第二十一轮):theme.ts 三档派生(getTheme(mode, profile) 默认 balanced 零破坏;UiProfile/PROFILE 变体[过渡表/阴影数组/全局样式/按钮卡片 hover];profileEffects 工作区背景与 Logo 渐变;UiProfileContext+useUiProfile);efficiency=时长全零+全局动效兜底+MuiTouchRipple 关+shadows 全 none+去渐变;premium=cubic-bezier(0.22,1,0.36,1) 分层时长+多层环境影/微光+按钮悬停抬 1px+Card 影随动;顶栏 Tune 菜单即时切换;localStorage(corrector-ui-profile)+后端 UI_PROFILE 权威双轨启动校准;Charts 三页 skipAnimation;打印页三档一致。389 tests、build OK;手测清单已交付。
- 班级管理独立+三档深化+台账修复(2026-09-20 第二十二轮):①台账「登记明细」崩溃根因=前后端契约错位(后端 LedgerRecordListResponse 字段为 items,前端曾读 data.records→setRecords(undefined)→records.map TypeError,必现);修复=client.fetchLedgerRecords 改读 items 并映射为 {total,records}(types 新增 LedgerRecordListResponse;全量扫描 client.ts 内联类型仅此一处错位);②三档 UI 深化(theme.ts,同一 Token 派生):efficiency=极简(灰阶 palette[装饰色去色+低饱和 success/warning/error/info 保留]/纯色背景/默认色 Chip 去填充转描边;保持零动效零阴影无渐变图表禁动画),premium=尊享(页面切换淡入 .premium-page-enter@AppShell ref 重放不 remount/按钮按下回弹/IconButton 悬停缩放/MenuItem 过渡/卡片顶部内高光+hover 影跃升+边框微亮/focus-visible 光晕/品牌色 ::selection/PageHeader 标题渐变细线);UI_PROFILE_LABELS.efficiency='极简'+注册表与 .env.example 文案同步(值域不变,无新增设置项);③班级管理独立:导航「班级管理」(/classes,与批改工作台并列;子路由 /classes/:section:overview|roster|handwriting|package|merge|analytics);新页 ClassHubPage+components/classes/{RosterSection(自 RosterImportDialog 提炼,原对话框删除),ClassOverviewSection(新建/删除;client.deleteClass 调既有 DELETE /classes/{id}),ClassPackageSection,ClassMergeSection};AnalyticsPage 改造为内嵌内容区块(去 PageHeader,数据包/合并移出);CorrectionPage 瘦身(花名册/新建班级按钮改跳转,移除对话框与状态);LedgerPage 文案同步;/analytics→/classes/analytics 重定向;guideContent/手册/README/UI规范 同步。验证:build 通过;389 tests 零回归;浏览器端到端复验(导航/六标签/登记明细/三档切换/重定向)全通过,无 Console 错误;档位切换样式取证:隐藏视口冻结 CSS 过渡属测试环境现象(非缺陷,前台 320ms 过渡正常)。
- 上传即识与定位点裁切根治(2026-09-20 第二十三轮):根因实测=①定位点检测假阳性(近角质心+0.4%阈值+返回前无“块真实/成形”校验→普通照片被“假找平”毁图、检测点3/4处为白仍过校验) ②裁切依赖二次找平/启发式(与定位点无关) ③WAITING_REVIEW 不在预识别白名单+静默分支无审计(真实任务零痕迹)。修复:sheet_align 块真实性三级校验(_block_center_in_window:96×96 下采样+暗占比+bbox 填充率≥0.55+宽高比0.45-2.2+expected_size 尺寸先验0.35-1.6×块估计(杀黑带穿窗)+排斥整窗全黑;_corner_candidates 缩略图网格连通簇候选(距角升序≤3)候选逐个原图校验;新增 _is_canonical_canvas(±4px)/_detect_blocks_at_targets 理论锚定 4/4;新增 crop_name_region_ex→(bytes|None, meta[basis/main_points/marker_points/region]);裁切=规范画布锚定 4/4 或 原图检测→找平→画布锚定复检 4/4,无定位点→None(废除启发式);name_pre_ocr:_ACTIVE_STATUSES 增 WAITING_REVIEW、无裁切→_scaled_full_image 整图识别、全分支审计(含“定位点依据 basis(块 n/4)”)。验证:数据回归(任务27不再假找平/任务29假画布拒绝裁切)、394 passed(+5)、端到端冒烟:标准卷旋转5°→审计“定位点依据 canonical(块 4/4);来源 ocr-endpoint;命中 1”且 DB=Li Ming/20260123;无定位点照片→“定位点依据 full-image”整图识别写入,全部通过。
- 纯本地 OCR 模式(2026-09-20 第二十四轮):新增设置 LOCAL_OCR_ONLY(默认关;四处同步 config.local_ocr_only / FIELDS[pipeline_b_ocr 首项,switch] / SettingsUpdateRequest / .env.example)。生效方式:OcrClient.extract 在图片校验后短路到 _extract_local_rapid(适配器 3:整页 RapidOCR 转录按页拼接→transcribed_text;姓名/学号=首页 crop_name_region_ex 依定位点裁图识别+parse_identity,裁图缺字段时逐字段用首页整页文本回填;全行平均置信度→recognition_quality[≥0.92 high/≥0.80 medium/其余 low]+quality_note;组件缺失 raise PipelineConfigError 绝不回退云端);延迟 import name_pre_ocr 的 recognize_lines/rapid_available/parse_identity(避免模块环,name_pre_ocr 顶层引用 ocr_client)。name_pre_ocr:新增 recognize_lines[(文本,置信度)]+rapid_available,_extract_via_local_rapid 改薄包装(测试桩兼容);run_pre_ocr 在 local_ocr_only 时跳过全部端点仅本地引擎。评分(cloud_decoupled._run_grading)、复核、渲染、管线 A 全部零改动。runtime 安装 rapidocr_onnxruntime 1.2.3(onnxruntime 1.30/opencv,自检新增组件行);验证:402 tests(+8:契约/无定位点回填/组件缺失配置错误/空文本网络错误/关开关走原路径/预识别跳端点/注册表与 schema);端到端冒烟(LOCAL_OCR_ONLY=true + OCR_BASE_URL 指向不可达 127.0.0.1:9):上传旋转 5° 合成标准卷→本地识别 Li Ming/20260123→任务 COMPLETED 且评分产出 '23 / 25'(4/4 通过,证明 OCR 完全本地化且评分链路不变)。
- 强制性姓名识别(2026-09-21 第二十五轮):新增设置 FORCE_NAME_RECOGNITION(默认关;四处同步 config/SettingsUpdateRequest/FIELDS[upload 组,switch]/.env.example)。效果:工作台选择/拖拽文件(单张/批量/ZIP/PDF)瞬间即用本地引擎识别姓名/年龄,无需点「开始批改」;不落盘、不建任务、强制本地(RapidOCR,不触任何云端/远程端点)。实现:①routes_corrections 新增只读端点 POST /api/corrections/probe-identity(开关关→403;组件缺→503;ZIP 复用 _extract_zip_images/PDF 复用 expand_pdf;逐张 asyncio.to_thread 串行,返回 items[index/source_name/name/age/student_id/status]);②name_pre_ocr 新增 parse_age(alter/aler/age/jahre/年龄/岁关键词邻近+1-120 过滤)与 probe_personal_info(依定位点裁姓名区优先→整图兜底;整图路径 parse_identity(require_keyword=True)防正文误报);_STOPWORDS 增补 alter/aler/age/jahre;parse_identity 新增 require_keyword 关键字参数(默认 False 零破坏);③schemas 新增 IdentityProbeItem/Response;④前端 client.probeIdentity + CorrectionPage:addFiles 新增项即触发(限并发 2,逐张'识别中…→结果'),开关开启时文件列表'文件名位置'改为识别结果显示(姓名·年龄 N;ZIP 逐页多行;未识别/失败占位;文件名移入悬停),移除/清空/编辑同步清理与重探测。验证:425 tests(+14 本功能);build+selfcheck 全过;HTTP 端到端(单图 Li Ming/16、ZIP 2 页有序、关开关 403)8/8;浏览器 UI 复验(注入文件不点批改):'识别中…'→'Li Ming · 年龄 16'、ZIP 逐页两行,Console 零错误。
- 强制上传即识的顺序保证与可验证性加固(2026-09-21 第二十六轮):复查确认完整链路严格为"先依定位点裁剪→再对裁剪产物识别"(probe_personal_info 先 crop_name_region_ex 后 recognize_lines;crop_name_region_ex 全分支需检测通过才裁——canonical 理论锚定 4/4、landmarks 检测+找平+复检 4/4,无检测→(None,meta),无定位点→整图回退继续识别,不中断)。加固:①probe_personal_info 透传裁剪依据 basis(canonical/landmarks/full-image)并加 debug 日志;②IdentityProbeItem 契约新增 basis 字段(端点透传;前端类型镜像,UI 不展示);③新增顺序契约测试:调用序列 crop→recognize、识别输入内容==裁剪产物(非原图)、无定位点回退整图不中断、canonical 透传、端点 basis 透传。验证:429 tests(+4)零回归;build+selfcheck 全过;三路径 HTTP 端到端(真实引擎):旋转 5° 照片→basis=landmarks+Li Ming/16;端正 1500×2121 规范画布→basis=canonical+Wang Wei/17;无定位点文字图→basis=full-image+Zhang San/15(status=ok,流程不中断)——7/7 通过。
- 大模型超参数管理与自动适配(2026-09-21 第二十七轮):根因=`llm_client` 固定发送 temperature 被 kimi-k3 类端点 400 拒绝(“Parameter 'temperature'=0.2 is not supported”)。新增 `services/llm_params.py`(唯一装配入口:基线[历史发送集 temperature=0.2/stream=false/response_format=json_object,未配置时逐字节一致]→三态配置[auto 自动 / omit 不发送该键 / 具体值]→自定义 `EXTRA_PARAMS`→内置模型画像[kimi-k3* 去 temperature;OpenAI o1/o3/o4/gpt-5* 去采样类参数]→学习记录[端点|模型级,优先级最高]);`LLMClient` 新增 `params_target`(pipeline_a/ocr/deepseek;空=legacy 兼容测试与未迁移调用点)并改由 assemble 组装请求体;HTTP 400 时正则解析参数名→剔除该键→重试一次→写 `app_settings.llm_unsupported_params`(内存缓存+异步落库)→日志逐次(`llm.request`)+审计(`llm.param_stripped`);chat 签名 temperature/max_tokens 默认改 None(调用点显式值仍优先,practice 0.4/style 0.3 现状不变);设置中心 30 新键(`LOCAL_VLM_`/`OCR_`/`DEEPSEEK_` × TEMPERATURE/TOP_P/MAX_TOKENS/PRESENCE_PENALTY/FREQUENCY_PENALTY/SEED/STOP/RESPONSE_FORMAT/STREAM/EXTRA_PARAMS,全为探索项支持恢复默认/一键回退;`FieldSpec` 新增 `validator` 钩子做三态与范围校验);前端左栏新增「自动适配记录」面板(查看+清除 `POST /api/settings/llm-params/clear`);6 处调用点传目标(local_vlm→pipeline_a、cloud_decoupled→deepseek、ocr_client/name_pre_ocr/exam_service→按端点、practice/style→按分支);测试 `test_llm_params.py` 40 用例(omit payload 无键断言/画像/400 自愈与学习记录/设置中心),469 tests、build OK。
- 数据维护双入口(2026-09-21 第二十八轮):新增 `services/demo_data_service.py` + routes_maintenance 两端点(均需 `confirm=true`,挂设置管理员 + 数据解锁双守卫)。①`POST /maintenance/clear-all-data`:单事务清空 15 张业务表(`CLEAR_ORDER`:错因→任务→考试报告→考卷→考试→台账记录→登记项→练习卷→手写样本/模型→风格画像→花名册→合并日志→学生→班级)+ 清空 uploads 全部文件(先统计后删,失败仅告警);**保留 app_settings**(设置中心配置/提示词附录/加密密钥包裹/自动适配记录);结束后 `style_learning_service.reload_cache`(本轮新增公共刷新函数)同步失效内存风格缓存。②`POST /maintenance/seed-demo-data`:先走同一清空再写入完整示例数据——2 示例班级 + 12 人花名册/学生档案、24 篇已完成批改(`finalize_result` 渲染报告、错因标准化落库、含管线 B 取值)、1 个 WAITING_REVIEW 任务、1 场考试(6 份 DONE 考卷 → `_mock_exam_payload` + `generate_exam_report` 置 REPORTED)、台账(`create_presets` 6 项 × 6 人 × 3 轮)、1 份练习卷(`render_markdown`)、1 份生效风格画像(复用 `_mock_style_payload` 并 reload_cache)、25 张本地合成示意答卷图(PIL，拉丁字符避免缺字);计数走统一审计事件 `maintenance.clear_all_data` / `maintenance.seed_demo_data`(`_summary` 中文摘要)。前端 SettingsDialog 基础运行新增「数据维护」区块(与「清除演示数据」并列;清除为 error 危险色 + 二次确认弹窗(明文「该操作将删除全部数据且无法恢复」),恢复示例数据为普通样式 + 覆盖提示确认;结果 Alert 附「刷新页面」);client 新增 `clearAllData`/`seedDemoData` + types `MaintenanceClearResult/MaintenanceSeedResult`。测试 `test_maintenance_data_reset.py` 6 用例(确认门禁 / 清空全覆盖 + app_settings 保留 + 缓存失效 / 示例数据完整性(含统计层 build_rankings/build_distribution 非空)/ 幂等重建)。**安全教训(必守)**:路由层用 `settings.upload_path` 定位文件目录,测试凡调用能删文件的接口(清空/恢复/压缩)必须 autouse fixture 把 `settings.upload_dir` 重定向到 tmp_path 并断言 upload_path 位置,否则会误清 `backend/data/uploads` 真实数据(本轮已发生并部分不可恢复)。475 tests、build OK。
- 二维码识别与绑定答题卷(2026-09-21 第二十七轮):在「强制性姓名识别」链上新增"二维码优先"通道(完全本地,不改契约,前端零改动)。①新服务 app/services/qr_identity.py:decode_qr_identity(content)——负载格式 V1|<学号>|<姓名>,解码用 cv2(QRCodeDetector,随 RapidOCR 已分发,零新增);策略:原图直扫→缩图(>1800)直扫→找平路径(sheet_align _align_to_canvas→规范画布 QR 版式区 ROI(1183,197,1417,431)+Otsu/×2 上采样增强重试→画布全图)→宽容差版式 ROI(相对比例双档)→网格分块兜底;所有入 cv2 的图像统一 np.ascontiguousarray(实测非连续切片数组会导致检测失败——关键坑);异常静默 None;render_qr_png(qrcode,H 级纠错,静默区 4 模块)。②probe_personal_info 与 run_pre_ocr 均新增步骤 0:QR 命中即产出(basis=qr,不触发裁剪/OCR;预识别审计"来源 qr")。③后端只读端点 GET /api/classes/rosters/{id}/qr.png(确定性生成,无需入库;404/503 语义)。④前端:AnswerSheetPage 版式重构(QR 16mm 置于姓名行右侧 x≈174-190mm,位于姓名区裁剪窗外;四角块/次块/书写线几何不变;?classId&rosterId 绑定并渲染"本卷已绑定"及 QR;未绑定保持旧版式);CorrectionPage 打印入口改"选择班级/学生"对话框(未选禁打印)。⑤依赖:runtime 安装 qrcode 8.2 + requirements + selfcheck 行。验证:488 tests(+12 test_qr_identity 含真 cv2 生成→解码往返/旋转/端点;+3 probe QR 优先级/回退/异常);build+selfcheck 全过;HTTP 冒烟 7/7(含码卷旋转 3°→basis=qr+Li Ming/20260123;遮挡 40%→无缝回退 landmarks;旧卷 landmarks;端点 PNG 可解码);浏览器实机:打印页 QR 加载 370×370+6 定位块几何正确;对话框级联选择/按钮态正确;上传即识 QR 命中 60ms 直出 "Li Ming";零 Console 错误。
