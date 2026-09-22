# Spec 完成度对照与交付说明(第二轮 · 全量补齐)

> 日期:2026-09-18 · 范围:`plans` 目录下本项目全部 7 份规格文档 · 目标:逐份核对 → 补齐全部未完成项 → 打通跨模块数据链路 → 全量验证。
>
> 第一轮审阅与修复记录见同目录《一致性审阅与修复报告.md》;本文件为其续篇。

---

## 一、七份 Spec 逐份对照结论(补齐后)

| # | Spec 文档 | 补齐前状态 | 结论 | 说明 |
|---|---|---|---|---|
| 1 | 德语作文双管线批改系统(上传图片功能评估与优化) | 后端完成 / 前端未做 | ✅ 已完成 | 本次补齐前端:指派三模式(自动识别/文件名/按名单顺序)、花名册导入对话框、单篇手动指派、客户端压缩(browser-image-compression)、分片提交(每片 20 个文件复用批次 ID)+ 总体进度条、文件自然排序、objectURL 泄漏修复、HEIC 直传、ReviewPage 学生信息纠错 |
| 2 | 设置中心改造 | 未实施 | ✅ 已完成 | 注册表驱动(31 项)、视图/更新/重置/回退/测试/提示词微调 7 个端点、.env 原地写回(保注释/原子替换)、敏感值三态(不改/清除/设置)+ 脱敏回显、回环限制 + 可选令牌(恒定时比较)、前端抽屉全面重写 |
| 3 | 设置中心增强 | 未实施 | ✅ 已完成 | 运行模式三卡(正常/演示/开发,互不冲突)、探索项徽标 + 单项"恢复默认" + 快照"一键回退"(app_settings 存储)、developer 项前端隐藏 + **后端 403 强制门控**、需重启项标记 + 清单提示 |
| 4 | 作业台账与考试统计 | 台账后端完成/考试后端大部分/前端全未做 | ✅ 已完成 | 台账:前端三工作区(快速登记/汇总矩阵/明细)+ 登记项管理 + 预设;**修复全局登记项记录归属班级丢失**(batch 增加 class_id)。考试:routes_exams 全端点 + ExamService 接线(独立队列/孤儿自愈)+ 前端列表与详情页(上传/轮询/人工修订/报告生成与导出) |
| 5 | 统一统计层建设 | 未实施 | ✅ 已完成 | statistics_service(总表/分布/排行/趋势/CSV)+ routes_statistics 5 端点 + StatsPage 四工作区(x-charts 柱/折线图);**学生对齐合并**(姓名桶并入学号桶)+ 画像/考试/台账 OR 匹配修复(跨模块数据链路的关键闭环) |
| 6 | 示范学习风格迁移 | 后端约 90% / 前端与测试未做 | ✅ 已完成 | main 接线与风格缓存加载(第一轮已补)、`tests/test_style_learning.py`(8 用例)、StyleLearningPage(三步引导/画像管理/编辑/启停/删除/详情)、工作台"示范学习生效"徽标、Prompt 注入含教师附录二次叠加 |
| 7 | 安全加固与数据加密 | 未实施 | ✅ 已完成 | AES-256-GCM/AES-256-SIV 透明加解密(16 张表/列)、scrypt 口令派生、密钥包裹与恢复密钥、迁移(幂等可重跑/后台进度)、关闭(decrypt_all/keep_ciphertext)、Plan B 归档重建、审计日志(JSONL,不含密钥)、require_unlocked 数据接口 423 守卫、前端解锁拦截(全局弹窗)+ 完整加密管理区块 |

> 说明:安全审阅中的"多用户认证"按批准方案落地为"回环限制 + 可选访问令牌"(SETTINGS_ADMIN_TOKEN);CORS 白名单与速率限制仍为记录项(回环部署低风险)。

## 二、关键改动点

### 后端接口(新增/变更)
- 设置中心:`GET/PUT /api/settings`、`POST /api/settings/reset|rollback|test`、`GET/PUT /api/settings/prompts`;
- 数据加密:`GET /api/encryption/status`、`POST .../setup|unlock|lock|change-password|migrate|disable|recovery/generate|recovery/reset|planb/archive-reinit`;
- 考试:`GET/POST /api/exams`、`GET/PUT/DELETE /api/exams/{id}`、`POST /api/exams/{id}/papers`、`PUT /api/exams/{id}/papers/{pid}`、`POST .../retry`、`POST/GET /api/exams/{id}/report`、`GET .../report/export`;
- 统计:`GET /api/statistics/gradebook|gradebook/export|distribution|rankings|trends`;
- 变更:班级分析新增 `with_ledger/with_exam` 查询参数;台账批量登记新增 `class_id`;数据类路由统一挂 `require_unlocked`。

### 数据库表与迁移
- 新增表:`app_settings`(设置快照/提示词附录/密钥包裹);迁移 `e42a079e0596_add_app_settings`(当前 head);
- 加密覆盖列(存储兼容,无结构迁移):correction_tasks(姓名/学号/result/ocr_result/edited_report/topic/assignment_name)、students、class_roster、error_records、homework_records、exam_papers、exam_reports、exams。

### 前端页面与组件
- 重写:`SettingsDrawer`(730 行,含模式卡/分组/脱敏/测试/提示词/回退);
- 新增:`settings/EncryptionSection`、`settings/UnlockDialog`(全局 423 拦截)、`RosterImportDialog`、`utils/imageCompress`、`StyleLearningPage`、`LedgerPage`、`ExamsPage`、`ExamDetailPage`、`StatsPage`;
- 改造:`CorrectionPage`(指派/压缩/分片/排序/花名册/风格徽标)、`ReviewPage`(学生纠错)、`AppShell`(4 个新导航 + 标题映射 + 解锁弹窗)、`App.tsx`(4 条新路由)、`api/client.ts`(+577 行:全量接口 + 令牌头 + 423 事件)、`types/index.ts`(+340 行类型)。

## 三、验证方式与记录

| 验证 | 结果 |
|---|---|
| 后端回归测试 | **203 passed**(新增 61:settings 19 / encryption 13 / exams 8 / statistics 10 / linkage 6 / style 5;既有 142 零回归) |
| 前端构建 | `npm run build`(tsc + vite)通过,1807 模块 |
| 端到端冒烟 | 线程内起服 + httpx 检查 **18/18 端点 200**(SPA 首页、health、settings、prompts、encryption/status、classes、tasks、students、class-diagnosis(含联动参数)、ledger/items、ledger/students、exams、statistics×4、style×2) |
| 环境自检 | `backend/scripts/selfcheck.py` 全 [OK],退出码 0 |
| 依赖 | `cryptography>=43` 已加入 requirements 并安装至 `.venv` 与 `runtime/python`(离线运行时同样具备加密能力) |

## 四、数据链路打通说明(闭环)

```
批改完成 ──► correction_tasks + error_records(确定性加密姓名/学号)
考卷识别 ──► exam_papers(逐题/知识点)──► exam_reports(报告快照)
作业登记 ──► homework_records(班内归属修复)──► students upsert
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
  学生画像(读时聚合三源)  班级分析(可选叠加台账/考试概览)  统一统计层(总表/分布/排行/趋势)
        └───────────────┴────────────────┘
                        ▼
       学生错题本 / 统计页 / 工作台(风格徽标) / 考试报告(可导出)
```

- **对齐口径**:学号优先、姓名兜底;统计层同名唯一学号时自动合并"姓名桶"与"学号桶"(重名多学号不合并,保持可辨识);
- **联动触发**:全部为**读时聚合**,无跨模块写同步、无触发器,任一源变更即时反映(零一致性问题);
- **冲突处理**:台账批内 upsert(同日同项覆盖)、考卷同名续传多页、人工修订优先且不被重跑覆盖、班级合并保留来源标注;
- **加密一致性**:统计/画像/渲染链路经透明类型读写,启用加密后所有聚合逻辑与接口契约不变;锁定态下数据接口统一 423,由前端全局解锁弹窗接管。

## 五、残余记录项(不阻塞)
- SSE 实时进度(当前轮询)、自定义评分细则 Rubric、双管线交叉校验、桌面壳打包、CORS 细粒度白名单/速率限制(回环低风险,已有令牌方案)。

---

## 六、第三轮增强(2026-09-18):重新批改 / 转录编辑 / 批注核对 / 图片编辑 / 设置中心窗口化

### 交付清单
| 方向 | 内容 |
|---|---|
| 重新批改 | `POST /tasks/{id}/recorrect` + `recorrect_service`(原地重跑:白名单覆盖、原图校验、上期摘要快照、先清旧错因再重建、teacher 编辑版/寄语保留、`recorrect_count/last_recorrect_at` 追溯);前端参数确认弹窗 + 完成差异横幅 + 轮询进度;迁移 `49a0a23dc150`(5 列,含 server_default) |
| 转录原文编辑 | `PUT /tasks/{id}/transcript`(写回 result 并重渲染系统报告);审阅页转录区移至原图正下方(`TranscriptPanel`:就地编辑/取消;批注模式下装饰渲染 + 悬停/弹层) |
| 批注核对增强 | `errorStyle.ts` 19 类「颜色 + 9 种线型」唯一映射(拼写红/语法黄/地道表达绿等);`AnnotationPanel`(图例计数筛选、错误清单↔全文双向联动、Alt+←/→ 导航、复制全部修正、按类别导出、未定位兜底清单、打印配色一致) |
| 图片编辑 | `ImageEditorDialog`(参数化操作栈:灰度/黑白/±90°旋转/拖拽裁剪/「姓名栏选区」引导;上/下一张翻页与计数;撤销/重置原图;WeakMap 原图跟踪;仅编辑版上传且文件名不变);姓名栏不作独立识别通道的原因已在界面与手册中说明(整图 OCR 抬头文本 → 身份决策链) |
| 教师寄语/学生版显示 | `PUT /tasks/{id}/teacher-message`(可编辑,10 条场景化预设语);`render_student_report` 新增 `show_score/show_tips`(探索项,默认与历史行为一致) |
| 设置中心窗口化 | `SettingsDialog` 替代原 SettingsDrawer:居中大窗(maxWidth xl)+ 左“大范围”导航 × 右“小项”区;新增「教学与报告」分组(及格线 `SCORE_PASS_LINE` 打通统计分布/排行/考试报告,默认 60 保持历史行为)、「探索性实验项」聚合面板;基础运行新增工作台默认评分标准/细致度(`DEFAULT_GRADING_STANDARD/DEFAULT_DETAIL_LEVEL`,经 `/api/health` 下发) |

### 验证
- 后端:**230 passed**(新增 recorrect 12 / transcript 5 / settings_extras 10);迁移 head `49a0a23dc150`;
- 前端:`tsc + vite` 构建通过;
- 端到端冒烟(临时库/临时上传目录,mock 管线,**20/20 全绿**):上传→完成 → 报告编辑 → 转录修订与批注一致 → 重新批改(入队/追溯/上期摘要/重跑完成/细致度覆盖/教师编辑版保留/转录 v2 覆盖/错因重建一致)→ 设置与加密端点 200 → 健康检查默认值;
- 图片编辑的画布交互为纯前端实现(端点冒烟无法覆盖),已通过构建校验与人工路径说明覆盖。

### 兼容与零残留
- 新建键默认值与历史行为一一对应(及格线 60、默认 GAOKAO/MEDIUM、学生版不显示分数/显示练习重点);
- 重跑与转录编辑仅动本任务数据;错误记录“先清后建”;图片编辑仅客户端替换,落盘只有一个版本;冒烟使用临时目录,正式数据零触碰。

## 八、练习卷生成 + 存档图片压缩(2026-09-18 第七轮;spec 计划全部清零)

**后端**:practice_sheets 表(迁移 b924fa2a598d,外键 fk_practice_sheets_class_id);practice_service(来源聚合/证据摘要/mock 命题/解析修复/CRUD);routes_practice 6 端点(挂 _data_guard);PRACTICE_SYSTEM_PROMPT + build_practice_user_message;image_compress(EXIF 纠偏→长边超限缩放→格式分支;JPEG COM / PNG Software 压缩标记保证重跑幂等;原子替换)+ image_compress_service(存量后台进度+审计)+ routes_maintenance(设置管理员+解锁双校验);上传钩子 _maybe_compress(两处落盘);图片端点魔数嗅探 _sniff_media_type;设置注册表 +3(IMAGE_COMPRESS_ENABLED/MAX_SIDE/QUALITY)。
**前端**:PracticePage(生成对话框:来源多选/班级筛选/题型 chips/题数/归档班级;列表:打开/打印/删除)、PracticeDetailPage(双页签+导出 .md+打印+删除)、PrintPracticePage(试卷+答案两页独立路由);client +8 接口、types +9 类型;AppShell 导航与标题;App.tsx 三条路由;SettingsDialog「上传与图片处理」内嵌「压缩存量图片」(进度+结果);使用指南 +2 条。

**验证**:pytest 275 passed(新增 test_practice.py + test_image_compress.py 共 23);前端 build 通过;端到端冒烟 20/20(压缩 8.79MB→2.00MB、长边 3400→2200、生成契约 6 题、二次批量 0 变更、删除确认)。


## 九、OCR 忠实性增强 + PDF 上传(2026-09-18 第八轮)

**落地**:需求一 提示词零修正红线(反例 ich findet;自检逐句反查/修正痕迹扫描);需求二 OCR 契约 v2(recognition_quality/quality_note,评分用户消息可选透传,默认逐字节不变)+ TranscriptPanel/ReviewPage 低质量提示;需求三 [unsicher:猜测文本] 二级标记(保留 [unleserlich])+ 前端红/黄高亮 Tooltip + 评分侧不据此扣分;需求四 PDF 上传全链路(pypdfium2 选装、单篇=多页/批量=每页一篇、ZIP 内支持、60 页/50MB/展开计入批量上限、加密/损坏 400)。

**仅评估未实现**:完全杜绝模型修正(不可行,提示词+复核兜底);标记点击跳转原图定位(需 OCR 坐标);低质量自动强制人工复核;打印视图标记着色。

**验证**:pytest 288 passed(新增 test_ocr_quality.py 10 + test_pdf_expand.py 3);前端 build 通过;冒烟 10/10(批量 PDF 2 任务、单篇多页合并、页图 JPEG、ZIP 混合 3 任务、损坏 400)。


## 十、批改意见排版 + 示例数据 + 启动选择(2026-09-18 第九轮)

**落地**:A 结构化排版(report_renderer 独立引用块+三色 Callout+解析拆条;MarkdownReport/TranscriptPanel 同构;覆盖审阅三视图/导出/打印);B 示例数据(RosterImportDialog 占位符中性化;POST /maintenance/clear-demo-data 按演示样例身份清理+设置面板按钮);C 启动门禁(STARTUP_DATA_PICKER 设置项/health 暴露;StartupDataDialog 导入数据包或直接进入,会话跳过;未处理前零数据请求,不再打开即弹密码)。

**仅评估未实现**:explanation 回写结构化字段(改契约);Timeline 图形化;数据库 is_demo 列;最近数据包路径记忆;导入前包预览端点。

**验证**:pytest 291 passed(新增 test_maintenance_clear_demo.py 3);前端 build 通过(ReportBlockquote/StartupDataDialog 类型校验)。


## 十一、连续审阅 + 队列分组(2026-09-18 第十轮)

**落地**:审阅页「上一份/下一份」(范围=同批次优先,否则同班级+作业;顺序=创建时间升序;首尾禁用;fetchTasks 按范围键缓存;切换全量重置视图状态防串数据;←/→ 快捷键;replace 导航);队列页按「作业名称·班级→批次→其他」分组渲染(组头三态全选+折叠+计数;筛选/多选/批量重试删除导出打印/轮询/空状态零改动)。

**仅评估未实现**:超 200 条兄弟列表分页导航;URL 携带来源上下文;跨组拖拽选择。

**验证**:npm run build 通过(类型与组件编译)。


## 十二、启动解锁流程重构(2026-09-18 第十一轮)

**落地**:根因=UnlockDialog 挂载自察状态自动弹出;修复=双模式化(受控/非受控,删除挂载自弹,保留 423 事件通道)+AppShell 分阶段门禁(等待健康→选择数据→加密检查→按需解锁→工作台;执行选择前零数据请求且不触加密模块;解锁后写会话跳过;受控解锁不刷新页面)+导入遇锁提示语。

**仅评估未实现**:被动 423 驱动替代主动状态查询;解锁成功全局免刷新(收益低)。

**验证**:npm run build 通过;手动清单:启动无解锁弹窗/直接进入后受控解锁/导入遇锁提示/未加密直入/设置关闭后历史行为/会话中 423 行为不变。


## 十三、OCR 异常检测与自愈(2026-09-18 第十二轮)

**落地**:检测器 ocr_anomaly.py(五信号加权评分,阈值/开关/次数可配);管线 B OCR-only 预算内重跑+自评纠偏+异常提前强制复核(默认开);管线 A 整管线重跑+可见降级提示(overall_comment 追加并重渲染);设置项×4(pipeline_b_ocr 组)+SettingsUpdateRequest;审计与日志全链路可追溯;契约/字段零改动。

**仅评估未实现**:德语词典/语言模型畸变打分;重试时 OCR 提示词加强;A 强制人工复核(状态机前提限制)。

**验证**:pytest 303 passed(新增 test_ocr_anomaly.py 8 + test_cloud_anomaly_retry.py 4;含用户异常样本命中与正常样本零误报);前端零改动不构建。


## 十四、macOS 适配版 + 应急启动版(2026-09-19 第十三轮)

**落地**:macOS 四脚本(启动/停止/自检/首次制作)+ setup_macos_runtime.py(架构自适应下载 python-build-standalone、装依赖、bash -n 校验、chmod +x、quarantine 清理、自检收尾);应急版双平台启动器(应急启动.bat GBK+CRLF / 应急启动.command)+ 应急配置.env(8 键明文,gitignore)+ example;文档(docs/macOS便携版制作与使用.md、手册 §16.17/§16.18、README);业务代码零改动。

**验证**:pytest 303 passed(零回归);前端 build 通过;实测:进程 env 覆盖 .env(OVERRIDE_PREFIX=TESTMARK)、应急配置 cmd 解析 8/8 键、backend/.env 哈希前后一致(零污染);.command 字节检查(LF/无BOM/shebang)通过(修正了 CRLF 问题)。

**仅评估未实现**:Mac 实机全路径验收与预烧 zip 发布(需 Mac 执行一次制作器);Intel 包发行策略;应急密钥轮换 SOP。


## 十五、管线 A 人工复核能力对齐(2026-09-20 第十四轮)

**落地**:local_vlm 三分支重构(确认转录→仅评分/开启复核→识别挂起/关闭→单次原样);共享自愈 run_ocr_with_guard(ocr_anomaly.py,B 调用处重构行为不变);服务层修复守卫(ctx.ocr_result 非空不再整管线重跑/追加提示);mock 复核去管线限定;后端文案去'仅管线 B'（schemas/base/main/routes_*）；前端 CorrectionPage 开关常显+提交修正+文案动态、ReviewPage 复核文案按管线动态；文档(README/手册第六章/ARCHITECTURE/SKILL)差异表述统一为'仅后端模型不同'。

**验证**:pytest 325 passed(新增 test_pipeline_a_review.py 5 用例:挂起/确认续跑不重识别/异常重试恢复/持续异常纠偏/关闭复核回归);冒烟 18/18(A 复核全链路、关闭复核、B 回归、批量独立挂起);前端 build、runtime selfcheck 通过。

**仅评估**:confirm 后丢弃 recognition_quality/quality_note(两管线既有语义一致);A 单次路径进度值(0.2)与 B(0.1/0.6)差异为历史行为,本轮仅对齐'开启复核'路径。


## 十六、视觉误报修复 + 标准答题卷适配(2026-09-20 第十五轮)

**根因**:UI 的「未收到任何图片,无法进行转录。」来自模型自写的 quality_note——终端点/模型未接收图片(或图片缺失)的调用异常被下游当成'识别质量低'。

**落地**:F1 存在性守卫(oc Client.extract/ local_vlm._run_ocr_stage);F2 raise_if_vision_failed(ocr_anomaly 模式表,命中即 PipelineConfigError 含替代路径,不再进质量纠偏链路);F3 reasoning-only 空 content 明确报错(llm_client);F4 连通性视觉探针(settings_service,测试连接对 OCR 端点自动带图探测);诊断工具 backend/scripts/diag_vision.py(保留为常驻工具,与计划的'删'有调整——校方端点排查可复用);标准答题卷:services/sheet_align.py 纯 PIL 找平(四角块检测+QUAD 透视+空白度校验,未命中/异常原字节回退)+SHEET_ALIGN_ENABLED+落盘钩子(两处链,PDF/ZIP 自然覆盖)+前端 /print/answer-sheet 模板页与工作台入口。契约零改动。

**验证**:pytest 342 passed(新增 test_vision_guard 10 用例、test_sheet_align 7 用例);前端 build;runtime selfcheck;冒烟 8/8(旋转卷→1500×2121 且角块/信息区就位;普通图尺寸不变);diag_vision dry 演练正常。

**仅评估**:校方若确为纯文本端点→OCR_* 指向独立视觉端点即可(B 架构原生支持,无需改码);OMR/自动判分与拍摄引导增强不做。

`n## 十七、视觉发送层 jpg 归一化(2026-09-20 第十六轮)`n`n**背景**:校方技术中心确认其端点按标准 OpenAI 格式接受「直接上传 jpg」——原发送层按扩展名猜测 MIME,PNG/WebP 源会以非 jpg 发送,且大图可能被网关静默丢弃(表现为"未收到图片")。`n`n**落地**:llm_client.image_to_data_url 统一 JPEG 发送——jpg 小图原字节直发(零重编码);非 jpg 自动转码(RGBA/LA/PA 白底合成);>3.5MB 或长边>2400px 触发缩边重编码护栏;解码失败/编码异常原字节回退。一处修改,四条链路生效(A 单次执行 / A 复核识别 / B OCR / 考试 OCR)。diag_vision.py 扩为五载荷(jpg_data_url / png_data_url / raw_base64_jpg / dual_jpg / text_only),可一键定位「端点只吃 jpg」「不支持多图」「网络问题」三种差异。`n`n**验证**:pytest 349 passed(新增 test_image_payload.py 7 用例);diag_vision dry 五载荷构造正常;全量 selfcheck 通过。`n
`n## 十八、答题卷精修 + 找平校正 + 显示逻辑修复(2026-09-20 第十七轮)`n`n**模板(AnswerSheetPage)**:信息区改 CSS grid 两列(label+弹性下划线,结构性消除重叠/错位);页眉/页脚移出信息区;双类多排定位块——主块 4×16mm(整页找平)+次块 2×8mm(y68mm,姓名/正文学区边界);书写线 21 行(y84 起,9mm 距);全页显式黑白+printColorAdjust,暗色主题/打印一致。`n`n**找平(sheet_align)关键修正**:原实现"块心四边形→内缩 52px 矩形再贴入"存在双重缺陷——① 与模板 mm 几何不一致(~14% 系统缩放偏差);② QUAD 会裁掉源四边形之外内容 → 四角块被裁掉一半(实测"57px 半块之谜")。现改为:精确 mm 映射(px=1500/210)、四象限近角优先粗定位→原分辨率 bbox 精测(±2px)、仅缺 1 角按平行四边形外推、源四边形外推为"整页四角"(块心距页边 20mm,框 170×257mm)后 QUAD 直接输出全幅 1500×2121(块完整、坐标与理论精确对应)。过程曾尝试画布环测仿射精修,经实验证伪(旋转卷残差为检测读数噪声,补偿会叠加)后移除。精度实测:端正卷四角/信息区/次块误差≈0px;旋转 8° ≤18px(画布 1.2%)。回退语义不变(未命中/异常→原字节)。`n`n**姓名小图**:新增只读端点 GET /tasks/{id}/name-crop(找平命中按 NAME_REGION(86,310,1086,416)+次块校准裁剪;普通照启发式 72%×40%;宽 900 JPEG;失败 404 前端静默);审阅页原图区主图调为中号(52vh)并在图下新增姓名小图条(点击看大图);ImageEditorDialog 首帧空白修复(loading 期间 canvas 卸载,redraw 用 ref+rAF 在挂载后补绘)。`n`n**开关**:SHEET_ALIGN_ENABLED(设置中心"上传"组,默认 true;关=跳过找平,name-crop 自动退回启发式)。`n`n**验证**:pytest 352 passed(test_sheet_align 扩为 9 用例含新几何/次块/name-crop 三态);前端 build;runtime selfcheck;冒烟 11/11(旋转卷→1500×2121、信息区/次块落位、name-crop 900×95≈9.4:1、普通图尺寸不变+启发式 576 宽)。`n

## 十九、任务核验(第4/5/6/7项) + 上传姓名预识别与大预览(2026-09-20 第十八轮)

**核验结论**:第4项=部分落地→本轮补齐(AnswerSheetPage 窄屏分级 zoom+滚动兜底,打印强制还原100%);第5项=已落地(ImageEditorDialog redrawRef+rAF 补绘);第6项=已落地→本轮升级为大预览卡(420px);第7项=部分落地→本轮补齐「上传后即刻识名」(预识别服务)与工作台大预览卡。

**识图方案**:不训练自研模型(需标注数据+GPU,评估否决);采用现成引擎直用——端点优先(复用既有 OCR 提示词/解析/视觉失败守卫)+本地 RapidOCR(rapidocr_onnxruntime 可选组件:完全离线、CPU、PP-OCR 字符集含中文与拉丁拼音);真实本地验证:RapidOCR 识别合成姓名区,Li Ming/20260123 解析完全正确。

**落地清单**:services/name_pre_ocr.py(条件更新防覆盖:仅活跃状态可写;踩坑记录——SQLAlchemy JSON 列 None 默认序列化为文本 null 而非 SQL NULL,result IS NULL 条件永不命中);NamePreOcrService(lifespan 启停,2 worker)与路由两处 schedule;NAME_PRE_OCR_ENABLED 设置;requirements +rapidocr_onnxruntime(约+150MB,缺失自动降级);前端三处:CorrectionPage 大预览卡、ReviewPage 姓名大预览卡+预识别占位、AnswerSheetPage 窄屏 zoom。

**验证**:pytest 364 passed(新增 test_name_pre_ocr.py 12 用例:解析器/引擎链/防覆盖/静默降级/mock短路/临时文件清理/调度幂等);前端 build;runtime selfcheck;冒烟 7/7(姓名在 PROCESSING 阶段先行出现、完成后保持、批改结果无影响、name-crop 可用)。

**分发影响**:离线包体积 +约150MB(本地识图组件;可由教师移除,移除后仅用端点)。


## 二十、续写纸识别与自动并页(2026-09-20 第十九轮)

**多元化定位点**:页底类型带(y277mm,三槽位 L/C/R x=90/105/120mm,8mm 块,位串表驱动 PAGE_TYPE_MAP:101=续写页、无/未登记=home);检测仅在找平成功画布上执行(普通照片永远 home,双重防误判);align_standard_sheet 扩展三元组返回,调用面(路由钩子/测试)全量适配。

**自动并页**:续写纸模板(主块×4+底带 L/R+无信息区+26 行线)与工作台入口;批量链路阶段二产出一维页序(含 page_type),新增 _group_pages 相邻归页(续写并入上一组;首张续写=孤儿组+审计);阶段三指派与阶段四建卡改按组(多页任务);max_batch_files 按组数;单篇接口零逻辑改动;契约零改动(image_paths 多页即既有语义)。

**验证**:pytest 373 passed(test_sheet_align 12 含类型带 3 用例;test_upload_merge 6 用例);前端 build;runtime selfcheck;冒烟 7/7(首页+续写→1 任务 2 页且报告正常;孤儿续写独立任务;name-crop 正常)。

**仅评估**:更多纸张类型启用(表驱动预留,如草稿页 100 仅登记不启用)。


## 二十一、手写样本模型 + 全局等待态(2026-09-20 第二十轮)

**定性**:本地训练=本地手写样本模板模型(特征签名库+花名册约束近邻判别,非神经网络训练;真 NN 训练评估否决)。

**落地**:handwriting_samples/handwriting_models 表+迁移 c71d9f4b2a30;handwriting_service(句库/326 维特征/档位推荐/近邻匹配/处理链+进度 ETA);routes_handwriting 全端点;name_pre_ocr 按档位增强;status_service 真实自检(10s 缓存)与 overview 聚合(批改/预识别/压缩/训练,percent+ETA,数据不足如实 null);/status/overview(不挂守卫,锁定时仍可用);前端:花名册对话框 Tab 化+手写模型区块(素材/抄写卡/上传/训练进度/绑定/概览/删除)+抄写卡打印页+SystemStatusBar(真实短语模板)+设置页档位画像行;NAME_PRE_OCR/ HANDWRITING_OCR_LEVEL 设置项;requirements 已有 numpy/rapidocr。

**验证**:pytest 389 passed(test_handwriting 9:句库/特征稳定与维度/推荐矩阵/手动优先/近邻命中/空库/模型聚合;test_status_overview 7:自检真实项(存储被文件占位时短语必变真实警示)/缓存/overview 批改进度 percent=60 eta=30/queue 容错);前端 build;runtime selfcheck 全部通过(生产库已升级至 c71d9f4b2a30);冒烟 12/12(素材→样本→训练完成[2/2]→模型就绪[16 核/14.9GB→medium 推荐]→批改中 overview 双条目→真实自检短语「数据库正常 · 存储可写 · 磁盘余量 58GB · 识别引擎就绪(识别端点已配置) · 前端产物就绪」)。

**过程记录**:生产库迁移版本落后(practice_sheets+handwriting 两支)已在验证中真实补升;data/ 下发现历史调试残留 _dbg2.db(无迁移表,非本轮产物,建议教师确认后清理)。

## 二十二、UI 性能承载评估 + 三档 UI(2026-09-20 第二十一轮)

**评估结论**:量化盘点(transition 5/boxShadow 9/gradient 9/backdrop-filter 0/动画 1/无路由过渡;Charts 3 页)——均衡档在低配设备可接受;高开销项=Charts 动画、Ripple+控件过渡、双阴影与浅色渐变(均轻-中)。

**落地**:theme.ts 三档派生(默认 balanced 逐字节保持既有;efficiency: durations 全 0+全局过渡动画兜底 0.001ms+MuiTouchRipple 关闭+shadows 全 none+去工作区渐变+Charts skipAnimation;premium:统一 easeOutQuint 曲线与 200/300/420 分层+25 级多层环境影/微光+按钮 hover -1px+Card 影随动+双径向背景);UiProfileContext 即时生效;顶栏 Tune 菜单与设置中心(UI_PROFILE, basic 组)双入口互同步;localStorage 缓存+后端权威启动校准;打印页三档一致。

**验证**:前端 build 通过(修复 Stack 漏导);pytest 389 passed 零回归;runtime selfcheck 全部通过;手测清单(三档×明暗×lg/窄屏):波纹/图表动画/阴影/渐变按档位生效、切换即时、刷新保持、双入口同步——待教师在浏览器确认观感。

## 二十三、班级管理独立 + 三档深化 + 台账报错修复(2026-09-20 第二十二轮)

**需求二(修复)**:「作业台账→登记明细」渲染崩溃根因为前后端契约字段错位——后端 GET /ledger/records 返回 {total, items}(schemas.LedgerRecordListResponse),前端 fetchLedgerRecords 曾声明并读取 {total, records},导致 setRecords(undefined)后 records.map 抛 TypeError(必现,与筛选条件无关);修复为前端适配层读取 items 并映射回 records 语义(后端契约零改),同扫描确认 client.ts 全部内联类型仅此一处错位。

**需求一(三档深化)**:极简档——灰阶调色板(装饰色全部去色,仅保留低饱和 success/warning/error/info)、纯色背景、默认色 Chip 去填充转描边、维持零动效/零阴影/无渐变/图表禁动画;高级档——页面切换内容淡入(AppShell 重放 CSS 动画,不 remount)、按钮悬停微抬+按下回弹+contained 主色微光、IconButton 悬停缩放、MenuItem 过渡、卡片顶部内高光与 hover 影跃升+边框微亮、focus-visible 光晕、品牌色选中文本、PageHeader 标题渐变细线;两档均由 theme.ts 同一 Token 派生,页面零分叉;显示名 efficiency='极简' 同步注册表与 .env.example 注释(值域不变,默认设置项零新增)。

**需求三(班级管理独立)**:新增与「批改工作台」并列的「班级管理」导航项(/classes,含子路由 /classes/:section:overview|roster|handwriting|package|merge|analytics);新页 ClassHubPage 组装六个区块——班级总览(新建/删除,前端新增 deleteClass 调用既有 DELETE /classes/{id})、花名册(自 RosterImportDialog 提炼为页内 RosterSection,原对话框删除)、手写模型(复用 HandwritingSection)、数据包与班级合并(自班级分析页迁移为页内区块)、共性错因分析(AnalyticsPage 改造为内嵌区块:去 PageHeader、移出管理操作);CorrectionPage 瘦身(花名册/新建班级入口改为跳转,移除对话框与相关状态);LedgerPage 提示文案同步;/analytics 重定向至 /classes/analytics(旧链接兼容);后端接口与 JSON 契约零变更。

**验证**:npm run build 通过(1844 模块);后端 pytest 389 全量通过;runtime selfcheck 全部 [OK];浏览器端到端复验全通过——导航为「批改工作台→班级管理→…」(班级分析独立项已移除)、六标签逐一渲染正常、/analytics 自动重定向、登记明细无错误卡片且 Console 无 map 报错、三档切换即时生效且刷新持久;全程无 Console 错误、无失败请求。对「档位切换样式刷新」做了双重运行时取证(className/CSSOM/动画时间轴):在隐藏视口下浏览器冻结动画时间轴(document.hidden → currentTime 恒 0)导致 CSS 过渡停在起点快照,属测试环境现象而非产品缺陷——前台可见浏览器中 320ms 过渡正常完成,刷新/首帧场景直接呈现终值。

## 二十四、上传即 OCR + 依据定位点裁切:根因根治(2026-09-20 第二十三轮)

**根因(全部有实测证据)**:(1) 定位点检测“几乎必然假阳性”——旧 _detect_corner 以 0.4% 极低暗点阈值在缩略图求“近角质心”,_refine_center 精测失败静默保留错误粗值,_detect_fiducials 返回前无“块心必须真黑/成形”校验(实测:普通照片 4 检测点中 3 点处暗占比 0.0,当前代码仍对其 changed=True 假找平,把照片错乱裁成画布);(2) 姓名区裁切依赖二次检测/二次找平,失败即退“与定位点无关”的启发式(72%×40%);(3) WAITING_REVIEW 不在预识别状态白名单,且裁剪失败/状态不符等分支静默 return 不写审计——真实上传任务在 audit.log 中零条 name.pre_ocr 记录,链路不可回溯。

**修复**:(1) sheet_align 检测重构为“块真实性三级校验”:缩略图 48×48 网格连通暗簇候选(距角升序≤3,逐候选原图复核)——_block_center_in_window(96×96 下采样;暗占比;bbox 填充率≥0.55;宽高比 0.45-2.2;expected_size 尺寸先验 0.35-1.6×块边长估计,杀“黑带穿窗”;排斥整窗近全黑);(2) 新增 _is_canonical_canvas(1500×2121±4)与 _detect_blocks_at_targets“理论锚定”检测(四角理论位置 ±64px 窗口,4/4 命中方可信——画布坐标由上传时的定位点建立);(3) 新增 crop_name_region_ex 返回 (bytes|None, meta)(meta: basis=canonical|landmarks|none、main_points(裁切所依据的实测块心)、marker_points、region、note),裁切三条路径:规范画布→锚定 4/4→规范几何裁;原始照片→定位点检测→找平→画布锚定复检 4/4→规范几何裁;无定位点依据→返回 None(废除启发式裁切,改由上层整图识别);(4) name_pre_ocr:_ACTIVE_STATUSES 增 WAITING_REVIEW(复核挂起时 result 为空,预识别先行展示、正式结果照常覆盖)、无裁切时 _scaled_full_image 生成整图输入、全分支审计(跳过/文件缺失/无输入/未识别/写入/异常)且 detail 含“定位点依据 basis(块 n/4)”。

**验证**:(1) 数据回归(本机真实图片只读):任务 27 普通照片修复后 changed=False(不再假找平)、任务 29 假画布 crop_name_region_ex 返回basis=none(拒绝误裁);(2) 单测:pytest 394 passed(+5:黑带照片不得假找平/无定位点不裁/画布锚定裁/landmarks 裁/WAITING_REVIEW 可写/整图回退);(3) 端到端冒烟(隔离临时环境,真实云端引擎):上传旋转 5° 合成标准卷→审计 name.pre_ocr“来源 ocr-endpoint;定位点依据 canonical(块 4/4);写入字段 student_name/student_id;命中 1;耗时 3889ms”,DB 写入 Li Ming/20260123;上传无定位点照片→“定位点依据 full-image”整图识别写入——整条链路“上传→定位点检测→依据定位点裁切→OCR→结果产出”全部打通。

## 二十五、纯本地 OCR 模式(LOCAL_OCR_ONLY)集成(2026-09-20 第二十四轮)

**需求**:新增可开关的「纯本地 OCR 模式」——识别(OCR)完全本地化(RapidOCR,离线 CPU,不调用任何云端/远程 OCR 端点,含 OCR_BASE_URL 的 vlm_openai 与 Azure),评分及其后续链路(DeepSeek 评分 / 报告渲染 / 复核流程)保持现状。

**落地**:(1) 设置四处同步:LOCAL_OCR_ONLY 默认 false(config.local_ocr_only / settings_service FIELDS[pipeline_b_ocr 组,switch,中文标签与说明] / schemas.SettingsUpdateRequest / .env.example);(2) OcrClient 新增「适配器 3」:_extract_local_rapid——extract 在图片存在性校验后短路到本地引擎;整页 RapidOCR 逐页转录拼接 transcribed_text;姓名/学号优先对首页 crop_name_region_ex(依定位点裁剪)的姓名区小图识别并复用 name_pre_ocr.parse_identity,裁图未解析出的字段用首页整页文本逐字段回填;全行平均置信度映射 recognition_quality(≥0.92 high/≥0.80 medium/其余 low)并附 quality_note;组件缺失时抛 PipelineConfigError(明确提示,绝不静默回退云端);extract 签名与 OcrExtractionResult 契约不变。(3) name_pre_ocr 扩展:recognize_lines(带置信度)/rapid_available,_extract_via_local_rapid 改其薄包装(既有调用与测试桩兼容);run_pre_ocr 在开关开启时跳过全部端点、仅本地引擎(确保完全离线)。(4) runtime 便携环境安装 rapidocr_onnxruntime 1.2.3(含 onnxruntime/opencv,分发体积约 +150MB);一键自检新增可选组件行。

**不动的部分**:cloud_decoupled 的两段式与 _run_grading(DeepSeek 评分)、run_ocr_with_guard(异常自愈/强制复核)、WAITING_REVIEW 复核状态机、报告渲染、故障转移逻辑;管线 A(识别+评分均由本地 VLM 完成,本开关不影响——其识别本就在本地);前端与全部 JSON 契约零改动。

**验证**:pytest 402 passed(+8 用例:本地短路契约/无定位点回填/组件缺失配置错误/全页空文本网络错误/开关关闭走原 provider/预识别跳过端点/注册表与 UpdateRequest 同步);前端 build 通过;runtime selfcheck 全部通过(新增「可选组件:本地 OCR(RapidOCR)可用」);端到端冒烟(LOCAL_OCR_ONLY=true 且 OCR_BASE_URL 指向不可达 127.0.0.1:9):上传旋转 5° 合成标准卷 → 本地识别姓名 'Li Ming' / 学号 '20260123' → 任务 COMPLETED、评分产出 overall_score '23 / 25'——4/4 通过:不可达端点下识别仍成功,证明 OCR 完全本地化,且评分链路与既有行为一致。

**仅评估**:花名册纠错在 OCR 层应用(extract 签名不含 class_id,按「签名不变」约束未做);管线 A 识别强制 RapidOCR(其识别本就在本地,未做);考试模块与手写样本链路本地化;复核界面「本地模式」角标与会话探针适配。

## 二十六、强制性姓名识别(选文件即识)(2026-09-21 第二十五轮)

**需求**:新增开关「强制性姓名识别」(FORCE_NAME_RECOGNITION,默认关):开启后教师在工作台选择/拖拽文件(单张、批量、ZIP、PDF)的瞬间,即用本地引擎识别姓名/年龄等个人信息并直接显示在文件列表;无需点击「开始批改」;识别强制本地(RapidOCR 离线 CPU,不调用任何云端/远程端点);关闭时行为与现状完全一致。

**落地**:(1) 后端新增只读探测端点 POST /api/corrections/probe-identity(纯内存:不落盘、不建任务、不入队、不写库;开关关闭→403;组件缺失→503;ZIP 复用 _extract_zip_images、PDF 复用 expand_pdf;逐张 asyncio.to_thread 串行识别,返回 items[index/source_name/name/age/student_id/status]);(2) name_pre_ocr 新增 parse_age(alter/aler/age/jahre/年龄/岁 关键词邻近+1-120 过滤)与 probe_personal_info(依定位点裁姓名区小图优先→整图兜底;组件缺失明确 error);_STOPWORDS 增补 alter/aler/age/jahre(防污染姓名);parse_identity 新增 require_keyword 关键字参数(整页文本只认「姓名/Name」关键词,防正文误报;默认 False 保持既有行为);(3) schemas 新增 IdentityProbeItem/IdentityProbeResponse + SettingsUpdateRequest.force_name_recognition;config/设置注册表(upload 组,switch)/.env.example 三处同步;(4) 前端 client.probeIdentity + CorrectionPage:addFiles 新增文件即触发探测(限并发 2,逐张「识别中…」→结果渐进更新);开关开启时文件列表的「文件名位置」改为识别结果显示(「姓名 · 年龄 N」;ZIP/PDF 逐页多行「第 N 页 · …」;未识别→「未识别到姓名」;失败→「识别失败(原因)」;文件名移入悬停提示);移除/清空/编辑文件时同步清理与重探测;页面挂载读取开关并为已选文件补齐探测。

**与「上传后姓名预识别」的区别**:本功能=选文件瞬间(无任务、不落盘、不持久化、强制本地);预识别=任务创建后(后台服务、端点优先/本地兜底、回写任务 student_name/student_id)。两者独立开关、互不冲突;本功能关闭时界面与现状完全一致。

**验证**:(1) pytest 425 passed 零回归(本功能 +14:parse_age 5/probe_personal_info 3/端点 4(403·503·单图·ZIP 页序)/设置同步 2);(2) npm run build 通过;runtime selfcheck 全部通过;(3) HTTP 级端到端(隔离临时环境、真实本地引擎):单图返回 {name:'Li Ming', age:'16', status:'ok'};ZIP(2 页)返回两条有序结果(Wang Wei/17、Zhang San/15);运行期关闭开关→403——8/8 通过;(4) 浏览器 UI 级复验(真实页面注入文件、全程不点击任何提交按钮):选文件瞬间出现「识别中…」→ 稳定为「Li Ming · 年龄 16」;ZIP 条目逐页显示「第 1 页 · Wang Wei · 年龄 17」「第 2 页 · Zhang San · 年龄 15」;Console 零错误、零失败请求。

**边界(仅评估)**:标准答题卷模板新增「年龄」信息栏(现为宽松解析学生手写);探测结果随提交流程持久化(当前仅列表展示,不落库);高页数探测并发按核数自适应。

## 二十七、强制上传即识:顺序保证与可验证性加固(2026-09-21 第二十六轮)

**要求**:「强制性姓名识别」开启时,必须严格"先依定位点自动裁剪→再对裁剪产物识别",同一时点自动串联,顺序不可颠倒/跳过/合并;无定位点按既有约定回退且不中断;不得对未裁剪原图直接识别。

**复查结论(实现已达标,附证据)**:probe_personal_info 先 crop_name_region_ex 后 recognize_lines,识别输入恒为裁剪产物;crop_name_region_ex 全分支均需定位点检测通过才裁(canonical=四角理论锚定 4/4;landmarks=全象限检测[块真实性三级校验]+找平+画布锚定复检 4/4;任一不通过→(None,meta));无定位点→整图回退继续识别(不中断);裁剪与识别在 asyncio.to_thread 内同步顺序执行,无颠倒可能。

**加固(本轮)**:①probe_personal_info 透传裁剪依据 basis(canonical/landmarks/full-image)并加 debug 日志(不产生日常噪音);②IdentityProbeItem 契约新增 basis 字段(端点透传;前端类型镜像,UI 不展示,保持"文件名位置只显示识别结果"不变);③新增顺序契约测试 4 例:调用序列 crop→recognize 且"识别输入内容==裁剪产物"(非原图)、无定位点回退整图且不中断、canonical 透传、端点 basis 透传。

**验证**:pytest 429 passed(+4 顺序契约用例)零回归;npm run build 通过;runtime selfcheck 全部通过;**三路径 HTTP 端到端**(隔离临时环境、真实本地引擎):①旋转 5° 合成标准卷→basis=landmarks + Li Ming/16;②端正 1500×2121 规范画布→basis=canonical + Wang Wei/17;③无定位点普通文字图→basis=full-image + Zhang San/15 且 status=ok(流程不中断)——7/7 通过,以真实响应证明"识别依据=依定位点裁剪产物(或明确标记的整图回退)"。

**仅评估**:①UI 展示裁剪依据角标或裁剪小图预览(与"文件名位置只显示识别结果"的既有要求取舍后再定);②探测审计落盘(当前仅 debug 日志,避免只读端点写审计噪音);③批量探测并发自适应。

## 二十八、二维码识别与绑定答题卷(2026-09-21 第二十七轮)

**背景**:上一轮"强制上传即识别"依赖本地 OCR(约 0.3-1s/张);本轮按评估结论落地"二维码优先"通道——为每位学生在标准答题卷上打印身份二维码,上传时本地解码直出身份(更快、更省 CPU),未命中无缝回退既有链。

**落地**:(1) 新服务 app/services/qr_identity.py:负载 V1|<学号>|<姓名>(确定性,无需入库);decode_qr_identity 多级策略(原图直扫→缩图→找平路径:sheet_align 找平至规范画布→QR 版式区 ROI[含 60px 边距]+Otsu/×2 上采样增强重试→画布全图;无定位块时宽容差版式 ROI 双档;网格分块兜底);实测关键坑:cv2 对非连续切片数组检测失败,全部入参统一 ascontiguousarray;组件缺失/异常一律静默 None。render_qr_png(qrcode,H 级纠错,静默区 4 模块)。(2) probe_personal_info 与 run_pre_ocr 新增步骤 0:QR 命中即产出身份(basis=qr;不触发裁剪/OCR;预识别审计"来源 qr")——完整保留既有"先依定位点裁剪→识别"顺序链作为回退(契约 basis 描述新增 qr 取值,端点/前端零改动)。(3) 后端只读端点 GET /api/classes/rosters/{id}/qr.png(404/503 语义;Cache-Control no-cache)。(4) 前端:AnswerSheetPage 版式重构——QR 16mm 打印于姓名行右侧(x≈174-190mm,位于姓名区裁剪窗 x12-152mm 之外),四角主块/次块/正文书写线几何一律不变;支持 ?classId&rosterId 绑定(渲染"本卷已绑定:姓名(学号)"),未绑定保持旧版式;CorrectionPage"打印标准答题卷"入口改为"选择班级/学生"对话框(复选级联、未选禁打印)。(5) 依赖:runtime 安装 qrcode 8.2(纯 Python)+ requirements.txt + selfcheck 新增组件行。

**验证**:(1) pytest 488 passed 零回归(本批 +12 test_qr_identity:负载往返/真 cv2 生成→解码往返/旋转 15°/非本系统格式忽略/垃圾字节/端点 200-可解码与 404;+3 test_force_name_probe:QR 命中短路[不触裁剪与 OCR]/未命中回退原链/异常静默回退);(2) npm run build 通过;runtime selfcheck 全部通过(含"二维码生成(qrcode)可用");(3) HTTP 冒烟 7/7(真实本地引擎):含码卷(旋转 3°)→ basis=qr + name='Li Ming'/student_id='20260123';二维码遮挡 40% → 无缝回退 basis=landmarks + OCR 身份(零中断);无码旧卷 → basis=landmarks 行为与之前一致;roster qr.png → 200 image/png 且可解码回身份;(4) 浏览器实机(真实页面):打印页页眉"本卷已绑定:Li Ming(20260123)"、QR 图片 370×370 加载成功、6 个定位块(4 主 2 次)几何正确;工作台对话框班级→学生级联、按钮禁用→可用;上传即识——注入含码卷后"识别中…"→ **60ms** 即稳定显示 "Li Ming";全程零 Console 错误、零失败请求。

**影响与边界**:契约仅新增 basis=qr 取值与 roster qr.png 新端点(零破坏);前端旧版式路径保留;设置项零新增;分发新增仅 qrcode(约 100KB 纯 Python,解码侧 cv2 已在包内)。仅评估:花名册"年龄"列扩展(QR 暂编码姓名+学号,年龄仍可由OCR 通道解析);更强鲁棒解码引擎(zxing-cpp)备选;班级批量生成 PDF 增强。
