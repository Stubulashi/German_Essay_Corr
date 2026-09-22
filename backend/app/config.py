"""全局配置模块

使用 pydantic-settings 从 .env 文件与环境变量加载配置。
所有配置项均与 `.env.example` 中的键名一一对应,做到"配置与代码解耦"。
"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 后端根目录(即 backend/ 目录),用于解析相对路径
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用全局配置(从 .env 加载)"""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 全局开关 ----------
    # 默认管线
    default_pipeline: Literal["PIPELINE_A_LOCAL", "PIPELINE_B_CLOUD"] = "PIPELINE_A_LOCAL"
    # 管线 A 网络错误/超时时是否允许自动故障转移至管线 B
    allow_auto_fallback: bool = True
    # 演示/开发模式:true 时返回样例数据,不调用真实模型
    mock_mode: bool = False

    # ---------- 管线 A:本地 VLM(OpenAI 兼容端点) ----------
    local_vlm_base_url: str = "http://127.0.0.1:8000/v1"
    local_vlm_api_key: str = "sk-local"
    local_vlm_model: str = "/models/Qwen3.8-27B-Q4_K_M"
    local_vlm_timeout: float = 600.0
    # ---- 管线 A 超参数(三态:auto=自动适配 / omit=不发送该键 / 具体值;见 services/llm_params.py) ----
    local_vlm_temperature: str = "auto"
    local_vlm_top_p: str = "auto"
    local_vlm_max_tokens: str = "auto"
    local_vlm_presence_penalty: str = "auto"
    local_vlm_frequency_penalty: str = "auto"
    local_vlm_seed: str = "auto"
    local_vlm_stop: str = "auto"
    local_vlm_response_format: str = "auto"
    local_vlm_stream: str = "auto"
    local_vlm_extra_params: str = "auto"

    # ---------- 管线 B 步骤 1:视觉 / OCR ----------
    ocr_provider: Literal["vlm_openai", "azure"] = "vlm_openai"
    ocr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ocr_api_key: str = ""
    ocr_model: str = "qwen2.5-vl-72b-instruct"
    ocr_timeout: float = 120.0
    # ---- OCR 超参数(三态:auto=自动适配 / omit=不发送该键 / 具体值) ----
    ocr_temperature: str = "auto"
    ocr_top_p: str = "auto"
    ocr_max_tokens: str = "auto"
    ocr_presence_penalty: str = "auto"
    ocr_frequency_penalty: str = "auto"
    ocr_seed: str = "auto"
    ocr_stop: str = "auto"
    ocr_response_format: str = "auto"
    ocr_stream: str = "auto"
    ocr_extra_params: str = "auto"
    # 纯本地 OCR 模式:识别(OCR)完全本地化(RapidOCR 离线 CPU,不调用任何云端/远程 OCR 端点);
    # 评分环节不受影响,仍按所选管线执行;默认关闭(保持既有行为)
    local_ocr_only: bool = False
    # ---------- OCR 转录异常检测与自愈(兜底;见 services/ocr_anomaly.py) ----------
    # 检测总开关(关闭后不做异常判定与重试)
    ocr_anomaly_enabled: bool = True
    # 异常命中阈值(加权分;默认 4)
    ocr_anomaly_threshold: int = 4
    # 自动重试预算:B 为 OCR 重跑次数,A 为整管线重跑次数(0=只检测不重试)
    ocr_anomaly_max_retry: int = 1
    # 重试用尽仍异常时:B 强制进入人工复核(未开复核时)
    ocr_anomaly_force_review: bool = True
    azure_ocr_endpoint: str = ""
    azure_ocr_key: str = ""

    # ---------- 管线 B 步骤 3:文本评分 LLM(DeepSeek) ----------
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_reasoning_model: str = "deepseek-reasoner"
    deepseek_timeout: float = 180.0
    # 是否使用深度推理模型(deepseek-reasoner / DeepSeek-R1)进行评分;false 时用 deepseek-chat
    deepseek_use_reasoning: bool = False
    # ---- DeepSeek 评分超参数(三态:auto=自动适配 / omit=不发送该键 / 具体值) ----
    deepseek_temperature: str = "auto"
    deepseek_top_p: str = "auto"
    deepseek_max_tokens: str = "auto"
    deepseek_presence_penalty: str = "auto"
    deepseek_frequency_penalty: str = "auto"
    deepseek_seed: str = "auto"
    deepseek_stop: str = "auto"
    deepseek_response_format: str = "auto"
    deepseek_stream: str = "auto"
    deepseek_extra_params: str = "auto"

    # ---------- 队列与存储 ----------
    max_concurrent_tasks: int = 2
    database_url: str = "sqlite+aiosqlite:///./data/corrector.db"
    upload_dir: str = "./data/uploads"
    server_port: int = 8765
    # 上传图片自动预处理(EXIF 纠偏 / 去阴影 / 对比度 / 保守裁边)
    image_preprocess: bool = True
    # 去阴影/光照均衡(手机拍照常见阴影;对均匀光照近似恒等,默认开启)
    image_shadow_removal: bool = True
    # 灰度化(默认关闭:彩色有助区分蓝黑笔迹与红笔痕迹;黑白打印稿可手动开启)
    image_grayscale: bool = False
    # 单次上传文件数上限(含 ZIP 展开后的条目数)
    max_batch_files: int = 200
    # 单次请求上传总量上限(MB,含 ZIP 压缩包本身)
    max_upload_total_mb: int = 500

    # ---------- 存档图片压缩(答卷/作文原图本地瘦身) ----------
    # 总开关:开启后上传即压缩,并提供“压缩存量图片”批量入口;可关闭以保留原字节
    image_compress_enabled: bool = True
    # 长边上限(px):超过才等比缩小,不放大
    image_compress_max_side: int = 2200
    # 有损质量(50-100):JPEG/WEBP 使用;PNG 仅做无损 optimize
    image_compress_quality: int = 88

    # ---------- 标准答题卷(四角定位块)自动找平 ----------
    # 上传后检测四角黑块并透视校正为规范页;未命中(普通照片)原样保留
    sheet_align_enabled: bool = True

    # ---------- 上传后姓名预识别 ----------
    # 提交后后台识别姓名/学号并即时展示(已配置端点优先、本地 RapidOCR 兜底;失败静默)
    name_pre_ocr_enabled: bool = True
    # 强制性姓名识别:在工作台选择/拖拽文件(含 ZIP/PDF)的瞬间,即用本地引擎(离线)
    # 识别姓名/年龄/学号并显示在文件列表;不落盘、不建任务、不调用任何云端/远程端点;默认关闭
    force_name_recognition: bool = False

    # ---------- 手写样本模型(姓名识别增强档位) ----------
    # auto = 按本机核心/内存自动推荐;light/medium/precise = 手动指定(优先级最高)
    handwriting_ocr_level: str = "auto"

    # ---------- 界面外观档位(三档,同一套设计 Token 派生;详见 docs/UI规范.md) ----------
    # balanced=均衡(默认,既有表现) / efficiency=效率优先(低配设备真削减) / premium=高级
    ui_profile: str = "balanced"

    # ---------- 教学与报告口径(设置中心"教学与报告") ----------
    # 及格线(0~100):统计分布/排行榜/考试报告共用口径;默认 60 = 历史行为
    score_pass_line: int = 60
    # 批改工作台默认评分标准/细致度(新任务初始值;默认与历史行为一致)
    default_grading_standard: str = "GAOKAO"
    default_detail_level: str = "MEDIUM"
    # 学生版报告显示项(探索性;默认与历史行为一致:不显示分数、显示练习重点)
    student_report_show_score: bool = False
    student_report_show_tips: bool = True

    # ---------- 设置中心 / 开发模式 / 数据加密 ----------
    # 设置接口访问令牌:为空时仅本机回环可访问设置相关接口;配置后所有设置请求必须携带 X-Admin-Token
    settings_admin_token: str = ""
    # 启动时弹出「班级数据包选择」对话框(设置中心可关闭;关闭后启动直入工作台,保持历史行为)
    startup_data_picker: bool = True
    # 开发模式:开启后设置面板放开"开发者专属"配置项(数据库/目录/端口/并发等)
    dev_mode: bool = False
    # 学生数据加密总开关(请通过设置中心"数据加密"向导开启/关闭,勿手工修改)
    encryption_enabled: bool = False
    # 开发模式万能密码(仅 dev_mode=true 时可能生效):极端情况下的管理员兜底解锁
    # 专用开关:需与 DEV_MODE 同时为 true 且 DEV_MASTER_PASSWORD 为 ≥64 位十六进制长 Hash
    dev_master_enabled: bool = False
    # 万能密码(长 Hash 串,管理员预先约定;仅本地 .env 注入,禁止提交仓库/下发前端)
    dev_master_password: str = ""

    # ---------- 派生属性 ----------
    @property
    def max_upload_total_bytes(self) -> int:
        """单次请求上传总量上限(字节)"""
        return max(1, self.max_upload_total_mb) * 1024 * 1024

    @property
    def upload_path(self) -> Path:
        """上传目录的绝对路径(不存在时自动创建)"""
        p = (BACKEND_DIR / self.upload_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_path(self) -> Path:
        """数据目录(数据库所在目录)"""
        p = (BACKEND_DIR / "data").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def pipeline_a_ready(self) -> bool:
        """管线 A 是否已配置(本地 VLM 端点非空即视为可用)"""
        return bool(self.local_vlm_base_url.strip())

    def pipeline_b_ready(self) -> bool:
        """管线 B 是否已配置(OCR 与 DeepSeek 密钥均需就绪)"""
        ocr_ok = bool(self.azure_ocr_key) if self.ocr_provider == "azure" else bool(self.ocr_api_key)
        return ocr_ok and bool(self.deepseek_api_key)


# 全局单例配置对象
settings = Settings()
