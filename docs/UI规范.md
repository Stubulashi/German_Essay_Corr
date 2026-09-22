# UI 规范(全站统一约定)

> 适用范围:`frontend/src` 下所有页面与组件。
> 设计 Token 的唯一来源是 `src/theme.ts`,页面内禁止再造一套颜色 / 圆角 / 阴影约定。
> 新增页面前请先阅读本规范,并按下文"新增页面清单"逐步执行。

---

## 一、设计 Token(来源:src/theme.ts)

| Token | 取值 | 说明 |
|---|---|---|
| 主色 | `#3F51B5`(深色模式 `#7986CB`) | 品牌靛蓝,用于主操作与选中态 |
| 辅色 | `#F59E0B` | 强调与高亮 |
| 圆角 | 卡片 16 / 对话框 20 / 手风琴 12 / 控件 10 / 标签 8 | 由主题组件覆盖注入,无需重复声明 |
| 描边 | `rgba(63,81,181,.10)`(浅)/ `rgba(255,255,255,.08)`(深) | 卡片、纸张、手风琴、对话框统一 hairline |
| 阴影 | 浅色模式单一柔和投影;深色模式无阴影 | 由 MuiCard 统一注入 |
| 字体层级 | h4 页面标题(700)→ h6 区块标题(600)→ subtitle1/2(600)→ body2(行高 1.65)→ caption 辅助 | 深浅模式共用 |
| 背景 | `#F4F6FB` 工作区渐变 + `#FFFFFF` 纸面 | AppShell 注入 |

## 二、组件规范(已由主题全局统一,页面只需选对用法)

### 按钮
- 页面头部 / 工具区次要操作:`size="small"` + `color="inherit"`(文本按钮);
- 页面主操作:`variant="contained"` + `size="large"` + `fullWidth`(如提交卡片);
- 危险操作:`color="error" / "warning"`,必须弹对话框二次确认;
- 全局已统一:无阴影(disableElevation)、圆角 10、字重 600、`textTransform: none`。

### 表单控件
- 密集配置面板统一 `size="small"`;多行文本用 `multiline + minRows`;日期用 `type="date"`;
- 输入框圆角已全局统一为 10;不要逐页覆盖圆角;
- 开关配置用 `Switch + FormControlLabel`(标题 + caption 说明);互斥模式选择用 `ToggleButtonGroup`;
- 键值型说明使用 `caption`(text.disabled)小标题 + body2 内容的两层结构。

### 反馈组件
- 空状态:统一 `暂无…`(可附一句引导),空数据卡片用 `Alert severity="info" variant="outlined"` 或居中占位;
- 加载态:`LinearProgress`(已注入圆角)或 `CircularProgress`(按钮内 14、页面级 28+);
- 错误提示:统一 `Alert severity="error"`,文案一律经 `extractErrorMessage` 提取后端 detail;
- 成功提示:优先使用就地提示(Alert / Snackbar),避免全局弹窗打断。

### 容器
- 卡片 `Card`:圆角 16 + hairline(主题注入);内部 `CardContent` 间距按密度取 p:2~3;
- 对话框 `Dialog`:圆角 20;标题走 `DialogTitle`;确认类对话框遵循"确认xxx?"+ 后果说明 + 取消/确认两按钮;
- 抽屉 `Drawer`:仅用于全站级(左侧导航、右侧设置面板);内容型界面不得塞入抽屉;
- 表格:优先 `size="small"`;表头已全局加粗且 `whiteSpace: nowrap`;过宽时外层 `overflow: auto`。

### 图标
- 默认使用 `@mui/icons-material` 的 **Outlined** 系列,与全站线性风格一致;
- 按钮内图标随按钮尺寸,独立展示图标 14~20px,不使用实心风格图标混排。

## 三、页面结构规范

1. **首屏标题区**:一律使用 `components/PageHeader`(`title` 必填;`subtitle` 一句话说明;`chips` 展示数据口径 / 计数;`actions` 放右侧操作),不要自写 h4/h5;
2. **卡片分区**:筛选区 → 主内容区 → 辅助区的顺序组织;卡片纵向间距固定 `3`(24px),栅格 `spacing={3}`;
3. **主次分层**:主操作 `contained` 显著;次要操作文本按钮;辅助信息用 `caption` / `text.disabled`;
4. **三态齐备**:任何异步数据区都要处理 加载中 / 空数据 / 错误 三种状态。

## 四、响应式规范

- `md(900px)` 为导航形态分界:桌面端常驻左侧抽屉;小屏自动切换为临时抽屉 + 顶栏汉堡按钮(由 AppShell 统一实现,页面无需处理);
- 页面内容栅格使用 MUI Grid2 响应式 `size={{ xs: 12, md: … }}` 或 CSS Grid 断点,保证 xs 单列;
- 多卡片 / 多按钮组使用 `Stack` + `flexWrap`,避免小屏溢出;
- 表格数据在窄屏允许横向滚动,不做逐列隐藏(教师端以信息完整优先)。

## 五、文案规范

| 场景 | 规范 | 示例 |
|---|---|---|
| 按钮 | 动词短语,不加句号与感叹号 | 刷新 / 保存 / 取消 / 重试全部失败 |
| 空状态 | "暂无 + 对象"(可补一句引导) | 暂无批改任务 / 暂无错因数据 |
| 加载态 | "正在 + 动作 + …" | 正在加载… / 正在提交… / 正在创建任务… |
| 错误提示 | 直接展示后端 detail(统一提取) | 文件过大(>50MB):xxx.jpg |
| 确认对话框 | 标题"确认xxx?"+ 后果说明 | 确认删除选中任务?删除后不可恢复 |
| 说明文字 | 名词短语 + 分号分隔,一句话讲清 | 勾选任务后可批量重试、导出、打印或删除。 |

## 六、使用指南(GuidePage)内容维护约定

- 内容数据集中在 `src/guide/guideContent.ts`(纯前端静态数据);
- 每个功能点必须包含四要素:作用(purpose)/ 适用场景(scenes)/ 操作步骤(steps)/ 注意事项(notes,可选),关键参数放 `params`;
- 新增功能模块时,同步在 `GUIDE_SECTIONS` 增加对应分组与条目(保持与导航模块一一对应);
- 文案使用教师口语化表达,避免开发术语。

## 七、本次统一改动清单

| 文件 | 改动 |
|---|---|
| `src/theme.ts` | 扩展统一 Token:MuiIconButton / ToggleButton / OutlinedInput / Accordion / Dialog / Menu / Alert / LinearProgress / Tab 等组件全局样式;抽离统一描边色与 hairline;字体层级与平滑滚动补全 |
| `src/components/PageHeader.tsx` | 新增:全站统一页面标题区(标题/说明/徽章/操作,响应式换行) |
| `src/components/AppShell.tsx` | 重构:桌面常驻 / 小屏临时抽屉(md 断点)+ 顶栏汉堡按钮;导航新增「使用指南」;顶栏新增使用指南入口;页面标题映射补全 |
| `src/pages/GuidePage.tsx` | 新增:使用指南页(目录跳转 + 关键字搜索 + 可折叠分组) |
| `src/guide/guideContent.ts` | 新增:9 大模块、30+ 功能点的结构化指南内容 |
| `src/App.tsx` | 注册 `/guide` 路由 |
| `src/pages/CorrectionPage.tsx` | 标题区改用 PageHeader(含使用指南快捷入口) |
| `src/pages/AnalyticsPage.tsx` | 标题区改用 PageHeader(统一 h4 层级 + 说明 + 操作区) |
| `src/pages/QueuePage.tsx` | 标题区改用 PageHeader(计数/批次徽章 + 五个操作按钮归位) |
| `src/pages/StudentsPage.tsx` | 标题区改用 PageHeader |
| `src/components/SettingsDrawer.tsx` | 底部新增「查看使用指南」入口;说明文案统一 |

## 八、新增页面清单(checklist)

- [ ] 在 `App.tsx` 注册路由;若为一级模块,在 `AppShell.tsx` 的 `NAV_ITEMS` 与 `pageTitle` 中登记;
- [ ] 首屏使用 `PageHeader`(标题 + 一句话说明);
- [ ] 内容按卡片分区,复用主题控件,不新建样式体系;
- [ ] 覆盖 加载 / 空 / 错误 三态;
- [ ] 小屏(<900px)检查:抽屉收起、栅格单列、按钮换行;
- [ ] 需要新 Token 时:先改 `theme.ts` 并更新本文档,再在页面引用;
- [ ] 功能对外可见时,同步更新「使用指南」内容(`guideContent.ts`)。

---

## 九、UI 性能档位(三档,2026-09-20 新增;同日深化:极简灰阶化 / 高级尊享补全)

### 设计原则

- 三档**完全由 `src/theme.ts` 同一套 Token 派生**(`getTheme(mode, profile)` 与 `PROFILE 变体`),页面内不感知、不重建样式体系;
- 切换**即时生效,无需刷新**;持久化双轨:localStorage 即时缓存 + 后端设置项 `UI_PROFILE` 为权威(启动校准,读取失败时保持本地值)。

### 三档定义

| 档位 | 定位 | 关键差异 |
|---|---|---|
| **均衡 balanced**(默认) | 既有表现,零变化 | — |
| **极简 efficiency** | 低性能电脑(集显/低内存),**最简界面** | 过渡/动画时长全表归零 + 全局样式兜底;关闭 Ripple 波纹;shadows 全 none(保留 hairline 描边,层次不丢);去除工作区渐变;**灰阶调色板**(装饰色全部去色,仅保留低饱和语义色 success/warning/error/info);纯色背景;Charts `skipAnimation`;默认色 Chip 去填充(转描边) |
| **高级 premium** | 尊享质感(第四代 Material 方向) | 统一 `cubic-bezier(0.22,1,0.36,1)` 曲线与 200/300/420ms 分层时长;多层低透明“环境影+主色微光”;**页面切换内容淡入**(`.premium-page-enter`,AppShell 重放);按钮悬停轻抬/按下回弹 + contained 主色微光;IconButton 悬停缩放;卡片顶部内高光/hover 影跃升+边框微亮;`focus-visible` 光晕与品牌色选中文本;工作区双径向渐变;PageHeader 标题下渐变细线 |

### 页面接入约定(少量消费点)

- 需要感知档位的仅限**极少数全局视觉点**:
  - `useUiProfile()`(来自 `src/theme.ts`)获取当前档位;
  - 工作区背景/Logo 渐变等用 `profileEffects(profile, mode)` 取值(AppShell 已接入);
  - Charts 传 `skipAnimation={profile === 'efficiency'}`;
  - premium 专属点缀(如 PageHeader 渐变细线)直接 `profile === 'premium'` 条件渲染;
- **打印类页面(答题卷/续写纸/抄写卡/报告打印)三档完全一致**,不接入档位。

### 档位变更清单(相对均衡档)

- **极简档删减**:全部颜色装饰(品牌色→中性灰阶)、全部阴影、全部渐变(背景/Logo)、全部动效与波纹、图表动画、默认 Chip 填充底——保留低饱和语义色与全部功能按钮;
- **高级档新增**:页面内容淡入过渡、按钮/图标/菜单项悬停动效与主色微光、卡片光影跃升与顶部高光、焦点光晕、品牌色选中文本、标题渐变细线、双径向工作区背景;
- 两档均**不新增页面级样式文件**;打印页不受影响。

### 新增页面清单增补(checklist)

- [ ] 新增任何动效/过渡:确认极简档下能被 theme 归零(走 theme.transitions 或被全局样式覆盖,不自建无限/长时长动画);
- [ ] 新增任何视觉装饰(阴影/渐变/模糊):三档各验证一次,且**禁止引入 backdrop-filter 毛玻璃**与大面积高模糊(与效率档性能结论冲突);
- [ ] 新增 Charts:必传 `skipAnimation={profile === 'efficiency'}`。
