"""设置中心服务(统一配置:字段注册表 / 视图 / 更新 / 探索项回退 / 连通性测试)

架构:
- SETTINGS_FIELDS 为全部配置项的**单点元数据注册表**(键名与 config.py 属性一一对应);
- 更新流程:校验(类型/范围/URL/developer 权限)→ 写回 .env(保留注释,原子替换)
  → 热字段 setattr 到配置单例(即时生效)→ 返回"需重启生效"清单;
- 探索性设置(exploratory):支持单侧"恢复默认"与整体"一键回退"
  (首次修改前把原值记入 app_settings 快照,回退即批量还原并清空快照);
- developer 字段:仅开发模式(dev_mode=true)可见可改,后端强制校验防止绕过前端。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BACKEND_DIR, Settings, settings
from app.models.db_models import AppSetting
from app.models.schemas import (
    ConnectivityResult,
    SettingsFieldOut,
    SettingsGroupOut,
    SettingsUpdateRequest,
    SettingsUpdateResult,
    SettingsViewResponse,
)
from app.services import audit_service, env_manager, llm_params

logger = logging.getLogger(__name__)

#: .env 文件路径(设置中心唯一持久化位置)
ENV_PATH = BACKEND_DIR / ".env"
#: 探索项回退快照在 app_settings 中的键名
SNAPSHOT_KEY = "exploratory_snapshot"
#: 字符串类配置值的长度上限
MAX_TEXT_LEN = 500
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SettingsValidationError(ValueError):
    """配置值不合法(路由层转 400)"""


class DeveloperOnlyError(SettingsValidationError):
    """开发者专属配置项(非开发模式修改时转 403)"""


@dataclass(frozen=True)
class FieldSpec:
    """配置项元数据(注册表条目)"""

    key: str  # .env 键名
    group: str
    label: str
    control: str  # switch|number|text|url|select|secret
    description: str = ""
    choices: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    sensitive: bool = False
    restart_required: bool = False
    exploratory: bool = False
    audience: str = "normal"  # normal | developer
    attr: str | None = None  # Settings 属性名(缺省 = key 小写)
    validator: str = ""  # 取值校验器名(services/llm_params 提供;空 = 无)

    @property
    def settings_attr(self) -> str:
        return self.attr or self.key.lower()


@dataclass(frozen=True)
class GroupSpec:
    id: str
    title: str
    description: str = ""


GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec("basic", "基础运行", "默认管线、演示模式与任务并发"),
    GroupSpec("pipeline_a", "管线 A · 本地 VLM", "本地模型端点(数据不出内网、零 API 成本)"),
    GroupSpec("pipeline_b_ocr", "管线 B · 识别(OCR)", "手写识别提供者与连接参数"),
    GroupSpec("pipeline_b_grading", "管线 B · 评分(DeepSeek)", "结构化评分 LLM 与深度推理开关"),
    GroupSpec("upload", "上传与图片处理", "预处理增强与批量上传护栏"),
    GroupSpec("teaching", "教学与报告", "及格线统计口径与学生版报告显示项(探索)"),
    GroupSpec("storage", "存储与高级(需重启)", "数据库 / 上传目录 / 端口 / 并发(开发者项)"),
    GroupSpec("security", "安全与访问", "设置接口访问令牌与运行模式"),
)

FIELDS: tuple[FieldSpec, ...] = (
    # ---------- 基础运行 ----------
    FieldSpec("DEFAULT_PIPELINE", "basic", "默认管线", "select",
              "被批改工作台默认选中的管线", choices=("PIPELINE_A_LOCAL", "PIPELINE_B_CLOUD")),
    FieldSpec("MOCK_MODE", "basic", "演示模式", "switch",
              "开启后所有批改返回样例数据、不调用真实模型(界面顶部会显示提示条)"),
    FieldSpec("ALLOW_AUTO_FALLBACK", "basic", "自动故障转移(A→B)", "switch",
              "管线 A 网络失败时自动改用云端管线 B 重跑"),
    FieldSpec("MAX_CONCURRENT_TASKS", "basic", "并发任务数", "number",
              "批改队列工作池数量(修改后需重启生效)", min_value=1, max_value=16,
              restart_required=True, audience="developer"),
    FieldSpec("DEFAULT_GRADING_STANDARD", "basic", "默认评分标准", "select",
              "新任务默认评分标准(批改工作台初始值)", choices=("GAOKAO", "DSD")),
    FieldSpec("DEFAULT_DETAIL_LEVEL", "basic", "默认细致度", "select",
              "新任务默认细致度(批改工作台初始值)", choices=("LOW", "MEDIUM", "HIGH")),
    # ---------- 管线 A ----------
    FieldSpec("LOCAL_VLM_BASE_URL", "pipeline_a", "端点地址", "url",
              "OpenAI 兼容端点,如 http://127.0.0.1:8000/v1"),
    FieldSpec("LOCAL_VLM_API_KEY", "pipeline_a", "API Key", "secret", "本地端点可为占位值", sensitive=True),
    FieldSpec("LOCAL_VLM_MODEL", "pipeline_a", "模型名", "text", "如 /models/Qwen3.8-27B-Q4_K_M"),
    FieldSpec("LOCAL_VLM_TIMEOUT", "pipeline_a", "超时(秒)", "number",
              "本地推理较慢,建议 300~900", min_value=10, max_value=3600),
    # ---- 管线 A 超参数(三态:auto=自动适配 / omit=不发送该键 / 具体值;详见 services/llm_params.py) ----
    FieldSpec("LOCAL_VLM_TEMPERATURE", "pipeline_a", "temperature(采样温度)", "text",
              "auto=自动适配(默认,未做任何配置时与历史行为一致);omit=不发送该键;"
              "或填 0~2 数值(批改任务建议低温,如 0.2)", validator="temperature", exploratory=True),
    FieldSpec("LOCAL_VLM_TOP_P", "pipeline_a", "top_p(核采样)", "text",
              "auto=自动适配(默认,画像未声明时不会发送);omit=不发送该键;或填 0~1 数值",
              validator="top_p", exploratory=True),
    FieldSpec("LOCAL_VLM_MAX_TOKENS", "pipeline_a", "max_tokens(最大生成数)", "text",
              "auto=自动适配(默认,调用点未指定时不发送);omit=不发送该键;或填 ≥1 的整数",
              validator="max_tokens", exploratory=True),
    FieldSpec("LOCAL_VLM_PRESENCE_PENALTY", "pipeline_a", "presence_penalty(主题惩罚)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填 -2~2 数值",
              validator="presence_penalty", exploratory=True),
    FieldSpec("LOCAL_VLM_FREQUENCY_PENALTY", "pipeline_a", "frequency_penalty(重复惩罚)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填 -2~2 数值",
              validator="frequency_penalty", exploratory=True),
    FieldSpec("LOCAL_VLM_SEED", "pipeline_a", "seed(随机种子)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填整数(固定种子便于复现)",
              validator="seed", exploratory=True),
    FieldSpec("LOCAL_VLM_STOP", "pipeline_a", "stop(停止序列)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填一个/多个序列(英文逗号分隔)",
              validator="stop", exploratory=True),
    FieldSpec("LOCAL_VLM_RESPONSE_FORMAT", "pipeline_a", "response_format(JSON 输出)", "select",
              "auto=跟随调用点请求(默认请求 json_object);omit=不发送该键;或强制 json_object/text",
              choices=("auto", "json_object", "text", "omit"),
              validator="response_format", exploratory=True),
    FieldSpec("LOCAL_VLM_STREAM", "pipeline_a", "stream(流式)", "select",
              "auto=发送 false(默认,系统按非流式读取响应);omit=不发送该键;不提供 true",
              choices=("auto", "false", "omit"), validator="stream", exploratory=True),
    FieldSpec("LOCAL_VLM_EXTRA_PARAMS", "pipeline_a", "自定义参数(扩展)", "text",
              "额外超参键值,分号分隔(如 min_p=0.05;repetition_penalty=1.05);值含英文逗号则按数组解析;"
              "与固定超参重名时以固定项为准;auto=不附加(默认)",
              validator="extra_params", exploratory=True),
    # ---------- 管线 B · OCR ----------
    FieldSpec("LOCAL_OCR_ONLY", "pipeline_b_ocr", "本地 OCR 模式", "switch",
              "开启后识别(OCR)完全在本地完成(RapidOCR,离线 CPU),不调用任何云端/远程 OCR 端点"
              "(含 OCR 端点与 Azure);评分仍按所选管线执行;需本地 OCR 组件(缺失时请重跑首次安装/一键自检);默认关闭"),
    FieldSpec("OCR_PROVIDER", "pipeline_b_ocr", "识别提供者", "select",
              "vlm_openai=OpenAI 兼容视觉模型;azure=Azure 计算机视觉",
              choices=("vlm_openai", "azure")),
    FieldSpec("OCR_BASE_URL", "pipeline_b_ocr", "端点地址", "url",
              "vlm_openai 模式使用,如 https://dashscope.aliyuncs.com/compatible-mode/v1"),
    FieldSpec("OCR_API_KEY", "pipeline_b_ocr", "API Key", "secret", "vlm_openai 模式的密钥", sensitive=True),
    FieldSpec("OCR_MODEL", "pipeline_b_ocr", "模型名", "text", "如 qwen2.5-vl-72b-instruct"),
    FieldSpec("OCR_ANOMALY_ENABLED", "pipeline_b_ocr", "转录异常检测", "switch",
              "检测畸变词/断词粘连/标签行混入等异常并自动重试(默认开启)"),
    FieldSpec("OCR_ANOMALY_THRESHOLD", "pipeline_b_ocr", "异常命中阈值", "number",
              "加权评分≥阈值判为异常(默认 4;调高更宽容)", min_value=1, max_value=20),
    FieldSpec("OCR_ANOMALY_MAX_RETRY", "pipeline_b_ocr", "异常自动重试次数", "number",
              "B=OCR 重跑次数,A=整管线重跑次数(默认 1;0=只检测不重试)", min_value=0, max_value=3),
    FieldSpec("OCR_ANOMALY_FORCE_REVIEW", "pipeline_b_ocr", "异常时强制人工复核", "switch",
              "重试用尽仍异常时:进入人工复核流程(默认开启)"),
    FieldSpec("OCR_TIMEOUT", "pipeline_b_ocr", "超时(秒)", "number", min_value=10, max_value=1200),
    FieldSpec("AZURE_OCR_ENDPOINT", "pipeline_b_ocr", "Azure 端点", "url",
              "azure 模式使用,如 https://<resource>.cognitiveservices.azure.com/"),
    FieldSpec("AZURE_OCR_KEY", "pipeline_b_ocr", "Azure 密钥", "secret", "azure 模式的订阅密钥", sensitive=True),
    # ---- OCR 超参数(三态:auto=自动适配 / omit=不发送该键 / 具体值) ----
    FieldSpec("OCR_TEMPERATURE", "pipeline_b_ocr", "temperature(采样温度)", "text",
              "auto=自动适配(默认,未做任何配置时与历史行为一致);omit=不发送该键;"
              "或填 0~2 数值;端点模型不支持时会自动剔除并记住(见「自动适配记录」)",
              validator="temperature", exploratory=True),
    FieldSpec("OCR_TOP_P", "pipeline_b_ocr", "top_p(核采样)", "text",
              "auto=自动适配(默认,画像未声明时不会发送);omit=不发送该键;或填 0~1 数值",
              validator="top_p", exploratory=True),
    FieldSpec("OCR_MAX_TOKENS", "pipeline_b_ocr", "max_tokens(最大生成数)", "text",
              "auto=自动适配(默认,调用点未指定时不发送);omit=不发送该键;或填 ≥1 的整数",
              validator="max_tokens", exploratory=True),
    FieldSpec("OCR_PRESENCE_PENALTY", "pipeline_b_ocr", "presence_penalty(主题惩罚)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填 -2~2 数值",
              validator="presence_penalty", exploratory=True),
    FieldSpec("OCR_FREQUENCY_PENALTY", "pipeline_b_ocr", "frequency_penalty(重复惩罚)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填 -2~2 数值",
              validator="frequency_penalty", exploratory=True),
    FieldSpec("OCR_SEED", "pipeline_b_ocr", "seed(随机种子)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填整数(固定种子便于复现)",
              validator="seed", exploratory=True),
    FieldSpec("OCR_STOP", "pipeline_b_ocr", "stop(停止序列)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填一个/多个序列(英文逗号分隔)",
              validator="stop", exploratory=True),
    FieldSpec("OCR_RESPONSE_FORMAT", "pipeline_b_ocr", "response_format(JSON 输出)", "select",
              "auto=跟随调用点请求(默认请求 json_object);omit=不发送该键;或强制 json_object/text",
              choices=("auto", "json_object", "text", "omit"),
              validator="response_format", exploratory=True),
    FieldSpec("OCR_STREAM", "pipeline_b_ocr", "stream(流式)", "select",
              "auto=发送 false(默认,系统按非流式读取响应);omit=不发送该键;不提供 true",
              choices=("auto", "false", "omit"), validator="stream", exploratory=True),
    FieldSpec("OCR_EXTRA_PARAMS", "pipeline_b_ocr", "自定义参数(扩展)", "text",
              "额外超参键值,分号分隔(如 min_p=0.05;repetition_penalty=1.05);值含英文逗号则按数组解析;"
              "与固定超参重名时以固定项为准;auto=不附加(默认)",
              validator="extra_params", exploratory=True),
    # ---------- 管线 B · 评分 ----------
    FieldSpec("DEEPSEEK_BASE_URL", "pipeline_b_grading", "端点地址", "url",
              "DeepSeek OpenAI 兼容地址"),
    FieldSpec("DEEPSEEK_API_KEY", "pipeline_b_grading", "API Key", "secret", "评分模型的密钥", sensitive=True),
    FieldSpec("DEEPSEEK_MODEL", "pipeline_b_grading", "常规评分模型", "text", "如 deepseek-chat"),
    FieldSpec("DEEPSEEK_REASONING_MODEL", "pipeline_b_grading", "深度推理模型", "text",
              "如 deepseek-reasoner"),
    FieldSpec("DEEPSEEK_TIMEOUT", "pipeline_b_grading", "超时(秒)", "number", min_value=10, max_value=1800),
    FieldSpec("DEEPSEEK_USE_REASONING", "pipeline_b_grading", "深度推理模式", "switch",
              "使用推理模型评分(更慢更细致,建议高细致度场景开启)", exploratory=True),
    # ---- DeepSeek 评分超参数(三态:auto=自动适配 / omit=不发送该键 / 具体值) ----
    FieldSpec("DEEPSEEK_TEMPERATURE", "pipeline_b_grading", "temperature(采样温度)", "text",
              "auto=自动适配(默认,未做任何配置时与历史行为一致);omit=不发送该键;"
              "或填 0~2 数值(批改任务建议低温,如 0.2)",
              validator="temperature", exploratory=True),
    FieldSpec("DEEPSEEK_TOP_P", "pipeline_b_grading", "top_p(核采样)", "text",
              "auto=自动适配(默认,画像未声明时不会发送);omit=不发送该键;或填 0~1 数值",
              validator="top_p", exploratory=True),
    FieldSpec("DEEPSEEK_MAX_TOKENS", "pipeline_b_grading", "max_tokens(最大生成数)", "text",
              "auto=自动适配(默认,调用点未指定时不发送);omit=不发送该键;或填 ≥1 的整数",
              validator="max_tokens", exploratory=True),
    FieldSpec("DEEPSEEK_PRESENCE_PENALTY", "pipeline_b_grading", "presence_penalty(主题惩罚)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填 -2~2 数值",
              validator="presence_penalty", exploratory=True),
    FieldSpec("DEEPSEEK_FREQUENCY_PENALTY", "pipeline_b_grading", "frequency_penalty(重复惩罚)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填 -2~2 数值",
              validator="frequency_penalty", exploratory=True),
    FieldSpec("DEEPSEEK_SEED", "pipeline_b_grading", "seed(随机种子)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填整数(固定种子便于复现)",
              validator="seed", exploratory=True),
    FieldSpec("DEEPSEEK_STOP", "pipeline_b_grading", "stop(停止序列)", "text",
              "auto=自动适配(默认不发送);omit=不发送该键;或填一个/多个序列(英文逗号分隔)",
              validator="stop", exploratory=True),
    FieldSpec("DEEPSEEK_RESPONSE_FORMAT", "pipeline_b_grading", "response_format(JSON 输出)", "select",
              "auto=跟随调用点请求(默认请求 json_object);omit=不发送该键;或强制 json_object/text",
              choices=("auto", "json_object", "text", "omit"),
              validator="response_format", exploratory=True),
    FieldSpec("DEEPSEEK_STREAM", "pipeline_b_grading", "stream(流式)", "select",
              "auto=发送 false(默认,系统按非流式读取响应);omit=不发送该键;不提供 true",
              choices=("auto", "false", "omit"), validator="stream", exploratory=True),
    FieldSpec("DEEPSEEK_EXTRA_PARAMS", "pipeline_b_grading", "自定义参数(扩展)", "text",
              "额外超参键值,分号分隔(如 min_p=0.05;repetition_penalty=1.05);值含英文逗号则按数组解析;"
              "与固定超参重名时以固定项为准;auto=不附加(默认)",
              validator="extra_params", exploratory=True),
    # ---------- 上传与图片 ----------
    FieldSpec("IMAGE_PREPROCESS", "upload", "图片自动预处理", "switch",
              "EXIF 纠偏 / 去阴影 / 对比度增强 / 保守裁边(总开关)"),
    FieldSpec("IMAGE_SHADOW_REMOVAL", "upload", "去阴影/光照均衡", "switch",
              "手机拍照常见阴影消除;对均匀光照近似无影响", exploratory=True),
    FieldSpec("IMAGE_GRAYSCALE", "upload", "灰度化", "switch",
              "黑白打印稿可开启以减小体积;彩色信息有助于区分笔迹颜色,默认关闭", exploratory=True),
    FieldSpec("MAX_BATCH_FILES", "upload", "单次文件数上限", "number",
              "含 ZIP 展开后的条目数,超出提示分批上传", min_value=1, max_value=2000),
    FieldSpec("MAX_UPLOAD_TOTAL_MB", "upload", "单次总量上限(MB)", "number",
              "含 ZIP 压缩包本身;解压后的总大小另有独立护栏", min_value=10, max_value=10000),
    FieldSpec("IMAGE_COMPRESS_ENABLED", "upload", "存档图片压缩", "switch",
              "上传即压缩 + 存量批量压缩总开关(默认开启;可关闭以保留原字节)"),
    FieldSpec("IMAGE_COMPRESS_MAX_SIDE", "upload", "压缩长边上限(px)", "number",
              "超过该长边才等比缩小(不放大);默认 2200", min_value=800, max_value=6000),
    FieldSpec("IMAGE_COMPRESS_QUALITY", "upload", "压缩质量(50-100)", "number",
              "JPEG/WEBP 有损质量;PNG 仅做无损 optimize;默认 88", min_value=50, max_value=100),
    FieldSpec("SHEET_ALIGN_ENABLED", "upload", "标准答题卷自动找平", "switch",
              "检测四角定位块并透视校正(仅标准卷生效,普通照片原样保留;默认开启)"),
    FieldSpec("NAME_PRE_OCR_ENABLED", "upload", "上传后姓名预识别", "switch",
              "提交后后台识别姓名/学号并在队列/审阅页即时显示(端点优先,本地 RapidOCR 兜底;默认开启)"),
    FieldSpec("FORCE_NAME_RECOGNITION", "upload", "强制性姓名识别", "switch",
              "开启后:在工作台选择/拖拽文件(含 ZIP/PDF)的瞬间,即用本地引擎(离线)识别姓名/年龄并显示在文件列表;"
              "不落盘、不建任务、不调用任何云端/远程端点;与「上传后姓名预识别」(任务创建后识别)互补;默认关闭"),
    FieldSpec("HANDWRITING_OCR_LEVEL", "upload", "手写识别增强档位", "select",
              "auto=按本机配置自动推荐(推荐依据:核心数与内存,设置页可见);"
              "light=仅花名册匹配;medium=加手写特征近邻;precise=多尺度近邻+学号校验;手动值优先",
              choices=("auto", "light", "medium", "precise")),
    # ---------- 启动行为 ----------
    FieldSpec("STARTUP_DATA_PICKER", "basic", "启动时选择班级数据包", "switch",
              "开启:每次打开先弹出数据包选择(可会话内跳过);关闭:直接进入工作台"),
    # ---------- 界面外观(三档由同一套设计 Token 派生) ----------
    FieldSpec("UI_PROFILE", "basic", "界面外观档位", "select",
              "balanced=均衡(默认);efficiency=极简(低配设备:灰阶界面、零动效、无阴影、"
              "仅保留低饱和语义色);premium=高级(页面过渡与光影质感);切换即时生效,与顶栏快捷按钮同步",
              choices=("balanced", "efficiency", "premium")),
    # ---------- 教学与报告(及格线口径与学生版显示项) ----------
    FieldSpec("SCORE_PASS_LINE", "teaching", "及格线(0~100)", "number",
              "统计分布 / 排行榜 / 考试报告的及格口径(默认 60 = 历史行为)", min_value=0, max_value=100),
    FieldSpec("STUDENT_REPORT_SHOW_SCORE", "teaching", "学生版显示得分", "switch",
              "在订正单顶部显示本次得分(默认关闭:弱化分数,由教师自行公布)", exploratory=True),
    FieldSpec("STUDENT_REPORT_SHOW_TIPS", "teaching", "学生版显示练习重点", "switch",
              "保留「下一步练习重点」板块(默认开启;关闭后订正单更精简)", exploratory=True),
    # ---------- 存储与高级(开发者项) ----------
    FieldSpec("UPLOAD_DIR", "storage", "上传目录", "text",
              "相对 backend 目录;修改后新上传文件写入新目录(需重启生效)",
              restart_required=True, audience="developer"),
    FieldSpec("DATABASE_URL", "storage", "数据库连接", "text",
              "SQLite 连接串;修改后需重启生效", restart_required=True, audience="developer"),
    FieldSpec("SERVER_PORT", "storage", "服务端口", "number",
              "默认 8765;修改后需重启并同步启动脚本", min_value=1024, max_value=65535,
              restart_required=True, audience="developer"),
    # ---------- 安全与访问 ----------
    FieldSpec("SETTINGS_ADMIN_TOKEN", "security", "访问令牌", "secret",
              "为空时仅本机可访问设置接口;配置后所有设置请求必须携带该令牌", sensitive=True),
    FieldSpec("DEV_MODE", "security", "开发模式", "switch",
              "开发者视角:放开全部配置项(含存储/端口/并发等隐藏项),与演示模式互不冲突"),
)

FIELD_MAP: dict[str, FieldSpec] = {spec.key.lower(): spec for spec in FIELDS}

#: 连通性测试允许携带的候选字段(先测后存)
_TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    "pipeline_a": ("local_vlm_base_url", "local_vlm_api_key", "local_vlm_model"),
    "ocr": ("ocr_provider", "ocr_base_url", "ocr_api_key", "azure_ocr_endpoint", "azure_ocr_key"),
    "deepseek": ("deepseek_base_url", "deepseek_api_key"),
}


# ---------------------------------------------------------
# 值处理工具
# ---------------------------------------------------------
def _field_default(spec: FieldSpec) -> Any:
    """取 Settings 模型字段的代码默认值(避免默认值双维护)"""
    model_field = Settings.model_fields.get(spec.settings_attr)
    return model_field.default if model_field is not None else None


def _to_env_str(value: Any) -> str:
    """Python 值 -> .env 字符串(bool 小写,浮点整数化)"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


def _mask_secret(text: str) -> str:
    """敏感值脱敏预览:短值全星号;长值保留首 3 尾 4"""
    if not text:
        return ""
    if len(text) <= 8:
        return "******"
    return f"{text[:3]}***{text[-4:]}"


def _normalize_value(spec: FieldSpec, raw: Any) -> tuple[str, Any, bool]:
    """校验并归一化单个配置值

    Returns:
        (env 字符串, Python 值, 是否为清除操作)

    Raises:
        SettingsValidationError
    """
    if spec.control == "secret":
        text = str(raw or "").strip()
        if not text:
            return "", "", True  # 空字符串 = 清除
        if _CONTROL_RE.search(text) or len(text) > MAX_TEXT_LEN:
            raise SettingsValidationError(f"{spec.label} 含非法字符或过长")
        if '"' in text or "'" in text:
            raise SettingsValidationError(f"{spec.label} 不能包含引号")
        return text, text, False

    if spec.control == "switch":
        value = bool(raw)
        return ("true" if value else "false"), value, False

    if spec.control == "number":
        try:
            number = float(raw)
        except (TypeError, ValueError) as e:
            raise SettingsValidationError(f"{spec.label} 必须是数字") from e
        if spec.min_value is not None and number < spec.min_value:
            raise SettingsValidationError(f"{spec.label} 不能小于 {spec.min_value:g}")
        if spec.max_value is not None and number > spec.max_value:
            raise SettingsValidationError(f"{spec.label} 不能大于 {spec.max_value:g}")
        default = _field_default(spec)
        value: int | float = int(number) if isinstance(default, int) else number
        return _to_env_str(value), value, False

    if spec.control == "select":
        text = str(raw or "").strip()
        if text not in spec.choices:
            raise SettingsValidationError(f"{spec.label} 取值必须是 {'/'.join(spec.choices)}")
        return text, text, False

    # text / url
    text = str(raw or "").strip()
    if _CONTROL_RE.search(text):
        raise SettingsValidationError(f"{spec.label} 不能包含控制字符")
    if len(text) > MAX_TEXT_LEN:
        raise SettingsValidationError(f"{spec.label} 过长(>500 字符)")
    if '"' in text or "'" in text:
        raise SettingsValidationError(f"{spec.label} 不能包含引号")
    if spec.control == "url" and text and not text.startswith(("http://", "https://")):
        raise SettingsValidationError(f"{spec.label} 必须是 http:// 或 https:// 开头的地址")
    if spec.validator:
        # 超参三态取值校验(auto / omit / 具体值;由 services/llm_params 提供规则)
        try:
            text = llm_params.validate_entry(spec.validator, text)
        except ValueError as e:
            raise SettingsValidationError(f"{spec.label}:{e}") from e
    return text, text, False


def _parse_env_value(spec: FieldSpec, env_str: str) -> Any:
    """把 .env 字符串解析回 Python 值(用于回退快照还原)"""
    text = env_str.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    if spec.control == "switch":
        return text.lower() in ("true", "1", "yes", "on")
    if spec.control == "number":
        default = _field_default(spec)
        number = float(text or 0)
        return int(number) if isinstance(default, int) else number
    return text


# ---------------------------------------------------------
# 探索项快照(app_settings KV)
# ---------------------------------------------------------
async def _load_snapshot(db: AsyncSession) -> dict[str, str]:
    row = await db.get(AppSetting, SNAPSHOT_KEY)
    if row is None or not row.value:
        return {}
    try:
        data = json.loads(row.value)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


async def _save_snapshot(db: AsyncSession, data: dict[str, str]) -> None:
    row = await db.get(AppSetting, SNAPSHOT_KEY)
    payload = json.dumps(data, ensure_ascii=False)
    if row is None:
        db.add(AppSetting(key=SNAPSHOT_KEY, value=payload))
    else:
        row.value = payload
    await db.commit()


async def snapshot_meta(db: AsyncSession) -> dict:
    """快照状态(供视图展示与前端按钮禁用)"""
    row = await db.get(AppSetting, SNAPSHOT_KEY)
    data = await _load_snapshot(db)
    return {
        "available": bool(data),
        "keys": sorted(data),
        "captured_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
    }


# ---------------------------------------------------------
# 视图 / 更新 / 恢复默认 / 一键回退
# ---------------------------------------------------------
def build_view() -> SettingsViewResponse:
    """构建设置视图:按分组输出元数据与当前值(敏感字段仅掩码)"""
    groups: list[SettingsGroupOut] = []
    for group in GROUPS:
        fields: list[SettingsFieldOut] = []
        for spec in FIELDS:
            if spec.group != group.id:
                continue
            current = getattr(settings, spec.settings_attr)
            default = _field_default(spec)
            field_out = SettingsFieldOut(
                key=spec.key,
                group=spec.group,
                label=spec.label,
                control=spec.control,
                description=spec.description,
                choices=list(spec.choices),
                min_value=spec.min_value,
                max_value=spec.max_value,
                restart_required=spec.restart_required,
                sensitive=spec.sensitive,
                exploratory=spec.exploratory,
                audience=spec.audience,
                default_value=default if isinstance(default, (bool, int, float, str)) else None,
            )
            if spec.sensitive:
                text = str(current or "")
                field_out.has_value = bool(text)
                field_out.masked = _mask_secret(text)
            else:
                field_out.value = current
            fields.append(field_out)
        groups.append(
            SettingsGroupOut(id=group.id, title=group.title, description=group.description, fields=fields)
        )
    return SettingsViewResponse(
        groups=groups,
        mock_mode=bool(settings.mock_mode),
        dev_mode=bool(settings.dev_mode),
        encryption_enabled=bool(settings.encryption_enabled),
        llm_learned_params=llm_params.learned_snapshot(),
    )


def _capture_snapshot_entries(
    snapshot: dict[str, str], spec: FieldSpec, current_env: str
) -> None:
    """首次修改探索项时记录"修改前值"(已有记录不覆盖)"""
    if spec.exploratory:
        snapshot.setdefault(spec.key, current_env)


async def apply_updates(db: AsyncSession, payload: SettingsUpdateRequest) -> SettingsUpdateResult:
    """应用设置更新:校验 -> 写回 .env -> 热生效 -> 汇总需重启项"""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return SettingsUpdateResult(message="没有需要更新的配置项")

    env_updates: dict[str, str] = {}
    hot_apply: dict[str, Any] = {}
    applied: list[str] = []
    cleared: list[str] = []
    restart_keys: list[str] = []
    snapshot_new: dict[str, str] = {}

    for field_name, raw in updates.items():
        spec = FIELD_MAP.get(field_name)
        if spec is None:
            raise SettingsValidationError(f"未知配置项:{field_name}")
        if spec.audience == "developer" and not settings.dev_mode:
            raise DeveloperOnlyError(
                f"配置项「{spec.label}」仅在开发模式下可修改;请先在「安全与访问」中开启开发模式"
            )
        env_str, py_value, is_clear = _normalize_value(spec, raw)
        current = getattr(settings, spec.settings_attr)
        if env_str == _to_env_str(current):
            continue  # 无变化
        _capture_snapshot_entries(snapshot_new, spec, _to_env_str(current))
        env_updates[spec.key] = env_str
        if spec.restart_required:
            restart_keys.append(spec.key)
        else:
            hot_apply[spec.settings_attr] = py_value
        if is_clear:
            cleared.append(spec.key)
        else:
            applied.append(spec.key)

    if not env_updates:
        return SettingsUpdateResult(message="配置无变化")

    if snapshot_new:
        snapshot = await _load_snapshot(db)
        for key, value in snapshot_new.items():
            snapshot.setdefault(key, value)
        await _save_snapshot(db, snapshot)

    env_manager.write_env_atomic(ENV_PATH, env_updates)
    for attr, value in hot_apply.items():
        setattr(settings, attr, value)

    logger.info("设置更新:%s", ", ".join(sorted(env_updates)))
    await audit_service.audit("settings.update", detail=", ".join(sorted(env_updates)))
    message = f"已保存 {len(env_updates)} 项配置"
    if restart_keys:
        message += f";其中 {len(restart_keys)} 项需重启后端生效"
    return SettingsUpdateResult(
        applied=sorted(applied),
        cleared=sorted(cleared),
        restart_required=sorted(restart_keys),
        message=message,
    )


async def reset_exploratory(db: AsyncSession, key: str) -> SettingsUpdateResult:
    """单项恢复默认(仅探索性设置;写前记快照,可再次回退)"""
    spec = FIELD_MAP.get((key or "").strip().lower())
    if spec is None:
        raise SettingsValidationError(f"未知配置项:{key}")
    if not spec.exploratory:
        raise SettingsValidationError(f"「{spec.label}」不是探索性设置,不支持恢复默认")

    default_value = _field_default(spec)
    env_str, py_value, _ = _normalize_value(spec, default_value)
    current = getattr(settings, spec.settings_attr)
    if env_str == _to_env_str(current):
        return SettingsUpdateResult(message=f"「{spec.label}」当前已是默认值")

    snapshot = await _load_snapshot(db)
    _capture_snapshot_entries(snapshot, spec, _to_env_str(current))
    await _save_snapshot(db, snapshot)

    env_manager.write_env_atomic(ENV_PATH, {spec.key: env_str})
    setattr(settings, spec.settings_attr, py_value)
    logger.info("探索项恢复默认:%s", spec.key)
    await audit_service.audit("settings.reset_exploratory", detail=spec.key)
    return SettingsUpdateResult(applied=[spec.key], message=f"「{spec.label}」已恢复默认值")


async def rollback_exploratory(db: AsyncSession) -> SettingsUpdateResult:
    """一键回退:把全部探索性改动还原到"本批改动之前"并清空快照"""
    snapshot = await _load_snapshot(db)
    if not snapshot:
        raise SettingsValidationError("暂无可回退的探索性改动")

    env_updates: dict[str, str] = {}
    hot_apply: dict[str, Any] = {}
    restored: list[str] = []
    for key, env_str in snapshot.items():
        spec = FIELD_MAP.get(key.lower())
        if spec is None:
            continue
        py_value = _parse_env_value(spec, env_str)
        env_updates[spec.key] = _to_env_str(py_value)
        hot_apply[spec.settings_attr] = py_value
        restored.append(spec.key)

    if not env_updates:
        await _save_snapshot(db, {})
        return SettingsUpdateResult(message="快照中没有可还原的配置项(已清空)")

    env_manager.write_env_atomic(ENV_PATH, env_updates)
    for attr, value in hot_apply.items():
        setattr(settings, attr, value)
    await _save_snapshot(db, {})
    logger.info("探索性设置一键回退:%s", ", ".join(restored))
    await audit_service.audit("settings.rollback", detail=", ".join(restored))
    return SettingsUpdateResult(
        applied=sorted(restored),
        message=f"已回退 {len(restored)} 项探索性改动到修改前的状态",
    )


# ---------------------------------------------------------
# 连通性测试(SSRF 防护:仅设置接口可调用 + 强制 http(s) + 禁重定向 + 5s 超时)
# ---------------------------------------------------------
async def _check_openai_endpoint(base_url: str, api_key: str, model: str | None) -> ConnectivityResult:
    url = (base_url or "").rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        return ConnectivityResult(ok=False, target="", detail=f"无法连接 {url}({type(e).__name__}),请检查地址与网络")
    latency = int((time.perf_counter() - started) * 1000)
    if response.status_code == 200:
        models: list[str] = []
        try:
            models = [str(m.get("id")) for m in response.json().get("data", []) if isinstance(m, dict)]
        except ValueError:
            pass
        detail = f"连接成功,延迟 {latency} ms"
        if models:
            detail += f",端点可见 {len(models)} 个模型"
            if model and model not in models:
                detail += f";当前配置模型 {model} 不在列表中(部分服务不返回完整列表,可忽略)"
        return ConnectivityResult(ok=True, target="", latency_ms=latency, detail=detail)
    if response.status_code in (401, 403):
        return ConnectivityResult(
            ok=False, target="", latency_ms=latency,
            detail=f"端点可达但鉴权失败(HTTP {response.status_code}),请检查 API Key",
        )
    return ConnectivityResult(ok=False, target="", latency_ms=latency, detail=f"端点返回 HTTP {response.status_code}")


#: 视觉失败特征(与 ocr_anomaly 同口径;探针响应含这些短语即判为不支持视觉)
_VISION_PROBE_FAILURES = (
    "no image",
    "cannot see",
    "can't see",
    "收到图片",
    "收到任何图片",
    "无法查看",
)


def _vision_probe_data_url() -> str:
    """生成探针用最小合法图片(8×8 白底 PNG 的 data URL)"""
    import base64
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _classify_vision_probe(status_code: int, body: str) -> tuple[bool, str]:
    """视觉探针响应归类为(可用, 说明);纯函数便于单测与文案统一"""
    text = (body or "")[:200]
    lowered = text.lower()
    if status_code == 200:
        if any(key in lowered for key in _VISION_PROBE_FAILURES):
            return False, "⚠ 该端点/模型疑似不支持图片输入(模型自述未收到图片)"
        return True, "视觉输入可用"
    if any(key in lowered for key in ("image", "vision", "multimodal", "unsupported", "invalid")):
        return False, f"⚠ 该端点/模型不支持图片输入(HTTP {status_code}):{text}"
    return False, f"视觉探针返回 HTTP {status_code}:{text}"


async def _check_openai_vision(base_url: str, api_key: str, model: str | None) -> ConnectivityResult:
    """视觉探针:发送含最小图片的 chat 请求,验证端点/模型是否真正接受图片输入"""
    url = (base_url or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model or "vision-probe",
        "max_tokens": 8,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "只回复 OK"},
                    {"type": "image_url", "image_url": {"url": _vision_probe_data_url()}},
                ],
            }
        ],
    }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as e:
        return ConnectivityResult(
            ok=False, target="", detail=f"视觉探针无法连接 {url}({type(e).__name__}),请检查地址与网络"
        )
    latency = int((time.perf_counter() - started) * 1000)
    ok, note = _classify_vision_probe(response.status_code, response.text)
    return ConnectivityResult(ok=ok, target="", latency_ms=latency, detail=f"{note}(延迟 {latency} ms)")


async def _check_azure_endpoint(endpoint: str, key: str) -> ConnectivityResult:
    started = time.perf_counter()
    headers = {"Ocp-Apim-Subscription-Key": key} if key else {}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(endpoint, headers=headers)
    except httpx.HTTPError as e:
        return ConnectivityResult(ok=False, target="", detail=f"无法连接 {endpoint}({type(e).__name__})")
    latency = int((time.perf_counter() - started) * 1000)
    if response.status_code in (200, 401, 403):
        note = "可达但密钥待验证" if response.status_code in (401, 403) else "连接成功"
        return ConnectivityResult(ok=True, target="", latency_ms=latency, detail=f"Azure 端点{note}(HTTP {response.status_code})")
    return ConnectivityResult(ok=False, target="", latency_ms=latency, detail=f"端点返回 HTTP {response.status_code}")


async def test_connectivity(target: str, overrides: dict | None = None) -> ConnectivityResult:
    """测试管线连通性;可携带未保存的候选值(先测后存)"""
    allowed = set(_TARGET_FIELDS.get(target, ()))
    values: dict[str, Any] = {}
    for key, value in (overrides or {}).items():
        if key not in allowed:
            raise SettingsValidationError(f"连通性测试不支持字段:{key}(仅允许 {', '.join(sorted(allowed))})")
        values[key] = value

    def val(name: str) -> Any:
        return values.get(name, getattr(settings, name))

    if target == "pipeline_a":
        result = await _check_openai_endpoint(str(val("local_vlm_base_url")), str(val("local_vlm_api_key") or ""), str(val("local_vlm_model") or ""))
    elif target == "deepseek":
        result = await _check_openai_endpoint(str(val("deepseek_base_url")), str(val("deepseek_api_key") or ""), None)
    else:  # ocr
        provider = str(val("ocr_provider") or "vlm_openai")
        if provider == "azure":
            result = await _check_azure_endpoint(str(val("azure_ocr_endpoint") or ""), str(val("azure_ocr_key") or ""))
        else:
            base_url, api_key, model = (
                str(val("ocr_base_url")),
                str(val("ocr_api_key") or ""),
                str(val("ocr_model") or ""),
            )
            result = await _check_openai_endpoint(base_url, api_key, model)
            if result.ok:
                # 视觉探针:先于任务发现“端点不支持图片输入”类配置问题
                vision = await _check_openai_vision(base_url, api_key, model)
                if vision.ok:
                    result.detail = f"{result.detail};视觉输入可用"
                    result.latency_ms = vision.latency_ms
                else:
                    result.ok = False
                    result.detail = f"{result.detail};{vision.detail}"
    result.target = target
    return result
